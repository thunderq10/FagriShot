"""Build original Arabic lettering masks from private, aligned tutorial screenshots and OCR boxes.

The optional draft directory holds the per-original-table exports from inference_pack.py.
Decoded game images, OCR and the English audit stay in the maintainer's private work area.
"""
import argparse
import base64
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import zstandard

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fagrishot.arabic import catalog_data
from fagrishot.arabic_font import visual_text


def normalized(text):
    return " ".join(text.replace("’", "'").replace("“", '"').replace("”", '"').split()).casefold()


def dictionary(args):
    reviewed = catalog_data(sorted(args.reviewed.glob("*.json")))
    drafts = catalog_data(sorted(args.draft.glob("*.json"))) if args.draft else {}
    seen = defaultdict(set)
    for line in args.catalog.open(encoding="utf8"):
        row = json.loads(line)
        if len(row["parts"]) != 1 or not isinstance(row["parts"][0], str):
            continue
        name, label = row["file"], row["label"]
        record = reviewed.get(name, {}).get(label)
        origin = "reviewed" if record else "draft"
        record = record or drafts.get(name, {}).get(label)
        if record and record["source_sha256"] == row["source_sha256"] and len(record["parts"]) == 1:
            source, text = normalized(row["parts"][0]), " ".join(record["parts"][0].split())
            if text and re.search(r"[\u0621-\u064a]", text):
                seen[source].add((origin, text))
    words = {}
    for source, options in seen.items():
        preferred = [v for origin, v in options if origin == "reviewed"]
        variants = preferred or [v for _, v in options]
        if len(set(variants)) == 1:
            words[source] = (variants[0], "reviewed" if preferred else "draft")
    for group in json.loads(args.terms.read_bytes()).values():
        for source, text in group.items():
            words[normalized(source)] = (text, "reviewed")
    if args.approved:
        for source, text in json.loads(args.approved.read_bytes()).items():
            words[normalized(source)] = (text, "reviewed")
    return words


def groups(line):
    group = []
    for word in sorted(line["words"], key=lambda w: w["rect"][0]):
        x, _, _, h = word["rect"]
        if group:
            px, _, pw, _ = group[-1]["rect"]
            if x - px - pw > max(9, 1.1 * h):
                yield group
                group = []
        group.append(word)
    if group:
        yield group


def candidates(lines, words):
    for line in lines:
        for group in groups(line):
            # Strip isolated numeric values and controller OCR at the edges only.
            while group and re.fullmatch(r"(?:[Oeo@+©®ø]|\d+(?:/\d+|%)?)", group[0]["text"]):
                group = group[1:]
            while group and re.fullmatch(r"(?:[O@+©®ø]|\d+(?:/\d+|%)?)", group[-1]["text"]):
                group = group[:-1]
            if not group:
                continue
            key = normalized(" ".join(w["text"] for w in group))
            if key not in words or len(key) < 3:
                continue
            rects = [w["rect"] for w in group]
            x = min(r[0] for r in rects)
            y = min(r[1] for r in rects)
            right = max(r[0] + r[2] for r in rects)
            bottom = max(r[1] + r[3] for r in rects)
            if 5 <= bottom-y <= 30 and right-x >= 11:
                yield key, words[key], (x, y, right, bottom)


def color(value):
    r, g, b = map(int, np.clip(np.rint(value), 0, 255))
    return (r << 16) | (g << 8) | b


@lru_cache(maxsize=128)
def font(face, size):
    return ImageFont.truetype(face, size, layout_engine=ImageFont.Layout.BASIC)


def letter(text, face, w, h):
    # The bitmap glyph compositor has no separate positioning for optional vowel marks.
    logical = "".join(c for c in unicodedata.normalize("NFC", text) if not (
        unicodedata.category(c) == "Mn" and "ARABIC" in unicodedata.name(c, "")))
    visual = visual_text(logical)
    for size in range(min(320, h*5+6), 26, -1):
        f = font(face, size)
        left, top, right, bottom = f.getbbox(visual)
        if 0 < right-left <= (w-1)*4 and 0 < bottom-top <= (h-1)*4:
            break
    else:
        return
    mask = Image.new("L",(w*4,h*4))
    draw = ImageDraw.Draw(mask)
    draw.text(((w*4-right+left)//2-left, (h*4-bottom+top)//2-top), visual, fill=255, font=f)
    mask = mask.resize((w,h),Image.Resampling.LANCZOS)
    return ((np.asarray(mask, dtype=np.uint16)*64+127)//255).astype(np.uint8)


def caption(en, jp, rect, phrase, face):
    x0,y0,x1,y1=rect
    a, b = max(0, int((x0-1)//4)), max(0, int((y0-1)//4))
    end, bot = min(200,math.ceil((x1+1)/4)), min(112,math.ceil((y1+1)/4))
    if end <= a or bot <= b:
        return
    im=en[4*b:4*bot,4*a:4*end]
    japan=jp[4*b:4*bot,4*a:4*end]
    diff=np.max(np.abs(im-japan),axis=2)
    # A genuine language variation must differ inside the OCR strokes. Keep textured scenery out of the paint area.
    if np.mean(diff > 28) < .075:
        return
    pixels=im.reshape(-1,3)
    rgb=np.median(pixels,axis=0)
    distance=np.max(abs(im-rgb),axis=2)
    quiet=distance<27
    if quiet.mean()<.36:
        return
    bg=np.median(im[quiet],axis=0)
    distances=np.max(abs(im-bg),axis=2)
    core=(distances>=max(48,np.percentile(distances,85)))
    if core.sum()<8 or (diff[core]>30).mean()<.32:
        return
    fg=np.median(im[distances>=max(48,np.percentile(distances,92))],axis=0)
    if np.max(abs(fg-bg))<49:
        return
    mask=letter(phrase,face,(end-a)*4,(bot-b)*4)
    if mask is None:
        return
    encoded=base64.b64encode(mask.reshape(bot-b,4,end-a,4).transpose(0,2,1,3).tobytes()).decode("ascii")
    return [a,b,end-a,bot-b,color(bg),color(fg),encoded]


def placed_caption(image, rect, phrase, face, ink_rgb=None):
    # Manually identified words include stylized banners on textured ground. Store references to
    # nearby clean original BC7 blocks. The shipped recipe contains no source pixels.
    import cv2
    from scipy.spatial import cKDTree

    x0,y0,x1,y1 = rect
    title = y1-y0 >= 31 and x1-x0 >= 80
    pad_x, pad_y = (7,5) if title else (1,1)
    a,b = max(0,math.floor((x0-pad_x)/4)),max(0,math.floor((y0-pad_y)/4))
    e,h = min(200,math.ceil((x1+pad_x)/4)),min(112,math.ceil((y1+pad_y)/4))
    w,hi = e-a,h-b
    rgb = np.asarray(image[:448], dtype=np.uint8)
    roi = image[b*4:h*4,a*4:e*4]
    bg = np.median(roi.reshape(-1,3),axis=0)
    deviation = np.max(abs(roi-bg),axis=2)
    fg = np.asarray(ink_rgb) if ink_rgb else np.median(roi[deviation>=np.percentile(deviation,94)],axis=0)
    if np.max(abs(fg-bg)) < 35:
        return
    pad = max(3,hi//2) if title else 0
    lettering = letter(phrase,face,w*4,hi*4-pad*2)
    if lettering is None:
        return
    mask = np.pad(lettering,((pad,pad),(0,0)))
    if title:
        # The source battle callouts combine a thick shadow, outline and lettering. Cover the entire
        # English title with a clean inset nameplate using the panel's own dark and accent colours.
        luma = roi.mean(axis=2)
        dark = np.median(roi[luma <= np.percentile(luma,35)],axis=0)
        xx = np.arange(w*4)
        accents = np.minimum(1, np.minimum(xx+1,w*4-xx)/max(w*.6,1))
        mask[4] = np.maximum(mask[4], np.rint(13*accents).astype(np.uint8))
        mask[-5] = np.maximum(mask[-5], np.rint(11*accents).astype(np.uint8))
        enc=base64.b64encode(mask.reshape(hi,4,w,4).transpose(0,2,1,3).tobytes()).decode("ascii")
        return [a,b,w,hi,color(dark),color(fg),enc,"plate"]
    delta = (fg-bg)
    strength = ((roi-bg)*delta).sum(axis=2) / max(1,np.sum(delta**2))
    paint = np.zeros(rgb.shape[:2],dtype=np.uint8)
    paint[b*4:h*4,a*4:e*4] = (strength>.20).astype(np.uint8)*255
    radius = max(3, min(11, (hi//2)*2+1))
    paint = cv2.dilate(paint,np.ones((radius,radius),dtype=np.uint8))
    clean = cv2.inpaint(rgb,paint,4,cv2.INPAINT_TELEA)
    dirty = paint.reshape(112,4,200,4).max(axis=(1,3))>0
    gy,gx = np.mgrid[:112,:200]
    inside=(gx>=a)&(gx<e)&(gy>=b)&(gy<h)
    near = (gx>=a-10)&(gx<e+10)&(gy>=b-8)&(gy<h+8)&(~dirty)&(~inside)
    if not near.any():
        return
    def features(array):
        tiles=array.reshape(112,4,200,4,3)
        return np.dstack((tiles.mean(axis=(1,3)),tiles.std(axis=(1,3))*.35,gx*.6,gy*1.4))
    available=np.flatnonzero(near)
    tree=cKDTree(features(rgb)[near])
    distances,indices=tree.query(features(clean)[b:h,a:e].reshape(-1,8))
    originals=(gy*200+gx)[b:h,a:e].copy()
    replace=dirty[b:h,a:e]
    originals[replace] = available[indices].reshape(hi,w)[replace]
    enc=base64.b64encode(mask.reshape(hi,4,w,4).transpose(0,2,1,3).tobytes()).decode("ascii")
    refs=base64.b64encode(originals.astype("<u2").tobytes()).decode("ascii")
    return [a,b,w,hi,color(bg),color(fg),enc,refs]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ("art","catalog","reviewed","terms","output","audit"):
        ap.add_argument("--"+key,required=True,type=Path)
    ap.add_argument("--draft",type=Path)
    ap.add_argument("--approved",type=Path)
    ap.add_argument("--alternative-ocr",type=Path)
    ap.add_argument("--placements",type=Path,help="private visually verified captions, keyed by tutorial basename")
    ap.add_argument("--face",type=Path,default=ROOT/"runtime/assets/fonts/DejaVuSans.ttf")
    args=ap.parse_args()
    words=dictionary(args)
    hashes=json.loads((args.art/"textures.json").read_bytes())
    placed=json.loads(args.placements.read_bytes()) if args.placements else {}
    recipe, notes={},{}
    for src in sorted((args.art/"ocr").glob("*.json")):
        name=src.stem
        if name=="TUT_Hub_AutoSave_001" or (hashes[name]["USen"]==hashes[name]["JPja"] and name not in placed):
            continue
        boxes=list(candidates(json.loads(src.read_bytes()),words))
        if args.alternative_ocr and (args.alternative_ocr / src.name).is_file():
            boxes.extend(candidates(json.loads((args.alternative_ocr / src.name).read_bytes()), words))
        if not boxes and name not in placed:
            continue
        en=np.array(Image.open(args.art/"USen"/(name+".png")).convert("RGB"),dtype=np.int16)
        jp=np.array(Image.open(args.art/"JPja"/(name+".png")).convert("RGB"),dtype=np.int16)
        touched=set()
        painted=[]
        rows=[]
        for entry in placed.get(name,[]):
            item=placed_caption(en,entry["rect"],entry["arabic"],str(args.face),entry.get("ink"))
            if item is None:
                continue
            x,y,w,h=item[:4]
            touched.update((i,j) for j in range(y,y+h) for i in range(x,x+w))
            painted.append(item)
            rows.append({"source":entry["source"],"arabic":entry["arabic"],"origin":"reviewed","rect":item[:4]})
        for source,(text,origin),rect in sorted(boxes,key=lambda box:-len(box[0])):
            item=caption(en,jp,rect,text,str(args.face))
            if item is None:
                continue
            x,y,w,h=item[:4]
            blocks={(i,j) for i in range(x,x+w) for j in range(y,y+h)}
            if touched & blocks:
                continue
            touched.update(blocks)
            painted.append(item)
            rows.append({"source":source,"arabic":text,"origin":origin,"rect":item[:4]})
        if painted:
            recipe[name]={"sha256":hashes[name]["USen"],"captions":painted}
            notes[name]=rows
    data=json.dumps(recipe,separators=(",",":")).encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(zstandard.ZstdCompressor(level=12).compress(data))
    args.audit.write_text(json.dumps(notes,ensure_ascii=False,indent=1),encoding="utf8")
    print(len(recipe),"tutorials,",sum(len(row) for row in notes.values()),"localized labels",
          dict(Counter(p["origin"] for rows in notes.values() for p in rows)),flush=True)


if __name__=="__main__":
    main()

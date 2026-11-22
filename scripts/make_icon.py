"""Generate the original FagriShot monogram assets from simple polygons (Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
im = Image.new("RGBA", (256, 256))
draw = ImageDraw.Draw(im)
draw.rounded_rectangle((4, 4, 251, 251), radius=50, fill="#234f3f")
draw.polygon([(49,195),(80,59),(205,59),(192,87),(105,87),(98,116),(171,116),
              (157,143),(91,143),(79,195)], fill="#f7f2d7")
draw.polygon([(106,195),(127,159),(151,159),(132,195)], fill="#e6b768")
for folder, name, size in [("launcher/public","icon.png",256),("launcher/src-tauri/icons","icon.png",256),
                           ("launcher/src-tauri/icons","32x32.png",32),("launcher/src-tauri/icons","128x128.png",128),
                           ("launcher/src-tauri/icons","128x128@2x.png",256)]:
    im.resize((size,size),Image.Resampling.LANCZOS).save(root/folder/name)
for target in ("launcher/src-tauri/icons/icon.ico", "game-build/fefw/res/fefw.ico"):
    im.save(root/target,sizes=[(s,s) for s in (16,24,32,48,64,128,256)])

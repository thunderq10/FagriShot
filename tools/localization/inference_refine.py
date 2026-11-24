"""Run a private translation-model pass against an explicitly loopback-bound llama.cpp server."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys
import time

from inference import (
    check,
    connect,
    editable,
    file_hash,
    lexical,
    normalized,
    rows,
    sections,
)


def api(port, method, path, body=None):
    # A fixed loopback IP and http.client prevent proxies, remote URLs and redirects.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    try:
        payload = (
            None
            if body is None
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        conn.request(method, path, payload, {"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError(
                f"Local inference server returned HTTP {response.status}."
            )
        return json.load(response)
    finally:
        conn.close()


def units(catalog, jp_dir=None):
    selected = {}
    first_cue = {}
    grouped = list(rows(catalog))
    table = None
    previous = ""
    if jp_dir:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from fagrishot.localization import read_resource
    for i, row in enumerate(grouped):
        if jp_dir and previous != row["file"]:
            previous = row["file"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.msbt\.zst", previous):
                raise ValueError("Unsafe catalog resource name.")
            path = jp_dir / previous
            table = read_resource(path) if path.is_file() else None
        japanese = ""
        if table and len(editable(row)) == 1 and row["label"] in table.labels:
            pieces = [s for s in table.entry(row["label"]).parts if isinstance(s, str)]
            if len(pieces) == 1:
                japanese = normalized(pieces[0])[:260]
        group = row["label"].rsplit("_", 1)[0]
        neighbors = []
        for j in (i - 1, i + 1):
            if not 0 <= j < len(grouped):
                continue
            other = grouped[j]
            if (
                row["file"] == other["file"]
                and other["label"].rsplit("_", 1)[0] == group
            ):
                text = " ".join(normalized(s) for s in editable(other) if s.strip())
                if text:
                    neighbors.append(text[:210])
        for part in editable(row):
            for s in sections(part):
                if lexical(s):
                    cue = (row["file"], row["label"])
                    if s not in first_cue:
                        first_cue[s] = cue
                        selected[s] = (row["file"], tuple(neighbors), japanese)
                    elif first_cue[s] != cue:
                        # One draft is reused for this text in every cue. Its first
                        # occurrence cannot establish a speaker for the other scenes.
                        selected[s] = ("", (), "")
    return selected


def name_matcher(terms):
    # These names are also common English words. Their reviewed MSBT name labels
    # are in the authored catalog. A match in arbitrary dialogue is ambiguous.
    ambiguous = {
        "Long",
        "Jester",
        "Creek",
        "Morrow",
        "Maxim",
        "Gladius",
        "Jasmine",
        "Mars",
        "Castor",
        "Aurora",
    }
    names = [n for n in terms.get("names", {}) if n not in ambiguous]
    if not names:
        return re.compile(r"(?!)")
    return re.compile(
        r"(?<!\w)(?:"
        + "|".join(map(re.escape, sorted(names, key=len, reverse=True)))
        + r")(?!\w)"
    )


def make_prompt(source, context, terms, names):
    file, neighbors, japanese = context
    hints = {name: terms["names"][name] for name in names.findall(source)}
    interface = file.startswith(
        ("System", "Skill", "Stat", "UI", "Guide", "Tutorial", "Item", "Facility")
    )
    if interface:
        for word in re.findall(r"\b[A-Z][A-Za-z.]{1,4}\b", source):
            if word in terms.get("ui", {}):
                hints[word] = terms["ui"][word]
    lines = []
    if not file:
        lines.append(
            "This text occurs in several cues. Use natural wording that works for different "
            "speakers and listeners when the English leaves their gender unspecified."
        )
    if hints:
        lines.append(
            "Approved terminology: "
            + " / ".join(k + " = " + v for k, v in hints.items())
            + "."
        )
    if neighbors:
        lines.append("Nearby text for context only: " + " / ".join(neighbors))
    if japanese:
        lines.append("Japanese reference for the same cue (context only): " + japanese)
    lines.append(
        "Translate the following segment into idiomatic Modern Standard Arabic, without additional explanation. "
        "Translate only the segment below. Keep every sentence, question, negation, number and name."
    )
    return "\n".join(lines) + "\n\n" + source


def refine(args):
    live = api(args.port, "GET", "/v1/models")
    if args.model.name not in {Path(m["id"]).name for m in live["data"]}:
        raise ValueError(f"The local server must be loaded with {args.model.name}.")
    terms = (
        json.loads(args.terms.read_bytes()) if args.terms else {"names": {}, "ui": {}}
    )
    names = name_matcher(terms)
    settings = {
        "model": file_hash(args.model),
        "terms": file_hash(args.terms) if args.terms else "",
        "recipe": 1 if args.plain else 2,
    }
    if args.plain:
        settings["plain"] = 1
    else:
        settings["catalog"] = file_hash(args.catalog)
    if args.editor:
        settings["editor"] = 1
    if args.jp_dir:
        settings["japanese"] = hashlib.sha256(
            "".join(
                file_hash(p) for p in sorted(args.jp_dir.glob("*.msbt.zst"))
            ).encode()
        ).hexdigest()
    profile = ("editor-" if args.editor else "hy-") + hashlib.sha256(
        json.dumps(settings, sort_keys=True).encode()
    ).hexdigest()[:20]
    contexts = units(args.catalog, args.jp_dir)
    with connect(args.work) as db:
        if args.pilot:
            queue = [r["english"] for r in json.loads(args.pilot.read_bytes())]
        elif args.pending:
            queue = json.loads(args.pending.read_bytes())
            if not isinstance(queue, list) or any(s not in contexts for s in queue):
                raise ValueError(
                    "Pending units must be authentic text from the private catalog."
                )
        elif args.selection == "flagged":
            queue = [
                r[0]
                for r in db.execute(
                    "SELECT source FROM translations WHERE profile=? AND issue<>''",
                    (args.base_profile,),
                )
            ]
        else:
            queue = list(contexts)
        done = {
            r[0]
            for r in db.execute(
                "SELECT source FROM translations WHERE profile=?", (profile,)
            )
        }
        queue = [s for s in queue if s not in done]
        if args.limit:
            queue = queue[: args.limit]
        receipt = json.dumps({"profile": profile, **settings}, indent=2)
        (args.work / (args.model.stem + "-profile.json")).write_text(
            receipt, encoding="utf-8"
        )
        (args.work / (profile + ".json")).write_text(receipt, encoding="utf-8")

        def translate(s):
            prompt = (
                "Translate the following segment into Modern Standard Arabic, without additional explanation.\n\n"
                + s
                if args.plain
                else make_prompt(s, contexts[s], terms, names)
            )
            body = {
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.15,
                "top_p": 0.65,
                "repeat_penalty": 1.05,
                "seed": 42,
                "max_tokens": min(600, max(64, len(s) * 2)),
                "stream": False,
            }
            if args.editor:
                body["messages"] = [
                    {
                        "role": "system",
                        "content": "أنت محرر تعريب للعبة تقمص أدوار خيالية. اكتب فصحى حية ومقتضبة تناسب المتكلم. "
                        "انقل المعنى الإنجليزي كاملا، بما فيه النفي والأسئلة والتهكم، وتجنب الترجمة الحرفية. استفد من السياق الياباني عند الحاجة. "
                        "لا تضف شرحا أو حوارا من السياق. التزم بالأسماء المعتمدة وأبق أرقام الأجهزة والرموز كما هي.",
                    },
                    {"role": "user", "content": prompt + "\n/no_think"},
                ]
                body["chat_template_kwargs"] = {"enable_thinking": False}
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "localized",
                        "schema": {
                            "type": "object",
                            "properties": {"arabic": {"type": "string"}},
                            "required": ["arabic"],
                            "additionalProperties": False,
                        },
                    },
                }
            reply = api(args.port, "POST", "/v1/chat/completions", body)["choices"][0]
            answer = reply["message"]["content"].strip()
            if args.editor:
                try:
                    answer = json.loads(answer)["arabic"].strip()
                except (KeyError, ValueError, TypeError, AttributeError):
                    return s, profile, "", "invalid-editor-output"
            issue = (
                check(s, answer)
                if reply.get("finish_reason") == "stop"
                else "incomplete"
            )
            return s, profile, answer, issue

        start = time.monotonic()
        count = flagged = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for begin in range(0, len(queue), args.workers * 4):
                for result in pool.map(
                    translate, queue[begin : begin + args.workers * 4]
                ):
                    db.execute(
                        "INSERT OR REPLACE INTO translations VALUES(?,?,?,?)", result
                    )
                    count += 1
                    flagged += bool(result[3])
                    if count % 8 == 0:
                        db.commit()
                db.commit()
                print(
                    f"{count}/{len(queue)} committed; {flagged} flagged; {count / (time.monotonic() - start):.1f} units/s",
                    flush=True,
                )


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--terms", type=Path)
    parser.add_argument(
        "--jp-dir",
        type=Path,
        help="Optional private original Msg/JPja folder for aligned single-text cues.",
    )
    parser.add_argument("--selection", choices=("all", "flagged"), default="flagged")
    parser.add_argument("--base-profile")
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--pending", type=Path)
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use the model vendor's single-segment translation instruction.",
    )
    parser.add_argument(
        "--editor",
        action="store_true",
        help="Use the Arabic RPG editor prompt and a single structured translation.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=24)
    a = parser.parse_args()
    if (
        not a.pilot
        and not a.pending
        and a.selection == "flagged"
        and not a.base_profile
    ):
        parser.error("Flagged selection needs a base model profile.")
    if not 1 <= a.port <= 65535 or a.workers < 1 or a.limit < 0:
        parser.error("Invalid port, worker count or limit.")
    refine(a)

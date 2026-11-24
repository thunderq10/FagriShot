"""Read the optional offline Arabic draft, one hash-verified message table at a time."""
import hashlib
import json
from pathlib import Path
import re


# Verified update 131072. Eligible labels contain nonblank ordinary text or a visible choice.
SOURCE_LABELS = 81103
ELIGIBLE_LABELS = 64503
FORMAT_ONLY = {"System.msbt.zst": {"MID_Sys_DateTime_MD_Message", "MID_Chapter_PartChapter"}}
_TABLE = re.compile(r"[A-Za-z0-9_-]+\.msbt\.zst")
_LIMIT = 16 * 1024 * 1024


class Campaign:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.manifest = self.folder / "manifest.json"
        self.files = {}
        self.digest = ""
        if not self.folder.exists():
            return
        if (self.folder.is_symlink() or self.folder.is_junction()
                or not self.manifest.is_file() or self.manifest.is_symlink()):
            raise ValueError("The Arabic campaign pack is incomplete. Re-extract the player.")
        raw = self.manifest.read_bytes()
        document = json.loads(raw)
        if (not isinstance(document, dict) or document.get("schema") != 1 or document.get("source_update") != 131072
                or document.get("source_labels") != SOURCE_LABELS or document.get("eligible_labels") != ELIGIBLE_LABELS
                or not isinstance(document.get("files"), dict)):
            raise ValueError("The Arabic campaign manifest does not match the supported game update.")
        for name, entry in document["files"].items():
            if (not _TABLE.fullmatch(name) or not isinstance(entry, dict)
                    or not isinstance(entry.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", entry["sha256"])
                    or type(entry.get("entries")) is not int or not 1 <= entry["entries"] <= ELIGIBLE_LABELS):
                raise ValueError("The Arabic campaign manifest contains an invalid table.")
        self.files = document["files"]
        if sum(e["entries"] for e in self.files.values()) > ELIGIBLE_LABELS:
            raise ValueError("The Arabic campaign entry count exceeds the game's source inventory.")
        expected = {name + ".json.zst" for name in self.files}
        actual = {p.name for p in self.folder.glob("*.json.zst") if p.is_file() and not p.is_symlink()}
        if actual != expected:
            raise ValueError("The Arabic campaign table files differ from the manifest. Re-extract the player.")
        self.digest = hashlib.sha256(raw).hexdigest()

    def entries(self, name):
        if name not in self.files:
            return {}
        import zstandard

        path = self.folder / (name + ".json.zst")
        if path.stat().st_size > _LIMIT:
            raise ValueError(f"The Arabic translation table is too large: {name}")
        blob = path.read_bytes()
        declared = self.files[name]
        if hashlib.sha256(blob).hexdigest() != declared["sha256"]:
            raise ValueError(f"The Arabic translation table has the wrong SHA-256: {name}")
        try:
            if not 0 < zstandard.frame_content_size(blob) <= _LIMIT:
                raise ValueError(f"The Arabic translation table has an invalid expanded size: {name}")
            raw = zstandard.ZstdDecompressor().decompress(blob, max_output_size=_LIMIT, allow_extra_data=False)
        except zstandard.ZstdError as exc:
            raise ValueError(f"The Arabic campaign table could not be decoded: {name}") from exc
        rows = json.loads(raw)
        if not isinstance(rows, dict) or len(rows) != declared["entries"]:
            raise ValueError(f"The Arabic campaign entry count differs from its manifest: {name}")
        for label, record in rows.items():
            if (not label or not isinstance(record, dict) or set(record) - {"source_sha256", "parts", "choices", "flow"}
                    or not isinstance(record.get("source_sha256"), str)
                    or not re.fullmatch("[0-9a-f]{64}", record["source_sha256"])
                    or ("flow" in record and record["flow"] != "auto")
                    or not isinstance(record.get("parts"), list) or not all(isinstance(p, str) for p in record["parts"])):
                raise ValueError(f"Invalid Arabic campaign entry: {name} / {label}")
            choices = record.get("choices", {})
            if (not isinstance(choices, dict) or any(not k.isascii() or not k.isdigit() or not isinstance(opts, list)
                    or len(opts) != 2 or not all(isinstance(s, str) for s in opts) for k, opts in choices.items())):
                raise ValueError(f"Invalid Arabic campaign choices: {name} / {label}")
        return rows

    def merged(self, name, reviewed):
        rows = self.entries(name)
        for label in rows.keys() & reviewed.keys():
            if rows[label]["source_sha256"] != reviewed[label]["source_sha256"]:
                raise ValueError(f"The reviewed and campaign sources disagree for {name} / {label}.")
        rows.update(reviewed)
        return rows

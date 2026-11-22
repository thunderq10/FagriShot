"""Isolated launcher protocol fixture. This script uses only synthetic folders and files."""
import json
import os
from pathlib import Path
import sys
import time


state = Path(os.environ["LOCALAPPDATA"]) / "FagriShot"
state.mkdir(parents=True, exist_ok=True)
receipt = state / "fixture-receipt.json"
action, *args = sys.argv[1:]
paths = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {"exefs": "", "romfs": ""}

if action == "status":
    print(json.dumps({**paths, "play_ready": receipt.is_file(), "locations_saved": receipt.is_file(), "build_available": True,
                      "exefs_present": bool(paths["exefs"]), "romfs_present": bool(paths["romfs"]),
                      "backup_ids": [], "settings": str(state), "saves": str(state / "saves"),
                      "logs": str(state / "logs"), "settings_warning": None}))
elif action == "build-and-prepare":
    assert len(args) == 4 and args[0] == "--exefs" and args[2] == "--romfs"
    assert Path(args[1]).is_dir() and Path(args[3]).is_dir()
    for i, phase in enumerate(("Checking game files", "Building game code", "Preparing shaders"), 1):
        print(f"[setup {i}/3] {phase}", flush=True)
        if i == 2 and (state / "pause").is_file():
            time.sleep(45)
        time.sleep(.1)
    receipt.write_text(json.dumps(dict(exefs=args[1], romfs=args[3])), encoding="utf-8")
elif action == "play":
    assert receipt.is_file()
    if args:
        assert args == ["--exefs", paths["exefs"], "--romfs", paths["romfs"]]
    print("Starting FagriShot.", flush=True)
    with (state / "plays.txt").open("a") as stream:
        print("fixture Play", file=stream)
else:
    raise SystemExit(f"Unexpected fixture action {action}")

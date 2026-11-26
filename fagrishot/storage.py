"""Persistent local state; build outputs and input directories are deliberately separate."""
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


def locations(*, create=True):
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        if not local.is_absolute():
            raise ValueError("Windows LOCALAPPDATA must point to an absolute user-data directory.")
        # Store Python virtualizes LocalAppData. Give the native game and shader tools the physical path.
        root = (local / "FagriShot").resolve()
        config, data, state, cache = root / "settings", root, root, root / "caches"
    else:
        home = Path.home()
        def xdg(name, fallback):
            value = Path(os.environ.get(name) or home / fallback)
            return (value if value.is_absolute() else home / fallback) / "fagrishot"
        config = xdg("XDG_CONFIG_HOME", ".config")
        data = xdg("XDG_DATA_HOME", ".local/share")
        state = xdg("XDG_STATE_HOME", ".local/state")
        cache = xdg("XDG_CACHE_HOME", ".cache")
    folders = {"settings": config, "saves": data / "saves", "backups": data / "backups",
               "mods": data / "mods", "logs": state / "logs", "caches": cache, "build": data / "build"}
    if create:
        for path in folders.values():
            path.mkdir(parents=True, exist_ok=True)
    return folders


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as out:
        temp = Path(out.name)
        try:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            out.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class OperationBusy(ValueError):
    """The other operation holding the lock must finish first."""


@contextmanager
def build_lock(path):
    """The OS releases the lock when a cancelled build process terminates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OperationBusy(f"Another FagriShot operation is using {path.name}. Wait for it to finish.") from exc
        yield


def check_separation(inputs, destinations):
    """Reject overlap in either direction, including a junction to an existing directory."""
    outputs = tuple(Path(dst).resolve() for dst in destinations)
    for path in inputs:
        src = Path(path).resolve()
        for dst in outputs:
            if src.is_relative_to(dst) or dst.is_relative_to(src):
                raise ValueError(f"Input and output folders overlap: {src} and {dst}. Select the extracted input subfolder.")

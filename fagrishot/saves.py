"""Snapshots of the isolated FagriShot save tree; publication and restore use same-volume renames."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from .storage import write_json

ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}")
KEEP = 10


def ordinary(path):
    s = path.lstat()
    if path.is_symlink() or getattr(s, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("A save or backup contains a link. Use an ordinary local save directory.")
    if not (stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode)):
        raise ValueError("Unrecognized file type in the save tree.")


def inventory(root):
    ordinary(root)
    if not root.is_dir():
        raise ValueError("The selected save tree is not a directory.")
    files = {}
    for item in sorted(root.iterdir()):
        ordinary(item)
        if item.is_dir():
            files.update({f"{item.name}/{k}": v for k, v in inventory(item).items()})
            files[item.name + "/"] = None
        else:
            with item.open("rb") as src:
                files[item.name] = hashlib.file_digest(src, "sha256").hexdigest()
    return files


def checked_copy(src, dest):
    expected = inventory(src)
    shutil.copytree(src, dest, symlinks=True)
    if inventory(dest) != expected or inventory(src) != expected:
        raise ValueError("Save content changed during the copy. Close the game and retry.")
    return expected


def list_backups(dirs):
    folder = dirs["backups"]
    if not folder.exists():
        return []
    ordinary(folder)
    return sorted(p.name for p in folder.iterdir() if ID.fullmatch(p.name) and p.is_dir())


def snapshot(dirs):
    saves, backups = dirs["saves"], dirs["backups"]
    if not saves.exists():
        return None
    ordinary(saves)
    if not any(saves.iterdir()):
        return None
    backups.mkdir(parents=True, exist_ok=True)
    ordinary(backups)
    key = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8]
    temp = backups / ("pending-" + key)
    temp.mkdir()
    try:
        files = checked_copy(saves, temp / "data")
        write_json(temp / "snapshot.json", {"schema": 1, "product": "FagriShot", "id": key, "files": files})
        temp.rename(backups / key)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    for old in list_backups(dirs)[:-KEEP]:
        # Only prune a directory that still contains a complete, authenticatable project snapshot.
        backup = backups / old
        verify(backup, old)
        shutil.rmtree(backup)
    return key


def verify(backup, key):
    ordinary(backup)
    record = backup / "snapshot.json"
    ordinary(record)
    descriptor = json.loads(record.read_text("utf-8"))
    actual = inventory(backup / "data")
    if descriptor != {"schema": 1, "product": "FagriShot", "id": key, "files": actual}:
        raise ValueError("Backup integrity check failed. Existing saves are unchanged.")
    return actual


def recover(dirs):
    saves = dirs["saves"]
    journal = saves.with_name(".fagrishot-restore.json")
    if not journal.exists():
        return
    ordinary(journal)
    record = json.loads(journal.read_text("utf-8"))
    key = record.get("previous")
    if not isinstance(key, str) or not ID.fullmatch(key):
        raise ValueError("The restore journal is invalid. Existing save trees have been preserved.")
    old = saves.with_name(".saves-before-restore")
    if old.exists():
        ordinary(old)
        if not saves.exists():
            old.rename(saves)
        else:
            expected = verify(dirs["backups"] / key, key)
            if inventory(old) != expected:
                raise ValueError("Previous saves changed during recovery. Both copies are retained.")
            shutil.rmtree(old)
    journal.unlink()


def restore(dirs, key):
    if not ID.fullmatch(key):
        raise ValueError("Select a backup ID from the backup list.")
    recover(dirs)
    ordinary(dirs["backups"])
    backup = dirs["backups"] / key
    expected = verify(backup, key)
    saves = dirs["saves"]
    saves.parent.mkdir(parents=True, exist_ok=True)
    temp = saves.with_name(".incoming-" + uuid.uuid4().hex)
    old = saves.with_name(".saves-before-restore")
    try:
        if checked_copy(backup / "data", temp) != expected:
            raise ValueError("Backup changed during restoration.")
        if old.exists():
            raise ValueError("The previous-save staging folder already exists. Its files have been preserved.")
        previous = snapshot(dirs)
        if previous:
            write_json(saves.with_name(".fagrishot-restore.json"), {"previous": previous})
            saves.rename(old)
        elif saves.exists():
            saves.rmdir()  # only an empty tree can be removed
        try:
            temp.rename(saves)
        except OSError:
            recover(dirs)
            raise
        recover(dirs)
    finally:
        if temp.exists():
            shutil.rmtree(temp)

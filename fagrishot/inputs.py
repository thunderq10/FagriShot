"""Read-only inspection of explicitly selected extracted game inputs."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat
import struct

from .file_state import directory_stamp, read_verified, write_verified


class InputError(ValueError):
    """An input or catalog cannot be used for the requested operation."""


def _plain_path(path, label, directory=False):
    path = Path(path).absolute()
    try:
        info = path.lstat()
    except OSError:
        raise InputError(f"{label} is missing or unreadable. Select the extracted input.") from None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise InputError(f"{label} contains a link or reparse point. Select an ordinary extracted copy.")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise InputError(f"{label} must be a regular {'directory' if directory else 'file'}.")
    return path


def _resolved_root(path, label):
    root = Path(path).resolve(strict=False)
    if root.parent == root:
        raise InputError(f"Select the extracted {label} directory inside the drive.")
    return root


def check_input_roots(exefs, romfs):
    """Check a prospective selection even before its directories exist."""
    left = _resolved_root(exefs, "ExeFS")
    right = _resolved_root(romfs, "RomFS")
    if left == right or left in right.parents or right in left.parents:
        raise InputError("Select separate, non-overlapping ExeFS and RomFS directories.")


def _stable_stat(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _file(path, label, inspect=None):
    _plain_path(path, label)
    with path.open("rb") as stream:
        start = os.fstat(stream.fileno())
        metadata = inspect(stream, start.st_size) if inspect else None
        stream.seek(0)
        digest = hashlib.sha256()
        size = 0
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
        if size != start.st_size or _stable_stat(start) != _stable_stat(path.lstat()):
            raise InputError(f"{label} changed during inspection. Finish extracting it and retry.")
    return size, digest.digest(), metadata


def _tree(root, label, inspect_entry=None):
    digest = hashlib.sha256(b"FagriShot extracted tree v1\0")
    files = total = 0

    def visit(folder):
        nonlocal files, total
        before = folder.lstat()
        for entry in sorted(folder.iterdir(), key=lambda path: path.name.encode("utf-8")):
            name = entry.relative_to(root).as_posix()
            path_bytes = name.encode("utf-8")
            is_directory = stat.S_ISDIR(entry.lstat().st_mode)
            digest.update(b"D" if is_directory else b"F")
            digest.update(struct.pack(">Q", len(path_bytes)) + path_bytes)
            if is_directory:
                visit(_plain_path(entry, label, directory=True))
            else:
                reader = inspect_entry(name) if inspect_entry else None
                size, file_hash, metadata = _file(entry, label, reader)
                if metadata is not None:
                    metadata.update(size=size, sha256=file_hash.hex())
                digest.update(struct.pack(">Q", size) + file_hash)
                files += 1
                total += size
        if _stable_stat(before) != _stable_stat(folder.lstat()):
            raise InputError(f"{label} changed during inspection. Finish extracting it and retry.")

    visit(root)
    if not files:
        raise InputError(f"{label} is empty. Select the extracted content directory.")
    return {"tree_sha256": digest.hexdigest(), "files": files, "bytes": total}


def _regular_romfs_entry(info):
    mode = info.st_mode
    if stat.S_ISLNK(mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise InputError("RomFS contains a link or reparse point. Select an ordinary extracted copy.")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise InputError("RomFS entries must be regular files or directories.")


def _open_romfs_file(path, flags):
    if hasattr(os, "O_NOFOLLOW"):
        return os.open(path, flags | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    # The Windows CRT follows links. Recheck the actual path immediately before opening
    # it, since the directory enumeration can run several files ahead of the reader.
    _regular_romfs_entry(os.lstat(path))
    return os.open(path, flags)


def _romfs_file(path, listed):
    changed = "RomFS changed during inspection. Finish extracting it and retry."
    with open(path, "rb", opener=_open_romfs_file) as stream:
        start = os.fstat(stream.fileno())
        # On Windows, DirEntry.stat() obtains size and timestamps from the directory listing,
        # but its file ID can be zero. The handle and the final path check have full IDs.
        if ((listed.st_size, listed.st_mtime_ns) != (start.st_size, start.st_mtime_ns)
                or (listed.st_ino and _stable_stat(listed) != _stable_stat(start)) or not stat.S_ISREG(start.st_mode)):
            raise InputError(changed)
        size = 0
        digest = hashlib.sha256()
        block_size = min(start.st_size + 1, 1024 * 1024)
        for block in iter(lambda: stream.read(block_size), b""):
            size += len(block)
            if size > start.st_size:
                raise InputError(changed)
            digest.update(block)
        end = os.lstat(path)
        _regular_romfs_entry(end)
        if size != start.st_size or _stable_stat(start) != _stable_stat(end):
            raise InputError(changed)
    # NTFS directory metadata for one hard link may lag writes made via a different name.
    return size, digest.digest(), start.st_nlink == end.st_nlink == 1


def _romfs_tree(root, workers=4):
    """Read independent files concurrently and append their hashes in canonical tree order."""
    digest = hashlib.sha256(b"FagriShot extracted tree v1\0")
    folders = []
    files = total = 0
    single_links = True

    def entries(folder, prefix):
        before = os.lstat(folder)
        _regular_romfs_entry(before)
        if not stat.S_ISDIR(before.st_mode):
            raise InputError(f"RomFS directory changed during inspection: {os.path.relpath(folder, root)}. "
                             "Finish extracting it and retry.")
        folders.append((folder, _stable_stat(before)))
        with os.scandir(folder) as scan:
            children = sorted(scan, key=lambda entry: entry.name.encode("utf-8"))
        for entry in children:
            listed = entry.stat(follow_symlinks=False)
            _regular_romfs_entry(listed)
            name = prefix + entry.name.encode("utf-8")
            framed = struct.pack(">Q", len(name)) + name
            if stat.S_ISDIR(listed.st_mode):
                yield b"D" + framed, None
                yield from entries(entry.path, name + b"/")
            else:
                yield b"F" + framed, executor.submit(_romfs_file, entry.path, listed)

    def append(item):
        nonlocal files, total, single_links
        framed, future = item
        digest.update(framed)
        if future is not None:
            size, file_hash, single = future.result()
            single_links &= single
            digest.update(struct.pack(">Q", size) + file_hash)
            files += 1
            total += size

    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = deque()
        for item in entries(root, b""):
            pending.append(item)
            if len(pending) >= workers * 4:
                append(pending.popleft())
        for item in pending:
            append(item)
    for folder, before in folders:
        after = os.lstat(folder)
        _regular_romfs_entry(after)
        if not stat.S_ISDIR(after.st_mode) or before != _stable_stat(after):
            raise InputError(f"RomFS directory changed during inspection: {os.path.relpath(folder, root)}. "
                             "Finish extracting it and retry.")
    if not files:
        raise InputError("RomFS is empty. Select the extracted content directory.")
    return {"tree_sha256": digest.hexdigest(), "files": files, "bytes": total}, single_links


def _verified_romfs(root, cache, refresh):
    stamp = directory_stamp(root) if cache is not None else None
    if stamp and not refresh:
        record = read_verified(cache, b"romfs")
        if isinstance(record, dict) and record.get("stamp") == stamp:
            return record["tree"]
    tree, single_links = _romfs_tree(root)
    if stamp and single_links:
        if directory_stamp(root) != stamp:
            raise InputError("RomFS changed during verification. Finish extracting it and retry.")
        write_verified(cache, b"romfs", {"stamp": stamp, "tree": tree})
    return tree


def _module(header, size):
    if len(header) < 0x100 or header[:4] != b"NSO0":
        raise InputError("The executable has an incomplete or unsupported NSO0 header. Select decrypted ExeFS.")
    version, _, flags = struct.unpack_from("<III", header, 4)
    segments = {}
    extents = []
    for i, name in enumerate(("text", "rodata", "data")):
        offset, memory_offset, memory_size = struct.unpack_from("<III", header, 0x10 + i * 16)
        compressed = bool(flags & (1 << i))
        stored_size = struct.unpack_from("<I", header, 0x60 + i * 4)[0] if compressed else memory_size
        if stored_size and (offset < 0x100 or offset + stored_size > size):
            raise InputError(f"NSO0 {name} extends outside the executable. Check the extracted copy.")
        if memory_size and not stored_size:
            raise InputError(f"NSO0 {name} is missing its stored bytes. Check the extracted copy.")
        if stored_size:
            extents.append((offset, offset + stored_size))
        compression = ("ZBIC" if flags & 0x80 else "LZ4") if compressed else "none"
        segments[name] = {"file_offset": offset, "stored_size": stored_size, "memory_offset": memory_offset,
                          "memory_size": memory_size, "compression": compression}
    ordered = sorted(extents)
    if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
        raise InputError("NSO0 segments overlap in the file. Check the extracted copy.")
    return {"build_id": header[0x40:0x60].hex(), "header_version": version, "flags": flags,
            "segments": segments}


def _program(stream, size):
    head = stream.read(0x80)
    if len(head) != 0x80 or head[:4] != b"META":
        raise InputError("main.npdm must contain an extracted META program descriptor.")
    offset, length = struct.unpack_from("<II", head, 0x70)
    if length < 0x40 or offset < 0x80 or offset + length > size:
        raise InputError("main.npdm has an incomplete ACI0 program descriptor.")
    stream.seek(offset)
    aci = stream.read(0x40)
    if aci[:4] != b"ACI0":
        raise InputError("main.npdm has an unsupported ACI0 program descriptor.")
    program_id = struct.unpack_from("<Q", aci, 0x10)[0]
    return {"program_id": f"{program_id:016X}", "raw_npdm_version": struct.unpack_from("<I", head, 0x18)[0]}


def inspect_exefs(exefs):
    """Fingerprint every ExeFS entry and inventory root NSO0 modules and main.npdm."""
    root = _resolved_root(_plain_path(exefs, "ExeFS", directory=True), "ExeFS")
    _plain_path(root / "main", "ExeFS main")
    modules = {}
    program = None

    def inspect_entry(name):
        def read(stream, size):
            nonlocal program
            if name == "main.npdm":
                program = _program(stream, size)
                return program
            head = stream.read(0x100)
            if name == "main" or head[:4] == b"NSO0":
                modules[name] = _module(head, size)
                return modules[name]
            return None

        return read if "/" not in name else None

    try:
        tree = _tree(root, "ExeFS", inspect_entry)
    except (OSError, UnicodeError):
        raise InputError("Could not read all ExeFS entries. Check extraction, file permissions and filenames.") from None
    return {"schema": 1, "program": program, "modules": modules, "exefs": tree}


def _cnmt(path, kind):
    if path is None:
        return None

    def read(stream, size):
        header = stream.read(0x38)
        expected_type, minimum = (0x80, 0x10) if kind == "base" else (0x81, 0x18)
        if len(header) < 0x20 + minimum or header[0xC] != expected_type:
            raise InputError(f"Select the extracted {kind} packaged .cnmt file.")
        meta_id, version = struct.unpack_from("<QI", header)
        extended, contents, metas = struct.unpack_from("<HHH", header, 0xE)
        extra = struct.unpack_from("<I", header, 0x2C)[0] if kind == "update" else 0
        if extended < minimum or 0x20 + extended + 0x38 * contents + 0x10 * metas + extra + 32 != size:
            raise InputError(f"The {kind} CNMT table lengths do not match its file size.")
        linked_id = struct.unpack_from("<Q", header, 0x20)[0]
        stream.seek(0)
        remaining = size - 32
        digest = hashlib.sha256()
        while remaining:
            block = stream.read(min(remaining, 1024 * 1024))
            if not block:
                raise InputError(f"The {kind} CNMT changed during inspection.")
            digest.update(block)
            remaining -= len(block)
        if stream.read(32) != digest.digest():
            raise InputError(f"The {kind} CNMT trailing digest does not match its data.")
        linked_key = "patch_id" if kind == "base" else "application_id"
        return {"id": f"{meta_id:016X}", "version": version, linked_key: f"{linked_id:016X}",
                "content_count": contents, "platform_byte": header[0xD]}

    size, digest, metadata = _file(Path(path), f"{kind} CNMT", read)
    return {**metadata, "size": size, "sha256": digest.hex()}


def inspect_inputs(exefs, romfs, *, base_cnmt=None, update_cnmt=None, romfs_cache=None, refresh=False):
    """Inventory the selected ExeFS and effective RomFS. Optional raw CNMTs identify releases."""
    exe_root = _plain_path(exefs, "ExeFS", directory=True)
    rom_root = _plain_path(romfs, "RomFS", directory=True)
    check_input_roots(exe_root, rom_root)
    try:
        base, update = _cnmt(base_cnmt, "base"), _cnmt(update_cnmt, "update")
        if base and update and (base["id"] != update["application_id"] or base["patch_id"] != update["id"]):
            raise InputError("The base and update CNMTs identify different applications. Select a matching pair.")
        inventory = inspect_exefs(exe_root)
        return {**inventory, "base_cnmt": base, "update_cnmt": update,
                "romfs": _verified_romfs(rom_root, romfs_cache, refresh)}
    except (OSError, UnicodeError):
        raise InputError("Could not read all game inputs. Check extraction, permissions and filenames.") from None


def _catalog(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data["schema"] != 1 or not isinstance(data["candidates"], list) or not isinstance(data["supported"], list):
            raise ValueError
        for item in data["candidates"]:
            names = ("id", "version", "program_id", "main_build_id", "main_plain_sha256")
            if not all(isinstance(item[key], str) and item[key] for key in names):
                raise ValueError
            if (not isinstance(item["update_version"], int) or not isinstance(item["sha256"], dict)
                    or not {"main", "main.npdm"} <= item["sha256"].keys()):
                raise ValueError
            for value in [item["main_plain_sha256"], *item["sha256"].values()]:
                if not isinstance(value, str) or len(value) != 64 or len(bytes.fromhex(value)) != 32:
                    raise ValueError
        for record in data["supported"]:
            if not isinstance(record["candidate"], str):
                raise ValueError
            for key in ("exefs_tree_sha256", "romfs_tree_sha256", "base_cnmt_sha256", "update_cnmt_sha256"):
                value = record[key]
                if key.endswith("cnmt_sha256") and value is None:
                    continue
                if not isinstance(value, str) or len(value) != 64 or len(bytes.fromhex(value)) != 32:
                    raise ValueError
        return data
    except (OSError, KeyError, TypeError, UnicodeError, ValueError):
        pass
    raise InputError("FagriShot's compatibility catalog is missing or invalid. Repair the tooling installation.")


def _match_executable(inventory, catalog):
    program = inventory.get("program")
    if not program:
        raise InputError("Select an ExeFS containing main.npdm to verify the program ID and descriptor hash.")
    matches = [candidate for candidate in catalog["candidates"] if candidate["program_id"] == program["program_id"]]
    if not matches:
        raise InputError(f"Program {program['program_id']} has no FagriShot executable profile. Check the selected game.")
    reasons = []
    for candidate in matches:
        modules = inventory["modules"]
        expected = candidate["sha256"]
        observed = {name: module["sha256"] for name, module in modules.items()}
        observed["main.npdm"] = program["sha256"]
        differences = set(expected) ^ set(observed)
        for name in expected.keys() & observed.keys():
            if observed[name] == expected[name] or (name == "main" and observed[name] == candidate["main_plain_sha256"]):
                continue
            differences.add(name)
        if "main" in modules and not modules["main"]["build_id"].startswith(candidate["main_build_id"]):
            differences.add("main (build ID)")
        if not differences:
            return candidate
        reasons.append(candidate["version"] + " differs at " + ", ".join(sorted(differences)))
    raise InputError("ExeFS is incompatible. " + ". ".join(reasons) + ". Select every module from the same update.")


def check_executable(inventory, manifest_path):
    """Check the raw or prepared main and the exact set and bytes of NSOs and main.npdm."""
    return _match_executable(inventory, _catalog(manifest_path))


def _content_candidate(inventory, catalog):
    candidate = _match_executable(inventory, catalog)
    if not inventory.get("romfs"):
        raise InputError("Select the matching extracted RomFS to check complete game compatibility.")
    base, update = inventory.get("base_cnmt"), inventory.get("update_cnmt")
    if base and base["id"] != candidate["program_id"]:
        raise InputError("The base CNMT identifies a different application from this Fortune’s Weave executable profile.")
    if update and (update["application_id"] != candidate["program_id"] or update["version"] != candidate["update_version"]):
        raise InputError("The update CNMT does not identify the same application and update as the selected executable.")
    return candidate


def check_support(inventory, manifest_path):
    """Require both the executable profile and an independently recorded complete content pairing."""
    catalog = _catalog(manifest_path)
    candidate = _content_candidate(inventory, catalog)
    base, update = inventory.get("base_cnmt"), inventory.get("update_cnmt")
    for record in catalog["supported"]:
        if (record["candidate"] == candidate["id"] and record["exefs_tree_sha256"] == inventory["exefs"]["tree_sha256"]
                and record["romfs_tree_sha256"] == inventory["romfs"]["tree_sha256"]
                and record["base_cnmt_sha256"] == (base and base["sha256"])
                and record["update_cnmt_sha256"] == (update and update["sha256"])):
            return record
    raise InputError(f"ExeFS matches {candidate['version']}. This RomFS and metadata combination has no verified "
                     "FagriShot pairing. Keep the inventory and check the content from the matching base and update.")


# Merged, effective RomFS as recorded at the pinned upstream game-build/fefw/src/fefw_main.cpp:369.
_TRIAL_FILE_COUNTS = {("04007EF01F8AE000", 131072): 252686}


def _hex_digest(value, lengths=(64,)):
    return isinstance(value, str) and len(value) in lengths and all(char in "0123456789abcdef" for char in value)


def _trial_scope(checkout, paths, source):
    checkout = Path(checkout).resolve()
    if not (checkout / "recomp/games/fefw/config.py").is_file():
        raise InputError("Local trials require the FagriShot source checkout.")
    if (not isinstance(source, dict) or not _hex_digest(source.get("commit"), (40, 64))
            or not _hex_digest(source.get("tracked_diff_sha256"))):
        raise InputError("The local trial needs the current source revision and tracked-change fingerprint.")
    # Only the fingerprint of the three absolute locations is persisted.
    locations = [os.path.normcase(str(Path(folder).resolve())) for folder in (checkout, paths["exefs"], paths["romfs"])]
    encoded = json.dumps(locations, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return {"locations_sha256": hashlib.sha256(encoded).hexdigest(),
            "commit": source["commit"], "tracked_diff_sha256": source["tracked_diff_sha256"]}


def _trial_tree(tree):
    if (not isinstance(tree, dict) or not _hex_digest(tree.get("tree_sha256"))
            or type(tree.get("files")) is not int or tree["files"] <= 0
            or type(tree.get("bytes")) is not int or tree["bytes"] <= 0):
        raise InputError("A local trial requires the complete inspection of both extracted input trees.")
    return {key: tree[key] for key in ("tree_sha256", "files", "bytes")}


def local_trial_receipt(inventory, manifest_path, *, checkout, paths, source):
    """Bind an explicit local trial to a complete inventory and the active source checkout."""
    candidate = _content_candidate(inventory, _catalog(manifest_path))
    expected = _TRIAL_FILE_COUNTS.get((candidate["program_id"], candidate["update_version"]))
    if expected is None:
        raise InputError("This executable profile has no upstream file-count reference for a local trial.")
    exefs, romfs = _trial_tree(inventory.get("exefs")), _trial_tree(inventory.get("romfs"))
    if romfs["files"] != expected:
        raise InputError(f"Local trial for {candidate['version']} requires the merged RomFS with {expected:,} files. "
                         f"The selected RomFS has {romfs['files']:,}.")
    base, update = inventory.get("base_cnmt"), inventory.get("update_cnmt")
    return {"schema": 1, "kind": "local-play-trial", "scope": _trial_scope(checkout, paths, source),
            "candidate": candidate["id"], "program_id": candidate["program_id"], "update_version": candidate["update_version"],
            "build_id": inventory["modules"]["main"]["build_id"], "exefs": exefs, "romfs": romfs,
            "base_cnmt_sha256": base and base["sha256"], "update_cnmt_sha256": update and update["sha256"]}


def local_trial_scope_matches(receipt, *, checkout, paths, source):
    """Report whether a receipt is recorded for this selection without re-inspecting either tree."""
    try:
        if not isinstance(receipt, dict) or receipt.get("schema") != 1 or receipt.get("kind") != "local-play-trial":
            return False
        _trial_tree(receipt["exefs"])
        romfs = _trial_tree(receipt["romfs"])
        if romfs["files"] != _TRIAL_FILE_COUNTS.get((receipt["program_id"], receipt["update_version"])):
            return False
        if (not _hex_digest(receipt["build_id"]) or not isinstance(receipt["candidate"], str)
                or not receipt["candidate"] or not all(receipt[key] is None or _hex_digest(receipt[key])
                    for key in ("base_cnmt_sha256", "update_cnmt_sha256"))):
            return False
        return receipt["scope"] == _trial_scope(checkout, paths, source)
    except (InputError, TypeError, KeyError, OSError):
        return False


def check_local_trial(inventory, manifest_path, receipt, *, checkout, paths, source):
    """Authorize only the exact enrollment after a fresh full inspection and executable check."""
    expected = local_trial_receipt(inventory, manifest_path, checkout=checkout, paths=paths, source=source)
    if receipt != expected:
        raise InputError("This selection and source revision have no matching local trial receipt. "
                         "Use enroll-local to enroll the exact inputs for testing.")
    return expected

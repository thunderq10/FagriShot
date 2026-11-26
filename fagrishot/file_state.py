"""Change detection for previously content-verified trees in the Windows player."""

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import struct

from .storage import write_json


def directory_stamp(root, *, suffix=None):
    """Fingerprint file identity, size, write and change times. None requires content verification.

    With a suffix, watch only matching files directly in root (the flat SPIR-V cache).
    Otherwise include every directory and file below root. Never use directory mtime alone.
    """
    root = Path(root)
    try:
        if os.name != "nt" or not _local_ntfs(root):
            return None
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return None
        digest = hashlib.sha256(b"FagriShot verified directory v1\0")
        digest.update(str((info.st_dev, info.st_ino)).encode("ascii"))
        def visit(folder):
            for name, is_dir, signature in sorted(_windows_entries(folder)):
                if suffix is not None and (is_dir or not name.lower().endswith(suffix)):
                    continue
                encoded = name.encode("utf-8")
                digest.update(struct.pack("<I", len(encoded)) + encoded + signature)
                if is_dir:
                    visit(folder / name)
                    digest.update(b"\0end dir\0")
        visit(root)
        return digest.hexdigest()
    except (OSError, UnicodeError, ValueError):
        # Remote/unsupported file systems and unstable directory walks use the full check.
        return None


if os.name == "nt":
    from ctypes import wintypes as w

    class _Blob(ctypes.Structure):
        _fields_ = [("length", w.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        @classmethod
        def from_bytes(cls, data):
            return cls(len(data), (ctypes.c_ubyte * len(data)).from_buffer_copy(data))

    _api = ctypes.WinDLL("kernel32", use_last_error=True)
    _api.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
    _api.CreateFileW.restype = w.HANDLE
    _api.CloseHandle.argtypes = (w.HANDLE,)
    _api.GetFileInformationByHandleEx.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
    _api.GetDriveTypeW.argtypes = (w.LPCWSTR,)
    _api.GetVolumePathNameW.argtypes = (w.LPCWSTR, w.LPWSTR, w.DWORD)
    _api.GetVolumeInformationW.argtypes = (w.LPCWSTR, w.LPWSTR, w.DWORD, w.LPDWORD,
                                          w.LPDWORD, w.LPDWORD, w.LPWSTR, w.DWORD)
    # FILE_ID_EXTD_DIR_INFO fixed header, followed by a counted UTF-16 name.
    _entry_header = struct.Struct("<II6qIIII16s")

    try:
        _crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    except OSError:
        _crypt = None
    if _crypt is not None:
        _blob_ptr = ctypes.POINTER(_Blob)
        _crypt.CryptProtectData.argtypes = (_blob_ptr, w.LPCWSTR, _blob_ptr, w.LPVOID, w.LPVOID, w.DWORD, _blob_ptr)
        _crypt.CryptUnprotectData.argtypes = (_blob_ptr, ctypes.POINTER(w.LPWSTR), _blob_ptr,
                                             w.LPVOID, w.LPVOID, w.DWORD, _blob_ptr)
    _api.LocalFree.argtypes = (w.HANDLE,)
    _api.LocalFree.restype = w.HANDLE


def _protected(data, purpose, *, seal):
    if _crypt is None:
        raise OSError("Windows data protection is unavailable")
    source = _Blob.from_bytes(data)
    entropy = _Blob.from_bytes(b"FagriShot verified data v1:" + purpose)
    result = _Blob()
    function = _crypt.CryptProtectData if seal else _crypt.CryptUnprotectData
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.length)
    finally:
        _api.LocalFree(result.data)


def read_verified(path, purpose):
    """Only trust an intact cache written for this Windows user and purpose."""
    if os.name == "nt":
        try:
            sealed = base64.b64decode(json.loads(path.read_bytes()), validate=True)
            return json.loads(_protected(sealed, purpose, seal=False))
        except (OSError, TypeError, ValueError):
            pass
    return None


def write_verified(path, purpose, record):
    if os.name == "nt":
        try:
            sealed = _protected(json.dumps(record).encode("utf-8"), purpose, seal=True)
            write_json(path, base64.b64encode(sealed).decode("ascii"))
        except OSError:
            # Cache storage/profile services may be unavailable; the full check still succeeded.
            pass


def _local_ntfs(root):
    volume = ctypes.create_unicode_buffer(32768)
    fs = ctypes.create_unicode_buffer(32)
    return (_api.GetVolumePathNameW(str(root), volume, len(volume))
            and _api.GetDriveTypeW(volume) in (2, 3)
            and _api.GetVolumeInformationW(volume, None, 0, None, None, None, fs, len(fs))
            and fs.value == "NTFS")


def _windows_entries(folder):
    # Directory enumeration supplies NTFS ChangeTime and the 128-bit file ID in the same
    # syscall for a batch of entries. Python DirEntry exposes creation time instead.
    handle = _api.CreateFileW(str(folder), 1, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buf = ctypes.create_string_buffer(64 * 1024)
        while _api.GetFileInformationByHandleEx(handle, 19, buf, len(buf)):  # FileIdExtdDirectoryInfo
            offset = 0
            while True:
                step, _, born, _, written, changed, size, _, attrs, length, _, _, file_id = _entry_header.unpack_from(buf, offset)
                start = offset + _entry_header.size
                name = buf[start:start + length].decode("utf-16-le")
                if name not in (".", ".."):
                    if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT or not changed or not any(file_id):
                        raise ValueError("Entry has no reliable change identity")
                    is_dir = bool(attrs & stat.FILE_ATTRIBUTE_DIRECTORY)
                    # NTFS may refresh a parent's cached directory timestamps when the
                    # child directory is opened. Its identity and enumerated children
                    # already cover replacements, additions, renames and removals.
                    signature = (b"D" + file_id if is_dir else
                                 b"F" + struct.pack("<4qI16s", born, written, changed, size, attrs, file_id))
                    yield name, is_dir, signature
                if not step:
                    break
                offset += step
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _api.CloseHandle(handle)

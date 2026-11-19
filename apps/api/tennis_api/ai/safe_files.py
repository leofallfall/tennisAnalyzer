"""Safe access to the files of an Evidence Pack.

Every read of a pack file goes through this module (C1 in the AI audit). Paths come from
``manifest.json``, which anyone who can write the pack folder can edit, so they are
untrusted:

* a path must be a plain relative POSIX path made of simple names (letters, digits,
  ``_ . -``, not starting with a dot): no absolute paths, no ``..``, no backslashes, no
  percent-encoding or other characters that another layer could reinterpret;
* the file is opened component by component relative to the pack directory with
  ``O_NOFOLLOW``, so a symlink anywhere on the way (including one swapped in after a
  check) makes the open fail instead of leaving the pack;
* the opened descriptor must be a regular file; its bytes are read once and the caller
  hashes and parses those same bytes (no check-then-reopen window).

Errors carry a stable code and never include absolute paths or filesystem details.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

MAX_PACK_FILE_BYTES = 64 * 1024 * 1024
MAX_PATH_DEPTH = 4
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_DIR_FD = os.open in os.supports_dir_fd and bool(_O_NOFOLLOW) and bool(_O_DIRECTORY)


class PackFileError(Exception):
    """A pack file could not be read safely. ``code`` is stable; no paths in ``message``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_safe_pack_path(path: str) -> bool:
    if not path or len(path) > 512:
        return False
    parts = path.split("/")
    return len(parts) <= MAX_PATH_DEPTH and all(_SEGMENT.fullmatch(p) for p in parts)


def check_pack_path(path: str) -> str:
    """Pydantic-friendly validator: return ``path`` or raise ``ValueError``."""
    if not is_safe_pack_path(path):
        raise ValueError(
            "must be a relative path inside the pack (simple names separated by '/', "
            "no '..', no absolute path)"
        )
    return path


def _open_dir_nofollow(path: Path) -> int:
    try:
        return os.open(path, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except FileNotFoundError:
        raise PackFileError("pack_missing", "The Evidence Pack folder is missing") from None
    except OSError:
        raise PackFileError(
            "pack_symlink", "The Evidence Pack folder is not a real directory"
        ) from None


def _read_fd(fd: int, max_bytes: int) -> bytes:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise PackFileError("pack_not_regular_file", "not a regular file")
    if st.st_size > max_bytes:
        raise PackFileError("pack_file_too_large", "file is too large")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PackFileError("pack_file_too_large", "file is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def read_pack_file(pack_dir: Path, rel: str, *, max_bytes: int = MAX_PACK_FILE_BYTES) -> bytes:
    """Read ``rel`` inside ``pack_dir`` without ever leaving the pack.

    Raises ``PackFileError`` with code ``pack_path_unsafe``, ``pack_missing``,
    ``pack_file_missing``, ``pack_symlink``, ``pack_not_regular_file`` or
    ``pack_file_too_large``.
    """
    if not is_safe_pack_path(rel):
        raise PackFileError("pack_path_unsafe", "unsafe path in the manifest")
    parts = rel.split("/")
    if _DIR_FD:
        fds = [_open_dir_nofollow(pack_dir)]
        try:
            for i, name in enumerate(parts):
                last = i == len(parts) - 1
                flags = os.O_RDONLY | _O_NOFOLLOW | (0 if last else _O_DIRECTORY)
                try:
                    fds.append(os.open(name, flags, dir_fd=fds[-1]))
                except FileNotFoundError:
                    raise PackFileError("pack_file_missing", "file is missing") from None
                except NotADirectoryError:
                    raise PackFileError("pack_file_missing", "file is missing") from None
                except OSError:
                    # ELOOP (symlink with O_NOFOLLOW) or a symlinked directory.
                    raise PackFileError("pack_symlink", "symbolic links are not allowed") from None
            return _read_fd(fds[-1], max_bytes)
        finally:
            for fd in fds:
                os.close(fd)
    # Fallback (no openat/O_NOFOLLOW, e.g. Windows): lstat every component, then
    # compare the resolved path with the pack directory.
    root = pack_dir
    if root.is_symlink():
        raise PackFileError("pack_symlink", "The Evidence Pack folder is not a real directory")
    current = root
    for name in parts:
        current = current / name
        if current.is_symlink():
            raise PackFileError("pack_symlink", "symbolic links are not allowed")
    try:
        resolved = current.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise PackFileError("pack_file_missing", "file is missing") from None
    if not resolved.is_relative_to(root.resolve()):
        raise PackFileError("pack_path_unsafe", "unsafe path in the manifest")
    fd = os.open(resolved, os.O_RDONLY | _O_BINARY | _O_NOFOLLOW)
    try:
        return _read_fd(fd, max_bytes)
    finally:
        os.close(fd)


def scan_pack_tree(pack_dir: Path) -> tuple[set[str], list[str], list[str]]:
    """List the pack folder without following links.

    Returns ``(regular_files, symlinks, other_entries)`` as relative POSIX paths.
    """
    files: set[str] = set()
    links: list[str] = []
    other: list[str] = []

    def walk(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as it:
            for entry in it:
                rel = f"{prefix}{entry.name}"
                if entry.is_symlink():
                    links.append(rel)
                elif entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), f"{rel}/")
                elif entry.is_file(follow_symlinks=False):
                    files.add(rel)
                else:
                    other.append(rel)

    walk(pack_dir, "")
    return files, sorted(links), sorted(other)

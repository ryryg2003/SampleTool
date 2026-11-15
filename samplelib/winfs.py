"""Windows-aware filesystem helpers (stdlib only).

Primary target is Windows/NTFS; everything also works on POSIX so the test
suite can run on Linux. Behaviour that differs is branched on ``os.name``.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys

IS_WINDOWS = os.name == "nt"

_LONG_PREFIX = "\\\\?\\"
_UNC_LONG_PREFIX = "\\\\?\\UNC\\"
# Win32 MAX_PATH is 260; directories are limited to 248. Prefix well before that.
_LONG_THRESHOLD = 240

# Windows error codes for files held open by another process.
WINERROR_SHARING_VIOLATION = 32
WINERROR_LOCK_VIOLATION = 33

_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def long_path(p: str) -> str:
    r"""Return ``p`` with the ``\\?\`` prefix on Windows when it is long.

    Identity on other platforms. Already-prefixed paths are returned as-is.
    """
    if not IS_WINDOWS or not p:
        return p
    if p.startswith(_LONG_PREFIX):
        return p
    ap = os.path.abspath(p)
    if len(ap) < _LONG_THRESHOLD and not _needs_prefix_anyway(ap):
        return p
    if ap.startswith("\\\\"):           # UNC \\server\share\...
        return _UNC_LONG_PREFIX + ap[2:]
    return _LONG_PREFIX + ap


def _needs_prefix_anyway(ap: str) -> bool:
    # Components ending in a dot or space are silently stripped by Win32
    # normalization unless the path is \\?\ prefixed.
    for part in ap.replace("/", "\\").split("\\")[1:]:
        if part and part not in (".", "..") and part[-1] in ". ":
            return True
    return False


def strip_long_prefix(p: str) -> str:
    if p.startswith(_UNC_LONG_PREFIX):
        return "\\\\" + p[len(_UNC_LONG_PREFIX):]
    if p.startswith(_LONG_PREFIX):
        return p[len(_LONG_PREFIX):]
    return p


def file_identity(p: str) -> str | None:
    """``"st_dev:st_ino"`` (NTFS volume serial + file index on Windows)."""
    try:
        st = os.stat(long_path(p), follow_symlinks=False)
    except OSError:
        return None
    return f"{st.st_dev}:{st.st_ino}"


def _st_is_reparse(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & _REPARSE_FLAG)


def is_reparse_point(p_or_entry) -> bool:
    """True for symlinks, junctions and any other reparse point.

    Accepts a path string or an ``os.DirEntry``. A missing path is not a
    reparse point (returns False).
    """
    if isinstance(p_or_entry, os.DirEntry):
        e = p_or_entry
        try:
            if e.is_symlink():
                return True
            is_junc = getattr(e, "is_junction", None)
            if is_junc is not None and is_junc():
                return True
            return _st_is_reparse(e.stat(follow_symlinks=False))
        except OSError:
            return False
    p = os.fspath(p_or_entry)
    try:
        st = os.lstat(long_path(p))
    except OSError:
        return False
    if _st_is_reparse(st):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        try:
            return bool(isjunction(long_path(p)))
        except OSError:
            return False
    return False


def nearest_existing(p: str) -> str | None:
    """Closest existing ancestor (or ``p`` itself)."""
    cur = os.path.abspath(p)
    while True:
        if os.path.lexists(long_path(cur)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def volume_id(p: str) -> int | None:
    q = nearest_existing(p)
    if q is None:
        return None
    try:
        return os.stat(long_path(q)).st_dev
    except OSError:
        return None


def same_volume(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` (or their nearest existing ancestors) share a volume."""
    va, vb = volume_id(a), volume_id(b)
    return va is not None and va == vb


def path_key(p: str) -> str:
    """Normalized, case-folded key for collision / containment checks.

    Windows paths are case-insensitive, so the key is always casefolded (even
    on POSIX, by contract: the library must stay valid on NTFS).
    """
    p = strip_long_prefix(os.fspath(p))
    p = os.path.normcase(os.path.normpath(os.path.abspath(p)))
    if IS_WINDOWS:
        p = p.replace("/", "\\")
    return p.casefold()


def name_key(name: str) -> str:
    return name.casefold()


def is_within(child: str, parent: str) -> bool:
    """True if ``child`` equals ``parent`` or lies beneath it (case-insensitive)."""
    c, par = path_key(child), path_key(parent)
    if c == par:
        return True
    if not par.endswith(os.sep):
        par += os.sep
    return c.startswith(par)


def is_locked_error(exc: BaseException) -> bool:
    """Sharing/lock violation (file open in another program)."""
    winerr = getattr(exc, "winerror", None)
    if winerr in (WINERROR_SHARING_VIOLATION, WINERROR_LOCK_VIOLATION):
        return True
    return isinstance(exc, PermissionError)


def reveal_in_explorer(p: str) -> None:
    """Show ``p`` selected in Explorer (Windows); open its folder elsewhere.

    Never uses a shell. Errors launching the viewer are swallowed.
    """
    p = os.path.abspath(strip_long_prefix(os.fspath(p)))
    target = p if os.path.lexists(p) else (nearest_existing(p) or p)
    try:
        if IS_WINDOWS:
            if os.path.isdir(target) and target != p:
                cmd = ["explorer", os.path.normpath(target)]
            else:
                # Explorer parses its own command line: it wants /select,"path"
                # with no space. Windows paths cannot contain '"', so this is safe.
                cmd = 'explorer /select,"%s"' % os.path.normpath(target)
            subprocess.Popen(cmd, close_fds=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target], close_fds=True)
        else:
            folder = target if os.path.isdir(target) else os.path.dirname(target)
            subprocess.Popen(["xdg-open", folder], close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        pass

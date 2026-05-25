"""
Best-effort objective magnification (``AppMag``) from ``.kfb`` bytes.

The KFB container is proprietary. Without a vendor SDK, this module **does not guess**
by default: :func:`read_appmag_from_kfb` returns ``None``.

Optional bounded ASCII scan (off by default) may recover embedded ``AppMag=NN``-style
fragments when the environment variable ``KFBIO_ALLOW_KFB_APPMAG_SCAN=1`` is set.

Vendor DLL integration placeholder: implement :func:`_try_vendor_dll_appmag` when
KFBIO documents export symbols and calling conventions.
"""

from __future__ import annotations

import os
import re
from typing import Optional

_APPMAG_RE = re.compile(rb"AppMag\s*=\s*(\d{1,3})", re.IGNORECASE)
_SCAN_BYTES = 4 * 1024 * 1024
_MAG_MIN = 4
_MAG_MAX = 100


def _try_vendor_dll_appmag(kfb_path: str) -> Optional[int]:
    """
    Reserved hook for KFBIO ``ImageOperationLib.dll`` (or similar) via ctypes.

    Parameters:
        kfb_path: str — absolute ``.kfb`` path.

    Returns:
        magnification: int | None — objective magnification when resolved.
    """
    return None


def _scan_kfb_for_appmag_ascii(kfb_path: str) -> Optional[int]:
    """Read first ``_SCAN_BYTES`` and return first plausible ``AppMag`` integer."""
    try:
        with open(kfb_path, "rb") as f:
            blob = f.read(_SCAN_BYTES)
    except OSError:
        return None
    m = _APPMAG_RE.search(blob)
    if not m:
        return None
    try:
        val = int(m.group(1))
    except ValueError:
        return None
    if not (_MAG_MIN <= val <= _MAG_MAX):
        return None
    return val


def read_appmag_from_kfb(kfb_path: str) -> Optional[int]:
    """
    Return objective magnification ``kk`` for ``layer i (kkx)`` labels, or ``None``.

    Order:
        1. Vendor DLL hook (when implemented).
        2. Optional bounded scan if ``KFBIO_ALLOW_KFB_APPMAG_SCAN=1``.
        3. Otherwise ``None`` (caller shows ``layer i`` only).
    """
    if not kfb_path or not os.path.isfile(kfb_path):
        return None
    mag = _try_vendor_dll_appmag(kfb_path)
    if mag is not None:
        return mag
    if os.environ.get("KFBIO_ALLOW_KFB_APPMAG_SCAN", "").strip() == "1":
        return _scan_kfb_for_appmag_ascii(kfb_path)
    return None

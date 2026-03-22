"""Development shim for the local src layout.

This allows `python -m shipyard.main` to work from the repository root
before the package is installed in editable mode.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"

_SRC_PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "shipyard"
if _SRC_PACKAGE_DIR.exists():
    __path__.append(str(_SRC_PACKAGE_DIR))

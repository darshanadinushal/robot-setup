"""Which Luna is in a folder: Luna Core (luna_core/, 1.0 and later) or the older robot_hud/ layout.

Everything here only reads files: no Python process is started, so it costs nothing on the Pi.
"""
from __future__ import annotations

import re
from pathlib import Path

# Newest layout first. Luna Core 1.x ships both (robot_hud/ is a compatibility copy that
# Luna Core 2.0 removes); older Luna has only robot_hud/.
PACKAGES = ("luna_core", "robot_hud")
VERSION_LINE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)
# A git tag, branch or commit for upgrade-pi.sh: no leading "-" (option) and no spaces.
GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def package_dir(luna_dir):
    """Luna's package folder (luna_core/ or robot_hud/), or None if this is not Luna."""
    for name in PACKAGES:
        folder = Path(luna_dir) / name
        if (folder / "app.py").is_file():
            return folder
    return None


def data_dir(luna_dir):
    """The folder with voices.json and languages/ (robot_hud/ when nothing is found, so
    error messages name the file an older Luna would have)."""
    for name in PACKAGES:
        folder = Path(luna_dir) / name
        if (folder / "voices.json").is_file() or (folder / "languages").is_dir():
            return folder
    return Path(luna_dir) / PACKAGES[-1]


def version(luna_dir):
    """Luna Core's version ("1.0.0"), or None for a Luna older than Luna Core 1.0."""
    try:
        text = (Path(luna_dir) / "luna_core" / "__init__.py").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = VERSION_LINE.search(text)
    return match.group(1) if match else None


def version_tuple(text):
    """(1, 0, 0) from "1.0.0" or "v1.0.0rc1"; None if it does not start with a number."""
    match = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    return tuple(int(part or 0) for part in match.groups()) if match else None


def describe(luna_dir):
    found = version(luna_dir)
    return f"Luna Core {found}" if found else "Luna older than Luna Core 1.0"

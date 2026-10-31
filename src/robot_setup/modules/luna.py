"""Luna module: adopt an existing Luna folder and prepare its Python environment."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import lunainfo
from ..config import LUNA_DIR_CANDIDATES, LUNA_PACKAGES, LUNA_STABLE_TAG, LUNA_SUPPORTED_MAJORS, \
    SetupError
from ..system import FAILED, PASSED, WARNING, Check
from .common import has_sound_card, missing_packages

FEATURE = "luna"
SYSTEM_PYTHON = "/usr/bin/python3"


def is_luna_dir(path):
    """main.py plus Luna's package: luna_core/ (Luna Core 1.0+) or robot_hud/ (older Luna)."""
    return (Path(path) / "main.py").is_file() and lunainfo.package_dir(path) is not None


def find_luna_dirs(home):
    return [Path(home) / name for name in LUNA_DIR_CANDIDATES if is_luna_dir(Path(home) / name)]


def preconditions(ctx):
    if not ctx.request.luna_dir or not is_luna_dir(ctx.luna_dir):
        return [f"Luna was not found in {ctx.luna_dir}. Choose the folder that contains main.py."]
    if not os.access(ctx.luna_dir, os.W_OK):
        return [f"You cannot write to {ctx.luna_dir}."]
    return []


def _uses_system_packages(venv):
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.replace(" ", "").lower() == "include-system-site-packages=true"
               for line in text.splitlines())


def venv_ready(ctx):
    venv = ctx.luna_dir / ".venv"
    if not ctx.venv_python.exists() or not _uses_system_packages(venv):
        return False
    return ctx.runner.run([ctx.venv_python, "-c", "import pygame"], timeout=60).ok


def create_venv(ctx):
    venv = ctx.luna_dir / ".venv"
    created = ctx.state.data["created"]
    if venv.exists():
        if created.get("venv") != str(venv):
            raise SetupError(f"{venv} exists but cannot use the system pygame. "
                             "Rename it, then run Repair.")
        ctx.log("Removing the incomplete environment from an earlier attempt.")
        shutil.rmtree(venv)
    created["venv"] = str(venv)
    ctx.state.save()
    python = SYSTEM_PYTHON if Path(SYSTEM_PYTHON).exists() else "python3"
    result = ctx.runner.run([python, "-m", "venv", "--system-site-packages", venv],
                            on_line=ctx.log, stream="all")
    if not result.ok:
        raise SetupError("Creating Luna's Python environment failed.")


def tasks(ctx):
    from ..engine import Task
    return [
        Task("luna-packages", FEATURE, "System packages for Luna",
             ("Install system packages: " + ", ".join(LUNA_PACKAGES),),
             is_done=lambda c: not missing_packages(c, LUNA_PACKAGES),
             system_ops=({"op": "apt_install", "packages": list(LUNA_PACKAGES)},),
             needs_network=True),
        Task("luna-environment", FEATURE, "Luna Python environment",
             (f"Create {ctx.luna_dir / '.venv'} using the system pygame",),
             is_done=venv_ready, apply=create_venv),
    ]


def version_check(ctx):
    """Is this Luna a Luna Core version Robot Setup supports? (Reads a file; starts nothing.)"""
    def check(status, detail):
        return Check("luna-version", "Luna Core version", status, detail)

    found = lunainfo.version(ctx.luna_dir)
    if found is None:
        return check(WARNING, "Older Luna without Luna Core: still supported, but upgrade with "
                              f"'robot-setup upgrade-luna {LUNA_STABLE_TAG}'.")
    numbers = lunainfo.version_tuple(found)
    if numbers is None or numbers[0] not in LUNA_SUPPORTED_MAJORS:
        return check(WARNING, f"Luna Core {found} is not a version this Robot Setup was tested "
                              f"with ({LUNA_STABLE_TAG}). Update Robot Setup.")
    return check(PASSED, f"Luna Core {found}")


def verify(ctx):
    ready = venv_ready(ctx)  # starts Python and imports pygame: do it once
    checks = [Check("luna-runtime", "Luna can start (pygame)", PASSED if ready else FAILED,
                    "" if ready else "Run Repair."),
              version_check(ctx)]
    speaker = has_sound_card(ctx, "aplay")
    checks.append(Check("speaker-device", "Speaker found", PASSED if speaker else WARNING,
                        "" if speaker else "No audio output device found."))
    return checks


def remove(ctx):
    return [f"Luna in {ctx.luna_dir} was not changed."]

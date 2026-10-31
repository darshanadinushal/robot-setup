"""Luna module: adopt an existing Luna folder and prepare its Python environment."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..config import LUNA_DIR_CANDIDATES, LUNA_PACKAGES, SetupError
from ..system import FAILED, PASSED, WARNING, Check
from .common import has_sound_card, missing_packages

FEATURE = "luna"
SYSTEM_PYTHON = "/usr/bin/python3"


def is_luna_dir(path):
    path = Path(path)
    # Luna Core 1.x uses luna_core/; older checkouts used robot_hud/.
    # No import or process launch: detection must work before dependencies are installed.
    return (path / "main.py").is_file() and any(
        (path / package / "app.py").is_file() for package in ("luna_core", "robot_hud")
    )


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


def verify(ctx):
    checks = [Check("luna-runtime", "Luna can start (pygame)",
                    PASSED if venv_ready(ctx) else FAILED,
                    "" if venv_ready(ctx) else "Run Repair.")]
    speaker = has_sound_card(ctx, "aplay")
    checks.append(Check("speaker-device", "Speaker found", PASSED if speaker else WARNING,
                        "" if speaker else "No audio output device found."))
    return checks


def remove(ctx):
    return [f"Luna in {ctx.luna_dir} was not changed."]

"""Offline voice module: PortAudio, Vosk + sounddevice, and the small English Vosk model."""
from __future__ import annotations

from ..config import PIP_PINS, VOICE_PACKAGES, SetupError
from ..system import FAILED, PASSED, WARNING, Check
from .common import has_pinned, has_sound_card, missing_packages, pip_install

FEATURE = "voice"
PINS = {name: PIP_PINS[name] for name in ("vosk", "sounddevice")}


def download_script(ctx):
    return ctx.luna_dir / "scripts" / "download-voice-model.py"


def model_ready(ctx):
    model = ctx.paths.voice_model
    return (model / "am" / "final.mdl").is_file() and (model / "conf" / "model.conf").is_file()


def download_model(ctx):
    result = ctx.runner.run([ctx.venv_python, download_script(ctx)], cwd=ctx.luna_dir,
                            on_line=ctx.log, stream="all")
    if not result.ok:
        raise SetupError("Downloading the voice model failed. Check the internet connection.")
    if not model_ready(ctx):
        raise SetupError(f"The voice model in {ctx.paths.voice_model} is incomplete. "
                         "Delete that folder, then run Repair.")


def preconditions(ctx):
    if not model_ready(ctx) and not download_script(ctx).is_file():
        return [f"{download_script(ctx)} is missing. Update Luna first."]
    return []


def tasks(ctx):
    from ..engine import Task
    return [
        Task("voice-packages", FEATURE, "Audio library for voice",
             ("Install system packages: " + ", ".join(VOICE_PACKAGES),),
             is_done=lambda c: not missing_packages(c, VOICE_PACKAGES),
             system_ops=({"op": "apt_install", "packages": list(VOICE_PACKAGES)},),
             needs_network=True),
        Task("voice-python", FEATURE, "Voice recognition packages",
             ("Install in Luna's environment: "
              + ", ".join(f"{n} {v}" for n, v in PINS.items()),),
             is_done=lambda c: has_pinned(c, PINS),
             apply=lambda c: pip_install(c, PINS), needs_network=True),
        Task("voice-model", FEATURE, "Offline voice model",
             (f"Download the voice model to {ctx.paths.voice_model}",),
             is_done=model_ready, apply=download_model, needs_network=True),
    ]


def verify(ctx):
    packages = has_pinned(ctx, PINS)
    model = model_ready(ctx)
    microphone = has_sound_card(ctx, "arecord")
    return [
        Check("voice-packages", "Voice packages", PASSED if packages else FAILED,
              "" if packages else "Run Repair."),
        Check("voice-model", "Voice model", PASSED if model else FAILED,
              "" if model else "Run Repair."),
        Check("microphone-device", "Microphone found", PASSED if microphone else WARNING,
              "" if microphone else "Connect a USB microphone."),
    ]


def remove(ctx):
    return [f"Voice model kept in {ctx.paths.voice_model}."]

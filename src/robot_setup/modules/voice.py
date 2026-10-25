"""Offline voice module: PortAudio, Vosk + sounddevice, and the small English and Swedish
Vosk models (Luna listens in the language of her selected voice)."""
from __future__ import annotations

from ..config import PIP_PINS, VOICE_PACKAGES, SetupError
from ..system import FAILED, PASSED, WARNING, Check
from .common import has_pinned, has_sound_card, missing_packages, pip_install

FEATURE = "voice"
PINS = {name: PIP_PINS[name] for name in ("vosk", "sounddevice")}
LANGUAGE_NAMES = {"en": "English", "sv": "Swedish"}


def download_script(ctx):
    return ctx.luna_dir / "scripts" / "download-voice-model.py"


def model_ready(ctx, language="en"):
    model = ctx.paths.voice_model_for(language)
    return (model / "am" / "final.mdl").is_file() and (model / "conf" / "model.conf").is_file()


def download_model(ctx, language="en"):
    result = ctx.runner.run([ctx.venv_python, download_script(ctx), language], cwd=ctx.luna_dir,
                            on_line=ctx.log, stream="all")
    if not result.ok:
        raise SetupError(f"Downloading the {LANGUAGE_NAMES[language]} voice model failed. "
                         "Check the internet connection, or update Luna.")
    if not model_ready(ctx, language):
        raise SetupError(f"The voice model in {ctx.paths.voice_model_for(language)} is incomplete. "
                         "Update Luna, delete that folder, then run Repair.")


def preconditions(ctx):
    if not all(model_ready(ctx, code) for code in LANGUAGE_NAMES) \
            and not download_script(ctx).is_file():
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
        Task("voice-model", FEATURE, "Offline voice model (English)",
             (f"Download the English voice model to {ctx.paths.voice_model}",),
             is_done=model_ready, apply=download_model, needs_network=True),
        Task("voice-model-sv", FEATURE, "Offline voice model (Swedish, about 290 MB)",
             (f"Download the Swedish voice model to {ctx.paths.voice_model_for('sv')}",),
             is_done=lambda c: model_ready(c, "sv"),
             apply=lambda c: download_model(c, "sv"), needs_network=True),
    ]


def verify(ctx):
    packages = has_pinned(ctx, PINS)
    model = model_ready(ctx)
    swedish = model_ready(ctx, "sv")
    microphone = has_sound_card(ctx, "arecord")
    return [
        Check("voice-packages", "Voice packages", PASSED if packages else FAILED,
              "" if packages else "Run Repair."),
        Check("voice-model", "Voice model", PASSED if model else FAILED,
              "" if model else "Run Repair."),
        Check("voice-model-sv", "Swedish voice model", PASSED if swedish else WARNING,
              "" if swedish else "Luna listens in English. Run Repair."),
        Check("microphone-device", "Microphone found", PASSED if microphone else WARNING,
              "" if microphone else "Connect a USB microphone."),
    ]


def remove(ctx):
    return ["Voice models kept in " + ", ".join(
        str(ctx.paths.voice_model_for(code)) for code in LANGUAGE_NAMES) + "."]

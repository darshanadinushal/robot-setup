"""Offline voice module: PortAudio, Vosk + sounddevice, and the small offline listening model of
every language in Luna's language packs (Luna listens in the language of her selected voice).

English is required; a missing model for another language is a warning, because Luna then keeps
listening in English.
"""
from __future__ import annotations

from .. import catalogue
from ..config import PIP_PINS, VOICE_PACKAGES, SetupError
from ..system import FAILED, PASSED, WARNING, Check
from .common import has_pinned, has_sound_card, missing_packages, pip_install

FEATURE = "voice"
PINS = {name: PIP_PINS[name] for name in ("vosk", "sounddevice")}


def download_script(ctx):
    return ctx.luna_dir / "scripts" / "download-voice-model.py"


def listening_models(ctx):
    """Luna's listening models (English first), or English only if they cannot be read."""
    try:
        return catalogue.listening_models(ctx.luna_dir)
    except SetupError:
        return tuple(catalogue.ListeningModel(*item) for item in catalogue.OLD_LUNA_MODELS)


def task_id(model):
    return "voice-model" if model.language == catalogue.DEFAULT_LANGUAGE \
        else f"voice-model-{model.language}"


def model_ready(ctx, model):
    folder = ctx.paths.listening_model(model.model)
    return (folder / "am" / "final.mdl").is_file() and (folder / "conf" / "model.conf").is_file()


def download_model(ctx, model):
    result = ctx.runner.run([ctx.venv_python, download_script(ctx), model.language],
                            cwd=ctx.luna_dir, on_line=ctx.log, stream="all")
    if not result.ok:
        raise SetupError(f"Downloading the {model.name} voice model failed. "
                         "Check the internet connection, or update Luna.")
    if not model_ready(ctx, model):
        raise SetupError(f"The voice model in {ctx.paths.listening_model(model.model)} is "
                         "incomplete. Update Luna, delete that folder, then run Repair.")


def preconditions(ctx):
    try:
        models = catalogue.listening_models(ctx.luna_dir)
    except SetupError as error:
        return [str(error)]
    if not all(model_ready(ctx, m) for m in models) and not download_script(ctx).is_file():
        return [f"{download_script(ctx)} is missing. Update Luna first."]
    return []


def tasks(ctx):
    from ..engine import Task
    items = [
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
    ]
    for model in listening_models(ctx):
        size = f", about {model.size_mb} MB" if model.size_mb else ""
        items.append(Task(
            task_id(model), FEATURE, f"Offline voice model ({model.name}{size})",
            (f"Download the {model.name} voice model to "
             f"{ctx.paths.listening_model(model.model)}",),
            is_done=lambda c, m=model: model_ready(c, m),
            apply=lambda c, m=model: download_model(c, m), needs_network=True))
    return items


def verify(ctx):
    packages = has_pinned(ctx, PINS)
    microphone = has_sound_card(ctx, "arecord")
    checks = [Check("voice-packages", "Voice packages", PASSED if packages else FAILED,
                    "" if packages else "Run Repair.")]
    for model in listening_models(ctx):
        ready = model_ready(ctx, model)
        if model.language == catalogue.DEFAULT_LANGUAGE:
            checks.append(Check(task_id(model), "Voice model", PASSED if ready else FAILED,
                                "" if ready else "Run Repair."))
        else:
            checks.append(Check(task_id(model), f"{model.name} voice model",
                                PASSED if ready else WARNING,
                                "" if ready else "Luna listens in English. Run Repair."))
    checks.append(Check("microphone-device", "Microphone found", PASSED if microphone else WARNING,
                        "" if microphone else "Connect a USB microphone."))
    return checks


def remove(ctx):
    return ["Voice models kept in " + ", ".join(
        str(ctx.paths.listening_model(m.model)) for m in listening_models(ctx)) + "."]

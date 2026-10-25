"""Constants, features, profiles, paths and the setup request (no secrets)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


class SetupError(Exception):
    """A setup problem with a short message that is safe to show to the user."""


# Canonical order: features are planned, installed and verified in this order.
FEATURES = ("luna", "voice", "piper", "ai", "startup", "autologin")
FEATURE_TITLES = {
    "luna": "Luna",
    "voice": "Offline voice",
    "piper": "Natural voice",
    "ai": "Online AI",
    "startup": "Automatic startup",
    "autologin": "Desktop auto-login",
}
FEATURE_REQUIRES = {
    "voice": ("luna",),
    "piper": ("luna",),
    "ai": ("luna",),
    "startup": ("luna",),
    "autologin": ("startup",),
}
PROFILES = {
    "minimal": ("luna",),
    "standard": ("luna", "voice", "startup"),
}

SUPPORTED_CODENAMES = ("trixie",)
SUPPORTED_MACHINES = ("aarch64",)
MIN_PYTHON = (3, 11)
MIN_FREE_MB = 1024

LUNA_DIR_CANDIDATES = ("luna", "ai-robot")
SERVICE_NAME = "luna.service"
VOICE_MODEL_NAME = "vosk-model-small-en-us-0.15"
# Offline listening models; Luna listens in the language of her selected voice.
VOICE_MODEL_NAMES = {"en": VOICE_MODEL_NAME, "sv": "vosk-model-small-sv-rhasspy-0.15"}

# Python packages installed into Luna's virtual environment (tested versions).
PIP_PINS = {"vosk": "0.3.45", "sounddevice": "0.5.6", "openai": "3.37.0",
            "piper-tts": "1.8.0", "onnxruntime": "1.31.0"}

LUNA_PACKAGES = ("python3", "python3-pygame", "python3-venv", "espeak-ng", "alsa-utils")
VOICE_PACKAGES = ("libportaudio2",)
# The privileged helper refuses any package outside this list.
ALLOWED_PACKAGES = frozenset(LUNA_PACKAGES + VOICE_PACKAGES)

# Natural (Piper) voices: English and Swedish, female and male. Downloads are pinned to one
# repository commit and checked by size and SHA-256, so a changed or damaged file is never used.
# The English voices are PROVISIONAL (non-commercial training data). A licensing review is
# required before distribution.
PIPER_VOICE_BASE = ("https://huggingface.co/rhasspy/piper-voices/resolve/"
                    "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/")


@dataclass(frozen=True)
class VoiceFile:
    name: str
    source: str  # path below PIPER_VOICE_BASE
    size: int
    sha256: str


@dataclass(frozen=True)
class PiperVoice:
    key: str
    language: str
    label: str
    model: str
    license_note: str
    sample: str  # spoken by the checks, in the voice's own language
    files: tuple

    @property
    def size_mb(self):
        return round(sum(f.size for f in self.files) / 1_000_000)


def _files(folder, model, onnx_size, onnx_sha, json_size, json_sha):
    return (VoiceFile(f"{model}.onnx", f"{folder}/{model}.onnx", onnx_size, onnx_sha),
            VoiceFile(f"{model}.onnx.json", f"{folder}/{model}.onnx.json", json_size, json_sha))


PIPER_VOICES = (
    PiperVoice(
        "EN_FEMALE", "en", "English female voice (Lessac)", "en_US-lessac-medium",
        "prototype only: training data is licensed for non-commercial use",
        "Hello, I am Luna. This is my English female voice.",
        _files("en/en_US/lessac/medium", "en_US-lessac-medium",
               63201294, "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
               4885, "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0")),
    PiperVoice(
        "EN_MALE", "en", "English male voice (Ryan)", "en_US-ryan-high",
        "prototype only: training data is CC BY-NC-SA 4.0 (non-commercial)",
        "Hello, I am Luna. This is my English male voice.",
        _files("en/en_US/ryan/high", "en_US-ryan-high",
               120786792, "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca",
               4166, "c6d3b98f08315cb4bebf0d49d50fc4ff491b503c64b940cd3d5ca28543b48011")),
    PiperVoice(
        "SV_FEMALE", "sv", "Swedish female voice (Alma)", "sv_SE-alma-medium",
        "model CC BY 4.0: credit the voice when Luna is shared",
        "Hej, jag heter Luna. Det här är min svenska kvinnliga röst.",
        _files("sv/sv_SE/alma/medium", "sv_SE-alma-medium",
               63434611, "f75455eb6afeb1db9e2905d5bd7e4115afcd3710e0d399b6a40981766d32fedc",
               6059, "6924380892f769afa92fc6b28ff91d558690d7fb4e3ef8cbf821cefadc8f38fe")),
    PiperVoice(
        "SV_MALE", "sv", "Swedish male voice (NST)", "sv_SE-nst-medium",
        "training data CC0; confirm the model licence before distribution",
        "Hej, jag heter Luna. Det här är min svenska manliga röst.",
        _files("sv/sv_SE/nst/medium", "sv_SE-nst-medium",
               63104526, "df011f56825a59dd1efc080c38a65a1ef70407e60f63050e9246f43a3d7e471e",
               4157, "d45dd74cbb4eca58694bf04a97e243044092476f28a55ae26424f0653086980a")),
)

AI_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


def resolve_features(base=(), add=(), remove=()):
    """Return the selected features in canonical order, including required features."""
    for name in list(base) + list(add) + list(remove):
        if name not in FEATURES:
            raise SetupError(f"Unknown feature: {name}")
    selected = (set(base) | set(add)) - set(remove)
    changed = True
    while changed:
        changed = False
        for name in list(selected):
            for needed in FEATURE_REQUIRES.get(name, ()):
                if needed in remove:
                    raise SetupError(
                        f"{FEATURE_TITLES[name]} needs {FEATURE_TITLES[needed]}.")
                if needed not in selected:
                    selected.add(needed)
                    changed = True
    return tuple(name for name in FEATURES if name in selected)


@dataclass
class Request:
    """What the user asked for. Saved in the state file, so it must never hold secrets."""

    features: tuple
    luna_dir: str
    profile: str = "custom"
    ai_model: str = ""
    validate_ai: bool = True

    def to_dict(self):
        return {
            "features": list(self.features),
            "luna_dir": self.luna_dir,
            "profile": self.profile,
            "ai_model": self.ai_model,
            "validate_ai": self.validate_ai,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise SetupError("The saved setup request is damaged.")
        features = resolve_features(data.get("features") or ())
        return cls(
            features=features,
            luna_dir=str(data.get("luna_dir") or ""),
            profile=str(data.get("profile") or "custom"),
            ai_model=str(data.get("ai_model") or ""),
            validate_ai=bool(data.get("validate_ai", True)),
        )


@dataclass
class Paths:
    """All per-user locations used by Robot Setup."""

    home: Path
    state_dir: Path

    @classmethod
    def for_user(cls, home=None):
        home = Path(home) if home else Path.home()
        override = os.environ.get("ROBOT_SETUP_STATE_DIR")
        state_dir = Path(override) if override else home / ".local" / "state" / "robot-setup"
        return cls(home=home, state_dir=state_dir)

    @property
    def state_file(self):
        return self.state_dir / "state.json"

    @property
    def lock_file(self):
        return self.state_dir / "lock"

    @property
    def backups_dir(self):
        return self.state_dir / "backups"

    @property
    def systemd_unit(self):
        return self.home / ".config" / "systemd" / "user" / SERVICE_NAME

    @property
    def launcher(self):
        return self.home / ".local" / "share" / "robot-setup" / "start-luna"

    @property
    def autostart_entry(self):
        return self.home / ".config" / "autostart" / "luna-robot-setup.desktop"

    @property
    def autostart_marker(self):
        """Boot id written by the launcher when the desktop login started it."""
        return self.state_dir / "autostart-boot-id"

    @property
    def env_file(self):
        return self.home / ".luna.env"

    @property
    def voice_model(self):
        return self.voice_model_for("en")

    def voice_model_for(self, language):
        return self.home / ".ai-robot-models" / VOICE_MODEL_NAMES[language]

    @property
    def piper_dir(self):
        return self.home / ".ai-robot-models" / "piper"

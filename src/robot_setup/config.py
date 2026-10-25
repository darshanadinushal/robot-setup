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
# English offline listening model (other languages are named in Luna's language packs).
VOICE_MODEL_NAME = "vosk-model-small-en-us-0.15"

# Python packages installed into Luna's virtual environment (tested versions).
PIP_PINS = {"vosk": "0.3.45", "sounddevice": "0.5.6", "openai": "3.37.0",
            "piper-tts": "1.8.0", "onnxruntime": "1.31.0"}

LUNA_PACKAGES = ("python3", "python3-pygame", "python3-venv", "espeak-ng", "alsa-utils")
VOICE_PACKAGES = ("libportaudio2",)
# The privileged helper refuses any package outside this list.
ALLOWED_PACKAGES = frozenset(LUNA_PACKAGES + VOICE_PACKAGES)

# Natural (Piper) voices and listening languages are read from Luna's folder
# (robot_hud/voices.json and robot_hud/languages), see catalogue.py.

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
        return self.listening_model(VOICE_MODEL_NAME)

    def listening_model(self, name):
        return self.home / ".ai-robot-models" / name

    @property
    def piper_dir(self):
        return self.home / ".ai-robot-models" / "piper"

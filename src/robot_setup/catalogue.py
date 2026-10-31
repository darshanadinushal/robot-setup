"""Luna's voices and listening languages, read from the Luna folder (one source of truth).

Luna's luna_core/voices.json (previously robot_hud/voices.json) lists the natural (Piper) voices with the size and SHA-256 of every
file, and luna_core/languages/<code>.json names each language's offline listening model. When a
voice or language is added to Luna, robot-setup downloads it on the next Install or Repair.
Everything is validated first, because file names become paths on disk and the SHA-256 values
decide which downloads are trusted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import VOICE_MODEL_NAME, SetupError

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,250}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
CODE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
DEFAULT_LANGUAGE = "en"
# Used with an older Luna that has no language packs yet.
OLD_LUNA_MODELS = ((DEFAULT_LANGUAGE, "English", VOICE_MODEL_NAME, 40),)


@dataclass(frozen=True)
class VoiceFile:
    name: str
    url: str
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


@dataclass(frozen=True)
class ListeningModel:
    language: str
    name: str  # language name in English
    model: str  # folder in ~/.ai-robot-models
    size_mb: int


def voices_file(luna_dir):
    """Prefer Luna Core 1.x metadata; fall back to the old robot_hud path."""
    root = Path(luna_dir)
    current = root / "luna_core" / "voices.json"
    return current if current.is_file() else root / "robot_hud" / "voices.json"


def languages_dir(luna_dir):
    """Use the canonical Luna Core languages when present, or legacy packs."""
    root = Path(luna_dir)
    current = root / "luna_core" / "languages"
    return current if current.is_dir() else root / "robot_hud" / "languages"


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SetupError(f"{path} could not be read ({type(error).__name__}). "
                         "Update Luna, then run Repair.") from None


def _invalid(path, problem):
    return SetupError(f"{path}: {problem}. Update Luna, then run Repair.")


def language_packs(luna_dir):
    """{code: pack data}, English first; {} for an older Luna without language packs."""
    folder = languages_dir(luna_dir)
    packs = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        data = _read(path)
        if not isinstance(data, dict) or data.get("code") != path.stem or not CODE.match(path.stem):
            raise _invalid(path, "the language code does not match the file name")
        packs[path.stem] = data
    if packs and DEFAULT_LANGUAGE not in packs:
        raise _invalid(folder, "the English language pack is missing")
    return dict(sorted(packs.items(), key=lambda item: item[0] != DEFAULT_LANGUAGE))


def listening_models(luna_dir):
    """The offline listening model of every language, English first."""
    packs = language_packs(luna_dir)
    if not packs:
        return tuple(ListeningModel(*item) for item in OLD_LUNA_MODELS)
    models = []
    for code, pack in packs.items():
        model = pack.get("listening_model")
        if not model:
            continue  # Luna listens in English for this language
        if not isinstance(model, str) or not SAFE_NAME.match(model):
            raise _invalid(languages_dir(luna_dir) / f"{code}.json", "unsafe listening_model")
        size = pack.get("listening_model_mb", 0)
        models.append(ListeningModel(code, str(pack.get("name") or code), model,
                                     size if isinstance(size, int) and size > 0 else 0))
    if not models or models[0].language != DEFAULT_LANGUAGE:
        raise _invalid(languages_dir(luna_dir), "English has no listening_model")
    return tuple(models)


def _voice(path, index, item, base, packs):
    where = f"voice {index + 1}"
    if not isinstance(item, dict):
        raise _invalid(path, f"{where} is not an object")
    key, language, model = item.get("key"), item.get("language"), item.get("model")
    if not isinstance(key, str) or not KEY.match(key):
        raise _invalid(path, f"{where} has an unsafe key")
    if not isinstance(language, str) or not CODE.match(language):
        raise _invalid(path, f"{key} has an unknown language")
    if not isinstance(model, str) or not SAFE_NAME.match(model):
        raise _invalid(path, f"{key} has an unsafe model name")
    files = []
    for entry in item.get("files") or ():
        name, source = entry.get("name"), entry.get("source")
        size, sha256 = entry.get("size"), entry.get("sha256")
        if not (isinstance(name, str) and SAFE_NAME.match(name) and name.startswith(model)):
            raise _invalid(path, f"{key} has an unsafe file name")
        if not (isinstance(source, str) and SAFE_SOURCE.match(source)
                and ".." not in source.split("/")):
            raise _invalid(path, f"{key} has an unsafe download path")
        if not (isinstance(size, int) and size > 0 and isinstance(sha256, str)
                and SHA256.match(sha256)):
            raise _invalid(path, f"{key} needs the size and SHA-256 of {name}")
        files.append(VoiceFile(name, base + source, size, sha256))
    if {f.name for f in files} != {model + ".onnx", model + ".onnx.json"}:
        raise _invalid(path, f"{key} needs {model}.onnx and {model}.onnx.json")
    pack = packs.get(language) or {}
    language_name = str(pack.get("name") or language)
    gender = str(item.get("gender") or "").lower()
    label = " ".join(part for part in (language_name, gender, "voice") if part)
    if item.get("name"):
        label += f" ({item['name']})"
    sample = pack.get("sample") or (packs.get(DEFAULT_LANGUAGE) or {}).get("sample") \
        or "Hello, I am Luna."
    note = item.get("license_details") or item.get("license_note") or ""
    return PiperVoice(key, language, label, model, str(note), str(sample), tuple(files))


def piper_voices(luna_dir):
    """Luna's natural voices, each with its pinned download files."""
    path = voices_file(luna_dir)
    if not path.is_file():
        raise SetupError(f"{path} is missing, so the natural voices are unknown. "
                         "Update Luna, then run Repair.")
    data = _read(path)
    base = data.get("download_base") if isinstance(data, dict) else None
    if not isinstance(base, str) or not base.startswith("https://") or not base.endswith("/"):
        raise _invalid(path, "download_base must be an https:// address ending in /")
    packs = language_packs(luna_dir)
    voices = [_voice(path, i, item, base, packs) for i, item in enumerate(data.get("voices") or ())]
    keys = [v.key for v in voices]
    if not voices or len(keys) != len(set(keys)):
        raise _invalid(path, "the voice list is empty or has duplicate keys")
    return tuple(voices)

"""Natural voice module (optional): Piper text-to-speech and Luna's natural voices.

The voices come from Luna's robot_hud/voices.json (see catalogue.py). Voices are stored outside the Luna checkout in ~/.ai-robot-models/piper. Each file is
downloaded to a .part file, checked by size and SHA-256, and only then renamed, so a broken
or interrupted download is never used. Luna keeps working with eSpeak if this feature fails.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import ssl
import urllib.error
import urllib.request

from .. import catalogue
from ..config import PIP_PINS, SetupError
from ..system import FAILED, PASSED, Check
from .common import has_pinned, pip_install

FEATURE = "piper"
PINS = {name: PIP_PINS[name] for name in ("piper-tts", "onnxruntime")}
CHUNK = 1 << 20

# Runs inside Luna's environment. Reports only exception class names and timings.
SYNTH_SCRIPT = (
    "import json, sys, time, wave\n"
    "try:\n"
    "    from piper import PiperVoice\n"
    "except Exception as error:\n"
    "    print(json.dumps({'error': type(error).__name__}))\n"
    "    sys.exit(0)\n"
    "out, rest = {}, sys.argv[1:]\n"
    "for i in range(0, len(rest), 4):\n"
    "    key, model, wav, text = rest[i:i + 4]\n"
    "    try:\n"
    "        start = time.monotonic()\n"
    "        voice = PiperVoice.load(model)\n"
    "        loaded = time.monotonic()\n"
    "        with wave.open(wav, 'wb') as handle:\n"
    "            voice.synthesize_wav(text, handle)\n"
    "        done = time.monotonic()\n"
    "        with wave.open(wav, 'rb') as handle:\n"
    "            audio = handle.getnframes() / float(handle.getframerate())\n"
    "        out[key] = {'load': round(loaded - start, 2), 'synth': round(done - loaded, 2),\n"
    "                    'audio': round(audio, 2)}\n"
    "    except Exception as error:\n"
    "        out[key] = {'error': type(error).__name__}\n"
    "print(json.dumps(out))\n"
)
FRIENDLY = {
    "ModuleNotFoundError": "Piper is not installed. Run Repair.",
    "ImportError": "Piper could not start. Run Repair.",
    "FileNotFoundError": "The voice files are missing. Run Repair.",
}


def luna_voices(ctx):
    """Luna's natural voices, read from the Luna folder."""
    return catalogue.piper_voices(ctx.luna_dir)


def known_voices(ctx):
    """Luna's natural voices, or () if her catalogue is missing or invalid."""
    try:
        return luna_voices(ctx)
    except SetupError:
        return ()


def fetch(url):
    """Open a download. Replaced in tests."""
    return urllib.request.urlopen(url, timeout=60)


def file_path(ctx, item):
    return ctx.paths.piper_dir / item.name


def file_ok(path, item):
    try:
        if path.stat().st_size != item.size:
            return False
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest() == item.sha256
    except OSError:
        return False


def voice_ready(ctx, voice):
    return all(file_ok(file_path(ctx, item), item) for item in voice.files)


def download_file(ctx, item):
    dest = file_path(ctx, item)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest, received, report = hashlib.sha256(), 0, 0.25
    finished = False
    try:
        try:
            with fetch(item.url) as response, open(part, "wb") as out:
                for chunk in iter(lambda: response.read(CHUNK), b""):
                    received += len(chunk)
                    if received > item.size:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    if item.size > 10 * CHUNK and received >= report * item.size:
                        ctx.log(f"  {item.name}: {int(report * 100)}%")
                        report += 0.25
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError,
                ConnectionError) as error:
            if isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError):
                raise SetupError(f"Downloading {item.name} failed: the secure connection could "
                                 "not be verified. Check the date and time on the robot, "
                                 "then run Repair.") from None
            raise SetupError(f"Downloading {item.name} failed. "
                             "Check the internet connection, then run Repair.") from None
        if received != item.size or digest.hexdigest() != item.sha256:
            raise SetupError(f"{item.name} was not the expected file, so it was not used. "
                             "Run Repair to download it again.")
        os.replace(part, dest)
        finished = True
    finally:
        if not finished:
            part.unlink(missing_ok=True)


def download_voice(ctx, voice):
    for item in voice.files:
        if not file_ok(file_path(ctx, item), item):
            ctx.log(f"Downloading {item.name} ({item.size // 1_000_000 or 1} MB)...")
            download_file(ctx, item)


def synthesize(ctx, jobs):
    """jobs: [(voice, wav_path, text)]. Returns {key: result} or {"error": name}."""
    if not ctx.venv_python.exists():
        return {"error": "ModuleNotFoundError"}
    args = []
    for voice, wav, text in jobs:
        args += [voice.key, file_path(ctx, voice.files[0]), wav, text]
    result = ctx.runner.run([ctx.venv_python, "-c", SYNTH_SCRIPT, *args],
                            cwd=ctx.paths.piper_dir, timeout=300)
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
        return data if isinstance(data, dict) else {"error": "Error"}
    except (ValueError, IndexError):
        return {"error": "Error"}


def generation_checks(ctx):
    """Make one sentence with each installed voice to prove Piper works on this device."""
    ready = [v for v in known_voices(ctx) if voice_ready(ctx, v)]
    checks = []
    if ready:
        work = ctx.paths.state_dir / "piper-check"
        work.mkdir(parents=True, exist_ok=True)
        jobs = [(v, work / f"{v.key.lower()}.wav", v.sample) for v in ready]
        try:
            results = synthesize(ctx, jobs)
        finally:
            for _, wav, _ in jobs:
                wav.unlink(missing_ok=True)
        for voice in ready:
            info = results.get(voice.key) or {"error": results.get("error", "Error")}
            check_id, label = f"piper-speech-{voice.key.lower()}", f"Speech from {voice.model}"
            if info.get("audio", 0) > 0.3:
                checks.append(Check(check_id, label, PASSED,
                                    f"{info['audio']} s of speech made in {info['synth']} s "
                                    f"(voice loaded in {info['load']} s)"))
            else:
                checks.append(Check(check_id, label, FAILED,
                                    FRIENDLY.get(info.get("error"),
                                                 "Piper could not make speech. Run Repair.")))
    return checks


def play_samples(ctx):
    """Speak one sentence with each voice through the speaker (used by the listening check)."""
    work = ctx.paths.state_dir / "piper-check"
    work.mkdir(parents=True, exist_ok=True)
    jobs = [(v, work / f"{v.key.lower()}.wav", v.sample)
            for v in known_voices(ctx) if voice_ready(ctx, v)]
    if not jobs:
        return "No natural voice is installed. Run Repair."
    try:
        results = synthesize(ctx, jobs)
        played = []
        for voice, wav, _ in jobs:
            if (results.get(voice.key) or {}).get("audio", 0) <= 0:
                continue
            ctx.log(f"Playing the {voice.label}...")
            if ctx.runner.run(["aplay", "-q", wav], timeout=60).ok:
                played.append(voice.label)
        if not played:
            return "Could not play the natural voices."
        return f"Played {len(played)} of {len(jobs)} voices: " + ", ".join(played) + "."
    finally:
        for _, wav, _ in jobs:
            wav.unlink(missing_ok=True)


def tasks(ctx):
    from ..engine import Task
    items = [
        Task("piper-python", FEATURE, "Natural voice engine",
             ("Install in Luna's environment: "
              + ", ".join(f"{n} {v}" for n, v in PINS.items()),),
             is_done=lambda c: has_pinned(c, PINS),
             apply=lambda c: pip_install(c, PINS), needs_network=True),
    ]
    for voice in known_voices(ctx):
        items.append(Task(
            f"piper-voice-{voice.key.lower()}", FEATURE, voice.label,
            (f"Download {voice.model} (about {voice.size_mb} MB) to {ctx.paths.piper_dir} "
             f"- {voice.license_note}",),
            is_done=lambda c, v=voice: voice_ready(c, v),
            apply=lambda c, v=voice: download_voice(c, v), needs_network=True))
    return items


def preconditions(ctx):
    try:
        luna_voices(ctx)
    except SetupError as error:
        return [str(error)]
    return []


def verify(ctx):
    package = has_pinned(ctx, PINS)
    checks = [Check("piper-package", "Natural voice engine", PASSED if package else FAILED,
                    "" if package else "Run Repair.")]
    try:
        voices = luna_voices(ctx)
    except SetupError as error:
        voices = ()
        checks.append(Check("piper-voices", "Natural voice list", FAILED, str(error)))
    for voice in voices:
        ready = voice_ready(ctx, voice)
        checks.append(Check(f"piper-voice-{voice.key.lower()}", f"Voice {voice.model}",
                            PASSED if ready else FAILED,
                            voice.license_note if ready else "Missing or damaged. Run Repair."))
    if package:
        checks.extend(generation_checks(ctx))
    return checks


def remove(ctx):
    total = sum(v.size_mb for v in known_voices(ctx))
    size = f" (about {total} MB)" if total else ""
    return [f"Natural voices kept in {ctx.paths.piper_dir}{size}. "
            "Delete that folder to remove them."]

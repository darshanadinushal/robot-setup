"""Test helpers: a fake device, a fake command runner and a context in a temporary home."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from robot_setup import engine  # noqa: E402
from robot_setup.config import Paths, Request  # noqa: E402
from robot_setup.system import DeviceInfo, Result  # noqa: E402

TEST_KEY = "sk-test-" + "x" * 40
FAKE_BASE = "https://voices.example/pinned/"
# The fake Luna's listening languages: language -> listening model folder.
LISTENING = {"en": "vosk-model-small-en-us-0.15", "sv": "vosk-model-small-sv-test"}
LANGUAGE_PACKS = {
    "en": {"code": "en", "name": "English", "native_name": "English",
           "listening_model": LISTENING["en"], "listening_model_mb": 40, "sample": "Hello sample."},
    "sv": {"code": "sv", "name": "Swedish", "native_name": "Svenska",
           "listening_model": LISTENING["sv"], "listening_model_mb": 289, "sample": "Hej sample."},
}
# File contents served by the fake download server.
VOICE_CONTENT = {"test-female.onnx": b"F" * 3000, "test-female.onnx.json": b"{}",
                 "test-male.onnx": b"M" * 5000, "test-male.onnx.json": b"{}"}


def fake_voice(key, language, gender, name, model):
    files = [{"name": model + suffix, "source": f"{language}/{model}{suffix}",
              "size": len(VOICE_CONTENT[model + suffix]),
              "sha256": hashlib.sha256(VOICE_CONTENT[model + suffix]).hexdigest()}
             for suffix in (".onnx", ".onnx.json")]
    return {"key": key, "language": language, "gender": gender, "name": name, "model": model,
            "espeak": language, "license_details": "test licence", "files": files}


TEST_VOICES = [fake_voice("EN_FEMALE", "en", "female", "Fem", "test-female"),
               fake_voice("SV_MALE", "sv", "male", "Man", "test-male")]


def write_luna_catalogue(luna, voices=None, packs=None, base=FAKE_BASE):
    """Write Luna's voices.json and language packs into a fake Luna folder."""
    folder = Path(luna) / "robot_hud" / "languages"
    folder.mkdir(parents=True, exist_ok=True)
    for code, pack in (LANGUAGE_PACKS if packs is None else packs).items():
        (folder / f"{code}.json").write_text(json.dumps(pack), encoding="utf-8")
    catalogue = {"default": "EN_FEMALE", "download_base": base,
                 "voices": TEST_VOICES if voices is None else voices}
    (Path(luna) / "robot_hud" / "voices.json").write_text(json.dumps(catalogue), encoding="utf-8")


def pi_device(**changes):
    values = dict(system="Linux", machine="aarch64", model="Raspberry Pi 5 Model B Rev 1.0",
                  os_name="Debian GNU/Linux 13 (trixie)", codename="trixie", python=(3, 13, 5),
                  user="robot", uid=1000, graphical=False, ssh=False, desktop="", free_mb=8000,
                  online=True, boot_id="boot-1")
    values.update(changes)
    return DeviceInfo(**values)


class FakeSystem:
    """Simulates apt, venv creation and pip so tasks can run end to end."""

    def __init__(self):
        self.calls = []
        self.packages = set()
        self.pip = {}
        self.fail = set()  # command names that should fail
        self.elevations = []
        self.download = None
        self.piper_output = None  # replaces the fake Piper result when set
        self.lightdm = None  # fake /etc/lightdm/lightdm.conf for auto-login tests
        self.default_target = "graphical.target"
        self.service = {"ActiveState": "inactive", "SubState": "dead", "Result": "success",
                        "NRestarts": "0", "ExecMainStartTimestamp": ""}

    def run(self, args, **kwargs):
        args = [str(a) for a in args]
        self.calls.append((args, kwargs))
        name = Path(args[0]).name
        if name in self.fail or (len(args) > 2 and args[1:3] == ["-m", "venv"] and "venv" in self.fail):
            return Result(1, "", "failed")
        if name == "dpkg-query":
            installed = args[-1] in self.packages
            return Result(0 if installed else 1, "install ok installed" if installed else "")
        if len(args) > 3 and args[1:3] == ["-m", "venv"]:
            venv = Path(args[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("")
            (venv / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
            return Result(0)
        if len(args) > 3 and args[1:4] == ["-m", "pip", "install"]:
            for spec in args[4:]:
                if "==" in spec:
                    package, version = spec.split("==")
                    self.pip[package] = version
            return Result(0, "installed")
        if len(args) > 2 and args[1] == "-c" and "metadata" in args[2]:
            return Result(0, json.dumps({n: self.pip.get(n) for n in args[3:]}))
        if len(args) > 2 and args[1] == "-c" and "PiperVoice" in args[2]:
            if self.piper_output is not None:
                return Result(0, self.piper_output)
            keys = args[3::4]
            return Result(0, json.dumps({k: {"load": 0.5, "synth": 0.4, "audio": 1.2}
                                         for k in keys}))
        if len(args) > 2 and args[1] == "-c":
            return Result(0, "ok")
        if any(str(a).endswith("download-voice-model.py") for a in args):
            if self.download:
                self.download(args[-1] if args[-1] in LISTENING else "en")
            return Result(0, "Installed")
        if name == "systemctl":
            if "get-default" in args:
                return Result(0, self.default_target + "\n")
            if "ActiveState" in args:
                return Result(0, "\n".join(f"{k}={v}" for k, v in self.service.items()))
            return Result(0, "inactive" if "is-active" in args else "")
        return Result(0, "")

    def elevate(self, ctx, operations):
        self.elevations.append(operations)
        for op in operations:
            if op["op"] == "apt_install":
                self.packages.update(op["packages"])
            elif op["op"] == "autologin" and self.lightdm:
                user = "robot" if op["enable"] else ""
                self.lightdm.write_text(f"[Seat:*]\nautologin-user={user}\n")
                self.default_target = "multi-user.target" if op.get("console") \
                    else "graphical.target"
        return {"ok": True}


class Sandbox:
    """A temporary home with a Luna checkout."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.luna = self.home / "luna"
        (self.luna / "robot_hud").mkdir(parents=True)
        (self.luna / "scripts").mkdir()
        (self.luna / "main.py").write_text("print('luna')\n")
        (self.luna / "robot_hud" / "app.py").write_text("")
        (self.luna / "scripts" / "download-voice-model.py").write_text("")
        write_luna_catalogue(self.luna)
        self.paths = Paths(home=self.home, state_dir=self.home / "state")
        self.system = FakeSystem()
        self.system.download = self.make_voice_model

    def context(self, features=("luna",), secrets=None, device=None, **request):
        req = Request(features=features, luna_dir=str(self.luna), **request)
        logs = []
        ctx = engine.open_context(req, paths=self.paths, runner=self.system,
                                  device=device or pi_device(), log=logs.append,
                                  secrets=secrets, elevate=self.system.elevate)
        ctx.logs = logs
        return ctx

    def make_voice_model(self, language="en"):
        model = self.paths.listening_model(LISTENING[language])
        (model / "am").mkdir(parents=True)
        (model / "conf").mkdir(parents=True)
        (model / "am" / "final.mdl").write_text("")
        (model / "conf" / "model.conf").write_text("")

    def cleanup(self):
        self.tmp.cleanup()

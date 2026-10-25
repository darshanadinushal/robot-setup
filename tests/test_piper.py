import hashlib
import io
import json
import ssl
import unittest
import urllib.error
from unittest import mock

from helpers import Sandbox

from robot_setup import checks, engine
from robot_setup.config import PIPER_VOICE_BASE, PIPER_VOICES, PiperVoice, VoiceFile, \
    resolve_features, PROFILES
from robot_setup.modules import piper
from robot_setup.state import State, VERIFIED

FEATURES = ("luna", "piper")


def fake_voice(key, model, content):
    files = []
    for suffix, data in ((".onnx", content), (".onnx.json", b"{}")):
        files.append(VoiceFile(model + suffix, f"voices/{model}{suffix}", len(data),
                               hashlib.sha256(data).hexdigest()))
    return PiperVoice(key, key[:2].lower(), f"{key.lower()} voice", model, "test licence",
                      f"Sample by {model}.", tuple(files))


class FakeServer:
    """Serves the fake voice files; can fail or return damaged data."""

    def __init__(self, content):
        self.content = content
        self.requests = []
        self.broken = set()
        self.offline = False

    def __call__(self, url):
        self.requests.append(url)
        if self.offline:
            raise urllib.error.URLError("offline")
        name = url.rsplit("/", 1)[-1]
        data = self.content[name]
        if name in self.broken:
            data = data[:-1] + b"X"
        return io.BytesIO(data)


class PiperTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.voices = (fake_voice("EN_FEMALE", "test-female", b"F" * 3000),
                       fake_voice("SV_MALE", "test-male", b"M" * 5000))
        content = {"test-female.onnx": b"F" * 3000, "test-male.onnx": b"M" * 5000,
                   "test-female.onnx.json": b"{}", "test-male.onnx.json": b"{}"}
        self.server = FakeServer(content)
        for patcher in (mock.patch.object(piper, "PIPER_VOICES", self.voices),
                        mock.patch.object(piper, "fetch", self.server)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.box.cleanup()

    def install(self):
        return engine.install(self.box.context(features=FEATURES))

    def test_feature_is_optional_and_needs_luna(self):
        self.assertNotIn("piper", PROFILES["standard"])
        self.assertEqual(resolve_features(add=["piper"]), FEATURES)

    def test_plan_shows_downloads_with_licence_notes(self):
        plan = engine.build_plan(self.box.context(features=FEATURES))
        ids = [t.id for t in plan.pending]
        self.assertEqual(ids[-3:], ["piper-python", "piper-voice-en_female", "piper-voice-sv_male"])
        text = "\n".join(engine.describe_plan(self.box.context(features=FEATURES), plan))
        self.assertIn("piper-tts 1.8.0", text)
        self.assertIn("test licence", text)
        self.assertIn(str(self.box.paths.piper_dir), text)

    def test_install_downloads_outside_luna_and_is_repeatable(self):
        outcome = self.install()
        self.assertTrue(outcome.ok, outcome.message)
        piper_dir = self.box.paths.piper_dir
        self.assertFalse(piper_dir.is_relative_to(self.box.luna))
        self.assertEqual(sorted(p.name for p in piper_dir.iterdir()),
                         ["test-female.onnx", "test-female.onnx.json",
                          "test-male.onnx", "test-male.onnx.json"])
        self.assertTrue(all(u.startswith(PIPER_VOICE_BASE) for u in self.server.requests))
        self.assertEqual(self.box.system.pip, {"piper-tts": "1.8.0", "onnxruntime": "1.31.0"})
        state = State(self.box.paths.state_file).load()
        self.assertTrue(all(i["status"] == VERIFIED for i in state.tasks.values()))
        count = len(self.server.requests)
        self.assertTrue(self.install().ok)
        self.assertEqual(len(self.server.requests), count, "nothing is downloaded twice")

    def test_damaged_download_is_rejected_and_repair_fixes_it(self):
        self.server.broken.add("test-male.onnx")
        outcome = self.install()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_task, "piper-voice-sv_male")
        self.assertIn("not the expected file", outcome.message)
        self.assertEqual(list(self.box.paths.piper_dir.glob("*.part")), [])
        self.assertFalse((self.box.paths.piper_dir / "test-male.onnx").exists())
        self.server.broken.clear()
        self.assertTrue(self.install().ok)

    def test_changed_file_on_disk_is_downloaded_again(self):
        self.assertTrue(self.install().ok)
        model = self.box.paths.piper_dir / "test-female.onnx"
        model.write_bytes(b"F" * 2999 + b"X")
        self.assertIn("piper-voice-en_female",
                      [t.id for t in engine.build_plan(self.box.context(features=FEATURES)).pending])
        self.assertTrue(self.install().ok)
        self.assertEqual(model.read_bytes(), b"F" * 3000)

    def test_network_failure_gives_friendly_message(self):
        self.server.offline = True
        outcome = self.install()
        self.assertFalse(outcome.ok)
        self.assertIn("internet connection", outcome.message)

    def test_certificate_failure_suggests_checking_the_clock(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        with mock.patch.object(piper, "fetch", side_effect=error):
            outcome = self.install()
        self.assertFalse(outcome.ok)
        self.assertIn("date and time", outcome.message)

    def test_verify_proves_each_voice_makes_speech(self):
        self.install()
        found = {c.id: c for c in engine.run_verify(self.box.context(features=FEATURES))}
        for key in ("piper-package", "piper-voice-en_female", "piper-voice-sv_male",
                    "piper-speech-en_female", "piper-speech-sv_male"):
            self.assertEqual(found[key].status, "passed", (key, found[key].detail))
        self.assertIn("1.2 s of speech", found["piper-speech-sv_male"].detail)
        piper_calls = [c[0] for c in self.box.system.calls if "PiperVoice" in " ".join(c[0])]
        self.assertIn("Sample by test-male.", piper_calls[-1], "each voice speaks its own sample")
        self.assertEqual(list((self.box.paths.state_dir / "piper-check").iterdir()), [])

    def test_verify_explains_piper_failures(self):
        self.install()
        self.box.system.piper_output = json.dumps({"error": "ModuleNotFoundError"})
        found = {c.id: c for c in engine.run_verify(self.box.context(features=FEATURES))}
        self.assertEqual(found["piper-speech-en_female"].status, "failed")
        self.assertIn("not installed", found["piper-speech-en_female"].detail)
        self.box.system.piper_output = json.dumps({"SV_MALE": {"error": "RuntimeError"}})
        found = {c.id: c for c in engine.run_verify(self.box.context(features=FEATURES))}
        self.assertEqual(found["piper-speech-sv_male"].status, "failed")
        self.assertEqual(found["piper-speech-en_female"].status, "failed")

    def test_missing_voice_fails_verify(self):
        self.install()
        (self.box.paths.piper_dir / "test-male.onnx.json").unlink()
        found = {c.id: c.status for c in engine.run_verify(self.box.context(features=FEATURES))}
        self.assertEqual(found["piper-voice-sv_male"], "failed")
        self.assertNotIn("piper-speech-sv_male", found)
        self.assertEqual(found["piper-speech-en_female"], "passed")

    def test_listening_check_plays_both_voices(self):
        self.install()
        ctx = self.box.context(features=FEATURES)
        check = next(c for c in checks.applicable(ctx) if c.id == "natural-voice")
        self.assertEqual(check.test(ctx), "Played 2 of 2 voices: en_female voice, sv_male voice.")
        played = [c[0] for c in self.box.system.calls if c[0][0] == "aplay"]
        self.assertEqual(len(played), 2)
        self.box.system.fail.add("aplay")
        self.assertEqual(check.test(ctx), "Could not play the natural voices.")

    def test_remove_keeps_voices(self):
        self.install()
        lines = engine.remove_all(self.box.context(features=FEATURES))
        self.assertTrue(any("Natural voices kept" in line for line in lines))
        self.assertTrue((self.box.paths.piper_dir / "test-male.onnx").exists())


class CatalogueTests(unittest.TestCase):
    def test_real_catalogue_is_pinned_and_complete(self):
        self.assertIn("/resolve/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/", PIPER_VOICE_BASE)
        self.assertEqual({v.key: v.model for v in PIPER_VOICES},
                         {"EN_FEMALE": "en_US-lessac-medium", "EN_MALE": "en_US-ryan-high",
                          "SV_FEMALE": "sv_SE-alma-medium", "SV_MALE": "sv_SE-nst-medium"})
        for voice in PIPER_VOICES:
            names = [f.name for f in voice.files]
            self.assertEqual(names, [voice.model + ".onnx", voice.model + ".onnx.json"])
            self.assertTrue(all(f.source.startswith(f"{voice.language}/") for f in voice.files))
            self.assertTrue(all(len(f.sha256) == 64 for f in voice.files))
            self.assertTrue(voice.license_note and voice.sample)
        self.assertTrue(all("non-commercial" in v.license_note
                            for v in PIPER_VOICES if v.language == "en"))


if __name__ == "__main__":
    unittest.main()

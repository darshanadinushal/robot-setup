import io
import json
import ssl
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from helpers import FAKE_BASE, LANGUAGE_PACKS, VOICE_CONTENT, Sandbox, fake_voice, \
    write_luna_catalogue

from robot_setup import catalogue, checks, engine
from robot_setup.config import SetupError, resolve_features, PROFILES
from robot_setup.modules import piper
from robot_setup.state import State, VERIFIED

FEATURES = ("luna", "piper")
TEST_FILE_VOICE = fake_voice("EN_FEMALE", "en", "female", "Fem", "test-female")
REAL_LUNA = Path(__file__).resolve().parents[2] / "ai-robot"


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
        self.server = FakeServer(dict(VOICE_CONTENT))
        patcher = mock.patch.object(piper, "fetch", self.server)
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
        self.assertTrue(all(u.startswith(FAKE_BASE) for u in self.server.requests))
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
        self.assertIn("Hej sample.", piper_calls[-1], "each voice speaks its own language")
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
        self.assertEqual(check.test(ctx), "Played 2 of 2 voices: English female voice (Fem), "
                         "Swedish male voice (Man).")
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
    """Voices and listening languages come from Luna's folder and are validated."""

    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_voices_and_languages_are_read_from_luna(self):
        voices = catalogue.piper_voices(self.box.luna)
        self.assertEqual([v.key for v in voices], ["EN_FEMALE", "SV_MALE"])
        self.assertEqual(voices[1].label, "Swedish male voice (Man)")
        self.assertEqual(voices[1].sample, "Hej sample.")
        self.assertEqual(voices[1].files[0].url, FAKE_BASE + "sv/test-male.onnx")
        models = catalogue.listening_models(self.box.luna)
        self.assertEqual([(m.language, m.size_mb) for m in models], [("en", 40), ("sv", 289)])

    def test_a_new_voice_and_language_need_no_code_change(self):
        packs = dict(LANGUAGE_PACKS, de={"code": "de", "name": "German", "native_name": "Deutsch",
                                         "listening_model": "vosk-model-small-de-test"})
        voices = [fake_voice("DE_FEMALE", "de", "female", "Eva", "test-female")]
        write_luna_catalogue(self.box.luna, voices=voices, packs=packs)
        ctx = self.box.context(features=("luna", "voice", "piper"))
        ids = [t.id for t in engine.build_plan(ctx).pending]
        self.assertIn("piper-voice-de_female", ids)
        self.assertIn("voice-model-de", ids)

    def test_missing_catalogue_asks_to_update_luna(self):
        (self.box.luna / "robot_hud" / "voices.json").unlink()
        ctx = self.box.context(features=FEATURES)
        problems = piper.preconditions(ctx)
        self.assertTrue(problems and "Update Luna" in problems[0])
        self.assertEqual([t.id for t in piper.tasks(ctx)], ["piper-python"])
        found = {c.id: c for c in piper.verify(ctx)}
        self.assertEqual(found["piper-voices"].status, "failed")

    def test_unsafe_entries_are_rejected(self):
        bad = [
            dict(TEST_FILE_VOICE, model="../evil"),
            dict(TEST_FILE_VOICE, key="../X"),
            dict(TEST_FILE_VOICE, files=[dict(f, name="../x.onnx") for f in TEST_FILE_VOICE["files"]]),
            dict(TEST_FILE_VOICE, files=[dict(f, source="../../x") for f in TEST_FILE_VOICE["files"]]),
            dict(TEST_FILE_VOICE, files=[dict(f, sha256="abc") for f in TEST_FILE_VOICE["files"]]),
            dict(TEST_FILE_VOICE, files=TEST_FILE_VOICE["files"][:1]),
        ]
        for voice in bad:
            write_luna_catalogue(self.box.luna, voices=[voice])
            with self.assertRaises(SetupError, msg=voice):
                catalogue.piper_voices(self.box.luna)
        write_luna_catalogue(self.box.luna, base="http://insecure.example/")
        with self.assertRaisesRegex(SetupError, "https"):
            catalogue.piper_voices(self.box.luna)
        write_luna_catalogue(self.box.luna, voices=[TEST_FILE_VOICE, TEST_FILE_VOICE])
        with self.assertRaisesRegex(SetupError, "duplicate"):
            catalogue.piper_voices(self.box.luna)

    def test_older_luna_without_language_packs_listens_in_english(self):
        import shutil
        shutil.rmtree(self.box.luna / "robot_hud" / "languages")
        models = catalogue.listening_models(self.box.luna)
        self.assertEqual([(m.language, m.model) for m in models],
                         [("en", "vosk-model-small-en-us-0.15")])

    @unittest.skipUnless((REAL_LUNA / "robot_hud" / "voices.json").is_file(),
                         "the Luna repository is not next to robot-setup")
    def test_real_luna_catalogue_is_valid_and_pinned(self):
        voices = catalogue.piper_voices(REAL_LUNA)
        self.assertTrue(all("/resolve/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/" in f.url
                            for v in voices for f in v.files))
        self.assertTrue(all("non-commercial" in v.license_note
                            for v in voices if v.language == "en"))
        self.assertEqual(catalogue.listening_models(REAL_LUNA)[0].language, "en")


if __name__ == "__main__":
    unittest.main()

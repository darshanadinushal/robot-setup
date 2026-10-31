"""Luna Core 1.x detection and voice metadata, without touching a real Pi."""
import shutil
import unittest

from helpers import Sandbox
from robot_setup import catalogue, engine
from robot_setup.modules import luna


class LunaCoreCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_legacy_folder_still_works(self):
        self.assertTrue(luna.is_luna_dir(self.box.luna))
        self.assertEqual(catalogue.voices_file(self.box.luna).parent.name, "robot_hud")
        self.assertEqual(catalogue.languages_dir(self.box.luna).parent.name, "robot_hud")

    def test_new_luna_core_without_legacy_folder_is_detected(self):
        new = self.box.luna / "luna_core"
        shutil.copytree(self.box.luna / "robot_hud", new)
        shutil.rmtree(self.box.luna / "robot_hud")
        self.assertTrue(luna.is_luna_dir(self.box.luna))
        self.assertEqual(catalogue.voices_file(self.box.luna), new / "voices.json")
        self.assertEqual(catalogue.languages_dir(self.box.luna), new / "languages")
        self.assertEqual([m.language for m in catalogue.listening_models(self.box.luna)],
                         ["en", "sv"])
        self.assertEqual(len(catalogue.piper_voices(self.box.luna)), 2)
        self.assertEqual(engine.build_plan(self.box.context(features=("luna", "voice"))).blockers, [])

    def test_canonical_metadata_wins_when_legacy_copy_exists(self):
        new = self.box.luna / "luna_core"
        shutil.copytree(self.box.luna / "robot_hud", new)
        self.assertEqual(catalogue.voices_file(self.box.luna), new / "voices.json")
        self.assertEqual(catalogue.languages_dir(self.box.luna), new / "languages")
        self.assertEqual(len(catalogue.piper_voices(self.box.luna)), 2)

    def test_random_main_py_is_not_mistaken_for_luna(self):
        (self.box.luna / "robot_hud" / "app.py").unlink()
        self.assertFalse(luna.is_luna_dir(self.box.luna))
        (self.box.luna / "luna_core").mkdir()
        self.assertFalse(luna.is_luna_dir(self.box.luna))


if __name__ == "__main__":
    unittest.main()

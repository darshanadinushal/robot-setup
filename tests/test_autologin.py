import unittest
from unittest import mock

from helpers import Sandbox

from robot_setup import engine
from robot_setup.modules import autologin

FEATURES = ("luna", "startup", "autologin")


class AutologinTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.conf = self.box.home / "lightdm.conf"
        self.conf.write_text("[Seat:*]\n#autologin-user=\n")
        raspi_config = self.box.home / "raspi-config"
        raspi_config.write_text("")
        self.box.system.lightdm = self.conf
        for patch in (mock.patch.object(autologin, "LIGHTDM_CONF", self.conf),
                      mock.patch.object(autologin, "RASPI_CONFIG", raspi_config)):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.box.cleanup()

    def install(self):
        outcome = engine.install(self.box.context(features=FEATURES))
        self.assertTrue(outcome.ok, outcome.message)
        return self.box.system.elevations[-1]

    def remove(self):
        return engine.remove_all(self.box.context(features=FEATURES))

    def test_remove_restores_desktop_password_login(self):
        self.assertIn({"op": "autologin", "enable": True}, self.install())
        self.assertEqual(autologin.autologin_user(self.conf), "robot")
        lines = self.remove()
        self.assertEqual(self.box.system.elevations[-1],
                         [{"op": "autologin", "enable": False, "console": False}])
        self.assertIsNone(autologin.autologin_user(self.conf))
        self.assertTrue(any("desktop asks for a password" in line for line in lines))

    def test_remove_restores_console_boot(self):
        self.box.system.default_target = "multi-user.target"
        self.install()
        self.remove()
        self.assertEqual(self.box.system.elevations[-1],
                         [{"op": "autologin", "enable": False, "console": True}])
        self.assertEqual(self.box.system.default_target, "multi-user.target")

    def test_remove_leaves_settings_changed_after_setup(self):
        self.install()
        self.conf.write_text("[Seat:*]\n")  # the user turned auto-login off by hand
        count = len(self.box.system.elevations)
        lines = self.remove()
        self.assertEqual(len(self.box.system.elevations), count, "nothing may be overwritten")
        self.assertTrue(any("changed after setup" in line for line in lines))

    def test_auto_login_for_another_user_blocks_setup(self):
        self.conf.write_text("[Seat:*]\nautologin-user=someone\n")
        blockers = engine.build_plan(self.box.context(features=FEATURES)).blockers
        self.assertTrue(any("'someone'" in b for b in blockers))

    def test_already_enabled_is_left_alone_on_remove(self):
        self.conf.write_text("[Seat:*]\nautologin-user=robot\n")
        self.install()
        count = len(self.box.system.elevations)
        lines = self.remove()
        self.assertEqual(len(self.box.system.elevations), count)
        self.assertTrue(any("left as it was" in line for line in lines))


if __name__ == "__main__":
    unittest.main()

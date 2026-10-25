"""Validate the generated luna.service with systemd itself (Linux with systemd only).

systemd only warns about many bad settings (for example a quoted WorkingDirectory=) and then
ignores them, so any message about the unit file counts as a failure here.
Set ROBOT_SETUP_REQUIRE_SYSTEMD=1 (as CI does) to fail instead of skip when systemd is missing.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import Sandbox

from robot_setup.modules import startup

ANALYZE = shutil.which("systemd-analyze")
REQUIRED = os.environ.get("ROBOT_SETUP_REQUIRE_SYSTEMD") == "1"


def unit_problems(unit_text):
    """Messages systemd-analyze reports about this unit (empty list when valid)."""
    with tempfile.TemporaryDirectory() as folder:
        unit = Path(folder) / "luna.service"
        unit.write_text(unit_text)
        result = subprocess.run([ANALYZE, "verify", "--man=no", str(unit)],
                                capture_output=True, text=True, timeout=60)
        output = (result.stdout + result.stderr).splitlines()
        problems = [line for line in output if "luna.service" in line]
        if result.returncode != 0 and not problems:
            problems = output or [f"exit code {result.returncode}"]
        return problems


@unittest.skipUnless(sys.platform.startswith("linux") and ANALYZE or REQUIRED,
                     "needs Linux with systemd-analyze")
class SystemdUnitTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ANALYZE, "systemd-analyze is required (ROBOT_SETUP_REQUIRE_SYSTEMD=1)")
        self.box = Sandbox()
        # A folder name with a space and a % sign exercises quoting and escaping.
        luna = self.box.home / "my robot" / "luna 100%"
        shutil.copytree(self.box.luna, luna)
        (luna / ".venv" / "bin").mkdir(parents=True)
        os.symlink(sys.executable, luna / ".venv" / "bin" / "python")
        self.box.luna = luna

    def tearDown(self):
        self.box.cleanup()

    def test_generated_unit_is_accepted_by_systemd(self):
        unit = startup.render_unit(self.box.context(features=("luna", "startup")))
        self.assertEqual(unit_problems(unit), [], unit)

    def test_check_catches_the_quoted_working_directory_bug(self):
        unit = startup.render_unit(self.box.context(features=("luna", "startup")))
        broken = unit.replace(f"WorkingDirectory={startup._systemd_path(self.box.luna)}",
                              f"WorkingDirectory={startup._systemd_quote(self.box.luna)}")
        self.assertNotEqual(broken, unit)
        self.assertNotEqual(unit_problems(broken), [])


if __name__ == "__main__":
    unittest.main()

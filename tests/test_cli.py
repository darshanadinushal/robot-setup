import contextlib
import io
import unittest
from unittest import mock

from helpers import TEST_KEY, Sandbox, pi_device

from robot_setup import cli, engine

REAL_OPEN_CONTEXT = engine.open_context


class CliTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        system = self.box.system

        def open_context(request=None, **kwargs):
            kwargs.update(runner=system, device=pi_device(), elevate=system.elevate,
                          log=lambda line: None)
            return REAL_OPEN_CONTEXT(request, **kwargs)

        patches = [mock.patch.object(cli.Paths, "for_user", return_value=self.box.paths),
                   mock.patch.object(cli.engine, "open_context", open_context)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.box.cleanup()

    def run_cli(self, *argv, stdin=""):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch("sys.stdin", io.StringIO(stdin)):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_dry_run_changes_nothing(self):
        code, out = self.run_cli("install", "--dry-run", "--luna-dir", str(self.box.luna))
        self.assertEqual(code, 0, out)
        self.assertIn("Install system packages", out)
        self.assertEqual(self.box.system.elevations, [])
        self.assertFalse(self.box.paths.state_file.exists())

    def test_install_then_status_and_repair(self):
        code, out = self.run_cli("install", "--yes", "--profile", "minimal",
                                 "--luna-dir", str(self.box.luna))
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("luna-environment", out)
        code, out = self.run_cli("repair", "--yes")
        self.assertEqual(code, 0, out)

    def test_with_adds_features_to_the_saved_setup(self):
        self.run_cli("install", "--yes", "--profile", "minimal", "--luna-dir", str(self.box.luna))
        code, out = self.run_cli("install", "--yes", "--with", "voice", "--with", "startup")
        self.assertEqual(code, 0, out)
        saved = cli.State(self.box.paths.state_file).load().data["request"]
        self.assertEqual(saved["features"], ["luna", "voice", "startup"])
        self.assertEqual(saved["luna_dir"], str(self.box.luna))

    def test_ai_key_from_stdin_is_not_saved_in_state(self):
        code, out = self.run_cli("install", "--yes", "--profile", "minimal", "--with", "ai",
                                 "--ai-model", "test-model", "--ai-key-stdin", "--skip-ai-check",
                                 "--luna-dir", str(self.box.luna), stdin=TEST_KEY + "\n")
        self.assertEqual(code, 0, out)
        self.assertNotIn(TEST_KEY, out)
        self.assertNotIn(TEST_KEY, self.box.paths.state_file.read_text())
        self.assertIn(TEST_KEY, self.box.paths.env_file.read_text())


if __name__ == "__main__":
    unittest.main()

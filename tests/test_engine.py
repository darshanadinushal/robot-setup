import os
import unittest
from pathlib import Path

from helpers import TEST_KEY, Sandbox, pi_device

from robot_setup import engine
from robot_setup.config import SetupError
from robot_setup.modules import startup
from robot_setup.state import FAILED, PENDING, RUNNING, VERIFIED, State


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_plan_lists_changes_and_blocks_missing_luna(self):
        ctx = self.box.context()
        plan = engine.build_plan(ctx)
        self.assertEqual(plan.blockers, [])
        self.assertEqual([t.id for t in plan.pending], ["luna-packages", "luna-environment"])
        (self.box.luna / "main.py").unlink()
        self.assertTrue(engine.build_plan(self.box.context()).blockers)

    def test_unsupported_os_blocks_unless_allowed(self):
        ctx = self.box.context(device=pi_device(codename="bookworm"))
        self.assertTrue(any("Trixie" in b for b in engine.build_plan(ctx).blockers))
        ctx.allow_unsupported = True
        self.assertEqual(engine.build_plan(ctx).blockers, [])

    def test_offline_blocks_only_when_downloads_are_needed(self):
        ctx = self.box.context(device=pi_device(online=False))
        self.assertTrue(engine.build_plan(ctx).blockers)
        self.assertTrue(engine.install(self.box.context()).ok)
        self.assertEqual(engine.build_plan(self.box.context(device=pi_device(online=False))).blockers, [])

    def test_install_runs_once_then_is_repeatable(self):
        outcome = engine.install(self.box.context(features=("luna", "voice", "startup")))
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(len(self.box.system.elevations), 1)
        self.assertEqual(self.box.system.elevations[0][0]["packages"],
                         sorted(["python3", "python3-pygame", "python3-venv", "espeak-ng",
                                 "alsa-utils", "libportaudio2"]))
        state = State(self.box.paths.state_file).load()
        self.assertTrue(all(i["status"] == VERIFIED for i in state.tasks.values()))
        self.assertEqual(state.data["install_boot_id"], "boot-1")
        again = self.box.context(features=("luna", "voice", "startup"))
        self.assertEqual(engine.build_plan(again).pending, [])
        self.assertTrue(engine.install(again).ok)
        self.assertEqual(len(self.box.system.elevations), 1)

    def test_failure_stops_and_repair_resumes(self):
        self.box.system.fail.add("venv")
        outcome = engine.install(self.box.context())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_task, "luna-environment")
        state = State(self.box.paths.state_file).load()
        self.assertEqual(state.task_status("luna-environment"), FAILED)
        self.box.system.fail.clear()
        self.assertTrue(engine.install(self.box.context()).ok)

    def test_interrupted_task_is_checked_again(self):
        state = State(self.box.paths.state_file)
        state.set_task("luna-environment", RUNNING)
        state.save()
        ctx = self.box.context()
        self.assertTrue(engine.install(ctx).ok)
        self.assertTrue(any("interrupted" in line for line in ctx.logs))

    def test_existing_foreign_venv_is_not_deleted(self):
        venv = self.box.luna / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
        outcome = engine.install(self.box.context())
        self.assertFalse(outcome.ok)
        self.assertIn("Rename it", outcome.message)
        self.assertTrue((venv / "pyvenv.cfg").exists())

    def test_ai_key_is_private_and_never_in_state(self):
        ctx = self.box.context(features=("luna", "ai"), secrets={"openai_api_key": TEST_KEY},
                               ai_model="test-model")
        self.assertTrue(engine.install(ctx).ok)
        env_text = self.box.paths.env_file.read_text()
        self.assertIn(f"OPENAI_API_KEY={TEST_KEY}", env_text)
        self.assertIn("LUNA_AI_MODEL=test-model", env_text)
        self.assertNotIn(TEST_KEY, self.box.paths.state_file.read_text())
        if os.name == "posix":
            self.assertEqual(self.box.paths.env_file.stat().st_mode & 0o777, 0o600)
        key_calls = [c for c in self.box.system.calls if TEST_KEY in " ".join(c[0])]
        self.assertEqual(key_calls, [], "the key must never appear in a command line")

    def test_ai_requires_model_and_key(self):
        blockers = engine.build_plan(self.box.context(features=("luna", "ai"))).blockers
        self.assertTrue(any("model" in b for b in blockers))
        self.assertTrue(any("API key" in b for b in blockers))

    def test_verify_reports_reboot_pending_then_passed(self):
        engine.install(self.box.context(features=("luna", "startup")))
        checks = {c.id: c.status for c in engine.run_verify(self.box.context(features=("luna", "startup")))}
        self.assertEqual(checks["startup-files"], "passed")
        self.assertEqual(checks["startup-reboot"], "pending")

    def reboot_check(self, service=None, login_started=True):
        engine.install(self.box.context(features=("luna", "startup")))
        if login_started:
            self.box.paths.autostart_marker.write_text("boot-2\n")
        self.box.system.service.update(service or {})
        ctx = self.box.context(features=("luna", "startup"), device=pi_device(boot_id="boot-2"))
        return startup.reboot_check(ctx)

    def test_reboot_check_distinguishes_how_luna_started_and_stopped(self):
        started = {"ExecMainStartTimestamp": "Sun 2026-10-25 10:00:00 CET"}
        cases = [
            ("not started at login", {}, False, "failed", "did not start Luna"),
            ("started by hand", {"ActiveState": "active"}, False, "warning", "by hand"),
            ("running", {"ActiveState": "active", **started}, True, "passed", "running"),
            ("crashed, recovered", {"ActiveState": "active", "NRestarts": "2"}, True,
             "warning", "restarted 2"),
            ("restarting", {"ActiveState": "activating", "SubState": "auto-restart"}, True,
             "warning", "restarting"),
            ("gave up", {"ActiveState": "failed", "Result": "exit-code", "NRestarts": "5"},
             True, "failed", "stopped after 5"),
            ("closed by user", {"Result": "success", **started}, True, "passed", "closed"),
            ("never ran", {}, True, "failed", "did not start"),
        ]
        for name, service, login, status, detail in cases:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                check = self.reboot_check(service, login)
                self.assertEqual(check.status, status, check.detail)
                self.assertIn(detail, check.detail)

    def test_remove_turns_startup_off_and_keeps_user_data(self):
        features = ("luna", "voice", "startup")
        engine.install(self.box.context(features=features))
        entry = self.box.paths.autostart_entry
        entry.write_text(entry.read_text() + "# my change\n")
        lines = engine.remove_all(self.box.context(features=features))
        self.assertFalse(self.box.paths.systemd_unit.exists())
        self.assertFalse(entry.exists(), "a changed autostart entry would still start Luna")
        self.assertFalse(self.box.paths.launcher.exists())
        self.assertTrue(any("changed copy is saved" in line for line in lines))
        saved = State(self.box.paths.state_file).load().data["backups"][-1]["backup"]
        self.assertIn("# my change", Path(saved).read_text())
        self.assertIn("Automatic startup is off.", lines)
        self.assertTrue((self.box.luna / ".venv").exists())
        self.assertTrue(self.box.paths.voice_model.exists())
        state = State(self.box.paths.state_file).load()
        self.assertIsNone(state.data["request"])
        self.assertTrue(all(i["status"] == PENDING for i in state.tasks.values()))

    def test_remove_reports_other_launchers_that_still_start_luna(self):
        engine.install(self.box.context(features=("luna", "startup")))
        autostart = self.box.home / ".config" / "labwc" / "autostart"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("bash ~/luna/scripts/run-pi.sh &\n")
        lines = engine.remove_all(self.box.context(features=("luna", "startup")))
        self.assertIn("Automatic startup is NOT fully off. Luna can still start from:", lines)
        self.assertTrue(any(str(autostart) in line for line in lines))

    def test_open_context_without_saved_request(self):
        with self.assertRaises(SetupError):
            engine.open_context(None, paths=self.box.paths, device=pi_device())


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_unit_restarts_only_on_failure_with_limits(self):
        unit = startup.render_unit(self.box.context())
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("StartLimitBurst=5", unit)
        self.assertIn("--fullscreen", unit)
        self.assertNotIn("[Install]", unit)  # started only by the session autostart entry

    def test_launcher_passes_display_to_user_manager(self):
        launcher = startup.render_launcher(self.box.context())
        self.assertIn("import-environment WAYLAND_DISPLAY DISPLAY", launcher)
        self.assertIn("systemctl --user start luna.service", launcher)
        self.assertIn('if [ "$1" = "--login" ]', launcher)  # only login records the boot
        self.assertIn('" --login\n', startup.render_autostart(self.box.context()))

    def test_paths_are_quoted_and_percent_escaped(self):
        self.assertEqual(startup._systemd_quote('/a b/%c"d'), '"/a b/%%c\\"d"')
        self.assertEqual(startup._systemd_path("/a b/%c"), "/a b/%%c")

    def test_working_directory_is_not_quoted(self):
        # Regression (0.1.0): systemd ignored a quoted WorkingDirectory= as "not absolute".
        line = next(l for l in startup.render_unit(self.box.context()).splitlines()
                    if l.startswith("WorkingDirectory="))
        self.assertEqual(line, f"WorkingDirectory={self.box.luna}")

    def test_unusable_folder_names_block_startup(self):
        self.assertIsNone(startup.unsupported_path("/home/robot/my luna"))
        self.assertIsNotNone(startup.unsupported_path("/home/robot/luna\n"))
        self.assertIsNotNone(startup.unsupported_path("/home/robot/luna "))

    def test_other_luna_autostart_blocks_setup(self):
        autostart = self.box.home / ".config" / "labwc" / "autostart"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("bash ~/luna/scripts/run-pi.sh &\n")
        blockers = engine.build_plan(self.box.context(features=("luna", "startup"))).blockers
        self.assertTrue(any("already started" in b for b in blockers))

    def test_commented_entries_are_ignored(self):
        autostart = self.box.home / ".config" / "labwc" / "autostart"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("# bash ~/luna/scripts/run-pi.sh &\n")
        self.assertEqual(startup.conflicts(self.box.context(features=("luna", "startup"))), [])


if __name__ == "__main__":
    unittest.main()

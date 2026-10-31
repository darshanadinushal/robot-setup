"""Luna Core 1.0 compatibility: both Luna layouts, the version, Luna Core's own startup files
(replaced, never run next to ours), its upgrade/rollback scripts, and cheap checks on the Pi."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import Sandbox, pi_device, write_luna_catalogue

from robot_setup import catalogue, cli, engine, lunainfo
from robot_setup.config import SetupError
from robot_setup.modules import luna, startup
from robot_setup.state import State
from robot_setup.system import Result

REAL_LUNA = Path(os.environ.get("ROBOT_SETUP_LUNA_DIR")
                 or Path(__file__).resolve().parents[2] / "ai-robot")
STARTUP = ("luna", "startup")


def luna_core_unit(repo):
    """luna.service exactly as Luna Core 1.0.0's scripts/install-service.sh writes it."""
    return ("[Unit]\nDescription=Luna robot face\n"
            "# Give up after 5 quick crashes in 2 minutes (then: bash scripts/run-pi.sh --no-plugins)\n"
            "StartLimitIntervalSec=120\nStartLimitBurst=5\n\n[Service]\nType=simple\n"
            f"WorkingDirectory={repo}\nExecStart={repo}/scripts/run-pi.sh\n"
            "# Leaving with the Quit button exits normally and is not restarted.\n"
            "Restart=on-failure\nRestartSec=3\nEnvironment=PYTHONUNBUFFERED=1\nNice=-2\n")


LUNA_CORE_DESKTOP = (
    "[Desktop Entry]\nType=Application\nName=Luna\n"
    "Comment=Start the Luna robot service with this desktop's display\n"
    "Exec=sh -c 'systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY "
    "XDG_RUNTIME_DIR XDG_SESSION_TYPE; systemctl --user restart luna.service'\n"
    "X-GNOME-Autostart-enabled=true\n")


class LayoutTests(unittest.TestCase):
    def test_luna_core_without_robot_hud_is_found(self):
        # Luna Core 2.0 removes robot_hud/; Robot Setup must not depend on it.
        box = Sandbox()
        self.addCleanup(box.cleanup)
        self.assertFalse((box.luna / "robot_hud").exists())
        self.assertTrue(luna.is_luna_dir(box.luna))
        self.assertEqual(catalogue.voices_file(box.luna), box.luna / "luna_core" / "voices.json")
        self.assertEqual(len(catalogue.piper_voices(box.luna)), 2)
        self.assertEqual(lunainfo.version(box.luna), "1.0.0")
        self.assertEqual(luna.version_check(box.context()).status, "passed")
        self.assertIn("Luna Core 1.0.0", "\n".join(
            engine.describe_plan(box.context(), engine.build_plan(box.context()))))

    def test_older_robot_hud_luna_still_installs(self):
        box = Sandbox(legacy=True)
        self.addCleanup(box.cleanup)
        self.assertTrue(luna.is_luna_dir(box.luna))
        self.assertEqual(catalogue.voices_file(box.luna), box.luna / "robot_hud" / "voices.json")
        self.assertIsNone(lunainfo.version(box.luna))
        check = luna.version_check(box.context())
        self.assertEqual(check.status, "warning")
        self.assertIn("upgrade-luna v1.0.0", check.detail)
        outcome = engine.install(box.context(features=("luna", "voice", "startup")))
        self.assertTrue(outcome.ok, outcome.message)

    def test_luna_core_catalogue_wins_over_the_compatibility_copy(self):
        box = Sandbox()
        self.addCleanup(box.cleanup)
        (box.luna / "robot_hud").mkdir()
        (box.luna / "robot_hud" / "app.py").write_text("")
        write_luna_catalogue(box.luna, voices=[], package="robot_hud")  # stale, invalid copy
        self.assertEqual([v.key for v in catalogue.piper_voices(box.luna)],
                         ["EN_FEMALE", "SV_MALE"])

    def test_unknown_major_version_warns(self):
        box = Sandbox()
        self.addCleanup(box.cleanup)
        (box.luna / "luna_core" / "__init__.py").write_text("__version__ = '2.0.0'\n")
        check = luna.version_check(box.context())
        self.assertEqual(check.status, "warning")
        self.assertIn("2.0.0", check.detail)
        self.assertEqual(lunainfo.version_tuple("v1.2"), (1, 2, 0))
        self.assertIsNone(lunainfo.version_tuple("main"))


class LunaCoreStartupTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.unit = self.box.paths.systemd_unit
        self.desktop = self.box.paths.luna_core_autostart

    def tearDown(self):
        self.box.cleanup()

    def install_luna_core_startup(self, repo=None):
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        self.desktop.parent.mkdir(parents=True, exist_ok=True)
        self.unit.write_text(luna_core_unit(repo or self.box.luna))
        self.desktop.write_text(LUNA_CORE_DESKTOP)

    def test_luna_core_startup_is_replaced_once_and_restored_on_remove(self):
        self.install_luna_core_startup()
        plan = engine.build_plan(self.box.context(features=STARTUP))
        self.assertEqual(plan.blockers, [])
        ids = [t.id for t in plan.pending]
        self.assertLess(ids.index("startup-replace-luna-core"), ids.index("startup-files"))

        self.assertTrue(engine.install(self.box.context(features=STARTUP)).ok)
        self.assertFalse(self.desktop.exists(), "it would restart Luna a second time at login")
        self.assertIn(startup.MANAGED_MARK, self.unit.read_text())
        state = State(self.box.paths.state_file).load()
        saved = {Path(i["path"]): Path(i["backup"]) for i in state.data["replaced_startup"]}
        self.assertEqual(saved[self.unit].read_text(), luna_core_unit(self.box.luna))
        self.assertEqual(saved[self.desktop].read_text(), LUNA_CORE_DESKTOP)
        again = engine.build_plan(self.box.context(features=STARTUP))
        self.assertEqual(again.pending, [])
        self.assertEqual(startup.conflicts(self.box.context(features=STARTUP)), [])

        lines = engine.remove_all(self.box.context(features=STARTUP))
        self.assertEqual(self.unit.read_text(), luna_core_unit(self.box.luna))
        self.assertEqual(self.desktop.read_text(), LUNA_CORE_DESKTOP)
        self.assertTrue(any("install-service.sh --remove" in line for line in lines))
        self.assertEqual(State(self.box.paths.state_file).load().data["replaced_startup"], [])

    def test_luna_core_startup_of_another_folder_blocks(self):
        self.install_luna_core_startup(repo=self.box.home / "other-luna")
        blockers = engine.build_plan(self.box.context(features=STARTUP)).blockers
        self.assertTrue(any(str(self.unit) in b for b in blockers), blockers)

    def test_any_other_entry_that_restarts_the_service_blocks(self):
        entry = self.box.home / ".config" / "autostart" / "my-luna.desktop"
        entry.parent.mkdir(parents=True)
        entry.write_text("[Desktop Entry]\nExec=systemctl --user restart luna.service\n")
        blockers = engine.build_plan(self.box.context(features=STARTUP)).blockers
        self.assertTrue(any(str(entry) in b for b in blockers), blockers)

    def test_changed_luna_core_desktop_entry_is_not_deleted(self):
        self.install_luna_core_startup()
        self.desktop.write_text(LUNA_CORE_DESKTOP.replace("restart luna.service",
                                                          "restart luna.service; run-pi.sh"))
        blockers = engine.build_plan(self.box.context(features=STARTUP)).blockers
        self.assertTrue(any(str(self.desktop) in b for b in blockers), blockers)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash")
                         and (REAL_LUNA / "scripts" / "install-service.sh").is_file(),
                         "needs Linux and the real Luna (ai-robot) checkout")
    def test_files_from_the_real_install_service_script_are_recognised(self):
        """Cross-repository check against Luna's actual scripts/install-service.sh."""
        bin_dir = self.box.home / "bin"
        bin_dir.mkdir()
        (bin_dir / "systemctl").write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / "systemctl").chmod(0o755)
        env = dict(os.environ, HOME=str(self.box.home), PATH=f"{bin_dir}:{os.environ['PATH']}")
        env.pop("XDG_CONFIG_HOME", None)
        script = REAL_LUNA / "scripts" / "install-service.sh"
        result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.box.luna = REAL_LUNA.resolve()
        ctx = self.box.context(features=STARTUP)
        self.assertEqual(set(startup.luna_core_startup(ctx)), {self.unit, self.desktop})
        self.assertEqual(engine.build_plan(ctx).blockers, [])
        self.assertTrue(engine.install(self.box.context(features=STARTUP)).ok)
        if "Robot Setup" in script.read_text():
            # Newer Luna Core refuses to overwrite Robot Setup's startup.
            ours = self.unit.read_text()
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True,
                                    text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("robot-setup", result.stdout + result.stderr)
            self.assertEqual(self.unit.read_text(), ours)
            self.assertFalse(self.desktop.exists())


class LunaScriptCommandTests(unittest.TestCase):
    """upgrade-luna / rollback-luna run Luna Core's own scripts; nothing is reimplemented."""

    def setUp(self):
        self.box = Sandbox()
        self.runs = []
        for name in ("upgrade-pi.sh", "rollback-pi.sh"):
            (self.box.luna / "scripts" / name).write_text("#!/bin/bash\n")

        class FakeRunner:
            def run(runner, args, **kwargs):
                self.runs.append(([str(a) for a in args], kwargs.get("cwd")))
                return Result(0, "")

        for patch in (mock.patch.object(cli.Paths, "for_user", return_value=self.box.paths),
                      mock.patch.object(cli, "Runner", FakeRunner)):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.box.cleanup()

    def run_cli(self, command, *rest):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main([command, "--luna-dir", str(self.box.luna), "--yes", *rest])
        return code, out.getvalue()

    def test_upgrade_uses_the_stable_tag_by_default(self):
        code, out = self.run_cli("upgrade-luna")
        self.assertEqual(code, 0, out)
        script = str(self.box.luna.resolve() / "scripts" / "upgrade-pi.sh")
        self.assertEqual(self.runs, [(["bash", script, "v1.0.0"], self.box.luna.resolve())])
        self.assertIn("Luna Core 1.0.0", out)

    def test_branch_is_allowed_with_a_note_but_options_are_not(self):
        code, out = self.run_cli("upgrade-luna", "main")
        self.assertEqual(code, 0, out)
        self.assertIn("not a release tag", out)
        code, out = self.run_cli("upgrade-luna", "--", "-rf")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.runs), 1)

    def test_rollback_passes_with_data(self):
        self.assertEqual(self.run_cli("rollback-luna", "--with-data")[0], 0)
        self.assertEqual(self.runs[0][0][1:], [str(self.box.luna.resolve() / "scripts" /
                                                   "rollback-pi.sh"), "--with-data"])

    def test_older_luna_without_scripts_gets_instructions(self):
        (self.box.luna / "scripts" / "upgrade-pi.sh").unlink()
        code, out = self.run_cli("upgrade-luna")
        self.assertEqual(code, 1)
        self.assertIn("git checkout v1.0.0", out)
        self.assertEqual(self.runs, [])


class PiCostTests(unittest.TestCase):
    """Fewer processes on the Pi: each Python start costs about a second of CPU there."""

    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def commands(self, name):
        return [args for args, _ in self.box.system.calls if Path(args[0]).name == name
                or (len(args) > 1 and args[1] == "-c" and name == "python")]

    def test_package_check_is_one_dpkg_query_call(self):
        engine.build_plan(self.box.context(features=("luna", "voice")))
        self.assertEqual(len(self.commands("dpkg-query")), 2)  # luna + voice packages

    def test_verify_starts_python_once_for_luna(self):
        engine.install(self.box.context())
        self.box.system.calls.clear()
        luna.verify(self.box.context())
        self.assertEqual(len(self.commands("python")), 1)

    def test_version_is_read_without_running_anything(self):
        self.box.system.calls.clear()
        self.assertEqual(luna.version_check(self.box.context()).status, "passed")
        self.assertEqual(self.box.system.calls, [])


if __name__ == "__main__":
    unittest.main()

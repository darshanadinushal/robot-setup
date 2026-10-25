import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (adds src to sys.path)

from robot_setup import privileged
from robot_setup.config import (FEATURES, PROFILES, Request, SetupError,
                                resolve_features)
from robot_setup.files import read_env_values, update_env_file, write_managed
from robot_setup.modules import autologin
from robot_setup.state import State
from robot_setup.system import device_checks, parse_os_release


class PrivilegedHelperTests(unittest.TestCase):
    def request(self, *operations):
        return {"version": 1, "operations": list(operations)}

    def test_accepts_allowed_operations(self):
        ops = privileged.validate_request(self.request(
            {"op": "apt_install", "packages": ["espeak-ng", "python3"]},
            {"op": "autologin", "enable": True}))
        self.assertEqual(ops[0]["packages"], ["espeak-ng", "python3"])

    def test_rejects_everything_else(self):
        bad = [
            {"op": "run", "command": "rm -rf /"},
            {"op": "apt_install", "packages": ["openssh-server"]},
            {"op": "apt_install", "packages": ["python3; rm -rf /"]},
            {"op": "apt_install", "packages": "python3"},
            {"op": "apt_install", "packages": ["python3"], "options": ["--allow-unauthenticated"]},
            {"op": "autologin", "enable": "yes"},
            {"op": "autologin", "enable": True, "user": "root"},
            {"op": "autologin", "enable": True, "console": True},
            {"op": "autologin", "enable": False, "console": "yes"},
            {"op": "autologin"},
        ]
        for op in bad:
            with self.subTest(op=op), self.assertRaises(privileged.RequestError):
                privileged.validate_request(self.request(op))
        for data in ({"operations": []}, {"version": 2, "operations": [{"op": "autologin",
                                                                           "enable": True}]},
                     {"version": 1, "operations": []}, [], "x"):
            with self.subTest(data=data), self.assertRaises(privileged.RequestError):
                privileged.validate_request(data)

    def test_execute_uses_fixed_commands_and_invoking_user(self):
        commands = []

        def run(args, env=None):
            commands.append((args, env))
            return 0
        user = types.SimpleNamespace(pw_name="robot")
        result = privileged.execute(
            [{"op": "apt_install", "packages": ["espeak-ng"]}], user, run=run)
        self.assertTrue(result["ok"])
        self.assertEqual(commands[0][0], ["apt-get", "update"])
        self.assertEqual(commands[1][0], ["apt-get", "install", "-y", "espeak-ng"])

    def test_autologin_maps_to_raspi_config_boot_behaviour(self):
        commands = []
        user = types.SimpleNamespace(pw_name="robot")
        with tempfile.NamedTemporaryFile(delete=False) as tool:
            pass
        self.addCleanup(os.unlink, tool.name)
        with mock.patch.object(privileged, "RASPI_CONFIG", tool.name):
            for op in ({"op": "autologin", "enable": True, "console": False},
                       {"op": "autologin", "enable": False, "console": False},
                       {"op": "autologin", "enable": False, "console": True}):
                privileged.execute([op], user,
                                   run=lambda args, env=None: commands.append((args, env)) or 0)
        self.assertEqual([c[0][-1] for c in commands], ["B4", "B3", "B1"])
        self.assertEqual(commands[0][1], {"SUDO_USER": "robot"})

    def test_execute_stops_after_failure(self):
        result = privileged.execute(
            [{"op": "apt_install", "packages": ["espeak-ng"]}],
            types.SimpleNamespace(pw_name="robot"), run=lambda args, env=None: 100)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["results"]), 1)

    def test_merge_combines_package_installs(self):
        merged = privileged.merge_operations([
            {"op": "apt_install", "packages": ["b", "a"]},
            {"op": "autologin", "enable": True},
            {"op": "apt_install", "packages": ["a", "c"]}])
        self.assertEqual(merged, [{"op": "apt_install", "packages": ["a", "b", "c"]},
                                  {"op": "autologin", "enable": True}])


class ConfigTests(unittest.TestCase):
    def test_profiles_and_dependencies(self):
        self.assertEqual(resolve_features(PROFILES["minimal"]), ("luna",))
        self.assertEqual(resolve_features((), ["autologin"]), ("luna", "startup", "autologin"))
        self.assertEqual(resolve_features(PROFILES["standard"], ["ai"], ["voice"]),
                         ("luna", "ai", "startup"))
        with self.assertRaises(SetupError):
            resolve_features(PROFILES["standard"], (), ["luna"])
        with self.assertRaises(SetupError):
            resolve_features(("camera",))

    def test_request_round_trip(self):
        request = Request(features=FEATURES[:3], luna_dir="/x", profile="custom",
                          ai_model="m", validate_ai=False)
        self.assertEqual(Request.from_dict(request.to_dict()), request)

    def test_os_release_and_device_checks(self):
        values = parse_os_release('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nVERSION_CODENAME=trixie\n')
        self.assertEqual(values["VERSION_CODENAME"], "trixie")
        checks = {c.id: c for c in device_checks(helpers.pi_device(machine="armv7l"))}
        self.assertTrue(checks["arch"].blocking)
        self.assertFalse(checks["internet"].blocking)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.box = helpers.Sandbox()
        self.ctx = self.box.context()

    def tearDown(self):
        self.box.cleanup()

    def test_user_file_is_backed_up_before_replacing(self):
        path = self.box.home / "file.txt"
        path.write_text("mine")
        self.assertEqual(write_managed(self.ctx, path, "ours", "t"), "written")
        backup = self.ctx.state.data["backups"][0]["backup"]
        self.assertEqual(Path(backup).read_text(), "mine")
        self.assertEqual(write_managed(self.ctx, path, "ours", "t"), "unchanged")
        self.assertEqual(write_managed(self.ctx, path, "ours v2", "t"), "written")
        self.assertEqual(len(self.ctx.state.data["backups"]), 1)  # our own file: no backup

    def test_env_file_keeps_other_lines(self):
        env = self.box.paths.env_file
        env.write_text("# comment\nOPENAI_API_KEY=old\nLUNA_VOICE=x\nOPENAI_API_KEY=dup\n")
        update_env_file(env, {"OPENAI_API_KEY": "new", "LUNA_AI_MODEL": "m"})
        text = env.read_text()
        self.assertEqual(text, "# comment\nOPENAI_API_KEY=new\nLUNA_VOICE=x\nLUNA_AI_MODEL=m\n")
        self.assertEqual(read_env_values(env)["LUNA_VOICE"], "x")
        if os.name == "posix":
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_state_round_trip_and_schema_check(self):
        state = State(self.box.paths.state_file)
        state.set_task("a", "verified")
        state.save()
        self.assertEqual(State(self.box.paths.state_file).load().task_status("a"), "verified")
        self.box.paths.state_file.write_text('{"schema": 99}')
        with self.assertRaises(SetupError):
            State(self.box.paths.state_file).load()

    def test_lightdm_autologin_parsing(self):
        conf = self.box.home / "lightdm.conf"
        conf.write_text("[LightDM]\nautologin-user=nobody\n[Seat:*]\n#autologin-user=x\n"
                        "autologin-user=robot\n")
        self.assertEqual(autologin.autologin_user(conf), "robot")
        conf.write_text("[Seat:*]\n#autologin-user=robot\n")
        self.assertIsNone(autologin.autologin_user(conf))


if __name__ == "__main__":
    unittest.main()

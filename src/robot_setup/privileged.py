"""Privileged system phase.

The root helper (`robot-setup-system`) is a fixed, root-owned program. It reads a small JSON
request from stdin, accepts only the operations and packages listed here, determines the user
from pkexec/sudo (never from the request) and never runs commands supplied by the caller.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import ALLOWED_PACKAGES, SetupError

HELPER_PATH = Path("/usr/lib/robot-setup/robot-setup-system")
REQUEST_VERSION = 1
MAX_REQUEST_BYTES = 16384
MAX_OPERATIONS = 10
RASPI_CONFIG = "/usr/bin/raspi-config"
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class RequestError(ValueError):
    pass


def validate_operation(op):
    if not isinstance(op, dict):
        raise RequestError("operation must be an object")
    kind = op.get("op")
    if kind == "apt_install":
        if set(op) != {"op", "packages"}:
            raise RequestError("apt_install takes only 'packages'")
        packages = op["packages"]
        if not isinstance(packages, list) or not 0 < len(packages) <= len(ALLOWED_PACKAGES):
            raise RequestError("packages must be a non-empty list")
        for name in packages:
            if not isinstance(name, str) or name not in ALLOWED_PACKAGES:
                raise RequestError("package not allowed")
        return {"op": kind, "packages": sorted(set(packages))}
    if kind == "autologin":
        if set(op) != {"op", "enable"} or not isinstance(op["enable"], bool):
            raise RequestError("autologin takes only 'enable' (true/false)")
        return {"op": kind, "enable": op["enable"]}
    raise RequestError("operation not allowed")


def validate_request(data):
    if not isinstance(data, dict) or set(data) != {"version", "operations"}:
        raise RequestError("malformed request")
    if data["version"] != REQUEST_VERSION:
        raise RequestError("unsupported request version")
    operations = data["operations"]
    if not isinstance(operations, list) or not 0 < len(operations) <= MAX_OPERATIONS:
        raise RequestError("operations must be a short non-empty list")
    return [validate_operation(op) for op in operations]


def invoking_user():
    import pwd
    for variable in ("PKEXEC_UID", "SUDO_UID"):
        value = os.environ.get(variable, "")
        if value.isdigit():
            uid = int(value)
            if uid == 0:
                raise RequestError("run Robot Setup from your normal account, not as root")
            return pwd.getpwuid(uid)
    raise RequestError("start this helper through pkexec or sudo")


def _run(args, extra_env=None):
    env = {"PATH": SAFE_PATH, "LANG": "C.UTF-8"}
    env.update(extra_env or {})
    try:
        return subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                              stdout=sys.stderr, stderr=sys.stderr).returncode
    except FileNotFoundError:
        return 127


def execute(operations, user, run=_run):
    results, apt_updated = [], False
    for op in operations:
        if op["op"] == "apt_install":
            code = 0
            if not apt_updated:
                code = run(["apt-get", "update"])
                apt_updated = True
            if code == 0:
                code = run(["apt-get", "install", "-y", *op["packages"]],
                           {"DEBIAN_FRONTEND": "noninteractive"})
        else:
            if not Path(RASPI_CONFIG).exists():
                code = 127
            else:
                behaviour = "B4" if op["enable"] else "B3"
                code = run([RASPI_CONFIG, "nonint", "do_boot_behaviour", behaviour],
                           {"SUDO_USER": user.pw_name})
        results.append({"op": op["op"], "ok": code == 0, "code": code})
        if code != 0:
            break
    ok = len(results) == len(operations) and all(r["ok"] for r in results)
    return {"ok": ok, "results": results}


def main():
    """Entry point of the root helper."""
    def reply(payload, code):
        print(json.dumps(payload), flush=True)
        return code

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return reply({"ok": False, "error": "The system helper must run as root."}, 2)
    try:
        user = invoking_user()
        raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise RequestError("request too large")
        operations = validate_request(json.loads(raw))
    except (RequestError, ValueError, KeyError) as error:
        return reply({"ok": False, "error": f"Request rejected: {error}"}, 3)
    result = execute(operations, user)
    if not result["ok"]:
        result["error"] = "A system step failed. See the messages above."
    return reply(result, 0 if result["ok"] else 1)


# Client side (runs as the normal user)

def helper_command(device):
    if HELPER_PATH.exists():
        base = [str(HELPER_PATH)]
    elif os.environ.get("ROBOT_SETUP_DEV") == "1":
        source = str(Path(__file__).resolve().parents[1])
        base = ["/usr/bin/python3", "-I", "-c",
                "import sys; sys.path.insert(0, %r); "
                "from robot_setup.privileged import main; sys.exit(main())" % source]
    else:
        raise SetupError("The system helper is missing. Reinstall the robot-setup package.")
    if device.graphical and not device.ssh and shutil.which("pkexec"):
        return ["pkexec", *base]
    return ["sudo", *base]


def merge_operations(operations):
    """Combine package installs into one apt operation; keep other operations in order."""
    packages, merged = [], []
    for op in operations:
        if op["op"] == "apt_install":
            packages.extend(p for p in op["packages"] if p not in packages)
        else:
            merged.append(dict(op))
    if packages:
        merged.insert(0, {"op": "apt_install", "packages": sorted(packages)})
    return merged


def elevate(ctx, operations):
    """Run system operations through the root helper. Raises SetupError on failure."""
    operations = merge_operations(operations)
    command = helper_command(ctx.device)
    ctx.log("Administrator permission is needed for system changes.")
    payload = json.dumps({"version": REQUEST_VERSION, "operations": operations})
    result = ctx.runner.run(command, input=payload, on_line=ctx.log, stream="stderr")
    if command[0] == "pkexec" and result.returncode in (126, 127):
        raise SetupError("Administrator permission was not given.")
    try:
        reply = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        reply = {"ok": False, "error": f"The system step failed (exit code {result.returncode})."}
    if not reply.get("ok"):
        raise SetupError(reply.get("error") or "A system step failed.")
    return reply

"""Automatic startup: a desktop autostart entry hands the session to a systemd user service.

Login -> autostart entry -> launcher imports WAYLAND_DISPLAY/DISPLAY into the user manager
-> `systemctl --user start luna.service` -> Luna (Restart=on-failure, limited).
Quitting Luna exits with code 0, so it stays closed; a crash is restarted.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from ..config import SERVICE_NAME
from ..files import is_current, remove_managed, write_managed
from ..system import FAILED, PASSED, PENDING, SKIPPED, WARNING, Check, read_text

FEATURE = "startup"
SESSION_VARIABLES = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE",
                     "XDG_CURRENT_DESKTOP")
JOURNAL_HINT = "See: journalctl --user -u luna.service"


def _systemd_quote(value):
    """Quote one argument of a command line setting (ExecStart=)."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def _systemd_path(value):
    """A path setting (WorkingDirectory=): systemd does not remove quotes here, so the path is
    written as is. Spaces are fine; only the % specifier character must be escaped."""
    return str(value).replace("%", "%%")


def unsupported_path(path):
    """Why systemd cannot use this path in a unit file, or None."""
    text = str(path)
    if any(ord(c) < 32 or c == "\x7f" for c in text):
        return "it contains control characters"
    if text != text.strip():
        return "it starts or ends with a space"
    if text.endswith("\\"):
        return "it ends with a backslash"
    return None


def render_unit(ctx):
    python = ctx.venv_python
    return (
        "# Managed by Robot Setup. Repair replaces this file after backing up your changes.\n"
        "[Unit]\n"
        "Description=Luna robot face\n"
        "After=graphical-session.target pipewire.service\n"
        "PartOf=graphical-session.target\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=5\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={_systemd_path(ctx.luna_dir)}\n"
        f"ExecStart={_systemd_quote(python)} {_systemd_quote(ctx.luna_dir / 'main.py')} --fullscreen\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "Environment=PYTHONUNBUFFERED=1\n"
    )


def render_launcher(ctx):
    variables = " ".join(SESSION_VARIABLES)
    state_dir = shlex.quote(str(ctx.paths.state_dir))
    marker = shlex.quote(str(ctx.paths.autostart_marker))
    return (
        "#!/bin/sh\n"
        "# Managed by Robot Setup: pass the desktop session to the Luna service and start it.\n"
        "# --login is used only by the desktop autostart entry; it records the boot so\n"
        "# 'robot-setup verify' can tell a login start from a manual start.\n"
        'if [ "$1" = "--login" ]; then\n'
        f"    mkdir -p {state_dir} && cat /proc/sys/kernel/random/boot_id > {marker} 2>/dev/null\n"
        "fi\n"
        f"systemctl --user import-environment {variables} 2>/dev/null\n"
        f"systemctl --user reset-failed {SERVICE_NAME} 2>/dev/null\n"
        f"exec systemctl --user start {SERVICE_NAME}\n"
    )


def render_autostart(ctx):
    launcher = str(ctx.paths.launcher).replace("%", "%%").replace('"', '\\"')
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Luna\n"
        "Comment=Start Luna (managed by Robot Setup)\n"
        f'Exec="{launcher}" --login\n'
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def managed_files(ctx):
    return [
        (ctx.paths.systemd_unit, render_unit(ctx), 0o644),
        (ctx.paths.launcher, render_launcher(ctx), 0o755),
        (ctx.paths.autostart_entry, render_autostart(ctx), 0o644),
    ]


def files_current(ctx):
    return all(is_current(ctx, path, content) for path, content, _ in managed_files(ctx))


def _startup_locations(ctx):
    config = ctx.paths.home / ".config"
    locations = [config / "labwc" / "autostart", config / "lxsession" / "LXDE-pi" / "autostart",
                 config / "wayfire.ini"]
    for folder in (config / "autostart", Path("/etc/xdg/autostart")):
        if folder.is_dir():
            locations.extend(sorted(folder.glob("*.desktop")))
    unit_dir = config / "systemd" / "user"
    if unit_dir.is_dir():
        locations.extend(sorted(unit_dir.glob("*.service")))
    return locations


def conflicts(ctx):
    """Other ways Luna is already started. Only one startup mechanism may be active."""
    ours = {ctx.paths.autostart_entry, ctx.paths.systemd_unit}
    luna_main = str(ctx.luna_dir / "main.py")
    found = []
    for path in _startup_locations(ctx):
        if path in ours:
            continue
        text = read_text(path) or ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            if "run-pi.sh" in line or luna_main in line or (
                    "main.py" in line and ("luna" in line.lower() or "ai-robot" in line)):
                found.append(path)
                break
    if ctx.paths.systemd_unit.exists() and ctx.state.owned(ctx.paths.systemd_unit) is None:
        found.append(ctx.paths.systemd_unit)
    return found


def preconditions(ctx):
    problem = unsupported_path(ctx.luna_dir)
    if problem:
        return [f"Automatic startup cannot use the Luna folder {ctx.luna_dir!r}: {problem}. "
                "Rename the folder."]
    return [f"Luna is already started by {path}. Remove that entry, then try again."
            for path in conflicts(ctx)]


def write_files(ctx):
    for path, content, mode in managed_files(ctx):
        write_managed(ctx, path, content, "startup-files", mode)
    ctx.state.save()
    result = ctx.runner.run(["systemctl", "--user", "daemon-reload"], timeout=30)
    if not result.ok:
        ctx.log("Note: the user service manager is not running yet; Luna starts at next login.")


def tasks(ctx):
    from ..engine import Task
    return [
        Task("startup-files", FEATURE, "Automatic startup",
             (f"Create {ctx.paths.systemd_unit} (restarts Luna after a crash)",
              f"Create {ctx.paths.launcher}",
              f"Create {ctx.paths.autostart_entry} (starts Luna at desktop login)"),
             is_done=files_current, apply=write_files),
    ]


def _systemctl(ctx, *args):
    return ctx.runner.run(["systemctl", "--user", *args], timeout=30)


def service_main_pid(ctx):
    value = _systemctl(ctx, "show", "-p", "MainPID", "--value", SERVICE_NAME).stdout.strip()
    return int(value) if value.isdigit() else 0


def luna_processes(ctx):
    """PIDs of this user's processes running Luna's main.py (Linux /proc)."""
    proc = Path("/proc")
    if not proc.is_dir() or not hasattr(os, "getuid"):
        return []
    luna_dir, pids = str(ctx.luna_dir), []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            args = [a.decode(errors="replace")
                    for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        if any(a.endswith("main.py") for a in args) and (
                cwd == luna_dir or str(ctx.luna_dir / "main.py") in args):
            pids.append(int(entry.name))
    return pids


def start_luna(ctx):
    """Start Luna now for the screen check. Returns a short message."""
    if not ctx.device.graphical:
        return "Open the Pi desktop to start Luna."
    main_pid = service_main_pid(ctx)
    if any(pid != main_pid for pid in luna_processes(ctx)):
        return "Luna is already running outside Robot Setup. Quit it first."
    if main_pid:
        return "Luna is already running."
    if files_current(ctx):
        result = ctx.runner.run([ctx.paths.launcher], timeout=30)
        return "Luna is starting." if result.ok else f"Luna could not start. {JOURNAL_HINT}"
    python = ctx.venv_python if ctx.venv_python.exists() else "python3"
    subprocess.Popen([str(python), "main.py", "--fullscreen"], cwd=ctx.luna_dir,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return "Luna is starting."


def service_status(ctx):
    """ActiveState, SubState, Result, NRestarts and ExecMainStartTimestamp of luna.service."""
    output = _systemctl(ctx, "show", "-p", "ActiveState", "-p", "SubState", "-p", "Result",
                        "-p", "NRestarts", "-p", "ExecMainStartTimestamp", SERVICE_NAME).stdout
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def reboot_check(ctx):
    """Did the desktop login start Luna in this boot, and what happened to it since?"""
    def check(status, detail):
        return Check("startup-reboot", "Luna starts after restart", status, detail)

    install_boot = ctx.state.data.get("install_boot_id")
    if install_boot and install_boot == ctx.device.boot_id:
        return check(PENDING, "Restart the Pi, then check again.")
    service = service_status(ctx)
    state, sub = service.get("ActiveState", ""), service.get("SubState", "")
    restarts = service.get("NRestarts", "0")
    login_started = bool(ctx.device.boot_id) and \
        (read_text(ctx.paths.autostart_marker) or "").strip() == ctx.device.boot_id
    if not login_started:
        if state == "active":
            return check(WARNING, "Luna is running, but it was not started by the desktop "
                                  "login (started by hand?). Restart the Pi to test again.")
        return check(FAILED, "The desktop login did not start Luna in this boot. Is someone "
                             "signed in to the desktop? Without auto-login, sign in first.")
    if state == "active":
        detail = "Started at login and running."
        if restarts not in ("", "0"):
            detail += f" It crashed and was restarted {restarts} time(s). {JOURNAL_HINT}"
            return check(WARNING, detail)
        return check(PASSED, detail)
    if state in ("activating", "reloading") and sub == "auto-restart":
        return check(WARNING, f"Started at login, crashed and is restarting. {JOURNAL_HINT}")
    if state == "failed":
        return check(FAILED, f"Started at login, then crashed ({service.get('Result') or 'error'})"
                             f" and stopped after {restarts} restart(s). {JOURNAL_HINT}")
    if state == "inactive" and service.get("Result") == "success" and \
            service.get("ExecMainStartTimestamp"):
        return check(PASSED, "Started at login; closed by the user since.")
    return check(FAILED, f"The login entry ran, but Luna did not start. {JOURNAL_HINT}")


def verify(ctx):
    checks = [Check("startup-files", "Startup files", PASSED if files_current(ctx) else FAILED,
                    "" if files_current(ctx) else "Run Repair.")]
    if ctx.device.graphical:
        environment = _systemctl(ctx, "show-environment").stdout
        handed_over = "WAYLAND_DISPLAY=" in environment or "DISPLAY=" in environment
        checks.append(Check("startup-session", "Desktop passed to Luna service",
                            PASSED if handed_over else PENDING,
                            "" if handed_over else "Happens at the next desktop login."))
    else:
        checks.append(Check("startup-session", "Desktop passed to Luna service", SKIPPED,
                            "Run on the Pi desktop to check."))
    checks.append(reboot_check(ctx))
    return checks


def remove(ctx):
    """Turn automatic startup off. Changed copies of our files are backed up, not kept in
    place, because a kept autostart entry would still start Luna."""
    _systemctl(ctx, "stop", SERVICE_NAME)
    lines = [f"{path}: {remove_managed(ctx, path, keep_changed=False)}"
             for path, _, _ in managed_files(ctx)]
    _systemctl(ctx, "daemon-reload")
    ctx.state.save()
    remaining = [p for p in (ctx.paths.autostart_entry, ctx.paths.systemd_unit) if p.exists()]
    remaining += conflicts(ctx)
    if remaining:
        lines.append("Automatic startup is NOT fully off. Luna can still start from:")
        lines.extend(f"  {path}" for path in dict.fromkeys(remaining))
        lines.append("Remove or edit these entries by hand.")
    else:
        lines.append("Automatic startup is off.")
    return lines

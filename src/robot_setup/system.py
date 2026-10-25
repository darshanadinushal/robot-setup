"""Running commands and detecting the device. Everything here is read-only."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import (MIN_FREE_MB, MIN_PYTHON, SUPPORTED_CODENAMES, SUPPORTED_MACHINES)

PASSED, FAILED, WARNING, SKIPPED, PENDING = "passed", "failed", "warning", "skipped", "pending"


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self):
        return self.returncode == 0


class Runner:
    """Runs commands without a shell. Tests replace it with a fake."""

    def run(self, args, *, input=None, env=None, cwd=None, timeout=None,
            on_line=None, stream="stderr"):
        """Run a command. With on_line, stream output lines ("stderr" or "all") as they arrive."""
        args = [str(a) for a in args]
        full_env = None if env is None else {**os.environ, **env}
        try:
            if on_line is None:
                proc = subprocess.run(args, input=input, capture_output=True, text=True,
                                      errors="replace", env=full_env, cwd=cwd, timeout=timeout)
                return Result(proc.returncode, proc.stdout, proc.stderr)
            return self._stream(args, input, full_env, cwd, timeout, on_line, stream)
        except FileNotFoundError:
            return Result(127, "", f"{args[0]}: command not found")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"{args[0]}: timed out")

    @staticmethod
    def _stream(args, input, env, cwd, timeout, on_line, stream):
        merge = stream == "all"
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge else subprocess.PIPE,
            text=True, errors="replace", env=env, cwd=cwd)
        if input is not None:
            proc.stdin.write(input)
            proc.stdin.close()
        out, err = [], []

        def pump(pipe, sink, show):
            for line in pipe:
                sink.append(line)
                if show:
                    on_line(line.rstrip("\n"))

        threads = [threading.Thread(target=pump, args=(proc.stdout, out, merge), daemon=True)]
        if not merge:
            threads.append(threading.Thread(target=pump, args=(proc.stderr, err, True), daemon=True))
        for thread in threads:
            thread.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        for thread in threads:
            thread.join()
        return Result(proc.returncode, "".join(out), "".join(err))


@dataclass
class Check:
    id: str
    label: str
    status: str
    detail: str = ""
    blocking: bool = False


@dataclass
class DeviceInfo:
    system: str = ""
    machine: str = ""
    model: str = ""
    os_name: str = ""
    codename: str = ""
    python: tuple = ()
    user: str = ""
    uid: int = -1
    graphical: bool = False
    ssh: bool = False
    desktop: str = ""
    free_mb: int = 0
    online: bool = False
    boot_id: str = ""
    extra: dict = field(default_factory=dict)


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_os_release(text):
    values = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    return values


def is_online(host="pypi.org", port=443, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def current_user():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USER") or os.environ.get("USERNAME") or ""


def detect_device(paths, check_online=True):
    release = parse_os_release(read_text("/etc/os-release"))
    model = (read_text("/proc/device-tree/model") or "").replace("\x00", "").strip()
    try:
        free_mb = shutil.disk_usage(paths.home).free // (1024 * 1024)
    except OSError:
        free_mb = 0
    return DeviceInfo(
        system=platform.system(),
        machine=platform.machine(),
        model=model,
        os_name=release.get("PRETTY_NAME", ""),
        codename=release.get("VERSION_CODENAME", ""),
        python=tuple(sys.version_info[:3]),
        user=current_user(),
        uid=os.getuid() if hasattr(os, "getuid") else -1,
        graphical=bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")),
        ssh=bool(os.environ.get("SSH_CONNECTION")),
        desktop=os.environ.get("XDG_CURRENT_DESKTOP", ""),
        free_mb=int(free_mb),
        online=is_online() if check_online else False,
        boot_id=(read_text("/proc/sys/kernel/random/boot_id") or "").strip(),
    )


def device_checks(info, allow_unsupported=False):
    """Compatibility checks. Unsupported OS or board blocks setup unless explicitly allowed."""
    def gate(ok, check_id, label, detail):
        if ok:
            return Check(check_id, label, PASSED, detail)
        return Check(check_id, label, WARNING if allow_unsupported else FAILED, detail,
                     blocking=not allow_unsupported)

    checks = [
        Check("linux", "Linux", PASSED if info.system == "Linux" else FAILED,
              info.system or "unknown", blocking=info.system != "Linux"),
        gate("Raspberry Pi 5" in info.model, "board", "Raspberry Pi 5",
             info.model or "Not a Raspberry Pi"),
        gate(info.codename in SUPPORTED_CODENAMES, "os", "Raspberry Pi OS Trixie",
             info.os_name or "unknown"),
        gate(info.machine in SUPPORTED_MACHINES, "arch", "64-bit (arm64)", info.machine or "unknown"),
        Check("python", "Python %d.%d or newer" % MIN_PYTHON,
              PASSED if tuple(info.python[:2]) >= MIN_PYTHON else FAILED,
              ".".join(map(str, info.python)), blocking=tuple(info.python[:2]) < MIN_PYTHON),
        Check("disk", f"{MIN_FREE_MB} MB free space",
              PASSED if info.free_mb >= MIN_FREE_MB else FAILED,
              f"{info.free_mb} MB free", blocking=info.free_mb < MIN_FREE_MB),
        Check("desktop", "Desktop session", PASSED if info.graphical else WARNING,
              info.desktop or ("found" if info.graphical else "Not found: screen checks run later")),
        Check("internet", "Internet", PASSED if info.online else WARNING,
              "connected" if info.online else "Needed only for downloads"),
    ]
    return checks

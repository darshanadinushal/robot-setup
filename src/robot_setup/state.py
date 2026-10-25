"""Installer state: what Robot Setup did, owns and verified. Never stores secrets."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from . import __version__
from .config import SetupError

SCHEMA = 1
PENDING, RUNNING, VERIFIED, FAILED, SKIPPED = "pending", "running", "verified", "failed", "skipped"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_state():
    return {
        "schema": SCHEMA,
        "installer_version": __version__,
        "updated": None,
        "request": None,
        "tasks": {},
        "owned_files": {},
        "created": {},
        "backups": [],
        "checks": {},
        "install_boot_id": None,
        "autologin": None,
    }


class State:
    def __init__(self, path):
        self.path = path
        self.data = empty_state()

    def load(self):
        if not self.path.exists():
            self.data = empty_state()
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SetupError(f"Cannot read {self.path}: {type(error).__name__}") from None
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise SetupError(f"{self.path} was written by an unsupported Robot Setup version.")
        merged = empty_state()
        merged.update(data)
        self.data = merged
        return self

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.data["updated"] = now()
        self.data["installer_version"] = __version__
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    # Tasks
    @property
    def tasks(self):
        return self.data["tasks"]

    def task_status(self, task_id):
        return self.tasks.get(task_id, {}).get("status", PENDING)

    def set_task(self, task_id, status, message=""):
        self.tasks[task_id] = {"status": status, "message": message, "updated": now()}

    def recover_interrupted(self):
        """Tasks left 'running' were interrupted; they will be re-checked and retried."""
        interrupted = [tid for tid, info in self.tasks.items() if info.get("status") == RUNNING]
        for tid in interrupted:
            self.set_task(tid, PENDING, "Interrupted; will be checked again.")
        return interrupted

    # Owned files
    def own(self, path, sha256, task_id):
        self.data["owned_files"][str(path)] = {"sha256": sha256, "task": task_id, "updated": now()}

    def owned(self, path):
        return self.data["owned_files"].get(str(path))

    def disown(self, path):
        self.data["owned_files"].pop(str(path), None)

    # Verification results
    def set_check(self, check_id, kind, status, detail=""):
        self.data["checks"][check_id] = {
            "kind": kind, "status": status, "detail": detail, "updated": now()}


@contextmanager
def locked(paths):
    """Allow only one setup operation at a time."""
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows development: no locking needed
        yield
        return
    handle = open(paths.lock_file, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SetupError("Robot Setup is already running.") from None
        yield
    finally:
        handle.close()

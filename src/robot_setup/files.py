"""Managed files, backups and Luna's ~/.luna.env file."""
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime

from .state import now


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".robot-setup.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def backup(ctx, path):
    """Copy a file Robot Setup is about to replace, and record where it went."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = ctx.paths.backups_dir / stamp / str(path).lstrip("/\\").replace(":", "")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    ctx.state.data["backups"].append({"path": str(path), "backup": str(target), "at": now()})
    ctx.log(f"Backed up {path} to {target}")
    return target


def is_current(ctx, path, content):
    """True when the file has exactly this content and Robot Setup owns it."""
    try:
        return path.read_bytes() == content.encode() and ctx.state.owned(path) is not None
    except OSError:
        return False


def write_managed(ctx, path, content, task_id, mode=0o644):
    """Write a file Robot Setup owns. Files created or changed by the user are backed up first."""
    data = content.encode()
    record = ctx.state.owned(path)
    if path.exists():
        current = path.read_bytes()
        if current == data:
            os.chmod(path, mode)
            ctx.state.own(path, sha256_bytes(data), task_id)
            return "unchanged"
        if record is None or record["sha256"] != sha256_bytes(current):
            backup(ctx, path)
    atomic_write(path, data, mode)
    ctx.state.own(path, sha256_bytes(data), task_id)
    return "written"


def remove_managed(ctx, path):
    """Delete a file only if Robot Setup created it and nobody changed it since."""
    record = ctx.state.owned(path)
    if record is None:
        return "not managed"
    if not path.exists():
        ctx.state.disown(path)
        return "already gone"
    if sha256_bytes(path.read_bytes()) != record["sha256"]:
        ctx.state.disown(path)
        return "kept (you changed it)"
    path.unlink()
    ctx.state.disown(path)
    return "removed"


def _env_key(line):
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None
    key = text.split("=", 1)[0].strip()
    if key.startswith("export "):
        key = key[len("export "):].strip()
    return key


def read_env_values(path):
    """Read KEY=VALUE pairs the same way Luna does."""
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key = _env_key(line)
        if key:
            values[key] = line.split("=", 1)[1].strip().strip('"').strip("'")
    return values


def update_env_file(path, updates):
    """Set keys in ~/.luna.env, keep every other line, and make it private (0600)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    out, seen = [], set()
    for line in lines:
        key = _env_key(line)
        if key in updates:
            if key not in seen:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
            continue
        out.append(line)
    out.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    atomic_write(path, ("\n".join(out) + "\n").encode(), 0o600)


def is_private(path):
    if os.name != "posix":
        return True
    try:
        info = path.stat()
    except OSError:
        return False
    return info.st_mode & 0o077 == 0 and info.st_uid == os.getuid()

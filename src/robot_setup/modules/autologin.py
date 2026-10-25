"""Desktop auto-login (explicit opt-in): sign in automatically so Luna starts after power-on."""
from __future__ import annotations

from pathlib import Path

from ..system import FAILED, PASSED, Check, read_text

FEATURE = "autologin"
LIGHTDM_CONF = Path("/etc/lightdm/lightdm.conf")
RASPI_CONFIG = Path("/usr/bin/raspi-config")


def autologin_user(conf=None):
    """The user LightDM signs in automatically, or None."""
    section = ""
    for raw in (read_text(conf or LIGHTDM_CONF) or "").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == "[Seat:*]" and line.startswith("autologin-user="):
            return line.split("=", 1)[1].strip() or None
    return None


def enabled(ctx):
    return autologin_user() == ctx.device.user


def remember_previous(ctx):
    if ctx.state.data.get("autologin") is None:
        ctx.state.data["autologin"] = {"previous": autologin_user(), "changed_by_setup": True}


def preconditions(ctx):
    if not RASPI_CONFIG.exists() or not LIGHTDM_CONF.exists():
        return ["Auto-login needs the Raspberry Pi desktop (raspi-config and LightDM)."]
    return []


def tasks(ctx):
    from ..engine import Task
    return [
        Task("autologin", FEATURE, "Desktop auto-login",
             (f"Sign in to the desktop automatically as {ctx.device.user} "
              "(anyone with the robot can use this account)",),
             is_done=enabled, system_ops=({"op": "autologin", "enable": True},),
             before_apply=remember_previous),
    ]


def verify(ctx):
    on = enabled(ctx)
    return [Check("autologin", "Desktop auto-login", PASSED if on else FAILED,
                  "" if on else "Run Repair.")]


def remove(ctx):
    record = ctx.state.data.get("autologin") or {}
    if not record.get("changed_by_setup") or record.get("previous") == ctx.device.user:
        return ["Auto-login: left as it was before Robot Setup."]
    ctx.system([{"op": "autologin", "enable": False}])
    ctx.state.data["autologin"] = None
    return ["Auto-login: turned off (desktop asks for a password again)."]

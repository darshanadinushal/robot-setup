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


def default_target(ctx):
    return ctx.runner.run(["systemctl", "get-default"], timeout=30).stdout.strip()


def remember_previous(ctx):
    """Record the login setup before Robot Setup changes it, so removal can restore it."""
    if ctx.state.data.get("autologin") is None:
        ctx.state.data["autologin"] = {"previous": autologin_user(),
                                       "previous_target": default_target(ctx),
                                       "changed_by_setup": True}


def preconditions(ctx):
    if not RASPI_CONFIG.exists() or not LIGHTDM_CONF.exists():
        return ["Auto-login needs the Raspberry Pi desktop (raspi-config and LightDM)."]
    other = autologin_user()
    if other and other != ctx.device.user:
        return [f"Auto-login is already set for the user '{other}'. Robot Setup will not take "
                "it over. Turn it off with raspi-config first, or skip auto-login."]
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
    """Restore the previous login setup, but only if it is still what Robot Setup applied."""
    record = ctx.state.data.get("autologin") or {}
    if not record.get("changed_by_setup") or record.get("previous") == ctx.device.user:
        return ["Auto-login: left as it was before Robot Setup."]
    current_user, current_target = autologin_user(), default_target(ctx)
    if current_user != ctx.device.user or current_target != "graphical.target":
        ctx.state.data["autologin"] = None
        return ["Auto-login: changed after setup "
                f"(login user: {current_user or 'none'}, boot: {current_target or 'unknown'}). "
                "Left as it is. Use raspi-config to change it."]
    console = record.get("previous_target") == "multi-user.target"
    ctx.system([{"op": "autologin", "enable": False, "console": console}])
    ctx.state.data["autologin"] = None
    if console:
        return ["Auto-login: restored (boots to the console and asks for a password again)."]
    return ["Auto-login: restored (desktop asks for a password again)."]

"""Built-in setup modules. Each module provides:

preconditions(ctx) -> list of blocker messages
tasks(ctx)         -> list of engine.Task (detect via is_done, apply or system_ops)
verify(ctx)        -> list of system.Check
remove(ctx)        -> list of report lines
"""
from . import ai, autologin, luna, startup, voice

MODULES = {
    "luna": luna,
    "voice": voice,
    "ai": ai,
    "startup": startup,
    "autologin": autologin,
}

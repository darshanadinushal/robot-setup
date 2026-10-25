"""Online AI module: openai package, API key and model in ~/.luna.env (0600)."""
from __future__ import annotations

import re

from ..config import AI_MODEL_PATTERN, PIP_PINS, SetupError
from ..files import is_private, read_env_values, update_env_file
from ..system import FAILED, PASSED, PENDING, SKIPPED, Check
from .common import has_pinned, pip_install

FEATURE = "ai"
PINS = {"openai": PIP_PINS["openai"]}
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{20,300}$")

VALIDATE_SCRIPT = (
    "import sys\n"
    "try:\n"
    "    from openai import OpenAI\n"
    "    OpenAI(timeout=20.0, max_retries=0).models.retrieve(sys.argv[1])\n"
    "    print('ok')\n"
    "except Exception as error:\n"
    "    print(type(error).__name__)\n"
)
# Only exception class names are reported, never messages (they could echo request data).
FRIENDLY = {
    "AuthenticationError": "The API key was rejected.",
    "PermissionDeniedError": "The API key cannot use this model.",
    "NotFoundError": "This model is not available. Check the model name.",
    "RateLimitError": "The account is out of credit or busy.",
    "ImportError": "The openai package is missing. Run Repair.",
    "ModuleNotFoundError": "The openai package is missing. Run Repair.",
}


def new_key(ctx):
    return (ctx.secrets.get("openai_api_key") or "").strip()


def saved_key(ctx):
    return read_env_values(ctx.paths.env_file).get("OPENAI_API_KEY", "")


def valid_key(key):
    return bool(KEY_PATTERN.match(key))


def preconditions(ctx):
    blockers = []
    if not AI_MODEL_PATTERN.match(ctx.request.ai_model or ""):
        blockers.append("Enter a valid AI model name (letters, numbers, . _ : -).")
    key = new_key(ctx)
    if key and not valid_key(key):
        blockers.append("The API key format is not valid.")
    elif not key and not saved_key(ctx):
        blockers.append("Enter your OpenAI API key.")
    return blockers


def config_ready(ctx):
    values = read_env_values(ctx.paths.env_file)
    key = new_key(ctx)
    return (is_private(ctx.paths.env_file)
            and bool(values.get("OPENAI_API_KEY"))
            and (not key or values.get("OPENAI_API_KEY") == key)
            and values.get("LUNA_AI_MODEL") == ctx.request.ai_model)


def write_config(ctx):
    updates = {"LUNA_AI_MODEL": ctx.request.ai_model}
    key = new_key(ctx)
    if key:
        updates = {"OPENAI_API_KEY": key, **updates}
    update_env_file(ctx.paths.env_file, updates)


def tasks(ctx):
    from ..engine import Task
    return [
        Task("ai-python", FEATURE, "AI package",
             (f"Install in Luna's environment: openai {PINS['openai']}",),
             is_done=lambda c: has_pinned(c, PINS),
             apply=lambda c: pip_install(c, PINS), needs_network=True),
        Task("ai-config", FEATURE, "AI settings",
             (f"Save the API key and model '{ctx.request.ai_model}' in {ctx.paths.env_file} "
              "(private file)",),
             is_done=config_ready, apply=write_config),
    ]


def check_online(ctx):
    """Ask the API whether the key can use the model. Returns a Check; never raises."""
    if not ctx.request.validate_ai:
        return Check("ai-online", "AI key and model", SKIPPED, "Online check turned off.")
    key = saved_key(ctx)
    if not key:
        return Check("ai-online", "AI key and model", FAILED, "No API key saved.")
    if not ctx.venv_python.exists():
        return Check("ai-online", "AI key and model", FAILED, "Run Repair.")
    result = ctx.runner.run([ctx.venv_python, "-c", VALIDATE_SCRIPT, ctx.request.ai_model],
                            env={"OPENAI_API_KEY": key}, timeout=60)
    answer = (result.stdout.strip().splitlines() or ["Error"])[-1]
    if answer == "ok":
        return Check("ai-online", "AI key and model", PASSED, ctx.request.ai_model)
    if answer in ("APIConnectionError", "APITimeoutError"):
        return Check("ai-online", "AI key and model", PENDING,
                     "No internet. Luna works offline; check again later.")
    return Check("ai-online", "AI key and model", FAILED,
                 FRIENDLY.get(answer, "The AI check failed. Luna still works offline."))


def verify(ctx):
    package = has_pinned(ctx, PINS)
    config = config_ready(ctx)
    return [
        Check("ai-package", "AI package", PASSED if package else FAILED,
              "" if package else "Run Repair."),
        Check("ai-config", "AI settings saved privately", PASSED if config else FAILED,
              "" if config else "Run Repair."),
        check_online(ctx),
    ]


def remove(ctx):
    return [f"AI settings kept in {ctx.paths.env_file}."]

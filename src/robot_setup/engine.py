"""Shared setup engine used by the CLI and the GUI: detect, plan, apply, verify, remove."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import privileged
from .config import FEATURE_TITLES, FEATURES, Paths, Request, SetupError
from .state import FAILED, PENDING, RUNNING, VERIFIED, State, locked
from .system import Runner, detect_device, device_checks


@dataclass
class Context:
    request: Request
    paths: Paths
    runner: Runner
    state: State
    device: object
    log: Callable[[str], None] = print
    secrets: dict = field(default_factory=dict)  # in memory only, never saved
    allow_unsupported: bool = False
    elevate: Optional[Callable] = None

    @property
    def luna_dir(self):
        return Path(self.request.luna_dir).expanduser()

    @property
    def venv_python(self):
        return self.luna_dir / ".venv" / "bin" / "python"

    def system(self, operations):
        return (self.elevate or privileged.elevate)(self, privileged.merge_operations(operations))


@dataclass
class Task:
    """One idempotent setup step.

    is_done inspects the real system (not the state file), so a step that was interrupted
    or changed by hand is detected and repeated. A task is either a system task
    (system_ops, run by the root helper) or a user task (apply, run as the normal user).
    """

    id: str
    feature: str
    title: str
    changes: tuple
    is_done: Callable[[Context], bool]
    apply: Optional[Callable[[Context], None]] = None
    system_ops: tuple = ()
    before_apply: Optional[Callable[[Context], None]] = None
    needs_network: bool = False

    @property
    def scope(self):
        return "system" if self.system_ops else "user"


@dataclass
class Plan:
    tasks: list
    pending: list
    blockers: list
    warnings: list
    checks: list


def modules():
    from .modules import MODULES
    return MODULES


def open_context(request=None, *, paths=None, runner=None, device=None, log=print,
                 secrets=None, allow_unsupported=False, elevate=None, check_online=True):
    paths = paths or Paths.for_user()
    state = State(paths.state_file).load()
    if request is None:
        if not state.data.get("request"):
            raise SetupError("Robot Setup has not installed anything yet.")
        request = Request.from_dict(state.data["request"])
    return Context(
        request=request, paths=paths, runner=runner or Runner(), state=state,
        device=device or detect_device(paths, check_online), log=log,
        secrets=dict(secrets or {}), allow_unsupported=allow_unsupported, elevate=elevate)


def build_plan(ctx):
    checks = device_checks(ctx.device, ctx.allow_unsupported)
    blockers = [f"{c.label}: {c.detail}" for c in checks if c.blocking]
    warnings = [f"{c.label}: {c.detail}" for c in checks if c.status == "warning"]
    tasks = []
    for name in ctx.request.features:
        module = modules()[name]
        blockers.extend(module.preconditions(ctx))
        tasks.extend(module.tasks(ctx))
    pending = [] if blockers else [t for t in tasks if not t.is_done(ctx)]
    if any(t.needs_network for t in pending) and not ctx.device.online:
        blockers.append("No internet connection. Connect to Wi-Fi or Ethernet, then try again.")
    return Plan(tasks=tasks, pending=pending, blockers=blockers, warnings=warnings, checks=checks)


def describe_plan(ctx, plan):
    lines = [
        "Features:    " + ", ".join(FEATURE_TITLES[f] for f in ctx.request.features),
        f"Luna folder: {ctx.luna_dir}",
    ]
    if plan.blockers:
        lines.append("Cannot continue:")
        lines.extend(f"  x {b}" for b in plan.blockers)
    for warning in plan.warnings:
        lines.append(f"  ! {warning}")
    if plan.pending:
        lines.append("Will change:")
        for task in plan.pending:
            who = "admin" if task.scope == "system" else "you  "
            for change in task.changes:
                lines.append(f"  [{who}] {change}")
    elif not plan.blockers:
        lines.append("Everything is already set up. Nothing will change.")
    done = [t for t in plan.tasks if t not in plan.pending]
    if done and not plan.blockers:
        lines.append("Already done: " + ", ".join(t.title for t in done))
    return lines


@dataclass
class Outcome:
    ok: bool
    message: str
    failed_task: str = ""


def install(ctx):
    """Apply the plan. Safe to run again: finished steps are detected and skipped."""
    with locked(ctx.paths):
        ctx.state.load()
        plan = build_plan(ctx)
        if plan.blockers:
            raise SetupError("\n".join(plan.blockers))
        state = ctx.state
        for task_id in state.recover_interrupted():
            ctx.log(f"Resuming interrupted step: {task_id}")
        state.data["request"] = ctx.request.to_dict()
        state.save()

        def fail(task, message):
            state.set_task(task.id, FAILED, message)
            state.save()
            ctx.log(f"Failed: {task.title}: {message}")
            return Outcome(False, f"{task.title}: {message}", task.id)

        system_tasks = [t for t in plan.tasks if t.scope == "system"]
        todo = [t for t in system_tasks if t in plan.pending]
        for task in system_tasks:
            if task not in todo:
                state.set_task(task.id, VERIFIED, "Already done.")
        if todo:
            for task in todo:
                if task.before_apply:
                    task.before_apply(ctx)
                state.set_task(task.id, RUNNING)
            state.save()
            ctx.log("Installing system packages and settings...")
            try:
                ctx.system([op for task in todo for op in task.system_ops])
            except SetupError as error:
                for task in todo:
                    state.set_task(task.id, FAILED, str(error))
                state.save()
                return Outcome(False, str(error), todo[0].id)
            for task in todo:
                if not task.is_done(ctx):
                    return fail(task, "The change could not be confirmed.")
                state.set_task(task.id, VERIFIED)
            state.save()

        for task in (t for t in plan.tasks if t.scope == "user"):
            if task.is_done(ctx):
                state.set_task(task.id, VERIFIED, "Already done.")
                continue
            ctx.log(f"{task.title}...")
            state.set_task(task.id, RUNNING)
            state.save()
            try:
                task.apply(ctx)
            except SetupError as error:
                return fail(task, str(error))
            except OSError as error:
                return fail(task, f"{type(error).__name__}: {error.strerror or error}")
            if not task.is_done(ctx):
                return fail(task, "The step finished but could not be confirmed.")
            state.set_task(task.id, VERIFIED)
            state.save()

        if "startup" in ctx.request.features and any(
                t.feature == "startup" for t in plan.pending):
            state.data["install_boot_id"] = ctx.device.boot_id
        state.save()
        return Outcome(True, "Setup finished.")


def run_verify(ctx):
    """Automated checks for every installed feature. Results are recorded in the state."""
    checks = []
    for name in ctx.request.features:
        checks.extend(modules()[name].verify(ctx))
    with locked(ctx.paths):
        ctx.state.load()
        for check in checks:
            ctx.state.set_check(check.id, "auto", check.status, check.detail)
        ctx.state.save()
    return checks


def remove_all(ctx):
    """Undo what Robot Setup changed. User data, Luna, models, keys and packages are kept."""
    lines = []
    with locked(ctx.paths):
        ctx.state.load()
        for name in reversed(FEATURES):
            if name in ctx.request.features:
                lines.extend(modules()[name].remove(ctx))
        for task_id, info in list(ctx.state.tasks.items()):
            info["status"] = PENDING
            info["message"] = "Removed."
        ctx.state.data["request"] = None
        ctx.state.data["checks"] = {}
        ctx.state.data["install_boot_id"] = None
        ctx.state.save()
    lines.append("Kept: Luna folder and .venv, voice models, ~/.luna.env, Luna settings, "
                 "apt packages.")
    return lines

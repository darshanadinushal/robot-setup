"""Command-line interface. Uses the same engine as the GUI; works over SSH."""
from __future__ import annotations

import argparse
import getpass
import os
import re
import shlex
import sys
from pathlib import Path

from . import __version__, checks, engine, lunainfo
from .config import FEATURE_TITLES, FEATURES, LUNA_STABLE_TAG, PROFILES, Paths, Request, \
    SetupError, resolve_features
from .modules.luna import find_luna_dirs, is_luna_dir
from .state import State, locked
from .system import Runner, detect_device, device_checks

MARKS = {"passed": "[ ok ]", "failed": "[FAIL]", "warning": "[warn]", "skipped": "[skip]",
         "pending": "[wait]", "verified": "[ ok ]", "running": "[....]"}


def print_checks(items):
    for item in items:
        detail = f" - {item.detail}" if item.detail else ""
        print(f"  {MARKS.get(item.status, item.status)} {item.label}{detail}")


def confirm(question, assume_yes=False):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise SetupError("Add --yes to confirm when not running interactively.")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def build_request(args, paths):
    saved = State(paths.state_file).load().data.get("request")
    saved = Request.from_dict(saved) if saved else None
    if args.profile:
        base, profile = PROFILES[args.profile], args.profile
    elif saved:
        base, profile = saved.features, saved.profile
    else:
        base, profile = PROFILES["standard"], "standard"
    if args.add or args.remove:
        profile = "custom"
    features = resolve_features(base, args.add or (), args.remove or ())
    if args.luna_dir:
        luna_dir = str(Path(args.luna_dir).expanduser().resolve())
    elif saved and saved.luna_dir:
        luna_dir = saved.luna_dir
    else:
        found = find_luna_dirs(paths.home)
        luna_dir = str(found[0] if found else paths.home / "luna")
    return Request(features=features, luna_dir=luna_dir, profile=profile,
                   ai_model=args.ai_model or (saved.ai_model if saved else ""),
                   validate_ai=not args.skip_ai_check)


def collect_secrets(args, request, paths):
    if "ai" not in request.features:
        return {}
    if args.ai_key_stdin:
        return {"openai_api_key": sys.stdin.readline().strip()}
    from .files import read_env_values
    if read_env_values(paths.env_file).get("OPENAI_API_KEY") and not args.new_ai_key:
        return {}
    if sys.stdin.isatty():
        return {"openai_api_key": getpass.getpass("OpenAI API key (hidden): ").strip()}
    return {}


def make_context(args, request=None):
    paths = Paths.for_user()
    if request is None and hasattr(args, "profile"):
        request = build_request(args, paths)
    secrets = collect_secrets(args, request, paths) if request is not None else {}
    return engine.open_context(request, paths=paths, secrets=secrets,
                               allow_unsupported=allow_unsupported(args))


def allow_unsupported(args):
    return bool(getattr(args, "allow_unsupported", False)
                or os.environ.get("ROBOT_SETUP_ALLOW_UNSUPPORTED") == "1")


def cmd_check(args):
    info = detect_device(Paths.for_user())
    items = device_checks(info, allow_unsupported(args))
    print("Device check")
    print_checks(items)
    return 1 if any(c.blocking for c in items) else 0


def cmd_plan(args):
    ctx = make_context(args)
    plan = engine.build_plan(ctx)
    print("\n".join(engine.describe_plan(ctx, plan)))
    return 1 if plan.blockers else 0


def cmd_install(args):
    ctx = make_context(args)
    plan = engine.build_plan(ctx)
    print("\n".join(engine.describe_plan(ctx, plan)))
    if plan.blockers:
        return 1
    if args.dry_run:
        return 0
    if plan.pending and not confirm("Continue?", args.yes):
        print("Nothing was changed.")
        return 1
    outcome = engine.install(ctx)
    ctx.secrets.clear()
    print(outcome.message)
    if not outcome.ok:
        print("Fix the problem, then run: robot-setup repair")
        return 1
    print("\nChecks")
    print_checks(engine.run_verify(ctx))
    print("\nNext: robot-setup verify --confirm   (screen, touch, speaker, microphone)")
    if "startup" in ctx.request.features:
        print("Then restart the Pi and run: robot-setup verify")
    return 0


def cmd_verify(args):
    ctx = make_context(args)
    print("Checks")
    results = engine.run_verify(ctx)
    print_checks(results)
    if args.confirm:
        if not sys.stdin.isatty():
            raise SetupError("--confirm needs an interactive terminal.")
        for check in checks.applicable(ctx):
            print(f"\n{check.instructions}")
            if check.test and input("Press Enter to run the test (s to skip): ").strip() != "s":
                print(check.test(ctx))
            answer = ""
            while answer not in ("y", "n", "s"):
                answer = input(f"{check.question} [y/n/s] ").strip().lower()[:1]
            checks.record_answer(ctx, check, {"y": "yes", "n": "no", "s": "skip"}[answer])
    return 1 if any(c.status == "failed" for c in results) else 0


def cmd_status(args):
    state = State(Paths.for_user().state_file).load()
    request = state.data.get("request")
    if not request:
        print("Robot Setup has not installed anything yet.")
        return 0
    print("Features:    " + ", ".join(FEATURE_TITLES[f] for f in request["features"]))
    print(f"Luna folder: {request['luna_dir']} ({lunainfo.describe(request['luna_dir'])})")
    print(f"Updated:     {state.data.get('updated')}")
    print("Steps")
    for task_id, info in sorted(state.tasks.items()):
        message = f" - {info['message']}" if info.get("message") else ""
        print(f"  {MARKS.get(info['status'], info['status'])} {task_id}{message}")
    if state.data["checks"]:
        print("Checks")
        for check_id, info in sorted(state.data["checks"].items()):
            detail = f" - {info['detail']}" if info.get("detail") else ""
            print(f"  {MARKS.get(info['status'], info['status'])} {check_id} ({info['kind']}){detail}")
    return 0


def cmd_remove(args):
    ctx = make_context(args)
    print("This removes Luna's automatic startup (and auto-login if Robot Setup turned it on).")
    print("Luna, its settings, keys, voice models and installed packages are kept.")
    if not confirm("Remove?", args.yes):
        print("Nothing was changed.")
        return 1
    for line in engine.remove_all(ctx):
        print(line)
    return 0


def luna_dir_for(args, paths):
    """--luna-dir, else the saved setup's folder, else ~/luna or ~/ai-robot."""
    if getattr(args, "luna_dir", None):
        return Path(args.luna_dir).expanduser().resolve()
    saved = State(paths.state_file).load().data.get("request")
    if saved and saved.get("luna_dir"):
        return Path(saved["luna_dir"])
    found = find_luna_dirs(paths.home)
    if found:
        return found[0]
    raise SetupError("Luna was not found. Add --luna-dir FOLDER.")


def run_luna_script(args, name, script_args, question):
    """Run one of Luna Core's own deployment scripts (upgrade-pi.sh, rollback-pi.sh). They back
    up Luna's settings, memories and plugin choices and check that Luna starts; Robot Setup does
    not repeat that work."""
    paths = Paths.for_user()
    luna_dir = luna_dir_for(args, paths)
    if not is_luna_dir(luna_dir):
        raise SetupError(f"Luna was not found in {luna_dir}.")
    script = luna_dir / "scripts" / name
    if not script.is_file():
        raise SetupError(
            f"{script} is missing: this Luna is older than Luna Core 1.0. Update it once with "
            f"git: cd {shlex.quote(str(luna_dir))} && git fetch --tags && "
            f"git checkout {LUNA_STABLE_TAG}   then run: robot-setup repair")
    print(f"Luna folder: {luna_dir} ({lunainfo.describe(luna_dir)})")
    if not confirm(question, args.yes):
        print("Nothing was changed.")
        return 1
    with locked(paths):
        result = Runner().run(["bash", script, *script_args], cwd=luna_dir,
                              on_line=print, stream="all")
    print(f"Luna folder: {luna_dir} ({lunainfo.describe(luna_dir)})")
    if not result.ok:
        print(f"{name} failed (exit code {result.returncode}); see the messages above.")
        return 1
    if State(paths.state_file).load().data.get("request"):
        print("Next: robot-setup repair   (downloads voices or languages this version added)")
    return 0


def cmd_upgrade_luna(args):
    if not lunainfo.GIT_REF.match(args.version):
        raise SetupError(f"Not a version: {args.version!r}. Use a tag such as {LUNA_STABLE_TAG}.")
    if not re.match(r"^v\d+\.\d+\.\d+$", args.version):
        print(f"Note: {args.version} is not a release tag. Stable releases are tags such as "
              f"{LUNA_STABLE_TAG}.")
    return run_luna_script(args, "upgrade-pi.sh", [args.version],
                           f"Upgrade Luna to {args.version}? (settings, memories and plugin "
                           "choices are backed up first)")


def cmd_rollback_luna(args):
    return run_luna_script(args, "rollback-pi.sh", ["--with-data"] if args.with_data else [],
                           "Go back to the Luna version before the last upgrade?")


def cmd_gui(args):
    try:
        from .gui import run
    except (ImportError, ValueError):
        raise SetupError("The GUI needs python3-gi and gir1.2-gtk-4.0. "
                         "Use the command line instead: robot-setup install") from None
    return run(allow_unsupported(args))


def add_request_options(parser):
    parser.add_argument("--profile", choices=sorted(PROFILES),
                        help="minimal: Luna only; standard: Luna, offline voice, startup")
    parser.add_argument("--with", dest="add", action="append", choices=FEATURES,
                        metavar="FEATURE", help="add a feature: " + ", ".join(FEATURES))
    parser.add_argument("--without", dest="remove", action="append", choices=FEATURES,
                        metavar="FEATURE", help="leave a feature out")
    parser.add_argument("--luna-dir", help="folder that contains Luna's main.py")
    parser.add_argument("--ai-model", help="AI model name saved as LUNA_AI_MODEL")
    parser.add_argument("--ai-key-stdin", action="store_true",
                        help="read the OpenAI API key from standard input")
    parser.add_argument("--new-ai-key", action="store_true",
                        help="ask for a new API key even if one is saved")
    parser.add_argument("--skip-ai-check", action="store_true",
                        help="do not check the key and model online")
    parser.add_argument("--allow-unsupported", action="store_true",
                        help="continue on an untested board or OS (development only)")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="robot-setup", description="Set up Luna and robot features on a Raspberry Pi.")
    parser.add_argument("--version", action="version", version=f"robot-setup {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    check = sub.add_parser("check", help="check this device")
    check.add_argument("--allow-unsupported", action="store_true")
    add_request_options(sub.add_parser("plan", help="show what would change"))
    install = sub.add_parser("install", help="install or add features")
    add_request_options(install)
    install.add_argument("--dry-run", action="store_true", help="only show the plan")
    install.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    repair = sub.add_parser("repair", help="re-run the saved setup and fix what is missing")
    add_request_options(repair)
    repair.add_argument("--dry-run", action="store_true", help="only show the plan")
    repair.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    verify = sub.add_parser("verify", help="check that everything works")
    verify.add_argument("--confirm", action="store_true",
                        help="also test screen, touch, speaker and microphone with you")
    verify.add_argument("--allow-unsupported", action="store_true")
    sub.add_parser("status", help="show what Robot Setup did")
    remove = sub.add_parser("remove", help="undo Robot Setup's changes (keeps your data)")
    remove.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    upgrade = sub.add_parser("upgrade-luna",
                             help=f"upgrade Luna with her own upgrade script (default {LUNA_STABLE_TAG})")
    upgrade.add_argument("version", nargs="?", default=LUNA_STABLE_TAG,
                         help=f"release tag (default {LUNA_STABLE_TAG}); a branch is for testing only")
    upgrade.add_argument("--luna-dir", help="folder that contains Luna's main.py")
    upgrade.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    rollback = sub.add_parser("rollback-luna",
                              help="go back to the Luna version before the last upgrade")
    rollback.add_argument("--with-data", action="store_true",
                          help="also restore the settings, memories and plugin choices "
                               "saved by the last upgrade")
    rollback.add_argument("--luna-dir", help="folder that contains Luna's main.py")
    rollback.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    gui = sub.add_parser("gui", help="open the setup wizard")
    gui.add_argument("--allow-unsupported", action="store_true")
    return parser


COMMANDS = {"check": cmd_check, "plan": cmd_plan, "install": cmd_install, "repair": cmd_install,
            "verify": cmd_verify, "status": cmd_status, "remove": cmd_remove, "gui": cmd_gui,
            "upgrade-luna": cmd_upgrade_luna, "rollback-luna": cmd_rollback_luna}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
            args.command, args.allow_unsupported = "gui", False
        else:
            parser.print_help()
            return 0
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("Run Robot Setup as the user who will use Luna, not with sudo. "
              "It asks for administrator permission when needed.", file=sys.stderr)
        return 2
    try:
        return COMMANDS[args.command](args)
    except SetupError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Run 'robot-setup repair' to continue safely.", file=sys.stderr)
        return 130

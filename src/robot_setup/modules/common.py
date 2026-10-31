"""Helpers shared by setup modules."""
from __future__ import annotations

import json

from ..config import SetupError

VERSION_SCRIPT = (
    "import json, sys\n"
    "from importlib import metadata\n"
    "found = {}\n"
    "for name in sys.argv[1:]:\n"
    "    try:\n"
    "        found[name] = metadata.version(name)\n"
    "    except metadata.PackageNotFoundError:\n"
    "        found[name] = None\n"
    "print(json.dumps(found))\n"
)


def missing_packages(ctx, packages):
    """apt packages that are not installed (checked without root, in one dpkg-query call)."""
    packages = list(packages)
    if not packages:
        return []
    # Exits non-zero when any package is unknown, but still lists the known ones.
    result = ctx.runner.run(["dpkg-query", "-W", "-f=${Package} ${Status}\\n", *packages])
    installed = set()
    for line in result.stdout.splitlines():
        name, _, status = line.partition(" ")
        if status.strip() == "install ok installed":
            installed.add(name.split(":", 1)[0])
    return [name for name in packages if name not in installed]


def venv_versions(ctx, names):
    if not ctx.venv_python.exists():
        return {name: None for name in names}
    result = ctx.runner.run([ctx.venv_python, "-c", VERSION_SCRIPT, *names], timeout=60)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1]) if result.ok else {}
    except (ValueError, IndexError):
        return {}


def has_pinned(ctx, pins):
    found = venv_versions(ctx, list(pins))
    return all(found.get(name) == version for name, version in pins.items())


def pip_install(ctx, pins):
    if not ctx.venv_python.exists():
        raise SetupError("Luna's Python environment is missing. Run Repair.")
    specs = [f"{name}=={version}" for name, version in pins.items()]
    result = ctx.runner.run(
        [ctx.venv_python, "-m", "pip", "install", "--disable-pip-version-check", *specs],
        on_line=ctx.log, stream="all")
    if not result.ok:
        raise SetupError("Installing Python packages failed. Check the internet connection.")


def has_sound_card(ctx, command):
    """True if `aplay -l` / `arecord -l` lists at least one card."""
    result = ctx.runner.run([command, "-l"], timeout=10)
    return result.ok and "card " in result.stdout

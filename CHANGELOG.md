# Changelog

Robot Setup versions (`src/robot_setup/__init__.py`, the `.deb` package version). Releases will
be git tags `vX.Y.Z`; fixes for a release go on a `release/X.Y.Z` branch.

## 0.4.0 (unreleased)
- Works with Luna Core 1.x (`darshanadinushal/ai-robot`, stable tag `v1.0.0`): Luna is found by
  `luna_core/app.py`, voices and languages are read from `luna_core/`. An older Luna with only
  `robot_hud/` still installs; `verify` warns and suggests `robot-setup upgrade-luna v1.0.0`.
- One `luna.service`: startup files written by Luna Core's `scripts/install-service.sh` for the
  same Luna folder are backed up and replaced instead of blocking Install; `remove` puts them back.
  Luna Core's `luna.desktop` is now detected, so Luna can no longer start twice at login.
- `robot-setup upgrade-luna [tag]` and `rollback-luna [--with-data]` run Luna Core's own
  `upgrade-pi.sh` / `rollback-pi.sh` (backup, start check, automatic undo).
- `status` and the plan show the Luna Core version.
- Raspberry Pi: one `dpkg-query` per package group instead of one per package, and `verify`
  starts Luna's Python once instead of twice.
- CI: tests also on ARM (`ubuntu-24.04-arm`) and against the real Luna Core (`v1.0.0` and `main`).

## 0.3.0
- Voices and listening languages are read from Luna's folder; Swedish listening model.

## 0.2.0
- Optional Natural voice feature: Piper with English and Swedish voices.

## 0.1.1
- Fix unquoted `WorkingDirectory` in `luna.service`; CI checks the unit with `systemd-analyze`.

## 0.1.0
- Engine, command line, GTK wizard, root helper, packaging and specification.

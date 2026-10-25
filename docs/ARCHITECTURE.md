# Robot Setup v1.0 — Architecture Specification

Status: frozen for version 1.0. Changes to this document need a review before they are implemented.

## 1. Purpose and principles

Robot Setup prepares a Raspberry Pi 5 to run Luna and, later, other robot features. It is a
**one-time tool**: it runs, configures the device, verifies the result and exits. It can be opened
again to repair the setup, add features, check the hardware or undo its changes.

Principles:

1. **No runtime dependency.** Luna and its services never need Robot Setup to be installed or
   running. Removing Robot Setup's package does not stop Luna.
2. **Use the platform.** apt installs packages, systemd restarts Luna, and the desktop session
   starts it. Robot Setup has no daemon, no supervisor and no plugin system.
3. **Inspect, then change.** Each step checks the real system before and after it runs. A step
   counts as done only when the check confirms it.
4. **Repeatable.** Running setup again changes only what is missing or wrong, and never duplicates
   services or overwrites the user's own settings.
5. **Least privilege.** The application runs as the normal user. One small, fixed root program
   performs a short list of allowed system operations.
6. **No secrets in state or logs.**

Out of scope for 1.0: automatic updates, Luna release downloads, operating system rollback, network
(Wi-Fi) setup, home automation, camera and motors, and fleet provisioning.

## 2. Components

```
robot-setup (.deb, Architecture: all)
├── GTK 4 wizard (gui.py) ─┐
├── CLI (cli.py) ──────────┴─► Engine (engine.py): detect → plan → apply → verify → record
│                                 ├── Feature modules (modules/*.py)
│                                 ├── State (state.py) and managed files (files.py)
│                                 ├── Device detection (system.py)
│                                 └── Hardware checks with the user (checks.py)
└── Root helper (privileged.py, installed as /usr/lib/robot-setup/robot-setup-system)

Installed result (independent of Robot Setup)
├── Luna folder + .venv          (user)
├── ~/.luna.env                  (user, 0600)
├── luna.service + launcher      (systemd user service, desktop autostart)
└── apt packages, LightDM login  (system)
```

| Component | Responsibility | Runs as |
|-----------|----------------|---------|
| Wizard / CLI | Collect choices, show plan and progress, ask for hardware confirmation | user |
| Engine | Build the plan, run tasks in order, record results | user |
| Modules | Describe one feature: preconditions, tasks, checks, removal | user |
| Root helper | Validate and run allowed system operations | root, one call per install |

### Package layout

| Path | Contents |
|------|----------|
| `/usr/bin/robot-setup` | Launcher for the CLI and wizard |
| `/usr/lib/robot-setup/robot_setup/` | Python package |
| `/usr/lib/robot-setup/robot-setup-system` | Root helper (root-owned, `#!/usr/bin/python3 -I`) |
| `/usr/share/applications/robot-setup.desktop` | Menu entry |
| `/usr/share/polkit-1/actions/io.github.darshanadinushal.robot-setup.policy` | pkexec authorization |

The package depends on `python3 (>= 3.11)`, `python3-gi`, `gir1.2-gtk-4.0`, `pkexec | policykit-1`
and `sudo`. It has **no maintainer scripts**: installing the package changes nothing else, and the
wizard runs only when the user opens it.

## 3. Features and profiles

| Feature | Requires | Minimal | Standard |
|---------|----------|---------|----------|
| `luna` | — | ✔ | ✔ |
| `voice` | luna | | ✔ |
| `piper` | luna | | optional |
| `ai` | luna | | optional |
| `startup` | luna | | ✔ |
| `autologin` | startup | | opt-in only |

Profiles (`minimal`, `standard`, `custom`) only set the starting selection. `--with` adds a
feature with its requirements. Removing a feature that another selected feature requires is an
error. Features always run in the order shown above.

## 4. Module contract

Every module in `src/robot_setup/modules/` implements:

| Function | Returns | Rules |
|----------|---------|-------|
| `preconditions(ctx)` | list of blocker messages | Read only. Blockers stop the whole plan. |
| `tasks(ctx)` | list of `Task` | Read only. Describes what may change. |
| `verify(ctx)` | list of `Check` | Automatic checks only. Must not change the system. |
| `remove(ctx)` | list of report lines | Removes only what the module owns. Keeps user data and shared packages. |

A `Task` has:

| Field | Meaning |
|-------|---------|
| `id`, `feature`, `title` | Stable identifier, owning feature, user-facing name |
| `changes` | Lines shown in the plan before anything runs |
| `is_done(ctx)` | Inspects the **real system** (not the state file) |
| `apply(ctx)` | User-scope change; raises `SetupError` with a readable message on failure |
| `system_ops(ctx)` | Root-scope change, expressed as allowed operations (section 7) |
| `before_apply(ctx)` | Optional; records information before the change (e.g. previous login setting) |
| `needs_network` | The task downloads something |

Rules for tasks:

- **Idempotent:** `apply` after a successful `apply` changes nothing.
- **Confirmed:** after `apply`, `is_done` must return true. Otherwise the task fails with
  "The step finished but could not be confirmed."
- **No arbitrary commands:** system tasks describe operations. They never pass command lines to root.

## 5. Engine lifecycle

```
detect ─► plan ─► apply (system phase, then user phase) ─► verify ─► record
```

1. **Detect** — `system.detect_device` collects the board model, OS codename, architecture, Python
   version, user, desktop session, SSH, free disk space, internet access and boot id.
2. **Plan** — `build_plan` combines device checks, module preconditions and every task's
   `is_done` result. The plan lists blockers, warnings and pending tasks with their changes.
   Internet access is a blocker only when a pending task needs downloads.
3. **Apply** — `install`:
   1. takes the state lock and rebuilds the plan;
   2. runs all pending system operations in **one** elevated call (package installs are merged
      into one apt operation);
   3. runs user tasks in order. Each task is marked `running`, applied, confirmed with `is_done`
      and marked `verified`;
   4. on the first failure, stops, marks the task `failed` with its message, and leaves the rest
      `pending`.
4. **Verify** — `run_verify` runs module checks (automatic) and the wizard or `verify --confirm`
   asks the user to confirm the hardware (section 9).
5. **Record** — state is saved after every task change.

### Task status

```
pending ─► running ─► verified
              │
              └────► failed ─► (repair) ─► running
```

`skipped` marks tasks of features that are no longer selected. A task left `running` by an
interrupted run (power loss, closed window) is reset to `pending` at the next start, and `is_done`
decides whether it must run again.

**Repair** is the same as install with the saved request: completed work is confirmed and skipped,
and missing or failed work runs again.

## 6. State

Location: `~/.local/state/robot-setup/state.json` (directory mode 0700). `ROBOT_SETUP_STATE_DIR`
overrides it for tests.

> Earlier drafts placed state in `/var/lib/robot-setup`. Version 1.0 keeps it per user because
> every managed file belongs to the Luna user and the root helper keeps no state. This means the
> state never needs root to read or write.

```json
{
  "schema": 1,
  "installer_version": "0.1.1",
  "updated": "2026-01-01T12:00:00+00:00",
  "request": {"profile": "standard", "features": ["luna", "voice", "startup"],
              "luna_dir": "/home/robot/luna", "ai_model": null, "validate_ai": true},
  "tasks": {"luna-environment": {"status": "verified", "updated": "...", "message": ""}},
  "owned_files": {"/home/robot/.config/systemd/user/luna.service":
                  {"sha256": "...", "task": "startup-files"}},
  "created": {"venv": "/home/robot/luna/.venv"},
  "backups": [{"path": "...", "backup": "...", "at": "..."}],
  "checks": {"screen": {"kind": "user", "status": "passed", "updated": "..."}},
  "install_boot_id": "…",
  "autologin": {"previous": null, "previous_target": "graphical.target", "changed_by_setup": true}
}
```

Rules:

- The state **never** contains API keys, passwords or other secrets.
- An exclusive lock (`flock` on `lock`) prevents two setups from running at the same time.
- An unknown `schema` value stops Robot Setup instead of guessing.
- State describes what Robot Setup did. Decisions are always based on the real system.

## 7. Privilege model

The wizard and CLI always run as the normal user and refuse to run as root. System changes go
through one root helper call per install:

| Session | Elevation |
|---------|-----------|
| Desktop | `pkexec /usr/lib/robot-setup/robot-setup-system` (polkit `auth_admin_keep`) |
| SSH or no display | `sudo /usr/lib/robot-setup/robot-setup-system` |

The helper:

1. is an installed, root-owned file, started with `python3 -I` (isolated mode, no user site
   packages, no `PYTHONPATH`) and writes no bytecode;
2. reads one JSON request from stdin (at most 16 KB and 10 operations);
3. **revalidates** every operation against the allow-list and rejects unknown operations, extra
   keys, wrong types and unlisted packages;
4. determines the user from `PKEXEC_UID` or `SUDO_UID` (never from the request) and rejects uid 0;
5. runs fixed command lines with a fixed `PATH`, never a command from the request;
6. prints progress to stderr and a JSON result to stdout.

Allowed operations in 1.0:

| Operation | Arguments | Runs |
|-----------|-----------|------|
| `apt_install` | `packages`: subset of `python3`, `python3-pygame`, `python3-venv`, `espeak-ng`, `alsa-utils`, `libportaudio2` | `apt-get update`, `apt-get install -y …` |
| `autologin` | `enable`: boolean; `console`: boolean, only with `enable: false` | `raspi-config nonint do_boot_behaviour` `B4` (desktop auto-login), `B3` (desktop, password) or `B1` (console, password) for the calling user |

Adding an operation requires a change to this table, the allow-list and its tests.

## 8. Startup mechanism

Luna needs the graphical session (Wayland display, audio, touch input). Version 1.0 combines a
desktop autostart entry with a systemd user service:

```
power on → (optional auto-login) → labwc desktop session
  → ~/.config/autostart/luna-robot-setup.desktop      Exec="…/start-luna" --login
  → ~/.local/share/robot-setup/start-luna
       (--login only) write the boot id to ~/.local/state/robot-setup/autostart-boot-id
       systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XAUTHORITY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP
       systemctl --user reset-failed luna.service
       systemctl --user start luna.service
  → ~/.config/systemd/user/luna.service
       ExecStart=<luna>/.venv/bin/python <luna>/main.py --fullscreen
```

Key decisions:

- **Environment.** `systemctl --user start` does not pass the caller's environment to the
  service, so the launcher imports the display variables first.
- **One startup mechanism.** The unit has no `[Install]` section and is started only by the
  autostart entry. Setup is blocked if another mechanism already starts Luna (for example
  `run-pi.sh` in labwc's autostart, LXDE autostart, `wayfire.ini`, `/etc/xdg/autostart`, or another
  user service), so Luna never runs twice.
- **Crash vs. quit.** `Restart=on-failure` with `RestartSec=5`, `StartLimitIntervalSec=300` and
  `StartLimitBurst=5`. Luna's quit button and Esc/Q exit with code 0, so the service stays stopped
  when the user closes Luna on purpose. A crash restarts it, at most five times in five minutes.
- **Lifetime.** `PartOf=graphical-session.target` stops Luna when the session ends.
- **Logs.** `journalctl --user -u luna.service`.
- **Login marker.** Only the autostart entry passes `--login`, so the recorded boot id proves the
  desktop login started Luna. Starting Luna by hand (or the wizard's screen test) does not write it.
- **Quoting.** Command lines (`ExecStart=`) quote each argument. Path settings
  (`WorkingDirectory=`) are written unquoted, because systemd does not remove quotes there and
  ignores the setting as "not absolute". In both, `%` is escaped as `%%`, so folders with spaces
  work. Folder paths that systemd cannot represent (control characters, leading or trailing spaces)
  block the startup feature.

Automatic login is a separate, opt-in feature because it removes the password prompt on the
device.

## 9. Verification

Verification distinguishes **installed**, **running** and **working**:

| Check | Kind | Pass condition |
|-------|------|----------------|
| Luna can start | auto | Luna's venv imports pygame |
| Speaker / microphone found | auto | a playback / capture device exists (warning if not) |
| Voice packages and models | auto | pinned packages present, English model files present; Swedish model missing is a warning (Luna listens in English) |
| Natural voice engine and voices | auto | pinned `piper-tts`/`onnxruntime`; each voice file has the expected size and SHA-256 |
| Speech from each natural voice | auto | Luna's venv loads each voice with Piper and makes one sentence (timings reported) |
| AI package and settings | auto | openai installed; `.luna.env` is private and has key and model |
| AI key and model | auto | online check succeeded; pending when offline, skipped when turned off |
| Startup files | auto | the three managed files are current |
| Desktop passed to Luna service | auto | the user manager has `WAYLAND_DISPLAY` or `DISPLAY` (desktop only) |
| Luna starts after restart | auto | see the table below; pending until the Pi has restarted since install |
| Desktop auto-login | auto | LightDM logs in the Luna user (only when selected) |
| Screen | user | Luna opens and the user confirms the face is visible |
| Touch | user | user closes Luna with the X button |
| Speaker | user | user hears the `espeak-ng` test sentence |
| Microphone | user | 3-second `arecord` recording is played back and the user hears it (voice feature only) |
| Natural voice | user | one sentence per voice (in its own language) is made with Piper and played with `aplay`; the user confirms all are clear |

Check status values: `passed`, `failed`, `warning`, `skipped`, `pending`. User checks are stored
separately from automatic checks and are never inferred from a package being installed.

The restart check combines the login marker (section 8) with `systemctl --user show luna.service`
(`ActiveState`, `SubState`, `Result`, `NRestarts`, `ExecMainStartTimestamp`):

| Started by login this boot | Service | Result |
|----------------------------|---------|--------|
| no | active | warning: running, but started by hand |
| no | not active | failed: login did not start Luna (is someone signed in?) |
| yes | active, no restarts | passed |
| yes | active after restarts, or restarting | warning: crashed and was restarted |
| yes | failed | failed: crashed until the restart limit |
| yes | inactive, exited successfully | passed: closed by the user |
| yes | never ran | failed: login entry ran but Luna did not start |

## 10. Recovery and rollback

Version 1.0 has **limited rollback**:

- **Retry:** a failed install stops and records the failure. `repair` resumes safely.
- **Backups:** before Robot Setup replaces a file that it did not write (or that the user changed),
  the file is copied to `~/.local/state/robot-setup/backups/<time>/…` and recorded.
- **Ownership:** each managed file is recorded with its sha256. `remove` deletes a file only when it
  still matches, otherwise it keeps the file and says so. Exception: startup files (autostart entry,
  launcher, unit) are always removed, because a kept entry would still start Luna; a changed copy is
  saved to the backups folder first. After removal, Robot Setup searches the autostart locations
  again and reports any remaining entry that can still start Luna instead of claiming success.
- **Existing environments:** an existing `.venv` that Robot Setup did not create is never deleted.
  The user is asked to rename it.
- **Shared packages:** apt packages are never removed, because other software may use them.
- **Login setting:** before enabling auto-login, Robot Setup records the previous login user and
  boot target (`systemctl get-default`). Auto-login for a different user blocks the feature, since
  it cannot be restored safely. `remove` restores the previous mode (desktop with password, B3, or
  console with password, B1) only if the current setting is still what Robot Setup applied (our
  user, graphical target). If someone changed it since, it is left as it is and reported.

Operating system rollback and Luna version rollback are out of scope for 1.0.

## 11. Security

- API key: entered in a password field or read from stdin (`--ai-key-stdin`), kept only in memory,
  written to `~/.luna.env` with mode 0600, and passed to the online check through the environment,
  never on a command line. Error messages report only the exception type.
- No secrets in state, logs or the plan.
- The root helper is fixed, validated and allow-listed (section 7).
- Downloads use apt (signed repositories), pip with pinned versions inside Luna's venv, and Luna's
  own voice-model download script.
- Automatic login is opt-in and clearly explained.

## 12. Luna integration

- Luna is **adopted** from an existing folder that contains `main.py` and `robot_hud/app.py`. The
  default candidates are `~/luna` and `~/ai-robot`. The user can choose another folder.
- Luna's `.venv` is created with `/usr/bin/python3 -m venv --system-site-packages`, so it uses
  apt's `python3-pygame`.
- Pinned Python packages: `vosk==0.3.45`, `sounddevice==0.5.6` (voice), `openai==3.37.0` (AI),
  `piper-tts==1.8.0`, `onnxruntime==1.31.0` (natural voice).
- The listening models (`vosk-model-small-en-us-0.15` and `vosk-model-small-sv-rhasspy-0.15`,
  about 290 MB, MIT, in `~/.ai-robot-models`) are downloaded with Luna's
  `scripts/download-voice-model.py <en|sv>`, one task each (`voice-model`, `voice-model-sv`).
- Natural voices are downloaded by Robot Setup itself to `~/.ai-robot-models/piper/`
  (`<model>.onnx` + `<model>.onnx.json`). The catalogue in `config.PIPER_VOICES` pins every file
  to one `rhasspy/piper-voices` commit with its size and SHA-256. Files are written to `.part`,
  checked, then renamed. Replacing a voice means changing the catalogue only. Voices: English
  `en_US-lessac-medium` (female), `en_US-ryan-high` (male); Swedish `sv_SE-alma-medium`
  (female), `sv_SE-nst-medium` (male). The English voices are provisional (non-commercial
  training data); a licensing review of all voices is required before distribution. Luna falls back to eSpeak when Piper or a voice is missing.
- The AI model has no default. The user enters a model name, which is saved as `LUNA_AI_MODEL`.
  Luna's built-in `DEFAULT_MODEL` is not a verified OpenAI model name and should be fixed in the
  Luna repository. If the online check fails, Luna's offline features keep working.

## 13. Acceptance criteria for 1.0

1. Installs on a Raspberry Pi 5 with Raspberry Pi OS Trixie 64-bit from the `.deb`.
2. Adopts an existing Luna folder without copying or downloading a second Luna.
3. Luna starts full screen after a reboot (with automatic login enabled).
4. Voice and online AI can be installed independently.
5. Luna starts and its offline features work without a network connection.
6. An interrupted install resumes safely with `repair`.
7. Running setup again does not duplicate Luna, its service or its settings.
8. Closing Robot Setup, or removing its package, does not stop Luna.
9. A crash restarts Luna; quitting Luna leaves it closed; repeated crashes stop after the limit.
10. `remove` keeps the Luna folder, `.luna.env`, the voice models and system packages.
11. The API key never appears in the state file, logs or process command lines.
12. The root helper rejects any operation, package or argument not in the allow-list.

## 14. Test strategy

- **Unit tests** (`tests/`, standard `unittest`, Linux and Windows): fake command runner, temporary
  home folder, fake device information and fake elevation. They cover feature resolution, state,
  file ownership and backups, `.luna.env` updates, root helper validation, startup files and
  conflict detection, install order, failure and resume, removal, and the CLI.
- **CI** (GitHub Actions): unit tests on Python 3.11 and 3.13, a compile check, and the `.deb` build
  with a listing of its contents. CI sets `ROBOT_SETUP_REQUIRE_SYSTEMD=1`, so the generated
  `luna.service` (for a folder with a space and `%` in its name) must pass
  `systemd-analyze verify` without any message about the unit; systemd only warns about many bad
  settings, so a warning counts as a failure.
- **Manual test on the Pi** (each release). Add one feature at a time so failures are isolated:
  1. Clean Raspberry Pi OS Trixie: build and install the `.deb`; `robot-setup check`,
     `robot-setup plan --profile minimal`, `robot-setup install --profile minimal --dry-run`.
  2. Minimal install from the desktop wizard; screen and touch checks.
  3. Add offline voice and startup (`robot-setup install --with voice --with startup`); reboot,
     sign in, `robot-setup verify` reports "Started at login and running".
  4. Crash test: `kill -SEGV $(systemctl --user show -p MainPID --value luna.service)` restarts
     Luna and verify warns about the restart; six quick crashes leave it stopped (verify fails).
  5. Quit Luna with the X button: it stays closed and verify reports "closed by the user".
  6. Pull the power during install, then `robot-setup repair`.
  7. Run install twice: the second run reports nothing to do.
  8. Disconnect the network and reboot: Luna starts and the offline voice works.
  9. Natural voice: `robot-setup install --with piper`, then `robot-setup verify --confirm`; all four
     voices make speech (note the timings) and sound clear in English and Swedish. Run `repair` after deleting one
     `.onnx` file: only that file is downloaded again.
  10. Auto-login last: enable it, reboot (no password), then `remove` restores the password login.
  11. `robot-setup remove`, reboot: Luna no longer starts automatically, and the data is kept.

### Assumptions to confirm on the Pi

- labwc on Trixie runs entries from `~/.config/autostart`.
- Trixie still uses LightDM, and `raspi-config nonint do_boot_behaviour B3/B4` still works.
- The pinned Python packages install on the Pi (aarch64 wheels or source builds).
- Piper speed on the Pi 5: the male `high` voice is the slowest; if it is too slow, switch the
  catalogue to `en_US-ryan-medium`.

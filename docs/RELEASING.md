# Robot Setup 1.0 release checklist

Robot Setup is a **one-time installer and repair wizard**, not an additional always-running
Luna service. It must remain compatible with Luna Core 1.x and the older `robot_hud` layout.
**Never merge, tag, publish a Debian package, or deploy without owner approval.**

## CI gate
- Run `python3 -m unittest discover -s tests -v` with Python 3.11, 3.12 and 3.13.
- Require all matrix jobs to pass on Ubuntu x86-64 and ARM Linux.
- Build the Python package and `robot-setup_<version>_all.deb`; validate the installed
  Python CLI, extracted Debian commands, and generated systemd user service.
- Verify that older Luna and modern `luna_core/` folders are both recognized.
- Never make real AI API calls as part of CI or echo API keys into logs.
- `1.0.0rc1` is a release candidate; its Debian version is `1.0.0~rc1`.
  Release only `1.0.0` after the Raspberry Pi checks below.

## Raspberry Pi 5 acceptance gate
1. Record the working Luna and Robot Setup versions, `robot-setup status`, service status
   and startup files. Back up `~/.local/state/robot-setup/`, `~/.luna.env`, the previous
   .deb and Luna's settings and memories before any upgrade. Preserve file permissions.
2. Inspect `systemctl --user cat luna.service`. Only **one installer** should manage it:
   if Robot Setup owns `luna.service`, do not run Luna Core's `install-service.sh`.
3. Install the candidate .deb, run `robot-setup --version`, `robot-setup check`,
   `robot-setup plan --profile standard` and `robot-setup verify`. Confirm that Repair
   does not overwrite a working setup or reinstall pinned packages unnecessarily.
4. Test the real touchscreen, microphone (including the selected audio device), Piper
   speech, offline English/Swedish, online AI key/model, plugins, and boot auto-login.
   Verify Luna starts exactly once at login and continues after Robot Setup exits.
5. Validate a failure case (failed voice download or missing audio), then Repair; confirm
   logs do not contain API keys. Measure Luna CPU/RAM/voice latency before and after.
6. Practice reverting to the previous known-good .deb and re-run `robot-setup verify`.
   Never delete the Luna checkout, `.venv`, voice models, memory, or API key on rollback.

## Stable release
- Update `src/robot_setup/__init__.py` to `1.0.0`, update `CHANGELOG.md`, and confirm
  `pyproject.toml` and Debian control derive the same version.
- After the owner approves and all acceptance checks pass, merge the release PR, add
  the Git tag `v1.0.0`, and publish the Debian artifact with the correct checksum.
- Pin and document supported Luna Core major versions. Keep past .debs available for rollback.

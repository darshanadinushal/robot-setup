# Robot Setup

Robot Setup is a one-time setup application for a Raspberry Pi robot. It installs and configures
[Luna](https://github.com/darshanadinushal/ai-robot) (the robot face, offline voice and optional
online AI) and makes it start automatically. When setup finishes, Robot Setup closes. Luna runs on
its own and does not depend on Robot Setup.

Open it again at any time to repair the installation, add features, check the hardware or undo
its changes.

Later versions will add more features to the same wizard, such as home automation and network
setup.

## Supported device

| Item | Requirement |
|------|-------------|
| Board | Raspberry Pi 5 |
| Operating system | Raspberry Pi OS (64-bit, Trixie) with desktop |
| Python | System Python 3.11 or newer |
| Display | Touch screen (the 7-inch display is the reference) |
| Network | Needed while packages and the voice model download. Luna works offline after that. |

Before running Robot Setup, use Raspberry Pi Imager to set the user name, Wi-Fi and hostname.
Robot Setup detects these settings. It does not change them.

## Features

| Feature | What it does | Profiles |
|---------|--------------|----------|
| Luna | Uses your existing Luna folder, installs system packages and creates Luna's Python environment | Minimal, Standard |
| Offline voice | Installs the speech libraries and downloads the offline speech model | Standard |
| Automatic startup | Starts Luna full screen when the desktop opens and restarts it if it crashes | Standard |
| Online AI | Saves your OpenAI API key and chosen model to `~/.luna.env` and checks them | Optional |
| Automatic login | Opens the desktop without a password after power-on | Optional, off by default |

A profile only sets the default selection. You can still turn individual features on or off.

## Install

1. Download `robot-setup_<version>_all.deb` from the releases page, or build it yourself (see below).
2. Install it:

   ```bash
   sudo apt install ./robot-setup_0.1.0_all.deb
   ```

3. Open **Robot Setup** from the applications menu, or run `robot-setup`.
4. Follow the wizard: device check, features, settings, review, install, hardware check.
5. Restart the Pi. Luna opens automatically, then run `robot-setup verify` once to confirm.

Installing the package only installs the wizard. Nothing on the system changes until you confirm
the plan in the wizard.

## Command line

The command line uses the same engine as the wizard, and also works over SSH.

```bash
robot-setup check                        # check this device
robot-setup plan --profile standard      # show what would change
robot-setup install --profile standard   # install (asks before changing anything)
robot-setup install --dry-run            # show the plan only
robot-setup install --with ai --ai-model <model> --ai-key-stdin < key.txt
robot-setup verify --confirm             # test screen, touch, speaker and microphone
robot-setup status                       # what Robot Setup installed and checked
robot-setup repair                       # resume or fix the saved setup
robot-setup remove                       # undo Robot Setup's changes
```

Useful options: `--profile minimal|standard|custom`, `--with FEATURE`, `--without FEATURE`,
`--luna-dir PATH`, `--yes`, `--allow-unsupported`.

Run Robot Setup as your normal user, not with `sudo`. It asks for administrator permission only
for system changes: through a password window in the desktop, or through `sudo` over SSH.

## What `remove` keeps

`robot-setup remove` deletes only the files Robot Setup created that nobody has changed since.
Startup files are the exception: they are always removed so Luna stops starting automatically,
and a copy you changed is saved in `~/.local/state/robot-setup/backups`. If another entry can
still start Luna, `remove` lists it. Automatic login is put back the way it was, but only if
Robot Setup turned it on and nobody changed it since. It keeps:

- your Luna folder and its `.venv`
- `~/.luna.env` (your API key and settings)
- the downloaded voice model
- system packages, because other programs may use them

To remove Robot Setup itself: `sudo apt remove robot-setup`. Luna keeps working.

## Development

```bash
python3 -m unittest discover -s tests -v   # tests run on Linux and Windows, no Pi needed
PYTHONPATH=src python3 -m robot_setup plan --profile standard --allow-unsupported
ROBOT_SETUP_DEV=1 PYTHONPATH=src python3 -m robot_setup install   # on a Pi, from the source tree
bash packaging/build-deb.sh                # builds build/robot-setup_<version>_all.deb
```

The project has no pip dependencies. The wizard needs `python3-gi` and `gir1.2-gtk-4.0` from apt.

| Environment variable | Purpose |
|----------------------|---------|
| `ROBOT_SETUP_DEV=1` | Run the administrator step from the source tree instead of `/usr/lib/robot-setup` |
| `ROBOT_SETUP_STATE_DIR` | Store setup state somewhere other than `~/.local/state/robot-setup` |
| `ROBOT_SETUP_ALLOW_UNSUPPORTED=1` | Same as `--allow-unsupported` (for testing on other devices) |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design specification.

## Roadmap

| Version | Scope |
|---------|-------|
| 1.0 | Luna, offline voice, online AI, automatic startup, hardware checks, repair and remove |
| 1.x | Audio device selection, log viewer, setup report export |
| 2.0 | Home automation, network setup, camera and sensors |
| Later | Verified Luna releases, signed packages, ready-made Raspberry Pi OS image |

## License

Apache License 2.0. See [LICENSE](LICENSE).

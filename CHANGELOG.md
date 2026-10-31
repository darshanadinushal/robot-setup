# Robot Setup changelog

## 1.0.0rc1 — release candidate (not stable)
- Recognize both Luna Core 1.x (`luna_core/`) and the original `robot_hud/` checkout.
- Read voice and listening-language metadata from `luna_core/` when available, with legacy fallback.
- Preserve the existing one-time wizard, `robot-setup` CLI, user systemd service,
  privacy of API keys, offline voice, repair, and removal behavior.
- Add regression tests for the two Luna layouts and package smoke tests.
- Expand GitHub CI to Python 3.11–3.13 on Linux x86-64 and ARM Linux.
- Make the Debian release-candidate version sortable before final 1.0.0.

## 0.3.0 — 2026-10-25
- Voice and listening-language catalogues are read from Luna's checkout.
- English and Swedish offline voice setup, Piper, autostart, repair and removal.

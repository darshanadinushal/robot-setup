#!/usr/bin/env bash
# Build robot-setup_<version>_all.deb into build/. Needs dpkg-deb (Debian, Ubuntu, Raspberry Pi OS).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c 'import sys; sys.path.insert(0, "src"); import robot_setup; print(robot_setup.__version__)')
# A Python rc release must sort before 1.0.0 in Debian: 1.0.0rc1 -> 1.0.0~rc1.
DEB_VERSION="${VERSION/rc/~rc}"
ROOT="build/deb/robot-setup_${DEB_VERSION}_all"
rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/robot-setup" \
  "$ROOT/usr/share/applications" "$ROOT/usr/share/polkit-1/actions" "$ROOT/usr/share/doc/robot-setup"

cp -r src/robot_setup "$ROOT/usr/lib/robot-setup/"
find "$ROOT" -name '__pycache__' -prune -exec rm -rf {} +
install -m 0755 packaging/bin/robot-setup "$ROOT/usr/bin/robot-setup"
install -m 0755 packaging/bin/robot-setup-system "$ROOT/usr/lib/robot-setup/robot-setup-system"
install -m 0644 packaging/robot-setup.desktop "$ROOT/usr/share/applications/robot-setup.desktop"
install -m 0644 packaging/io.github.darshanadinushal.robot-setup.policy \
  "$ROOT/usr/share/polkit-1/actions/io.github.darshanadinushal.robot-setup.policy"
install -m 0644 LICENSE "$ROOT/usr/share/doc/robot-setup/copyright"
sed "s/@VERSION@/${DEB_VERSION}/" packaging/debian/control.in > "$ROOT/DEBIAN/control"

find "$ROOT/usr" -type d -exec chmod 0755 {} +
find "$ROOT/usr/lib/robot-setup/robot_setup" -type f -exec chmod 0644 {} +

dpkg-deb --root-owner-group --build "$ROOT" "build/robot-setup_${DEB_VERSION}_all.deb"
echo "Built build/robot-setup_${DEB_VERSION}_all.deb"

#!/usr/bin/env bash
# Wrap a built T3 Code AppImage in a .deb that the oklabs-apps host agent
# can install from the OK Forge apt repo.
#
# Usage: .oklabs/build-deb.sh <version> <appimage>
# Output: build/t3code_<version>_amd64.deb
set -euo pipefail

VERSION="${1:?usage: build-deb.sh <version> <appimage>}"
APPIMAGE="${2:?usage: build-deb.sh <version> <appimage>}"
test -f "$APPIMAGE"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKGROOT="$SRC/build/pkgroot"
rm -rf "$PKGROOT"

install -d "$PKGROOT/opt/t3code" \
           "$PKGROOT/usr/bin" \
           "$PKGROOT/usr/share/applications" \
           "$PKGROOT/usr/share/oklabs/t3code" \
           "$PKGROOT/usr/share/doc/t3code"

install -m 0755 "$APPIMAGE" "$PKGROOT/opt/t3code/T3-Code.AppImage"
printf '%s\n' "$VERSION" > "$PKGROOT/opt/t3code/VERSION"
printf 'managed by oklabs-forks t3code package; built from git.oklabs.uk/BeFeast/t3code\n' \
  > "$PKGROOT/opt/t3code/.oklabs-managed"
install -m 0755 "$SRC/.oklabs/t3-code" "$PKGROOT/usr/bin/t3-code"
install -m 0644 "$SRC/.oklabs/t3-code.desktop" "$PKGROOT/usr/share/applications/t3-code.desktop"
install -m 0644 "$SRC/.oklabs/install.yml" "$PKGROOT/usr/share/oklabs/t3code/install.yml"
if [[ -f "$SRC/LICENSE" ]]; then
  install -m 0644 "$SRC/LICENSE" "$PKGROOT/usr/share/doc/t3code/copyright"
fi

INSTALLED_KB="$(du -sk "$PKGROOT" | cut -f1)"
install -d "$PKGROOT/DEBIAN"
cat > "$PKGROOT/DEBIAN/control" <<EOF
Package: t3code
Version: $VERSION
Section: devel
Priority: optional
Architecture: amd64
Maintainer: OK Labs <oleg@befeast.com>
Installed-Size: $INSTALLED_KB
Depends: libfuse2t64 | libfuse2
Homepage: https://git.oklabs.uk/BeFeast/t3code
Description: T3 Code desktop (OK Labs fork)
 Electron desktop client built from the OK Labs fork of pingdotgg/t3code.
 Ships as an AppImage under /opt/t3code; in-app auto-update is disabled.
 .
 The matching server bundle is published to the OK Forge generic registry
 (t3-server) so maestro stays on the same +oklabs version string.
EOF

install -d "$SRC/build"
DEB="$SRC/build/t3code_${VERSION}_amd64.deb"
# version may contain '+'; dpkg-deb is fine with it in the filename
dpkg-deb --build --root-owner-group "$PKGROOT" "$DEB"
echo "$DEB"

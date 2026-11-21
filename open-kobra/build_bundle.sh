#!/bin/sh
# Build the all-in-one USB package: Rinkhals + vanilla-klipper + open-kobra in
# one update.swu (bundle/update.sh, bundle/run.sh).  Run ./build.sh first; the
# open-kobra install package is taken from its output.
#
# Inputs (pinned by SHA-256, never modified except the last line of the
# Rinkhals installer, see below):
#   Rinkhals 20261101_01 update-k2p-k3.swu
#     https://github.com/rinkhals-community/Rinkhals/releases/download/20261101_01/update-k2p-k3.swu
#   vanilla-klipper 2026-06-01 app-vanilla-klipper-k2p-k3.swu
#     https://github.com/Kobra-S1/vanilla-klipper-swu/raw/main/releases/2026-06-01/app-vanilla-klipper-k2p-k3.swu
# Default location vendor/bundle/; override with OPEN_KOBRA_RINKHALS_SWU and
# OPEN_KOBRA_VANILLA_KLIPPER_SWU.

set -eu

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
K3_SWU_PASSWORD="U2FsdGVkX19deTfqpXHZnB5GeyQ/dtlbHjkUnwgCi+w="

RINKHALS_SWU="${OPEN_KOBRA_RINKHALS_SWU:-$PROJECT_ROOT/vendor/bundle/update-k2p-k3.swu}"
RINKHALS_SHA256=b4b38811f8e5909c1bb34224fd6008e36b0f29e2b5b8ea5dfd248cc38001b44c
RINKHALS_URL=https://github.com/rinkhals-community/Rinkhals/releases/download/20261101_01/update-k2p-k3.swu
VK_SWU="${OPEN_KOBRA_VANILLA_KLIPPER_SWU:-$PROJECT_ROOT/vendor/bundle/app-vanilla-klipper-k2p-k3.swu}"
VK_SHA256=64171e66c2972b1dcc2394591799163ed286e27aa0382cdcd83e4d6c282c6eb5
VK_URL=https://github.com/Kobra-S1/vanilla-klipper-swu/raw/main/releases/2026-06-01/app-vanilla-klipper-k2p-k3.swu

DIST_DIR="${OPEN_KOBRA_DIST_DIR:-$PROJECT_ROOT/dist}"
INSTALL_SWU="$DIST_DIR/install/aGVscF9zb3Nf/update.swu"
BUNDLE_SWU="$DIST_DIR/bundle/aGVscF9zb3Nf/update.swu"

die() {
    printf 'HATA: %s\n' "$*" >&2
    exit 1
}

for TOOL in tar gzip zip unzip md5sum sha256sum; do
    command -v "$TOOL" >/dev/null 2>&1 || die "Gerekli araç bulunamadı: $TOOL"
done

check_input() {
    [ -f "$1" ] || die "$1 yok. İndirin: $3"
    ACT=$(sha256sum "$1" | awk '{print $1}')
    [ "$ACT" = "$2" ] || die "$1 SHA-256 uyuşmuyor ($ACT != $2)"
}
check_input "$RINKHALS_SWU" "$RINKHALS_SHA256" "$RINKHALS_URL"
check_input "$VK_SWU" "$VK_SHA256" "$VK_URL"
[ -f "$INSTALL_SWU" ] || die "$INSTALL_SWU yok; önce ./build.sh çalıştırın."

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
PAYLOAD="$WORK/payload"
mkdir -p "$PAYLOAD/bundle"

unpack_swu() {
    mkdir -p "$2"
    unzip -q -P "$K3_SWU_PASSWORD" "$1" -d "$WORK/zip.$$"
    tar -xzf "$WORK/zip.$$/update_swu/setup.tar.gz" -C "$2"
    rm -rf "$WORK/zip.$$"
}

# Rinkhals at the payload root: its installer reads /useremain/update_swu itself
unpack_swu "$RINKHALS_SWU" "$PAYLOAD"
RINKHALS_VERSION=$(tr -d ' \t\r\n' < "$PAYLOAD/.version")
# The Rinkhals installer ends with a single bare "reboot"; it becomes "exit 0" so
# vanilla-klipper and open-kobra are installed before the one reboot of run.sh.
LAST_LINE=$(awk 'NF { last=$0 } END { print last }' "$PAYLOAD/update.sh")
REBOOTS=$(awk '/^[[:space:]]*reboot[[:space:]]*$/ { n++ } END { print n + 0 }' "$PAYLOAD/update.sh")
[ "$LAST_LINE" = "reboot" ] && [ "$REBOOTS" = "1" ] \
    || die "Rinkhals update.sh beklenen biçimde değil (son satır '$LAST_LINE', reboot satırı $REBOOTS)"
sed 's/^reboot$/exit 0/' "$PAYLOAD/update.sh" > "$PAYLOAD/rinkhals-update.sh"
rm -f "$PAYLOAD/update.sh"
chmod 755 "$PAYLOAD/rinkhals-update.sh"

VK_TMP="$WORK/vk"
unpack_swu "$VK_SWU" "$VK_TMP"
[ -f "$VK_TMP/vanilla-klipper/app.sh" ] && [ -f "$VK_TMP/vanilla-klipper/klippy/klippy.py" ] \
    || die "vanilla-klipper paketi beklenen içerikte değil"
mv "$VK_TMP/vanilla-klipper" "$PAYLOAD/bundle/vanilla-klipper"
VK_VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PAYLOAD/bundle/vanilla-klipper/app.json" | head -n 1)

unpack_swu "$INSTALL_SWU" "$PAYLOAD/bundle/open-kobra"
[ -f "$PAYLOAD/bundle/open-kobra/update.sh" ] || die "open-kobra kurulum paketi beklenen içerikte değil"
OK_VERSION=$(tr -d ' \t\r\n' < "$PAYLOAD/bundle/open-kobra/VERSION")

# numpy as a tar.gz: before Rinkhals has booted there is no python3 or unzip
# on the printer (they come from Rinkhals' /usr), but BusyBox tar is there
NP_WHEEL=$(ls "$PAYLOAD/bundle/open-kobra/python"/numpy-*.whl 2>/dev/null | head -n 1)
[ -n "$NP_WHEEL" ] || die "open-kobra paketinde numpy wheel yok"
mkdir -p "$WORK/np"
unzip -q "$NP_WHEEL" -d "$WORK/np"
[ -f "$WORK/np/numpy/__init__.py" ] || die "numpy wheel beklenen içerikte değil"
printf 'installed-by-open-kobra\n' > "$WORK/np/numpy/.open-kobra-installed"
(cd "$WORK/np" && tar --owner=0 --group=0 --numeric-owner -czf "$PAYLOAD/bundle/numpy.tar.gz" numpy numpy-*.dist-info)

cp "$PROJECT_ROOT/bundle/update.sh" "$PAYLOAD/update.sh"
cp "$PROJECT_ROOT/bundle/run.sh" "$PAYLOAD/bundle/run.sh"
chmod 755 "$PAYLOAD/update.sh" "$PAYLOAD/bundle/run.sh"
printf 'RINKHALS=%s\nVANILLA_KLIPPER=%s\nOPEN_KOBRA=%s\n' "$RINKHALS_VERSION" "$VK_VERSION" "$OK_VERSION" \
    > "$PAYLOAD/bundle/VERSIONS"

(cd "$PAYLOAD/bundle" && find . -type f ! -name MANIFEST.md5 | LC_ALL=C sort | while IFS= read -r F; do
    md5sum "$F"
done > MANIFEST.md5)

mkdir -p "$WORK/pack/update_swu"
(cd "$PAYLOAD" && tar --owner=0 --group=0 --numeric-owner -cf "$WORK/pack/update_swu/setup.tar" .)
gzip -n "$WORK/pack/update_swu/setup.tar"
md5sum "$WORK/pack/update_swu/setup.tar.gz" | awk '{ print $1 }' > "$WORK/pack/update_swu/setup.tar.gz.md5"
rm -rf "$DIST_DIR/bundle"
mkdir -p "$(dirname "$BUNDLE_SWU")"
(cd "$WORK/pack" && zip -0 -q -P "$K3_SWU_PASSWORD" -r "$BUNDLE_SWU" update_swu)

printf 'Hepsi bir arada paket: %s (Rinkhals %s, vanilla-klipper %s, open-kobra %s)\n' \
    "$BUNDLE_SWU" "$RINKHALS_VERSION" "$VK_VERSION" "$OK_VERSION"

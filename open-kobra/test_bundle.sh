#!/bin/sh
# End-to-end test of the all-in-one package (dist/bundle) on a simulated stock
# Kobra 3: the real Rinkhals, vanilla-klipper and open-kobra installers run in a
# bubblewrap sandbox where /useremain, /userdata, /mnt and /tmp are fake
# directories.  Run ./build.sh and ./build_bundle.sh first.

set -eu

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
K3_SWU_PASSWORD="U2FsdGVkX19deTfqpXHZnB5GeyQ/dtlbHjkUnwgCi+w="
BUNDLE_SWU="${OPEN_KOBRA_DIST_DIR:-$PROJECT_ROOT/dist}/bundle/aGVscF9zb3Nf/update.swu"

fail() {
    printf '  [FAIL] %s\n' "$1" >&2
    exit 1
}
pass() {
    printf '  [OK] %s\n' "$1"
}

command -v bwrap >/dev/null 2>&1 || { printf '  [SKIP] bwrap yok\n'; exit 0; }
[ -f "$BUNDLE_SWU" ] || fail "$BUNDLE_SWU yok; önce ./build.sh ve ./build_bundle.sh"

# Not on tmpfs: the Rinkhals installer reads the third df column, which is "Used"
# unless the device name wraps (as on the printer), and wants 500 MB of it
WORK=$(mktemp -d "${OPEN_KOBRA_BUNDLE_TEST_DIR:-/var/tmp}/okbundle.XXXXXX")
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT INT TERM

# A stock Kobra 3 on firmware 2.4.6.7 with the bundle on the USB stick
make_stock_root() {
    R="$1"
    mkdir -p "$R/useremain/dev" "$R/useremain/app/gk/cert1" "$R/useremain/app/gk/cert3" "$R/useremain/app/gk/gcodes" \
        "$R/userdata/app/gk/config" "$R/mnt/udisk/aGVscF9zb3Nf" "$R/tmp"
    printf '2.4.6.7\n' > "$R/useremain/dev/version"
    printf '{"modelId": "20024"}\n' > "$R/userdata/app/gk/config/api.cfg"
    printf '[device]\nsn=TEST\n' > "$R/userdata/app/gk/config/device.ini"
    printf '{}\n' > "$R/userdata/app/gk/config/device_account.json"
    printf 'cert\n' > "$R/useremain/app/gk/cert1/a.pem"
    cp "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh" "$R/userdata/app/gk/start.sh"
    printf '#!/bin/sh\n# stock restart_k3c.sh\n' > "$R/userdata/app/gk/restart_k3c.sh"
    printf '# stock printer.cfg\n[printer]\nkinematics: cartesian\n' > "$R/userdata/app/gk/printer.cfg"
    cp "$BUNDLE_SWU" "$R/mnt/udisk/aGVscF9zb3Nf/update.swu"
    mkdir -p "$R/systty"
}

# The ACE Pro as the kernel shows it: /sys/class/tty/ttyACM0/device/../product = ACE
plug_ace() {
    mkdir -p "$1/systty/ttyACM0/device"
    printf 'ACE\n' > "$1/systty/ttyACM0/product"
}

# Commands of the stock 2.4.6.7 root filesystem (BusyBox applets and /bin, /usr/bin,
# /sbin, /usr/sbin).  Before Rinkhals has booted nothing else is on PATH: no cmp,
# python3, unzip, zip or stat.  The sandbox only sees these (host versions).
STOCK_TOOLS="ash awk basename cat chmod chown cp cut date dd df dirname du echo env expr false find flock
free grep gzip head hostname id kill killall ln ls md5sum mkdir mkfifo mknod mktemp mount mv nice nohup
printenv ps pwd readlink realpath reboot renice rm sed seq setsid sh sleep sort sync tail tar tee test
timeout touch tr true truncate umount uname uniq unlink usleep wc which xargs yes"
STOCK_BIN="$WORK/stockbin"
mkdir -p "$STOCK_BIN"
for T in $STOCK_TOOLS; do
    P=$(command -v "$T" 2>/dev/null) || continue
    case "$P" in /*) ln -s "$P" "$STOCK_BIN/$T" ;; esac
done
if [ ! -e "$STOCK_BIN/usleep" ]; then
    printf '#!/bin/sh\nexit 0\n' > "$STOCK_BIN/usleep"
    chmod +x "$STOCK_BIN/usleep"
fi

# What the stock updater does with update.swu, then run update.sh in the sandbox
run_bundle() {
    R="$1"
    rm -rf "$R/useremain/update_swu"
    unzip -q -P "$K3_SWU_PASSWORD" "$R/mnt/udisk/aGVscF9zb3Nf/update.swu" -d "$R/useremain"
    tar -xzf "$R/useremain/update_swu/setup.tar.gz" -C "$R/useremain/update_swu"
    rm -f "$R/useremain/update_swu/setup.tar.gz" "$R/useremain/update_swu/setup.tar.gz.md5"
    [ -z "${2:-}" ] || "$2" "$R"
    bwrap --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/sbin /sbin \
        --symlink usr/lib /lib --symlink usr/lib64 /lib64 --ro-bind /etc /etc \
        --proc /proc --dev /dev \
        --bind "$R/useremain" /useremain --bind "$R/userdata" /userdata \
        --bind "$R/mnt" /mnt --bind "$R/tmp" /tmp \
        --ro-bind "$STOCK_BIN" /stockbin --setenv PATH /stockbin \
        --ro-bind "$R/systty" /systty --setenv OPEN_KOBRA_BUNDLE_SYS_TTY /systty \
        --setenv OPEN_KOBRA_BUNDLE_SYNC 1 \
        --setenv OPEN_KOBRA_BUNDLE_REBOOT "touch /tmp/rebooted" \
        /stockbin/sh /useremain/update_swu/update.sh > "$R/run.log" 2>&1
}

has_line() {
    awk -v t="$2" '$0 == t { f=1; exit } END { exit !f }' "$1"
}

echo "1. Stok Kobra 3'e hepsi bir arada kurulum..."
A="$WORK/fresh"
make_stock_root "$A"
plug_ace "$A"
run_bundle "$A" || { tail -30 "$A/run.log"; fail "Kurulum başarısız oldu"; }
RV=$(cat "$A/useremain/rinkhals/.version")
[ -e "$A/useremain/rinkhals/$RV/usr/bin/python" ] && [ -d "$A/useremain/rinkhals/$RV/home/rinkhals/apps/40-moonraker" ] \
    || fail "Rinkhals kurulmadı ($RV)"
grep -q "Rinkhals/begin" "$A/userdata/app/gk/start.sh" || fail "Rinkhals yükleyicisi start.sh'e eklenmedi"
# The boot repair hook needs the stock start.sh check (cmp), which works without Rinkhals' tools
grep -q "open-kobra-rescue/begin" "$A/userdata/app/gk/start.sh" || fail "Açılış kurtarma kancası eklenmedi (stok araçlarla start.sh denetimi)"
VK="$A/useremain/home/rinkhals/apps/vanilla-klipper"
[ -x "$VK/app.sh" ] && [ -f "$VK/klippy/klippy.py" ] && [ -f "$VK/.enabled" ] || fail "vanilla-klipper kurulmadı veya etkin değil"
[ -f "$VK/klippy/extras/leviq3.py" ] && [ -f "$VK/klippy/extras/ace/__init__.py" ] || fail "leviq3 / ACE sürücüsü vanilla-klipper'a kurulmadı"
[ -f "$VK/lib/python3.11/site-packages/numpy/__init__.py" ] && [ -f "$VK/lib/python3.11/site-packages/numpy/.open-kobra-installed" ] \
    || fail "numpy kurulmadı"
# numpy comes from the bundle's tar (no python3 needed), the open-kobra installer then skips it
grep -q "numpy kuruldu\." "$A/useremain/open-kobra-bundle-install.log" || fail "numpy paketin tar'ından kurulmadı"
[ ! -e "$VK/lib/python3.11/.open-kobra-numpy."* ] 2>/dev/null || fail "numpy geçici dizini kaldı"
H="$A/useremain/home/rinkhals"
[ -f "$H/.open-kobra-version" ] || fail "open-kobra sürüm işareti yok"
[ -f "$H/apps/kobra-screen/.enabled" ] && [ ! -f "$H/apps/kobra-screen/.disabled" ] || fail "kobra-screen etkin değil"
[ -f "$H/apps/05-open-kobra-guard/.enabled" ] || fail "Açılış koruması etkin değil"
CFG="$H/printer_data/config/printer.klipper.cfg"
has_line "$CFG" "[include leviq3.cfg]" && has_line "$CFG" "[include open_kobra_homing.cfg]" || fail "printer.klipper.cfg include satırları yok"
has_line "$CFG" "[include ace_K3.cfg]" && ! has_line "$CFG" "#[include ace_K3.cfg]" || fail "ACE takılıyken [include ace_K3.cfg] açılmadı"
[ "$(cat "$H/apps/05-open-kobra-guard/leviq3.py.md5")" = "$(md5sum "$VK/klippy/extras/leviq3.py" | awk '{print $1}')" ] \
    || fail "Koruma md5'i kurulan leviq3.py ile uyuşmuyor"
[ ! -e "$A/useremain/update_swu" ] && [ ! -e "$A/useremain/open-kobra-bundle" ] || fail "Geçici kurulum dizinleri kaldı"
[ ! -e "$A/mnt/udisk/aGVscF9zb3Nf/update.swu" ] || fail "USB update.swu silinmedi (her açılışta yeniden kurulurdu)"
[ -f "$A/tmp/rebooted" ] || fail "Kurulum sonunda yeniden başlatılmadı"
grep -q "Kurulum tamamlandı" "$A/useremain/open-kobra-bundle-install.log" || fail "Kurulum günlüğü tamamlandı demiyor"
grep -q "Kurulum tamamlandı" "$A/mnt/udisk/aGVscF9zb3Nf/open-kobra-bundle-install.log" || fail "USB günlüğü yazılmadı"
pass "Rinkhals, vanilla-klipper ve open-kobra tek kurulumda kuruldu, kobra-screen etkin, tek yeniden başlatma"

echo "2. Aynı paket ikinci kez (Rinkhals zaten kurulu)..."
cp "$BUNDLE_SWU" "$A/mnt/udisk/aGVscF9zb3Nf/update.swu"
rm -f "$A/tmp/rebooted"
printf '\n[gcode_macro USER_MACRO]\ngcode: M117 hi\n' >> "$CFG"
run_bundle "$A" || { tail -30 "$A/run.log"; fail "İkinci kurulum başarısız oldu"; }
grep -q "zaten kurulu, atlanıyor" "$A/useremain/open-kobra-bundle-install.log" || fail "Kurulu Rinkhals yeniden kurulmamalıydı"
[ "$(cat "$A/useremain/rinkhals/.version")" = "$RV" ] || fail "Rinkhals sürüm dizini değişti"
[ "$(grep -c "Rinkhals/begin" "$A/userdata/app/gk/start.sh")" = "1" ] || fail "Rinkhals yükleyicisi iki kez eklendi"
grep -q "USER_MACRO" "$CFG" || fail "Kullanıcının printer.klipper.cfg eklemesi silindi"
[ -f "$A/tmp/rebooted" ] || fail "İkinci kurulumda yeniden başlatılmadı"
pass "Kurulu Rinkhals atlandı, kullanıcı ayarı korundu"

echo "2b. ACE Pro takılı değilken..."
N="$WORK/no_ace"
make_stock_root "$N"
run_bundle "$N" || { tail -30 "$N/run.log"; fail "ACE'siz kurulum başarısız oldu"; }
has_line "$N/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "#[include ace_K3.cfg]" \
    || fail "ACE yokken #[include ace_K3.cfg] kapalı kalmalıydı"
pass "ACE yokken ace_K3.cfg include'u kapalı kaldı"

untouched() {
    R="$1"
    [ ! -e "$R/useremain/rinkhals" ] || fail "$2: Rinkhals kurulmamalıydı"
    [ ! -e "$R/useremain/home" ] || fail "$2: uygulama kurulmamalıydı"
    [ ! -e "$R/useremain/open-kobra-bundle" ] || fail "$2: geçici dizin kaldı"
    ! grep -q "Rinkhals/begin" "$R/userdata/app/gk/start.sh" || fail "$2: start.sh değişmemeliydi"
    [ ! -f "$R/tmp/rebooted" ] || fail "$2: yeniden başlatılmamalıydı"
    grep -q "Hiçbir şey değiştirilmedi" "$R/mnt/udisk/aGVscF9zb3Nf/open-kobra-bundle-install.log" || fail "$2: USB günlüğünde neden yok"
}

echo "3. Yanlış model, yanlış firmware, bozuk paket..."
B="$WORK/model"
make_stock_root "$B"
printf '{"modelId": "20025"}\n' > "$B/userdata/app/gk/config/api.cfg"
if run_bundle "$B"; then fail "Yanlış modelde kurulum başarısız olmalıydı"; fi
untouched "$B" "yanlış model"
C="$WORK/fw"
make_stock_root "$C"
printf '2.4.5\n' > "$C/useremain/dev/version"
if run_bundle "$C"; then fail "Denenmemiş firmware'de kurulum başarısız olmalıydı"; fi
untouched "$C" "yanlış firmware"
corrupt() { printf '# changed\n' >> "$1/useremain/update_swu/bundle/run.sh"; }
D="$WORK/corrupt"
make_stock_root "$D"
if run_bundle "$D" corrupt; then fail "Bozuk pakette kurulum başarısız olmalıydı"; fi
untouched "$D" "bozuk paket"
pass "Model, firmware ve bütünlük hatalarında hiçbir şeye dokunulmadı"

echo "4. Rinkhals kurucusu yarıda kalırsa..."
break_rinkhals() { rm -rf "$1/useremain/update_swu/rinkhals/opt"; }
E="$WORK/rinkhals_fail"
make_stock_root "$E"
if run_bundle "$E" break_rinkhals; then fail "Rinkhals kopyası eksikken kurulum başarısız olmalıydı"; fi
[ ! -f "$E/useremain/rinkhals/.version" ] || fail "Yarım Rinkhals .version'a yazılmamalıydı"
! grep -q "Rinkhals/begin" "$E/userdata/app/gk/start.sh" || fail "Yarım Rinkhals için start.sh değişmemeliydi"
[ ! -e "$E/useremain/home/rinkhals/apps/vanilla-klipper" ] || fail "Rinkhals olmadan vanilla-klipper kurulmamalıydı"
[ ! -f "$E/tmp/rebooted" ] || fail "Rinkhals hatasında yeniden başlatılmamalıydı"
grep -q "Rinkhals kurulamadı" "$E/useremain/open-kobra-bundle-install.log" || fail "Rinkhals hatası günlüğe yazılmadı"
pass "Rinkhals hatasında yazıcı stok yazılımıyla kaldı"

echo "Hepsi bir arada paket testleri geçti."

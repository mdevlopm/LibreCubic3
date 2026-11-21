#!/bin/sh
# Second half of the all-in-one installer, run from /useremain/open-kobra-bundle:
# 1. Rinkhals (its own installer, unchanged except that it does not reboot),
#    skipped when the same Rinkhals version is already installed
# 2. vanilla-klipper (copied and enabled like its own installer, plus .enabled)
# 3. open-kobra (its own installer), kobra-screen enabled
# 4. one reboot
STAGE=$(cd "$(dirname "$0")" && pwd)
SRC=/useremain/update_swu
USB_DIR=/mnt/udisk/aGVscF9zb3Nf
LOG_FILE=/useremain/open-kobra-bundle-install.log
APPS=/useremain/home/rinkhals/apps
REBOOT_CMD="${OPEN_KOBRA_BUNDLE_REBOOT:-reboot}"

log_msg() {
    LINE="$(date '+%Y-%m-%d %H:%M:%S'): $*"
    printf '%s\n' "$LINE"
    printf '%s\n' "$LINE" >> "$LOG_FILE" 2>/dev/null || true
    if [ -d "$USB_DIR" ]; then
        printf '%s\n' "$LINE" >> "$USB_DIR/open-kobra-bundle-install.log" 2>/dev/null || true
    fi
}

beep() {
    PWM_FILE=/sys/class/pwm/pwmchip0/pwm0/enable
    if [ -w "$PWM_FILE" ]; then
        echo 1 > "$PWM_FILE" 2>/dev/null || true
        usleep $(($1 * 1000)) 2>/dev/null || sleep 1
        echo 0 > "$PWM_FILE" 2>/dev/null || true
    fi
}

beep_error() {
    beep 300; usleep 300000 2>/dev/null || sleep 1
    beep 300; usleep 300000 2>/dev/null || sleep 1
    beep 300
}

versions_value() {
    sed -n "s/^$1=//p" "$STAGE/VERSIONS" | head -n 1
}

# Rinkhals is ready to boot: .version names an install with the bundled
# version, the files its installer checks, and the loader in start.sh
rinkhals_ready() {
    CUR=$(tr -d ' \t\r\n' < /useremain/rinkhals/.version 2>/dev/null)
    [ -n "$CUR" ] || return 1
    DIR=/useremain/rinkhals/$CUR
    [ "$(tr -d ' \t\r\n' < "$DIR/.version" 2>/dev/null)" = "$RINKHALS_VERSION" ] || return 1
    [ -e "$DIR/usr/bin/python" ] && [ -d "$DIR/home/rinkhals" ] && [ -d "$DIR/opt/rinkhals" ] || return 1
    grep -q "Rinkhals/begin" /userdata/app/gk/start.sh 2>/dev/null
}

finish() {
    rm -rf "$STAGE" 2>/dev/null || true
    rm -rf "$SRC" 2>/dev/null || true
    rm -f "$USB_DIR/update.swu" 2>/dev/null || true
    sync
    log_msg "$1"
    log_msg "Yeniden başlatılıyor."
    $REBOOT_CMD
    exit "$2"
}

# numpy for SHAPER_CALIBRATE.  The open-kobra installer unpacks it with python3,
# which only exists once Rinkhals has booted; it skips an installed numpy.
install_numpy() {
    SITE="$1"
    if [ ! -d "$SITE" ]; then
        log_msg "UYARI: $SITE yok, numpy kurulmadı."
        return 0
    fi
    if [ -f "$SITE/numpy/__init__.py" ]; then
        log_msg "numpy zaten kurulu."
        return 0
    fi
    NP_TMP="$SITE/../.open-kobra-numpy.$$"
    rm -rf "$NP_TMP"
    if mkdir -p "$NP_TMP" && tar -xzf "$STAGE/numpy.tar.gz" -C "$NP_TMP" \
            && [ -f "$NP_TMP/numpy/__init__.py" ] && ls -d "$NP_TMP"/numpy-*.dist-info >/dev/null 2>&1; then
        rm -rf "$SITE/numpy" "$SITE"/numpy-*.dist-info
        mv "$NP_TMP/numpy" "$SITE/numpy" && mv "$NP_TMP"/numpy-*.dist-info "$SITE/"
        sync
        log_msg "numpy kuruldu."
    else
        log_msg "UYARI: numpy açılamadı; Input Shaper için normal paketi sonradan bir kez daha kurun."
    fi
    rm -rf "$NP_TMP"
}

# With an ACE Pro on USB at install time, turn on vanilla-klipper's
# "#[include ace_K3.cfg]" (its own installation guide has the user do it by hand)
enable_ace_include() {
    CFG=/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg
    ACE_FOUND=0
    for T in "${OPEN_KOBRA_BUNDLE_SYS_TTY:-/sys/class/tty}"/ttyACM*; do
        [ "$(cat "$T/device/../product" 2>/dev/null)" = "ACE" ] && ACE_FOUND=1
    done
    if [ "$ACE_FOUND" != "1" ]; then
        log_msg "ACE Pro bulunamadı; printer.klipper.cfg içindeki #[include ace_K3.cfg] kapalı bırakıldı."
        return 0
    fi
    awk '$0 == "#[include ace_K3.cfg]" { f = 1; exit } END { exit !f }' "$CFG" 2>/dev/null || return 0
    if sed 's/^#\[include ace_K3\.cfg\]$/[include ace_K3.cfg]/' "$CFG" > "$CFG.bundle.tmp" && mv "$CFG.bundle.tmp" "$CFG"; then
        log_msg "ACE Pro bulundu; [include ace_K3.cfg] açıldı."
    else
        rm -f "$CFG.bundle.tmp"
        log_msg "UYARI: [include ace_K3.cfg] açılamadı; Mainsail'den elle açın."
    fi
}

RINKHALS_VERSION=$(versions_value RINKHALS)
VK_VERSION=$(versions_value VANILLA_KLIPPER)

# 1. Rinkhals
if rinkhals_ready; then
    log_msg "Rinkhals $RINKHALS_VERSION zaten kurulu, atlanıyor."
else
    log_msg "Rinkhals $RINKHALS_VERSION kuruluyor (Rinkhals kurucusu, ilerleme ekranda)..."
    sh "$SRC/rinkhals-update.sh" async
    RC=$?
    if [ "$RC" -ne 0 ] || ! rinkhals_ready; then
        # The Rinkhals installer stops before it switches .version or patches
        # start.sh, so the printer still boots the firmware it had
        log_msg "HATA: Rinkhals kurulamadı (çıkış $RC). Ayrıntı: /useremain/rinkhals/install.log"
        log_msg "vanilla-klipper ve open-kobra kurulmadı; yazıcı önceki yazılımıyla açılır."
        rm -rf "$STAGE" "$SRC" 2>/dev/null || true
        sync
        beep_error
        exit 1
    fi
    log_msg "Rinkhals $RINKHALS_VERSION kuruldu."
fi
rm -rf "$SRC" 2>/dev/null || true

# 2. vanilla-klipper
VK_DEST=$APPS/vanilla-klipper
log_msg "vanilla-klipper $VK_VERSION kuruluyor..."
if mkdir -p "$VK_DEST" && cp -r "$STAGE/vanilla-klipper/"* "$VK_DEST/" \
        && chmod +x "$VK_DEST/app.sh" && [ -f "$VK_DEST/klippy/klippy.py" ]; then
    rm -f "$VK_DEST/.disabled" "$APPS/vanilla-klipper.disabled" 2>/dev/null || true
    : > "$VK_DEST/.enabled"
    sync
    log_msg "vanilla-klipper kuruldu ve etkinleştirildi."
    install_numpy "$VK_DEST/lib/python3.11/site-packages"
else
    beep_error
    finish "HATA: vanilla-klipper kopyalanamadı; open-kobra kurulmadı. Rinkhals kurulu, yazıcı stok Klipper ile açılır." 1
fi

# 3. open-kobra
log_msg "open-kobra kuruluyor..."
if sh "$STAGE/open-kobra/update.sh"; then
    rm -f "$APPS/kobra-screen/.disabled" "$APPS/kobra-screen.disabled" 2>/dev/null || true
    [ -d "$APPS/kobra-screen" ] && : > "$APPS/kobra-screen/.enabled"
    log_msg "open-kobra kuruldu; kobra-screen etkinleştirildi."
    enable_ace_include
else
    # The open-kobra installer rolls itself back on failure
    beep_error
    finish "HATA: open-kobra kurulamadı ve geri alındı (ayrıntı: open-kobra-install.log). Rinkhals ve vanilla-klipper kurulu." 1
fi

finish "Kurulum tamamlandı: Rinkhals $RINKHALS_VERSION, vanilla-klipper $VK_VERSION, open-kobra $(versions_value OPEN_KOBRA)." 0

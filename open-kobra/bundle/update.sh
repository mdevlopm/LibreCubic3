#!/bin/sh
# All-in-one USB installer: Rinkhals, vanilla-klipper and open-kobra in one update.
# The stock updater unpacks the package to /useremain/update_swu and runs this file.
# The Rinkhals installer deletes /useremain/update_swu when it finishes, so the
# rest of the package is first copied to /useremain/open-kobra-bundle and the
# installation continues from there (run.sh).
if [ "$1" != "async" ] && [ -z "${OPEN_KOBRA_BUNDLE_SYNC:-}" ]; then
    nohup "$0" async >/dev/null 2>&1 &
    exit 0
fi

SRC=/useremain/update_swu
STAGE=/useremain/open-kobra-bundle
USB_DIR=/mnt/udisk/aGVscF9zb3Nf
LOG_FILE=/useremain/open-kobra-bundle-install.log
FW_REQUIRED=2.4.6.7

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

# Nothing has been changed yet: three short beeps, the printer keeps its firmware
fail_untouched() {
    log_msg "HATA: $*"
    log_msg "Hiçbir şey değiştirilmedi."
    rm -rf "$STAGE" 2>/dev/null || true
    beep 300; usleep 300000 2>/dev/null || sleep 1
    beep 300; usleep 300000 2>/dev/null || sleep 1
    beep 300
    exit 1
}

verify_manifest() {
    DIR="$1"
    [ -f "$DIR/MANIFEST.md5" ] || return 1
    (cd "$DIR" && md5sum -c -s MANIFEST.md5) 2>/dev/null && return 0
    # BusyBox md5sum without -s
    (cd "$DIR" && md5sum -c MANIFEST.md5 >/dev/null 2>&1)
}

log_msg "Hepsi bir arada kurulum başlıyor ($(cat "$SRC/bundle/VERSIONS" 2>/dev/null | tr '\n' ' '))"

MODEL_ID=$(sed -n 's/.*"modelId"[[:space:]]*:[[:space:]]*"*\([0-9][0-9]*\)"*.*/\1/p' /userdata/app/gk/config/api.cfg 2>/dev/null | head -n 1)
[ "$MODEL_ID" = "20024" ] || fail_untouched "Bu paket yalnız Anycubic Kobra 3 içindir (modelId 20024, bulunan: '${MODEL_ID:-yok}')."

FW_VER=$(tr -d ' \t\r\n' < /useremain/dev/version 2>/dev/null)
[ "$FW_VER" = "$FW_REQUIRED" ] || fail_untouched "Yalnız stok firmware $FW_REQUIRED ile denendi (bulunan: '${FW_VER:-yok}'). Önce resmi $FW_REQUIRED güncellemesini kurun."

for REQ in "$SRC/rinkhals-update.sh" "$SRC/.version" "$SRC/rinkhals/tools.sh" "$SRC/start.sh.patch" "$SRC/bundle/run.sh"; do
    [ -s "$REQ" ] || fail_untouched "Paket eksik: $REQ"
done
verify_manifest "$SRC/bundle" || fail_untouched "Paket bütünlük denetimi (MD5) geçmedi; USB'ye yeniden kopyalayın."

# Available is the field before "Use%" (BusyBox puts a long device name on a line of its own)
FREE_KB=$(df -k /useremain 2>/dev/null | awk 'NR > 1 { for (i = 2; i <= NF; i++) if ($i ~ /%$/) { print $(i - 1); exit } }')
case "$FREE_KB" in
    ''|*[!0-9]*) fail_untouched "/useremain boş alanı okunamadı." ;;
esac
if [ "$FREE_KB" -lt 700000 ]; then
    fail_untouched "/useremain üzerinde yeterli boş alan yok (en az 700 MB gerekli, boş: ${FREE_KB} KB)."
fi

rm -rf "$STAGE"
if ! cp -r "$SRC/bundle" "$STAGE" || ! verify_manifest "$STAGE"; then
    fail_untouched "Kurulum dosyaları $STAGE içine kopyalanamadı."
fi
sync
exec sh "$STAGE/run.sh"

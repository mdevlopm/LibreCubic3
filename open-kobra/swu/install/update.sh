#!/bin/sh
# open-kobra installer update.sh (runs on Kobra 3 via Rinkhals / SWU updater)
# Set OPEN_KOBRA_ROOT for safe local testing against a simulated rootfs.

ROOT="${OPEN_KOBRA_ROOT:-}"
while [ -n "$ROOT" ] && [ "${ROOT%/}" != "$ROOT" ]; do
    ROOT="${ROOT%/}"
done
UPDATE_PATH=$(cd "$(dirname "$0")" && pwd)

USB_DIR="$ROOT/mnt/udisk/aGVscF9zb3Nf"
USB_ROOT="$ROOT/mnt/udisk"
RINKHALS_DIR="$ROOT/useremain/rinkhals"
RINKHALS_HOME="$ROOT/useremain/home/rinkhals"
USER_APPS_DIR="$RINKHALS_HOME/apps"
CONFIG_DIR="$RINKHALS_HOME/printer_data/config"
BAK_BASE="$ROOT/useremain/bak"
BASELINE_DIR="$BAK_BASE/open-kobra-baseline"
TARGETS_MANIFEST="$BAK_BASE/open-kobra-targets.list"
VERSION_MARKER="$RINKHALS_HOME/.open-kobra-version"
MIN_FREE_KB="${OPEN_KOBRA_MIN_FREE_KB:-10240}"

LOG_INTERNAL="$RINKHALS_DIR/open-kobra-install.log"
LOG_USB=""
if [ -d "$USB_DIR" ]; then
    LOG_USB="$USB_DIR/open-kobra-install.log"
elif [ -d "$USB_ROOT" ]; then
    LOG_USB="$USB_ROOT/open-kobra-install.log"
fi

log_msg() {
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    LINE="$TS: $*"
    printf '%s\n' "$LINE"
    if [ -n "$LOG_USB" ]; then
        printf '%s\n' "$LINE" >> "$LOG_USB" 2>/dev/null || true
    fi
    if [ -d "$RINKHALS_DIR" ] && [ -w "$RINKHALS_DIR" ]; then
        printf '%s\n' "$LINE" >> "$LOG_INTERNAL" 2>/dev/null || true
    fi
}

file_has_exact_line() {
    F_PATH="$1"
    F_NEED="$2"
    [ -f "$F_PATH" ] || return 1
    awk -v t="$F_NEED" '$0 == t { found=1; exit } END { exit !found }' "$F_PATH"
}

file_has_literal() {
    F_PATH="$1"
    F_NEED="$2"
    [ -f "$F_PATH" ] || return 1
    awk -v t="$F_NEED" 'index($0, t) > 0 { found=1; exit } END { exit !found }' "$F_PATH"
}

check_block_balance() {
    CFG="$1"
    [ -f "$CFG" ] || return 0
    awk '
    /^# open-kobra\/begin/ { depth++; begins++ }
    /^# open-kobra\/end/ {
        if (depth <= 0) { bad = 1 }
        depth--
        ends++
    }
    END {
        if (bad || depth != 0 || begins != ends) exit 1
        exit 0
    }
    ' "$CFG"
}

filter_out_line() {
    F_PATH="$1"
    F_OMIT="$2"
    [ -f "$F_PATH" ] || return 0
    TMP_FLT="${F_PATH}.openkobra.tmp"
    awk -v t="$F_OMIT" '$0 != t { print }' "$F_PATH" > "$TMP_FLT" && sync && mv "$TMP_FLT" "$F_PATH" && sync
    STATUS_FLT=$?
    rm -f "$TMP_FLT" 2>/dev/null || true
    return "$STATUS_FLT"
}

record_unique_line() {
    F_PATH="$1"
    F_LINE="$2"
    [ -n "$F_LINE" ] || return 0
    if ! file_has_exact_line "$F_PATH" "$F_LINE"; then
        printf '%s\n' "$F_LINE" >> "$F_PATH" || return 1
    fi
    return 0
}

atomic_copy_file() {
    SRC_F="$1"
    DST_F="$2"
    DST_DIR=$(dirname "$DST_F")
    mkdir -p "$DST_DIR" || return 1
    TMP_F="${DST_F}.openkobra.tmp"
    cp -PRp "$SRC_F" "$TMP_F" && sync && mv "$TMP_F" "$DST_F" && sync
    RC_AC=$?
    if [ "$RC_AC" -ne 0 ]; then
        rm -f "$TMP_F" 2>/dev/null || true
    fi
    return "$RC_AC"
}

maybe_crash_point() {
    TAG="$1"
    if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_CRASH_AFTER:-}" = "$TAG" ]; then
        kill -9 $$ 2>/dev/null || exit 137
    fi
}

maybe_pause_for_signal_test() {
    TAG="$1"
    if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_PAUSE_AFTER:-}" = "$TAG" ]; then
        if [ -n "${OPEN_KOBRA_READY_FILE:-}" ]; then
            : > "$OPEN_KOBRA_READY_FILE"
        fi
        N_WAIT=0
        while [ "$N_WAIT" -lt 100 ]; do
            sleep 1
            N_WAIT=$((N_WAIT + 1))
        done
    fi
}

beep() {
    MS="${1:-300}"
    PWM_FILE="$ROOT/sys/class/pwm/pwmchip0/pwm0/enable"
    if [ -w "$PWM_FILE" ]; then
        echo 1 > "$PWM_FILE" 2>/dev/null || true
        if command -v usleep >/dev/null 2>&1; then
            usleep $((MS * 1000)) 2>/dev/null || true
        else
            sleep 1
        fi
        echo 0 > "$PWM_FILE" 2>/dev/null || true
    fi
}

beep_pause_ms() {
    MS="${1:-300}"
    PWM_FILE="$ROOT/sys/class/pwm/pwmchip0/pwm0/enable"
    if [ -w "$PWM_FILE" ]; then
        if command -v usleep >/dev/null 2>&1; then
            usleep $((MS * 1000)) 2>/dev/null || true
        else
            sleep 1
        fi
    fi
}

beep_error() {
    beep 300
    beep_pause_ms 300
    beep 300
    beep_pause_ms 300
    beep 300
}

beep_critical() {
    beep 200
    beep_pause_ms 200
    beep 200
    beep_pause_ms 200
    beep 200
    beep_pause_ms 200
    beep 200
    beep_pause_ms 200
    beep 200
}

rel_path() {
    P="$1"
    if [ -n "$ROOT" ]; then
        case "$P" in
            "$ROOT"/*) P="${P#"$ROOT"/}" ;;
            "$ROOT") P="" ;;
        esac
    fi
    while [ "${P#/}" != "$P" ]; do
        P="${P#/}"
    done
    printf '%s\n' "$P"
}

check_free_space_kb() {
    NEED_KB="$1"
    if [ ! -w "$ROOT/useremain" ]; then
        return 1
    fi
    FREE_KB=$(df -Pk "$ROOT/useremain" | tail -n 1 | awk '{print $4}')
    case "$FREE_KB" in
        ''|*[!0-9]*)
            return 2
            ;;
    esac
    if [ "$FREE_KB" -lt "$NEED_KB" ]; then
        return 3
    fi
    return 0
}

calc_required_kb() {
    PAY_KB=$(du -sk "$UPDATE_PATH" 2>/dev/null | awk '{print $1}' || true)
    case "$PAY_KB" in
        ''|*[!0-9]*) PAY_KB=256 ;;
    esac
    BAK_EST_KB=0
    for EST_P in \
        "$USER_APPS_DIR/kobra-screen" \
        "$USER_APPS_DIR/ace-standalone" \
        "$USER_APPS_DIR/05-open-kobra-guard" \
        "$RINKHALS_HOME/open-kobra" \
        "$ACE_EXT_DEST" \
        "$ACE_TEMP_DEST" \
        "$ROOT/useremain/open-kobra/rescue" \
        "$CONFIG_DIR/printer.klipper.cfg"; do
        if [ -e "$EST_P" ]; then
            P_KB=$(du -sk "$EST_P" 2>/dev/null | awk '{print $1}' || true)
            case "$P_KB" in
                ''|*[!0-9]*) ;;
                *) BAK_EST_KB=$((BAK_EST_KB + P_KB)) ;;
            esac
        fi
    done
    printf '%s\n' $((MIN_FREE_KB + PAY_KB * 2 + BAK_EST_KB))
}

ROLLBACK_ACTIVE=0
IN_ROLLBACK=0
ROLLBACK_FAILED=0
CREATED_BASELINE=0
RUN_BAK_DIR=""
STOPPED_APPS=""
RESCUE_CREATED=0
RESCUE_START_MODIFIED=0
CREATED_TARGETS_MANIFEST=0
KOBRA_SCREEN_CFG_CREATED=0

# kobra-screen stops the stock K3SysUi while it runs and its "stop" does not bring it back. If we stopped a running
# kobra-screen and it is NOT restarted (not enabled / rollback failed) the printer would have no UI at all (B-05).
# On a real printer only (ROOT empty); tests opt in with OPEN_KOBRA_ENSURE_STOCK_UI=1.
stock_ui_running() {
    for SU_COMM in "$ROOT"/proc/[0-9]*/comm; do
        [ -r "$SU_COMM" ] || continue
        SU_NAME=$(cat "$SU_COMM" 2>/dev/null || true)
        [ "$SU_NAME" = "K3SysUi" ] && return 0
    done
    return 1
}

kobra_screen_running() {
    KS_STAT=$(OPEN_KOBRA_ROOT="$ROOT" "$USER_APPS_DIR/kobra-screen/app.sh" status 2>/dev/null || true)
    printf '%s\n' "$KS_STAT" | awk 'index($0, "Status: started") > 0 { found=1; exit } END { exit !found }'
}

ensure_stock_ui_after_stop() {
    case " $STOPPED_APPS " in
        *" kobra-screen "*) ;;
        *) return 0 ;;
    esac
    if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_ENSURE_STOCK_UI:-0}" != "1" ]; then
        return 0
    fi
    if [ -x "$USER_APPS_DIR/kobra-screen/app.sh" ] && kobra_screen_running; then
        return 0
    fi
    if stock_ui_running; then
        return 0
    fi
    GK_UI_DIR="$ROOT/userdata/app/gk"
    if [ ! -x "$GK_UI_DIR/K3SysUi" ]; then
        log_msg "UYARI: Stok arayüz başlatılamadı ($GK_UI_DIR/K3SysUi çalıştırılabilir değil); yazıcıyı yeniden başlatın."
        return 0
    fi
    mkdir -p "$ROOT/tmp/rinkhals" 2>/dev/null || true
    (
        cd "$GK_UI_DIR" || exit 1
        USE_MUTABLE_CONFIG=1
        LD_LIBRARY_PATH="$GK_UI_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        export USE_MUTABLE_CONFIG LD_LIBRARY_PATH
        ./K3SysUi >> "$ROOT/tmp/rinkhals/K3SysUi.log" 2>&1 &
    ) </dev/null >/dev/null 2>&1
    log_msg "kobra-screen yeniden başlatılmadı: stok arayüz (K3SysUi) başlatıldı."
}

restart_stopped_apps() {
    ONLY_IF_ENABLED="$1"
    for R_APP in $STOPPED_APPS; do
        [ -n "$R_APP" ] || continue
        R_DIR="$USER_APPS_DIR/$R_APP"
        [ -x "$R_DIR/app.sh" ] || continue
        if [ "$ONLY_IF_ENABLED" = "1" ]; then
            if [ ! -f "$R_DIR/.enabled" ] && [ ! -f "$USER_APPS_DIR/${R_APP}.enabled" ]; then
                continue
            fi
            if [ -f "$R_DIR/.disabled" ] || [ -f "$USER_APPS_DIR/${R_APP}.disabled" ]; then
                continue
            fi
        fi
        if OPEN_KOBRA_ROOT="$ROOT" "$R_DIR/app.sh" start >/dev/null 2>&1; then
            log_msg "Uygulama yeniden başlatıldı: $R_APP"
        else
            log_msg "UYARI: Uygulama yeniden başlatılamadı: $R_APP"
        fi
    done
    ensure_stock_ui_after_stop
}

rollback() {
    if [ "$IN_ROLLBACK" = "1" ]; then
        return 0
    fi
    if [ "$ROLLBACK_ACTIVE" != "1" ] || [ -z "$RUN_BAK_DIR" ]; then
        return 0
    fi
    IN_ROLLBACK=1
    trap - INT TERM HUP 2>/dev/null || true
    log_msg "GERİ ALMA: Kurulum başarısız oldu, değişiklikler geri alınıyor..."

    # Restore modified targets FIRST (e.g. printer.klipper.cfg) before removing newly created files
    # so an interruption mid-rollback never leaves dangling [include ...] directives.
    if [ -f "$RUN_BAK_DIR/modified.list" ]; then
        while IFS= read -r REL; do
            [ -z "$REL" ] && continue
            SRC="$RUN_BAK_DIR/files/$REL"
            TARGET="$ROOT/$REL"
            if [ ! -e "$SRC" ] && [ ! -L "$SRC" ]; then
                ROLLBACK_FAILED=1
                log_msg "GERİ ALMA BAŞARISIZ: /$REL — yedek dosyası eksik: $SRC"
                continue
            fi
            if ! rm -rf "$TARGET"; then
                ROLLBACK_FAILED=1
                log_msg "GERİ ALMA BAŞARISIZ: /$REL — hedef temizlenemedi (elle kurtarın: $SRC)"
                continue
            fi
            if ! mkdir -p "$(dirname "$TARGET")"; then
                ROLLBACK_FAILED=1
                log_msg "GERİ ALMA BAŞARISIZ: /$REL — dizin oluşturulamadı (elle kurtarın: $SRC)"
                continue
            fi
            if [ -d "$SRC" ] && [ ! -L "$SRC" ]; then
                if ! cp -PRp "$SRC" "$TARGET"; then
                    ROLLBACK_FAILED=1
                    log_msg "GERİ ALMA BAŞARISIZ: /$REL — dizin kopyalanamadı (elle kurtarın: $SRC)"
                else
                    log_msg "GERİ ALMA: Yedekten geri yüklendi: /$REL"
                fi
            else
                if ! atomic_copy_file "$SRC" "$TARGET"; then
                    ROLLBACK_FAILED=1
                    log_msg "GERİ ALMA BAŞARISIZ: /$REL — dosya geri yüklenemedi (elle kurtarın: $SRC)"
                else
                    log_msg "GERİ ALMA: Yedekten geri yüklendi: /$REL"
                fi
            fi
        done < "$RUN_BAK_DIR/modified.list"
    fi

    rm -rf "${ACE_EXT_DEST:-/nonexistent-open-kobra}.openkobra.new" "${ACE_EXT_DEST:-/nonexistent-open-kobra}.openkobra.old" 2>/dev/null || true

    if [ -f "$RUN_BAK_DIR/created.list" ]; then
        while IFS= read -r REL; do
            [ -z "$REL" ] && continue
            TARGET="$ROOT/$REL"
            if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
                if ! rm -rf "$TARGET" || [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
                    ROLLBACK_FAILED=1
                    log_msg "GERİ ALMA BAŞARISIZ: Oluşturulan yol silinemedi: /$REL"
                else
                    log_msg "GERİ ALMA: Oluşturulan yol silindi: /$REL"
                fi
            fi
        done < "$RUN_BAK_DIR/created.list"
    fi

    if [ "$RESCUE_START_MODIFIED" = "1" ] && [ -f "$RUN_BAK_DIR/rescue_start.sh.bak" ]; then
        if atomic_copy_file "$RUN_BAK_DIR/rescue_start.sh.bak" "$ROOT/userdata/app/gk/start.sh"; then
            log_msg "GERİ ALMA: start.sh kurtarma öncesi haline döndürüldü."
        else
            ROLLBACK_FAILED=1
            log_msg "GERİ ALMA BAŞARISIZ: start.sh geri yüklenemedi ($RUN_BAK_DIR/rescue_start.sh.bak)"
        fi
    fi

    if [ "$RESCUE_CREATED" = "1" ] && [ -d "$ROOT/useremain/open-kobra/rescue" ]; then
        if ! rm -rf "$ROOT/useremain/open-kobra/rescue" || [ -e "$ROOT/useremain/open-kobra/rescue" ]; then
            ROLLBACK_FAILED=1
            log_msg "GERİ ALMA BAŞARISIZ: Yarım kalan rescue dizini temizlenemedi."
        else
            rmdir "$ROOT/useremain/open-kobra" 2>/dev/null || true
            log_msg "GERİ ALMA: Oluşturulan rescue dizini temizlendi."
        fi
    elif [ -d "$RUN_BAK_DIR/rescue_prev" ]; then
        if [ -f "$RUN_BAK_DIR/rescue_prev/rescue.sh" ]; then
            atomic_copy_file "$RUN_BAK_DIR/rescue_prev/rescue.sh" "$ROOT/useremain/open-kobra/rescue/rescue.sh" || ROLLBACK_FAILED=1
        fi
        if [ -d "$RUN_BAK_DIR/rescue_prev/manifests" ]; then
            mkdir -p "$ROOT/useremain/open-kobra/rescue/manifests" 2>/dev/null || true
            cp -PRp "$RUN_BAK_DIR/rescue_prev/manifests"/. "$ROOT/useremain/open-kobra/rescue/manifests"/ 2>/dev/null || ROLLBACK_FAILED=1
        fi
        if [ -d "$RUN_BAK_DIR/rescue_prev/stock" ]; then
            rm -rf "$ROOT/useremain/open-kobra/rescue/stock" 2>/dev/null || true
            cp -PRp "$RUN_BAK_DIR/rescue_prev/stock" "$ROOT/useremain/open-kobra/rescue/stock" 2>/dev/null || ROLLBACK_FAILED=1
        else
            rm -rf "$ROOT/useremain/open-kobra/rescue/stock" 2>/dev/null || true
        fi
        for RF_PREV in hook.block start.sh.orig start.sh.orig.fw binary_repair.disabled; do
            if [ -f "$RUN_BAK_DIR/rescue_prev/$RF_PREV" ]; then
                atomic_copy_file "$RUN_BAK_DIR/rescue_prev/$RF_PREV" "$ROOT/useremain/open-kobra/rescue/$RF_PREV" || ROLLBACK_FAILED=1
            else
                rm -f "$ROOT/useremain/open-kobra/rescue/$RF_PREV" 2>/dev/null || true
            fi
        done
    fi

    if [ -f "$RUN_BAK_DIR/kobra_screen_cfg.bak" ] && [ -n "${KOBRA_SCREEN_CFG_DEST:-}" ]; then
        if ! atomic_copy_file "$RUN_BAK_DIR/kobra_screen_cfg.bak" "$KOBRA_SCREEN_CFG_DEST"; then
            ROLLBACK_FAILED=1
            log_msg "GERİ ALMA BAŞARISIZ: kobra-screen.config geri yüklenemedi"
        else
            log_msg "GERİ ALMA: kobra-screen.config yedekten geri yüklendi."
        fi
    elif [ "$KOBRA_SCREEN_CFG_CREATED" = "1" ] && [ -n "${KOBRA_SCREEN_CFG_DEST:-}" ] && [ -e "$KOBRA_SCREEN_CFG_DEST" ]; then
        if ! rm -f "$KOBRA_SCREEN_CFG_DEST" || [ -e "$KOBRA_SCREEN_CFG_DEST" ]; then
            ROLLBACK_FAILED=1
            log_msg "GERİ ALMA BAŞARISIZ: Oluşturulan kobra-screen.config silinemedi."
        else
            log_msg "GERİ ALMA: Oluşturulan kobra-screen.config silindi."
        fi
    fi

    if [ "$CREATED_BASELINE" = "1" ] && [ -d "$BASELINE_DIR" ]; then
        if ! rm -rf "$BASELINE_DIR"; then
            ROLLBACK_FAILED=1
            log_msg "GERİ ALMA BAŞARISIZ: Yarım kalan baseline temizlenemedi: $BASELINE_DIR"
        fi
        rm -f "$TARGETS_MANIFEST" 2>/dev/null || true
    elif [ -f "$RUN_BAK_DIR/targets.list.bak" ]; then
        if ! atomic_copy_file "$RUN_BAK_DIR/targets.list.bak" "$TARGETS_MANIFEST"; then
            ROLLBACK_FAILED=1
            log_msg "GERİ ALMA BAŞARISIZ: targets.list geri yüklenemedi"
        fi
    elif [ "$CREATED_TARGETS_MANIFEST" = "1" ]; then
        rm -f "$TARGETS_MANIFEST" 2>/dev/null || true
    fi

    rm -f "$USER_APPS_DIR"/*.openkobra.installing "$USER_APPS_DIR"/*.openkobra.was_enabled "$USER_APPS_DIR"/*.openkobra.was_disabled 2>/dev/null || true
    sync 2>/dev/null || true

    if [ "$ROLLBACK_FAILED" = "0" ]; then
        restart_stopped_apps 0
    fi

    ROLLBACK_ACTIVE=0
}

fail_preflight() {
    log_msg "ÖN KOŞUL HATASI: $*"
    log_msg "Hiçbir sistem dosyasına dokunulmadan çıkılıyor."
    beep_error
    exit 1
}

fail_install() {
    trap - INT TERM HUP 2>/dev/null || true
    log_msg "KURULUM HATASI: $*"
    if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_BREAK_ROLLBACK:-0}" = "1" ] && [ -d "$RUN_BAK_DIR/files" ]; then
        rm -rf "$RUN_BAK_DIR/files" 2>/dev/null || true
    fi
    rollback
    if [ "$ROLLBACK_FAILED" = "1" ]; then
        log_msg "KRİTİK: GERİ ALMA TAMAMLANAMADI — elle müdahale gerekli! Yedek konumu: $RUN_BAK_DIR"
        beep_critical
        exit 2
    fi
    beep_error
    exit 1
}

on_signal() {
    SIG_NAME="${1:-SIGNAL}"
    trap - INT TERM HUP 2>/dev/null || true
    log_msg "SİNYAL ($SIG_NAME): Kurulum yarıda kesildi, geri alınıyor..."
    rollback
    if [ "$ROLLBACK_FAILED" = "1" ]; then
        log_msg "KRİTİK: SİNYAL SONRASI GERİ ALMA TAMAMLANAMADI — elle müdahale gerekli! Yedek: $RUN_BAK_DIR"
        beep_critical
        exit 2
    fi
    beep_error
    exit 143
}

backup_or_mark_new() {
    ABS="$1"
    REL=$(rel_path "$ABS")
    record_unique_line "$TARGETS_MANIFEST" "$REL" || true
    if [ -e "$ABS" ] || [ -L "$ABS" ]; then
        if ! file_has_exact_line "$RUN_BAK_DIR/modified.list" "$REL"; then
            mkdir -p "$RUN_BAK_DIR/files/$(dirname "$REL")" || fail_preflight "Yedek dizini oluşturulamadı: $RUN_BAK_DIR"
            cp -PRp "$ABS" "$RUN_BAK_DIR/files/$REL" || fail_preflight "Yedek alınamadı: /$REL"
            printf '%s\n' "$REL" >> "$RUN_BAK_DIR/modified.list"
        fi
    else
        record_unique_line "$RUN_BAK_DIR/created.list" "$REL" || fail_preflight "created.list yazılamadı."
    fi
}

log_msg "open-kobra kurulumu başlatılıyor..."

# 1. Verify package payload existence, MD5 manifest, and Python syntax before touching root
for REQ in \
    "$UPDATE_PATH/VERSION" \
    "$UPDATE_PATH/MANIFEST.md5" \
    "$UPDATE_PATH/apps/kobra-screen/app.sh" \
    "$UPDATE_PATH/apps/kobra-screen/app.json" \
    "$UPDATE_PATH/apps/ace-standalone/app.sh" \
    "$UPDATE_PATH/apps/ace-standalone/app.json" \
    "$UPDATE_PATH/apps/05-open-kobra-guard/app.sh" \
    "$UPDATE_PATH/apps/05-open-kobra-guard/app.json" \
    "$UPDATE_PATH/apps/05-open-kobra-guard/leviq3.py.md5" \
    "$UPDATE_PATH/rescue/rescue.sh" \
    "$UPDATE_PATH/rescue/manifests/K3_2.4.6.7.md5" \
    "$UPDATE_PATH/config/open_kobra_homing.cfg" \
    "$UPDATE_PATH/leviq3/leviq3.py" \
    "$UPDATE_PATH/leviq3/leviq3.cfg"; do
    if [ ! -f "$REQ" ]; then
        fail_preflight "Paket içeriği eksik: $REQ"
    fi
done

# Verify every payload file against MANIFEST.md5 (no GNU md5sum flags required)
while IFS= read -r MLINE; do
    [ -z "$MLINE" ] && continue
    EXP_HASH=$(printf '%s\n' "$MLINE" | awk '{print $1}')
    REL_FILE=$(printf '%s\n' "$MLINE" | awk '{$1=""; sub(/^[ \t]+/, ""); print}')
    FULL_FILE="$UPDATE_PATH/$REL_FILE"
    if [ ! -f "$FULL_FILE" ]; then
        fail_preflight "Manifest dosyası eksik: $REL_FILE"
    fi
    ACT_HASH=$(md5sum "$FULL_FILE" | awk '{print $1}')
    if [ "$ACT_HASH" != "$EXP_HASH" ]; then
        fail_preflight "Paket bütünlük (MD5) hatası: $REL_FILE ($ACT_HASH != $EXP_HASH)"
    fi
done < "$UPDATE_PATH/MANIFEST.md5"

GUARD_EXP_MD5=$(awk 'NF { print $1; exit }' "$UPDATE_PATH/apps/05-open-kobra-guard/leviq3.py.md5" | tr -d ' \t\r\n')
LEVIQ_ACT_MD5=$(md5sum "$UPDATE_PATH/leviq3/leviq3.py" | awk '{print $1}')
if [ "$GUARD_EXP_MD5" != "$LEVIQ_ACT_MD5" ]; then
    fail_preflight "leviq3.py.md5 beklenen özet ile uyuşmuyor ($GUARD_EXP_MD5 != $LEVIQ_ACT_MD5)."
fi

# Stock K3SysUi/gkapi launches with LD_LIBRARY_PATH=/userdata/app/gk:...; put system/Rinkhals libs first
RINKHALS_LIB_BASE="$RINKHALS_DIR/.current"
if [ ! -d "$RINKHALS_LIB_BASE" ] && [ -f "$RINKHALS_DIR/.version" ]; then
    _RK_VER=$(tr -d ' \t\r\n' < "$RINKHALS_DIR/.version" 2>/dev/null || true)
    if [ -n "$_RK_VER" ] && [ -d "$RINKHALS_DIR/$_RK_VER" ]; then
        RINKHALS_LIB_BASE="$RINKHALS_DIR/$_RK_VER"
    fi
fi
export LD_LIBRARY_PATH="/lib:/usr/lib:$RINKHALS_LIB_BASE/lib:$RINKHALS_LIB_BASE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

PY_CHECK_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PY_CHECK_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CHECK_BIN="python"
fi

ACE_DRIVER_SRC_DIR="$UPDATE_PATH/ace-driver"
ACE_DRIVER_PRESENT=0
ACE_DRIVER_HAS_CFG=0
ACE_DRIVER_VERSION="bilinmiyor"
if [ -d "$ACE_DRIVER_SRC_DIR" ]; then
    if [ ! -f "$ACE_DRIVER_SRC_DIR/klippy/extras/ace/__init__.py" ] || [ ! -f "$ACE_DRIVER_SRC_DIR/klippy/extras/temperature_ace.py" ]; then
        fail_preflight "ACE sürücü paketi eksik (klippy/extras/ace/__init__.py veya temperature_ace.py yok)."
    fi
fi

# Compile-check leviq3.py and all ace-driver *.py files in a single Python process (low ARM RAM overhead)
if [ -n "$PY_CHECK_BIN" ]; then
    TMP_PYC="${TMPDIR:-/tmp}/openkobra_pycheck_$$.pyc"
    PY_CHECK_ERR=$(
        "$PY_CHECK_BIN" - "$UPDATE_PATH/leviq3/leviq3.py" "$ACE_DRIVER_SRC_DIR" "$TMP_PYC" 2>&1 <<'PY'
import os
import py_compile
import sys

leviq_py, ace_dir, tmp_pyc = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    try:
        py_compile.compile(leviq_py, cfile=tmp_pyc, doraise=True)
    except Exception:
        print("LEVIQ3:" + leviq_py)
        sys.exit(1)
    if os.path.isdir(ace_dir):
        for dirpath, _dirs, files in os.walk(ace_dir):
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    py_compile.compile(path, cfile=tmp_pyc, doraise=True)
                except Exception:
                    print("ACE:" + path)
                    sys.exit(1)
finally:
    if os.path.exists(tmp_pyc):
        try:
            os.remove(tmp_pyc)
        except OSError:
            pass
PY
    ) || {
        rm -f "$TMP_PYC" 2>/dev/null || true
        case "$PY_CHECK_ERR" in
            ACE:*)
                fail_preflight "ACE sürücüsü sözdizimi derleme denetiminden geçemedi: ${PY_CHECK_ERR#ACE:}"
                ;;
            *)
                fail_preflight "leviq3.py sözdizimi derleme denetiminden geçemedi."
                ;;
        esac
    }
    rm -f "$TMP_PYC" 2>/dev/null || true
fi

PKG_VERSION=$(tr -d ' \t\r\n' < "$UPDATE_PATH/VERSION")
if [ -z "$PKG_VERSION" ]; then
    fail_preflight "VERSION dosyası boş."
fi
PREV_VERSION=""
if [ -f "$VERSION_MARKER" ] && [ ! -L "$VERSION_MARKER" ]; then
    PREV_VERSION=$(tr -d ' \t\r\n' < "$VERSION_MARKER" 2>/dev/null || true)
fi
log_msg "Paket sürümü ve bütünlüğü doğrulandı: $PKG_VERSION"
if [ -z "$PREV_VERSION" ]; then
    log_msg "Kurulu open-kobra sürümü yok: ilk kurulum ($PKG_VERSION)."
elif [ "$PREV_VERSION" = "$PKG_VERSION" ]; then
    log_msg "open-kobra yeniden kuruluyor (sürüm $PKG_VERSION)."
else
    log_msg "open-kobra sürüm değişikliği: $PREV_VERSION -> $PKG_VERSION"
fi

# 1b. Optional ACE driver payload (ace-driver/): complete and syntactically valid, or the install stops before touching anything
if [ -d "$ACE_DRIVER_SRC_DIR" ]; then
    ACE_DRIVER_PRESENT=1
    if [ -s "$ACE_DRIVER_SRC_DIR/config/ace_K3.cfg" ]; then
        ACE_DRIVER_HAS_CFG=1
    fi
    if [ -s "$ACE_DRIVER_SRC_DIR/VERSION" ]; then
        ACE_DRIVER_VERSION=$(tr -d ' \t\r\n' < "$ACE_DRIVER_SRC_DIR/VERSION")
    fi
    log_msg "ACE sürücü paketi doğrulandı (sürüm: $ACE_DRIVER_VERSION)"
else
    log_msg "ACE sürücü paketi yok; ACE sürücü adımı atlanacak."
    if [ -f "$RINKHALS_HOME/open-kobra/ace-driver.version" ]; then
        log_msg "NOT: Bu pakette ACE sürücüsü yok; daha önce kurulan sürücü (sürüm $(tr -d ' \t\r\n' < "$RINKHALS_HOME/open-kobra/ace-driver.version" 2>/dev/null)) olduğu gibi bırakıldı."
    fi
fi

# 2. Preflight: Check Rinkhals installation
if [ ! -d "$RINKHALS_DIR" ] || [ ! -f "$RINKHALS_DIR/.version" ]; then
    fail_preflight "Rinkhals kurulu görünmüyor ($RINKHALS_DIR/.version bulunamadı)."
fi

RINKHALS_VER=$(tr -d ' \t\r\n' < "$RINKHALS_DIR/.version")
if [ -z "$RINKHALS_VER" ]; then
    fail_preflight "Rinkhals sürüm dosyası boş: $RINKHALS_DIR/.version"
fi

RINKHALS_CURRENT=""
if [ -d "$RINKHALS_DIR/.current" ]; then
    RINKHALS_CURRENT="$RINKHALS_DIR/.current"
elif [ -d "$RINKHALS_DIR/$RINKHALS_VER" ]; then
    RINKHALS_CURRENT="$RINKHALS_DIR/$RINKHALS_VER"
else
    fail_preflight "Rinkhals çalışma dizini bulunamadı ($RINKHALS_DIR/$RINKHALS_VER)."
fi

if [ ! -f "$RINKHALS_CURRENT/tools.sh" ] && [ ! -f "$RINKHALS_CURRENT/start.sh" ]; then
    fail_preflight "Geçerli Rinkhals kurulumu doğrulanamadı ($RINKHALS_CURRENT)."
fi
log_msg "Rinkhals doğrulandı: sürüm $RINKHALS_VER"

# 3. Preflight: Check printer model is Anycubic Kobra 3 (modelId 20024; quoted or numeric)
API_CFG="$ROOT/userdata/app/gk/config/api.cfg"
if [ ! -f "$API_CFG" ]; then
    fail_preflight "Yazıcı model yapılandırması bulunamadı ($API_CFG)."
fi
MODEL_ID=$(awk 'index($0, "\"modelId\"") > 0 { print; exit }' "$API_CFG" | sed -n 's/.*"modelId"[[:space:]]*:[[:space:]]*"*\([0-9][0-9]*\)"*.*/\1/p')
if [ "$MODEL_ID" != "20024" ]; then
    fail_preflight "Bu paket yalnızca Anycubic Kobra 3 (modelId=20024) içindir. Algılanan modelId: '${MODEL_ID:-bilinmiyor}'."
fi
log_msg "Yazıcı modeli doğrulandı: Kobra 3 (modelId=20024)"

# 4. Preflight: Locate vanilla-klipper and moonraker apps
BUILTIN_APPS_DIR="$RINKHALS_CURRENT/home/rinkhals/apps"

VANILLA_KLIPPER_DIR=""
if [ -f "$USER_APPS_DIR/vanilla-klipper/app.sh" ]; then
    VANILLA_KLIPPER_DIR="$USER_APPS_DIR/vanilla-klipper"
elif [ -f "$BUILTIN_APPS_DIR/vanilla-klipper/app.sh" ]; then
    VANILLA_KLIPPER_DIR="$BUILTIN_APPS_DIR/vanilla-klipper"
else
    fail_preflight "vanilla-klipper uygulaması kurulu değil. Önce vanilla-klipper kurun."
fi

if [ ! -d "$VANILLA_KLIPPER_DIR/klippy/extras" ] || [ ! -f "$VANILLA_KLIPPER_DIR/klippy/klippy.py" ]; then
    fail_preflight "vanilla-klipper klippy/extras dizini geçersiz: $VANILLA_KLIPPER_DIR/klippy/extras"
fi
log_msg "vanilla-klipper bulundu: $VANILLA_KLIPPER_DIR"

ACE_EXTRAS_DIR="$VANILLA_KLIPPER_DIR/klippy/extras"
ACE_EXT_DEST="$ACE_EXTRAS_DIR/ace"
ACE_TEMP_DEST="$ACE_EXTRAS_DIR/temperature_ace.py"
ACE_CFG_TPL_DEST="$VANILLA_KLIPPER_DIR/ace_K3.cfg"
ACE_CFG_LIVE_DEST="$CONFIG_DIR/ace_K3.cfg"
ACE_CFG_LIVE_NEW="$CONFIG_DIR/ace_K3.cfg.new"

MOONRAKER_DIR=""
for CAND in \
    "$USER_APPS_DIR/40-moonraker" \
    "$BUILTIN_APPS_DIR/40-moonraker" \
    "$USER_APPS_DIR/moonraker" \
    "$BUILTIN_APPS_DIR/moonraker"; do
    if [ -f "$CAND/app.sh" ]; then
        MOONRAKER_DIR="$CAND"
        break
    fi
done
if [ -z "$MOONRAKER_DIR" ]; then
    fail_preflight "Moonraker uygulaması (40-moonraker) kurulu değil. Önce Moonraker uygulamasını kurun."
fi
log_msg "Moonraker bulundu: $MOONRAKER_DIR"

# 5. Preflight: Target vanilla-klipper configuration is strictly printer.klipper.cfg
TARGET_CFG="$CONFIG_DIR/printer.klipper.cfg"
if [ -L "$TARGET_CFG" ]; then
    fail_preflight "$TARGET_CFG bir sembolik bağ; doğrudan üzerine yazma reddedildi."
fi
if [ -L "$VERSION_MARKER" ]; then
    fail_preflight "$VERSION_MARKER bir sembolik bağ; güvenlik nedeniyle reddedildi."
fi
CFG_FROM_TEMPLATE=0
if [ ! -s "$TARGET_CFG" ]; then
    if [ -f "$VANILLA_KLIPPER_DIR/printer.klipper_K3.cfg" ]; then
        CFG_FROM_TEMPLATE=1
    else
        fail_preflight "Klipper yapılandırma dosyası bulunamadı ($TARGET_CFG veya printer.klipper_K3.cfg)."
    fi
elif ! check_block_balance "$TARGET_CFG"; then
    fail_preflight "$TARGET_CFG içinde eşleşmemiş '# open-kobra/begin' veya '# open-kobra/end' işareti var. Veri kaybını önlemek için çıkılıyor."
fi
log_msg "Hedef Klipper yapılandırması: $TARGET_CFG"

# 6. Preflight: Free space (with backup/rollback margin) & writability on /useremain
if [ ! -w "$ROOT/useremain" ]; then
    fail_preflight "/useremain yazılabilir değil."
fi
REQ_KB=$(calc_required_kb)
if ! check_free_space_kb "$REQ_KB"; then
    fail_preflight "/useremain üzerinde yeterli boş alan yok (en az ${REQ_KB} KB gerekli)."
fi
log_msg "Boş alan kontrolü geçti (gereken: ${REQ_KB} KB)"

# Clean leftover temp artifacts and recover any interrupted atomic app swap before snapshotting
for APP_REC in kobra-screen ace-standalone 05-open-kobra-guard; do
    APP_P="$USER_APPS_DIR/$APP_REC"
    if [ ! -e "$APP_P" ] && [ -d "${APP_P}.openkobra.old" ]; then
        mv "${APP_P}.openkobra.old" "$APP_P" 2>/dev/null || true
    fi
    rm -rf "${APP_P}.openkobra.new" "${APP_P}.openkobra.old" 2>/dev/null || true
done
rm -f "$CONFIG_DIR"/*.openkobra.tmp "$CONFIG_DIR"/*.openkobra.guard.tmp 2>/dev/null || true
if [ ! -e "$ACE_EXT_DEST" ] && [ -d "${ACE_EXT_DEST}.openkobra.old" ]; then
    mv "${ACE_EXT_DEST}.openkobra.old" "$ACE_EXT_DEST" 2>/dev/null || true
fi
rm -rf "${ACE_EXT_DEST}.openkobra.new" "${ACE_EXT_DEST}.openkobra.old" 2>/dev/null || true
rm -f "${ACE_TEMP_DEST}.openkobra.tmp" "${ACE_CFG_TPL_DEST}.openkobra.tmp" 2>/dev/null || true

# 7. Create dated backup and baseline snapshot before modifying anything
DATE_TAG=$(date '+%Y%m%d-%H%M%S')
RUN_BAK_DIR="$BAK_BASE/open-kobra-$DATE_TAG"
if [ -e "$RUN_BAK_DIR" ]; then
    N=1
    while [ -e "${RUN_BAK_DIR}-${N}" ]; do
        N=$((N + 1))
    done
    RUN_BAK_DIR="${RUN_BAK_DIR}-${N}"
fi

mkdir -p "$RUN_BAK_DIR/files" "$RUN_BAK_DIR/preserve" "$BAK_BASE" || fail_preflight "Yedek dizini oluşturulamadı: $RUN_BAK_DIR"
: > "$RUN_BAK_DIR/modified.list"
: > "$RUN_BAK_DIR/created.list"
if [ -f "$TARGETS_MANIFEST" ]; then
    cp -PRp "$TARGETS_MANIFEST" "$RUN_BAK_DIR/targets.list.bak" || fail_preflight "targets.list yedeği alınamadı."
else
    CREATED_TARGETS_MANIFEST=1
    : > "$TARGETS_MANIFEST"
fi

KOBRA_SCREEN_DEST="$USER_APPS_DIR/kobra-screen"
KOBRA_SCREEN_CFG_DEST="$USER_APPS_DIR/kobra-screen.config"
if [ -L "$KOBRA_SCREEN_CFG_DEST" ]; then
    fail_preflight "$KOBRA_SCREEN_CFG_DEST bir sembolik bağ; doğrudan üzerine yazma reddedildi."
elif [ -f "$KOBRA_SCREEN_CFG_DEST" ]; then
    cp -PRp "$KOBRA_SCREEN_CFG_DEST" "$RUN_BAK_DIR/kobra_screen_cfg.bak" || fail_preflight "kobra-screen.config yedeği alınamadı."
else
    KOBRA_SCREEN_CFG_CREATED=1
fi
ACE_STANDALONE_DEST="$USER_APPS_DIR/ace-standalone"
GUARD_DEST="$USER_APPS_DIR/05-open-kobra-guard"
OPEN_KOBRA_STATE_DIR="$RINKHALS_HOME/open-kobra"
LEVIQ3_PERSIST_DEST="$OPEN_KOBRA_STATE_DIR/leviq3.py"
LEVIQ3_CFG_PERSIST_DEST="$OPEN_KOBRA_STATE_DIR/leviq3.cfg"
HOMING_CFG_PERSIST_DEST="$OPEN_KOBRA_STATE_DIR/open_kobra_homing.cfg"
RESTORE_SCRIPT_DEST="$OPEN_KOBRA_STATE_DIR/restore-leviq3.sh"
LEVIQ3_PY_DEST="$VANILLA_KLIPPER_DIR/klippy/extras/leviq3.py"
LEVIQ3_CFG_DEST="$CONFIG_DIR/leviq3.cfg"
LEVIQ3_CFG_NEW_DEST="$CONFIG_DIR/leviq3.cfg.new"
HOMING_CFG_DEST="$CONFIG_DIR/open_kobra_homing.cfg"
HOMING_CFG_NEW_DEST="$CONFIG_DIR/open_kobra_homing.cfg.new"
LIMITS_CFG_DEST="$CONFIG_DIR/open_kobra_limits.cfg"
EXTRUDE_LOCK_CFG_DEST="$CONFIG_DIR/open_kobra_limits_zz_ekstruzyon_yasak.cfg"

backup_or_mark_new "$KOBRA_SCREEN_DEST"
backup_or_mark_new "$ACE_STANDALONE_DEST"
backup_or_mark_new "$GUARD_DEST"
backup_or_mark_new "$OPEN_KOBRA_STATE_DIR"
backup_or_mark_new "$LEVIQ3_PY_DEST"
backup_or_mark_new "$LEVIQ3_CFG_DEST"
backup_or_mark_new "$LEVIQ3_CFG_NEW_DEST"
backup_or_mark_new "$HOMING_CFG_DEST"
backup_or_mark_new "$HOMING_CFG_NEW_DEST"
backup_or_mark_new "$LIMITS_CFG_DEST"
backup_or_mark_new "$EXTRUDE_LOCK_CFG_DEST"
backup_or_mark_new "$TARGET_CFG"
backup_or_mark_new "$VERSION_MARKER"

# ACE driver originals. A baseline taken by an older package does not know these targets: record their current
# (still original) state in it BEFORE they are registered anywhere, otherwise an uninstall would delete the
# vanilla driver instead of restoring it.
ACE_TARGETS_TMP="${TMPDIR:-/tmp}/openkobra_ace_targets_$$.txt"
ACE_AUG_CHANGED=0

write_ace_targets_list() {
    {
        printf '%s\n' "$ACE_EXT_DEST" "$ACE_TEMP_DEST"
        if [ "$ACE_DRIVER_HAS_CFG" = "1" ]; then
            printf '%s\n' "$ACE_CFG_TPL_DEST" "$ACE_CFG_LIVE_NEW"
        fi
    } > "$ACE_TARGETS_TMP"
}

augment_baseline_for_ace() {
    write_ace_targets_list || return 1
    while IFS= read -r AUG_ABS; do
        [ -n "$AUG_ABS" ] || continue
        AUG_REL=$(rel_path "$AUG_ABS")
        if file_has_exact_line "$BASELINE_DIR/modified.list" "$AUG_REL" || file_has_exact_line "$BASELINE_DIR/created.list" "$AUG_REL"; then
            continue
        fi
        if [ -e "$AUG_ABS" ] || [ -L "$AUG_ABS" ]; then
            mkdir -p "$BASELINE_DIR/files/$(dirname "$AUG_REL")" || return 1
            rm -rf "$BASELINE_DIR/files/$AUG_REL" || return 1
            cp -PRp "$AUG_ABS" "$BASELINE_DIR/files/$AUG_REL" || return 1
            sync
            printf '%s\n' "$AUG_REL" >> "$BASELINE_DIR/modified.list" || return 1
        else
            printf '%s\n' "$AUG_REL" >> "$BASELINE_DIR/created.list" || return 1
        fi
        ACE_AUG_CHANGED=1
    done < "$ACE_TARGETS_TMP"
    sync
    return 0
}

write_ace_orig_manifest() {
    ACE_MF="$BASELINE_DIR/ace-orig.md5"
    ACE_MF_TMP="${ACE_MF}.openkobra.tmp"
    write_ace_targets_list || return 1
    : > "$ACE_MF_TMP" || return 1
    while IFS= read -r MF_ABS; do
        [ -n "$MF_ABS" ] || continue
        MF_REL=$(rel_path "$MF_ABS")
        [ -e "$BASELINE_DIR/files/$MF_REL" ] || continue
        if [ -d "$BASELINE_DIR/files/$MF_REL" ]; then
            (cd "$BASELINE_DIR/files" && find "$MF_REL" -type f | LC_ALL=C sort | while IFS= read -r MF_F; do
                md5sum "$MF_F"
            done) >> "$ACE_MF_TMP" || return 1
        else
            (cd "$BASELINE_DIR/files" && md5sum "$MF_REL") >> "$ACE_MF_TMP" || return 1
        fi
    done < "$ACE_TARGETS_TMP"
    sync && mv "$ACE_MF_TMP" "$ACE_MF" && sync
}

if [ "$ACE_DRIVER_PRESENT" = "1" ]; then
    if [ -f "$BASELINE_DIR/.complete" ]; then
        augment_baseline_for_ace || { rm -f "$ACE_TARGETS_TMP" 2>/dev/null || true; fail_preflight "İlk durum yedeğine ACE sürücü orijinalleri eklenemedi."; }
    fi
    write_ace_targets_list || fail_preflight "ACE hedef listesi yazılamadı."
    while IFS= read -r ACE_T; do
        [ -n "$ACE_T" ] || continue
        backup_or_mark_new "$ACE_T"
    done < "$ACE_TARGETS_TMP"
    rm -f "$ACE_TARGETS_TMP" 2>/dev/null || true
fi

{
    printf 'VERSION=%s\n' "$PKG_VERSION"
    printf 'TIMESTAMP=%s\n' "$DATE_TAG"
    printf 'VANILLA_KLIPPER_REL=%s\n' "$(rel_path "$VANILLA_KLIPPER_DIR")"
    printf 'CONFIG_DIR_REL=%s\n' "$(rel_path "$CONFIG_DIR")"
    printf 'TARGET_CFG_REL=%s\n' "$(rel_path "$TARGET_CFG")"
} > "$RUN_BAK_DIR/meta" || fail_preflight "Yedek meta dosyası yazılamadı."

if [ ! -f "$BASELINE_DIR/.complete" ]; then
    rm -rf "$BASELINE_DIR" 2>/dev/null || true
    cp -PRp "$RUN_BAK_DIR" "$BASELINE_DIR" || fail_preflight "İlk durum yedeği oluşturulamadı: $BASELINE_DIR"
    TARGET_CFG_REL=$(rel_path "$TARGET_CFG")
    filter_out_line "$BASELINE_DIR/modified.list" "$TARGET_CFG_REL" || fail_preflight "Baseline düzenlenemedi."
    filter_out_line "$BASELINE_DIR/created.list" "$TARGET_CFG_REL" || fail_preflight "Baseline düzenlenemedi."
    rm -f "$BASELINE_DIR/files/$TARGET_CFG_REL" 2>/dev/null || true
    sync
    : > "$BASELINE_DIR/.complete" || fail_preflight "Baseline tamamlama işareti yazılamadı."
    sync
    CREATED_BASELINE=1
    log_msg "İlk durum yedeği alındı: $BASELINE_DIR"
fi
if [ "$ACE_DRIVER_PRESENT" = "1" ] && [ -f "$BASELINE_DIR/.complete" ] && { [ ! -f "$BASELINE_DIR/ace-orig.md5" ] || [ "$ACE_AUG_CHANGED" = "1" ]; }; then
    write_ace_orig_manifest || { rm -f "$ACE_TARGETS_TMP" 2>/dev/null || true; fail_preflight "ACE sürücü orijinallerinin MD5 manifesti yazılamadı."; }
    rm -f "$ACE_TARGETS_TMP" 2>/dev/null || true
fi
sync
log_msg "Tarihli yedek alındı: $RUN_BAK_DIR"
maybe_crash_point "after_backup"

# From this point onward, errors and termination signals trigger full rollback.
ROLLBACK_ACTIVE=1
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

# 8. Stop running open-kobra apps cleanly and remember them so both success and rollback restore state
for APP_NAME in kobra-screen ace-standalone 05-open-kobra-guard; do
    STOP_APP_DIR="$USER_APPS_DIR/$APP_NAME"
    if [ -x "$STOP_APP_DIR/app.sh" ]; then
        APP_STAT=$(OPEN_KOBRA_ROOT="$ROOT" "$STOP_APP_DIR/app.sh" status 2>/dev/null || true)
        if printf '%s\n' "$APP_STAT" | awk 'index($0, "Status: started") > 0 { found=1; exit } END { exit !found }'; then
            STOPPED_APPS="$STOPPED_APPS $APP_NAME"
            if OPEN_KOBRA_ROOT="$ROOT" "$STOP_APP_DIR/app.sh" stop >/dev/null 2>&1; then
                log_msg "Çalışan uygulama kurulum için durduruldu: $APP_NAME"
            else
                log_msg "UYARI: Uygulama durdurulurken hata oluştu: $APP_NAME"
            fi
        fi
    fi
done

install_app_dir() {
    APP_NAME="$1"
    SRC_DIR="$2"
    DEST_DIR="$3"
    DEFAULT_ENABLED="${4:-0}"
    PRESERVE_APP_DIR="$RUN_BAK_DIR/preserve/$APP_NAME"
    INSTALL_FLAG="$USER_APPS_DIR/${APP_NAME}.openkobra.installing"

    check_free_space_kb "$MIN_FREE_KB" || return 1

    mkdir -p "$USER_APPS_DIR" "$PRESERVE_APP_DIR" || return 1

    if [ -f "$DEST_DIR/config.json" ]; then
        cp -PRp "$DEST_DIR/config.json" "$PRESERVE_APP_DIR/config.json" || return 1
    elif [ -f "$INSTALL_FLAG" ]; then
        PREV_CFG=$(for CFG_CAND in "$BAK_BASE"/*/preserve/"${APP_NAME}"/config.json; do
            [ -f "$CFG_CAND" ] || continue
            printf '%s\n' "$CFG_CAND"
        done | sort -r | head -n 1 || true)
        if [ -n "$PREV_CFG" ] && [ -f "$PREV_CFG" ]; then
            cp -PRp "$PREV_CFG" "$PRESERVE_APP_DIR/config.json" || return 1
        fi
    fi

    HAD_ENABLED=0
    HAD_DISABLED=0
    if [ -f "$DEST_DIR/.enabled" ]; then
        HAD_ENABLED=1
    elif [ -f "$DEST_DIR/.disabled" ]; then
        HAD_DISABLED=1
    elif [ -f "$INSTALL_FLAG" ]; then
        if [ -f "$USER_APPS_DIR/${APP_NAME}.openkobra.was_enabled" ]; then
            HAD_ENABLED=1
        elif [ -f "$USER_APPS_DIR/${APP_NAME}.openkobra.was_disabled" ]; then
            HAD_DISABLED=1
        fi
    fi

    HAS_SIDECAR_ENABLED=0
    HAS_SIDECAR_DISABLED=0
    [ -f "$USER_APPS_DIR/${APP_NAME}.enabled" ] && HAS_SIDECAR_ENABLED=1
    [ -f "$USER_APPS_DIR/${APP_NAME}.disabled" ] && HAS_SIDECAR_DISABLED=1

    rm -f "$USER_APPS_DIR/${APP_NAME}.openkobra.was_enabled" "$USER_APPS_DIR/${APP_NAME}.openkobra.was_disabled"
    if [ "$HAD_ENABLED" = "1" ]; then
        : > "$USER_APPS_DIR/${APP_NAME}.openkobra.was_enabled"
    elif [ "$HAD_DISABLED" = "1" ]; then
        : > "$USER_APPS_DIR/${APP_NAME}.openkobra.was_disabled"
    fi
    : > "$INSTALL_FLAG"

    STAGE_APP_DIR="${DEST_DIR}.openkobra.new"
    OLD_APP_DIR="${DEST_DIR}.openkobra.old"
    rm -rf "$STAGE_APP_DIR" "$OLD_APP_DIR" || return 1
    mkdir -p "$STAGE_APP_DIR" || return 1
    cp -PRp "$SRC_DIR"/. "$STAGE_APP_DIR"/ || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }
    chmod +x "$STAGE_APP_DIR/app.sh" || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }

    if [ -f "$PRESERVE_APP_DIR/config.json" ]; then
        atomic_copy_file "$PRESERVE_APP_DIR/config.json" "$STAGE_APP_DIR/config.json" || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }
    fi

    rm -f "$STAGE_APP_DIR/.enabled" "$STAGE_APP_DIR/.disabled"
    if [ "$HAD_ENABLED" = "1" ]; then
        : > "$STAGE_APP_DIR/.enabled" || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }
        STATE_DESC="onceki acik durum korundu"
    elif [ "$HAD_DISABLED" = "1" ]; then
        : > "$STAGE_APP_DIR/.disabled" || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }
        STATE_DESC="onceki kapali durum korundu"
    elif [ "$HAS_SIDECAR_DISABLED" = "1" ]; then
        : > "$STAGE_APP_DIR/.disabled" || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }
        STATE_DESC="yan isaretci ile kapali korundu"
    elif [ "$HAS_SIDECAR_ENABLED" = "1" ]; then
        STATE_DESC="yan isaretci ile acik korundu"
    elif [ "$DEFAULT_ENABLED" = "1" ]; then
        : > "$STAGE_APP_DIR/.enabled" || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }
        STATE_DESC="varsayilan acik (acilis bekcisi)"
    else
        STATE_DESC="varsayilan kapali"
    fi

    sync
    if [ -e "$DEST_DIR" ] || [ -L "$DEST_DIR" ]; then
        mv "$DEST_DIR" "$OLD_APP_DIR" || { rm -rf "$STAGE_APP_DIR" 2>/dev/null || true; return 1; }
    fi
    if ! mv "$STAGE_APP_DIR" "$DEST_DIR"; then
        if [ -e "$OLD_APP_DIR" ] || [ -L "$OLD_APP_DIR" ]; then
            mv "$OLD_APP_DIR" "$DEST_DIR" 2>/dev/null || true
        fi
        rm -rf "$STAGE_APP_DIR" 2>/dev/null || true
        return 1
    fi
    sync
    rm -rf "$OLD_APP_DIR" 2>/dev/null || true

    rm -f "$INSTALL_FLAG" "$USER_APPS_DIR/${APP_NAME}.openkobra.was_enabled" "$USER_APPS_DIR/${APP_NAME}.openkobra.was_disabled"
    sync
    log_msg "Uygulama kuruldu ($STATE_DESC): $APP_NAME -> $DEST_DIR"
}

if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_FAIL_AT:-}" = "kobra_screen" ]; then
    fail_install "Test arızası tetiklendi: kobra_screen"
fi
install_app_dir "kobra-screen" "$UPDATE_PATH/apps/kobra-screen" "$KOBRA_SCREEN_DEST" 0 || fail_install "kobra-screen kopyalanamadı."
maybe_crash_point "after_kobra_screen"

if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_FAIL_AT:-}" = "ace_standalone" ]; then
    fail_install "Test arızası tetiklendi: ace_standalone"
fi
install_app_dir "ace-standalone" "$UPDATE_PATH/apps/ace-standalone" "$ACE_STANDALONE_DEST" 0 || fail_install "ace-standalone kopyalanamadı."
maybe_crash_point "after_ace_standalone"

if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_FAIL_AT:-}" = "guard" ]; then
    fail_install "Test arızası tetiklendi: guard"
fi
install_app_dir "05-open-kobra-guard" "$UPDATE_PATH/apps/05-open-kobra-guard" "$GUARD_DEST" 1 || fail_install "05-open-kobra-guard kopyalanamadı."
maybe_crash_point "after_guard"
maybe_pause_for_signal_test "after_guard"

# 9. Install LeviQ3 extra, persistent backup copies, restore helper, and user-preserving leviq3.cfg
if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_FAIL_AT:-}" = "leviq3_py" ]; then
    fail_install "Test arızası tetiklendi: leviq3_py"
fi
atomic_copy_file "$UPDATE_PATH/leviq3/leviq3.py" "$LEVIQ3_PY_DEST" || fail_install "leviq3.py kopyalanamadı: $LEVIQ3_PY_DEST"
log_msg "leviq3.py kopyalandı: $LEVIQ3_PY_DEST"
maybe_crash_point "after_leviq3_py"

mkdir -p "$OPEN_KOBRA_STATE_DIR" || fail_install "open-kobra kalıcı durum dizini oluşturulamadı: $OPEN_KOBRA_STATE_DIR"
atomic_copy_file "$UPDATE_PATH/leviq3/leviq3.py" "$LEVIQ3_PERSIST_DEST" || fail_install "Kalıcı leviq3.py kopyalanamadı: $LEVIQ3_PERSIST_DEST"
atomic_copy_file "$UPDATE_PATH/apps/05-open-kobra-guard/leviq3.py.md5" "$OPEN_KOBRA_STATE_DIR/leviq3.py.md5" || fail_install "Kalıcı leviq3.py.md5 kopyalanamadı: $OPEN_KOBRA_STATE_DIR/leviq3.py.md5"

RESTORE_TMP="${RESTORE_SCRIPT_DEST}.openkobra.tmp"
cat > "$RESTORE_TMP" <<'EOF' || { rm -f "$RESTORE_TMP" 2>/dev/null || true; fail_install "restore-leviq3.sh oluşturulamadı: $RESTORE_SCRIPT_DEST"; }
#!/bin/sh
# Restore leviq3.py into vanilla-klipper klippy/extras after a vanilla-klipper app update.
set -eu
ROOT="${OPEN_KOBRA_ROOT:-}"
while [ -n "$ROOT" ] && [ "${ROOT%/}" != "$ROOT" ]; do
    ROOT="${ROOT%/}"
done
SELF_DIR=$(cd "$(dirname "$0")" && pwd)
SRC_PY="$SELF_DIR/leviq3.py"
if [ ! -f "$SRC_PY" ]; then
    printf 'HATA: Kalıcı leviq3.py bulunamadı: %s\n' "$SRC_PY" >&2
    exit 1
fi
RESTORED=0
RINKHALS_DIR="$ROOT/useremain/rinkhals"
RINKHALS_VER=$(tr -d ' \t\r\n' < "$RINKHALS_DIR/.version" 2>/dev/null || true)
BUILTIN_VK=""
if [ -d "$RINKHALS_DIR/.current/home/rinkhals/apps/vanilla-klipper" ]; then
    BUILTIN_VK="$RINKHALS_DIR/.current/home/rinkhals/apps/vanilla-klipper"
elif [ -n "$RINKHALS_VER" ] && [ -d "$RINKHALS_DIR/$RINKHALS_VER/home/rinkhals/apps/vanilla-klipper" ]; then
    BUILTIN_VK="$RINKHALS_DIR/$RINKHALS_VER/home/rinkhals/apps/vanilla-klipper"
fi
for VK_DIR in "$ROOT/useremain/home/rinkhals/apps/vanilla-klipper" "$BUILTIN_VK"; do
    [ -n "$VK_DIR" ] || continue
    if [ -d "$VK_DIR/klippy/extras" ]; then
        TMP_R="$VK_DIR/klippy/extras/leviq3.py.openkobra.tmp"
        cp -PRp "$SRC_PY" "$TMP_R" && sync && mv "$TMP_R" "$VK_DIR/klippy/extras/leviq3.py" && sync
        RESTORED=$((RESTORED + 1))
        printf 'leviq3.py geri yüklendi: %s/klippy/extras/leviq3.py\n' "$VK_DIR"
    fi
done
if [ "$RESTORED" -eq 0 ]; then
    printf 'HATA: vanilla-klipper klippy/extras dizini bulunamadı.\n' >&2
    exit 1
fi
EOF
chmod +x "$RESTORE_TMP" && sync && mv "$RESTORE_TMP" "$RESTORE_SCRIPT_DEST" && sync || {
    rm -f "$RESTORE_TMP" 2>/dev/null || true
    fail_install "restore-leviq3.sh yerine taşınamadı."
}

mkdir -p "$CONFIG_DIR" || fail_install "Klipper config dizini oluşturulamadı: $CONFIG_DIR"

# Preserve user-edited leviq3.cfg on reinstall/upgrade: overwrite ONLY if absent or identical to previous stock copy
if [ ! -f "$LEVIQ3_CFG_DEST" ]; then
    atomic_copy_file "$UPDATE_PATH/leviq3/leviq3.cfg" "$LEVIQ3_CFG_DEST" || fail_install "leviq3.cfg kopyalanamadı: $LEVIQ3_CFG_DEST"
    log_msg "leviq3.cfg oluşturuldu: $LEVIQ3_CFG_DEST"
elif cmp -s "$LEVIQ3_CFG_DEST" "$UPDATE_PATH/leviq3/leviq3.cfg"; then
    rm -f "$LEVIQ3_CFG_NEW_DEST" 2>/dev/null || true
    log_msg "leviq3.cfg zaten güncel: $LEVIQ3_CFG_DEST"
elif [ -f "$LEVIQ3_CFG_PERSIST_DEST" ] && cmp -s "$LEVIQ3_CFG_DEST" "$LEVIQ3_CFG_PERSIST_DEST"; then
    atomic_copy_file "$UPDATE_PATH/leviq3/leviq3.cfg" "$LEVIQ3_CFG_DEST" || fail_install "leviq3.cfg güncellenemedi: $LEVIQ3_CFG_DEST"
    rm -f "$LEVIQ3_CFG_NEW_DEST" 2>/dev/null || true
    log_msg "Düzenlenmemiş leviq3.cfg yeni sürümle güncellendi: $LEVIQ3_CFG_DEST"
else
    atomic_copy_file "$UPDATE_PATH/leviq3/leviq3.cfg" "$LEVIQ3_CFG_NEW_DEST" || fail_install "leviq3.cfg.new yazılamadı: $LEVIQ3_CFG_NEW_DEST"
    log_msg "Kullanıcı tarafından düzenlenen leviq3.cfg korundu; yeni varsayılanlar şuraya bırakıldı: $LEVIQ3_CFG_NEW_DEST"
fi

atomic_copy_file "$UPDATE_PATH/leviq3/leviq3.cfg" "$LEVIQ3_CFG_PERSIST_DEST" || fail_install "Kalıcı leviq3.cfg kopyalanamadı: $LEVIQ3_CFG_PERSIST_DEST"
log_msg "Kalıcı leviq3 kopyası ve geri yükleme betiği kaydedildi: $OPEN_KOBRA_STATE_DIR"
maybe_crash_point "after_leviq3_cfg"

# 9b. ACE driver (optional payload): originals are already backed up (run backup + baseline); copy atomically
install_ace_dir() {
    ACE_STAGE="${ACE_EXT_DEST}.openkobra.new"
    ACE_OLD="${ACE_EXT_DEST}.openkobra.old"
    SRC_ACE_DIR="$ACE_DRIVER_SRC_DIR/klippy/extras/ace"
    check_free_space_kb "$MIN_FREE_KB" || return 1
    rm -rf "$ACE_STAGE" "$ACE_OLD" || return 1
    mkdir -p "$ACE_STAGE" || return 1
    cp -PRp "$SRC_ACE_DIR"/. "$ACE_STAGE"/ || { rm -rf "$ACE_STAGE" 2>/dev/null || true; return 1; }
    for ACE_F in "$SRC_ACE_DIR"/*; do
        [ -e "$ACE_F" ] || continue
        if ! cmp -s "$ACE_F" "$ACE_STAGE/$(basename "$ACE_F")"; then
            rm -rf "$ACE_STAGE" 2>/dev/null || true
            return 1
        fi
    done
    sync
    if [ -e "$ACE_EXT_DEST" ] || [ -L "$ACE_EXT_DEST" ]; then
        mv "$ACE_EXT_DEST" "$ACE_OLD" || { rm -rf "$ACE_STAGE" 2>/dev/null || true; return 1; }
    fi
    if ! mv "$ACE_STAGE" "$ACE_EXT_DEST"; then
        if [ -e "$ACE_OLD" ] || [ -L "$ACE_OLD" ]; then
            mv "$ACE_OLD" "$ACE_EXT_DEST" 2>/dev/null || true
        fi
        rm -rf "$ACE_STAGE" 2>/dev/null || true
        return 1
    fi
    sync
    rm -rf "$ACE_OLD" 2>/dev/null || true
    return 0
}

if [ "$ACE_DRIVER_PRESENT" = "1" ]; then
    if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_FAIL_AT:-}" = "ace_driver" ]; then
        fail_install "Test arızası tetiklendi: ace_driver"
    fi
    install_ace_dir || fail_install "ACE sürücü dizini kurulamadı: $ACE_EXT_DEST"
    log_msg "ACE sürücüsü kopyalandı: $ACE_EXT_DEST"
    maybe_crash_point "after_ace_dir"
    atomic_copy_file "$ACE_DRIVER_SRC_DIR/klippy/extras/temperature_ace.py" "$ACE_TEMP_DEST" || fail_install "temperature_ace.py kopyalanamadı: $ACE_TEMP_DEST"
    log_msg "temperature_ace.py kopyalandı: $ACE_TEMP_DEST"
    maybe_crash_point "after_ace_temp"
    if [ "$ACE_DRIVER_HAS_CFG" = "1" ]; then
        # The vanilla template is replaced (backed up above). The LIVE printer_data/config/ace_K3.cfg holds the user's
        # edits and is never overwritten: when it differs, the driver's defaults are left next to it as ace_K3.cfg.new.
        atomic_copy_file "$ACE_DRIVER_SRC_DIR/config/ace_K3.cfg" "$ACE_CFG_TPL_DEST" || fail_install "ace_K3.cfg şablonu kopyalanamadı: $ACE_CFG_TPL_DEST"
        if [ ! -f "$ACE_CFG_LIVE_DEST" ]; then
            rm -f "$ACE_CFG_LIVE_NEW" 2>/dev/null || true
            log_msg "ace_K3.cfg şablonu güncellendi; $ACE_CFG_LIVE_DEST yok, vanilla-klipper ilk başlatmada şablondan oluşturacak."
        elif cmp -s "$ACE_CFG_LIVE_DEST" "$ACE_DRIVER_SRC_DIR/config/ace_K3.cfg"; then
            rm -f "$ACE_CFG_LIVE_NEW" 2>/dev/null || true
            log_msg "ace_K3.cfg zaten güncel: $ACE_CFG_LIVE_DEST"
        else
            atomic_copy_file "$ACE_DRIVER_SRC_DIR/config/ace_K3.cfg" "$ACE_CFG_LIVE_NEW" || fail_install "ace_K3.cfg.new yazılamadı: $ACE_CFG_LIVE_NEW"
            log_msg "Kullanıcının ace_K3.cfg dosyası korundu; yeni sürücünün varsayılanları şuraya bırakıldı (karşılaştırıp elle birleştirin): $ACE_CFG_LIVE_NEW"
        fi
    fi
    maybe_crash_point "after_ace_cfg"
    ACE_VER_TMP="$OPEN_KOBRA_STATE_DIR/ace-driver.version.openkobra.tmp"
    printf '%s\n' "$ACE_DRIVER_VERSION" > "$ACE_VER_TMP" && sync && mv "$ACE_VER_TMP" "$OPEN_KOBRA_STATE_DIR/ace-driver.version" && sync || {
        rm -f "$ACE_VER_TMP" 2>/dev/null || true
        fail_install "ace-driver.version yazılamadı."
    }
    log_msg "ACE sürücüsü kuruldu (sürüm: $ACE_DRIVER_VERSION). NOT: Klipper'ın yeni ACE sürücüsünü yüklemesi için yazıcıyı yeniden başlatın (veya Rinkhals Manage apps'ten vanilla-klipper'ı durdurup başlatın); paket Klipper'ı kendisi yeniden başlatmaz."
fi

# 9c. numpy (optional install): extract wheel to vanilla-klipper site-packages if present.
# This step is NOT part of rollback; numpy only adds files and does not break Klipper.
NP_WHEEL=""
for W in "$UPDATE_PATH/python"/numpy-*.whl; do
    [ -f "$W" ] && NP_WHEEL="$W" && break
done
if [ -n "$NP_WHEEL" ]; then
    NP_SITE="$VANILLA_KLIPPER_DIR/lib/python3.11/site-packages"
    if [ ! -d "$NP_SITE" ]; then
        log_msg "UYARI: numpy hedef dizini yok ($NP_SITE), numpy atlanıyor."
    elif [ -d "$NP_SITE/numpy-1.24.4.dist-info" ] && [ -f "$NP_SITE/numpy/__init__.py" ]; then
        log_msg "numpy zaten kurulu, atlanıyor."
    else
        # Unpack next to site-packages (/tmp is a small RAM tmpfs; numpy unpacks to ~25 MB) so mv is a rename
        NP_TMP="$VANILLA_KLIPPER_DIR/.openkobra_numpy_$$"
        mkdir -p "$NP_TMP"
        if python3 -m zipfile -e "$NP_WHEEL" "$NP_TMP" 2>/dev/null; then
            if [ -f "$NP_TMP/numpy/__init__.py" ] && [ -f "$NP_TMP/numpy-1.24.4.dist-info/METADATA" ]; then
                rm -rf "$NP_SITE/numpy" "$NP_SITE/numpy-1.24.4.dist-info" 2>/dev/null || true
                if mv "$NP_TMP/numpy" "$NP_SITE/numpy" && mv "$NP_TMP/numpy-1.24.4.dist-info" "$NP_SITE/numpy-1.24.4.dist-info"; then
                    printf 'installed-by-open-kobra\n' > "$NP_SITE/numpy/.open-kobra-installed"
                    sync
                    log_msg "numpy kuruldu: $NP_SITE/numpy"
                else
                    log_msg "UYARI: numpy yerine taşınamadı, numpy atlanıyor."
                fi
            else
                log_msg "UYARI: wheel içinde numpy/__init__.py veya numpy-1.24.4.dist-info/METADATA yok, numpy atlanıyor."
            fi
        else
            log_msg "UYARI: wheel açılamadı (python3 -m zipfile başarısız), numpy atlanıyor."
        fi
        rm -rf "$NP_TMP" 2>/dev/null || true
    fi
else
    log_msg "numpy wheel pakette yok, atlanıyor."
fi

# 10. Create empty open_kobra_limits.cfg if absent (preserve existing user limits on upgrade)
if [ ! -f "$LIMITS_CFG_DEST" ]; then
    LIMITS_TMP="${LIMITS_CFG_DEST}.openkobra.tmp"
    printf '# open-kobra kullanici sinirlari (kobra-screen tarafindan yonetilir)\n' > "$LIMITS_TMP" && sync && mv "$LIMITS_TMP" "$LIMITS_CFG_DEST" && sync || {
        rm -f "$LIMITS_TMP" 2>/dev/null || true
        fail_install "open_kobra_limits.cfg oluşturulamadı."
    }
    log_msg "Boş open_kobra_limits.cfg oluşturuldu: $LIMITS_CFG_DEST"
else
    log_msg "Mevcut open_kobra_limits.cfg korundu: $LIMITS_CFG_DEST"
fi

# 10a. Eski ekstrüzyon güvenlik kilidi dosyasını (open_kobra_limits_zz_ekstruzyon_yasak.cfg) temizle:
# - Dosya bizim ürettiğimiz şablondan geliyorsa (yorum satırında "open-kobra varsayilan guvenlik kilidi"
#   VEYA "open-kobra GECICI GUVENLIK" geçiyor VE dosyada min_extrude_temp: 320 dışında anlamlı ayar yok)
#   sil ve log'a yaz. Kullanıcının elle değiştirdiği (şablon dışı) dosyaya dokunma, uyarı log'u yaz.
# - Silme yedek/geri alma kapsamında olsun (dosya zaten backup_or_mark_new ile yedekleniyor — 843. satır).
if [ -f "$EXTRUDE_LOCK_CFG_DEST" ]; then
    # Şablon içerik mi kontrol et: yorum satırında "open-kobra varsayilan guvenlik kilidi" VEYA
    # "open-kobra GECICI GUVENLIK" geçiyor mu?
    IS_TEMPLATE=0
    # Only our exact settings: comments, blank lines, [extruder] and min_extrude_temp: 320
    if awk '/^[[:space:]]*([#;]|$)/ { next }
            /^[[:space:]]*\[extruder\][[:space:]]*$/ { next }
            /^[[:space:]]*min_extrude_temp[[:space:]]*[:=][[:space:]]*320(\.0*)?[[:space:]]*$/ { m=1; next }
            { bad=1 }
            END { exit !(m && !bad) }' "$EXTRUDE_LOCK_CFG_DEST" 2>/dev/null; then
        HAS_MIN_EXTRUDE=1
    else
        HAS_MIN_EXTRUDE=0
    fi
    if awk 'index($0, "open-kobra varsayilan guvenlik kilidi") > 0 || index($0, "open-kobra GECICI GUVENLIK") > 0 { f=1; exit } END { exit !f }' "$EXTRUDE_LOCK_CFG_DEST" 2>/dev/null; then
        IS_TEMPLATE=1
    fi
    if [ "$IS_TEMPLATE" = "1" ] && [ "$HAS_MIN_EXTRUDE" = "1" ]; then
        # Şablon dosyası — sil (yedek alındı, geri alma getirir)
        rm -f "$EXTRUDE_LOCK_CFG_DEST" 2>/dev/null || true
        log_msg "Eski şablon ekstrüzyon kilidi dosyası silindi: $EXTRUDE_LOCK_CFG_DEST"
    else
        # Kullanıcı değiştirmiş — koru, uyar
        log_msg "UYARI: Kullanıcı düzenlemesi olan $EXTRUDE_LOCK_CFG_DEST korundu (şablon dışı)."
    fi
else
    log_msg "Ekstrüzyon kilidi dosyası zaten yok: $EXTRUDE_LOCK_CFG_DEST"
fi

# 10a (devam). kobra-screen.config: extrusion_lock anahtarını KURMA (varsayılan artık kapalı).
# Dosya yoksa {} oluştur; varsa dokunma (kullanıcı değeri korunur).
if [ ! -e "$KOBRA_SCREEN_CFG_DEST" ]; then
    KSCFG_TMP="${KOBRA_SCREEN_CFG_DEST}.openkobra.tmp"
    if printf '{"language": "en"}\n' > "$KSCFG_TMP" && sync && mv "$KSCFG_TMP" "$KOBRA_SCREEN_CFG_DEST" && sync; then
        log_msg "Boş kobra-screen.config oluşturuldu: $KOBRA_SCREEN_CFG_DEST"
    else
        rm -f "$KSCFG_TMP" 2>/dev/null || true
        fail_install "kobra-screen.config oluşturulamadı: $KOBRA_SCREEN_CFG_DEST"
    fi
fi
maybe_crash_point "after_limits_cfg"

# 10b. Install open_kobra_homing.cfg (preserve user edits on upgrade, same policy as leviq3.cfg)
if [ ! -f "$HOMING_CFG_DEST" ]; then
    atomic_copy_file "$UPDATE_PATH/config/open_kobra_homing.cfg" "$HOMING_CFG_DEST" || fail_install "open_kobra_homing.cfg kopyalanamadı: $HOMING_CFG_DEST"
    log_msg "open_kobra_homing.cfg oluşturuldu: $HOMING_CFG_DEST"
elif cmp -s "$HOMING_CFG_DEST" "$UPDATE_PATH/config/open_kobra_homing.cfg"; then
    rm -f "$HOMING_CFG_NEW_DEST" 2>/dev/null || true
    log_msg "open_kobra_homing.cfg zaten güncel: $HOMING_CFG_DEST"
elif [ -f "$HOMING_CFG_PERSIST_DEST" ] && cmp -s "$HOMING_CFG_DEST" "$HOMING_CFG_PERSIST_DEST"; then
    atomic_copy_file "$UPDATE_PATH/config/open_kobra_homing.cfg" "$HOMING_CFG_DEST" || fail_install "open_kobra_homing.cfg güncellenemedi: $HOMING_CFG_DEST"
    rm -f "$HOMING_CFG_NEW_DEST" 2>/dev/null || true
    log_msg "Düzenlenmemiş open_kobra_homing.cfg yeni sürümle güncellendi: $HOMING_CFG_DEST"
else
    atomic_copy_file "$UPDATE_PATH/config/open_kobra_homing.cfg" "$HOMING_CFG_NEW_DEST" || fail_install "open_kobra_homing.cfg.new yazılamadı: $HOMING_CFG_NEW_DEST"
    log_msg "Kullanıcı tarafından düzenlenen open_kobra_homing.cfg korundu; yeni varsayılanlar şuraya bırakıldı: $HOMING_CFG_NEW_DEST"
fi
atomic_copy_file "$UPDATE_PATH/config/open_kobra_homing.cfg" "$HOMING_CFG_PERSIST_DEST" || fail_install "Kalıcı open_kobra_homing.cfg kopyalanamadı: $HOMING_CFG_PERSIST_DEST"
maybe_crash_point "after_homing_cfg"

# 11. Inject include lines into K3 vanilla-klipper config (balance + parse + sync + mv + sync)
if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_FAIL_AT:-}" = "config_edit" ]; then
    fail_install "Test arızası tetiklendi: config_edit"
fi
check_free_space_kb "$MIN_FREE_KB" || fail_install "Yapılandırma güncellemesi öncesi boş disk alanı yetersiz."

if [ "$CFG_FROM_TEMPLATE" = "1" ] || [ ! -s "$TARGET_CFG" ]; then
    atomic_copy_file "$VANILLA_KLIPPER_DIR/printer.klipper_K3.cfg" "$TARGET_CFG" || fail_install "printer.klipper_K3.cfg kopyalanamadı."
fi

if ! check_block_balance "$TARGET_CFG"; then
    fail_install "$TARGET_CFG içinde eşleşmemiş '# open-kobra/begin' veya '# open-kobra/end' bloğu bulundu; dosya değiştirilmedi."
fi

HAD_SAVE_CONFIG=0
if file_has_literal "$TARGET_CFG" "#*# <---------------------- SAVE_CONFIG ---------------------->"; then
    HAD_SAVE_CONFIG=1
fi

TMP_CFG="${TARGET_CFG}.openkobra.tmp"
awk '
BEGIN { skip = 0; inserted = 0 }
/^# open-kobra\/begin/ { skip = 1; next }
/^# open-kobra\/end/ { skip = 0; next }
skip == 1 { next }
index($0, "#*# <---------------------- SAVE_CONFIG ---------------------->") == 1 {
    if (!inserted) {
        print "# open-kobra/begin"
        print "[include leviq3.cfg]"
        print "[include open_kobra_homing.cfg]"
        print "[include open_kobra_limits*.cfg]"
        print "# open-kobra/end"
        inserted = 1
    }
    print
    next
}
{ print }
END {
    if (!inserted) {
        print ""
        print "# open-kobra/begin"
        print "[include leviq3.cfg]"
        print "[include open_kobra_homing.cfg]"
        print "[include open_kobra_limits*.cfg]"
        print "# open-kobra/end"
    }
}
' "$TARGET_CFG" > "$TMP_CFG" || {
    rm -f "$TMP_CFG" 2>/dev/null || true
    fail_install "Klipper yapılandırması güncellenemedi: $TARGET_CFG"
}

if [ ! -s "$TMP_CFG" ] \
    || ! file_has_literal "$TMP_CFG" "[include leviq3.cfg]" \
    || ! file_has_literal "$TMP_CFG" "[include open_kobra_homing.cfg]" \
    || ! file_has_literal "$TMP_CFG" "[include open_kobra_limits*.cfg]" \
    || ! check_block_balance "$TMP_CFG"; then
    rm -f "$TMP_CFG" 2>/dev/null || true
    fail_install "Güncellenen Klipper yapılandırması bütünlük doğrulamasından geçemedi."
fi

if [ "$HAD_SAVE_CONFIG" = "1" ] && ! file_has_literal "$TMP_CFG" "#*# <---------------------- SAVE_CONFIG ---------------------->"; then
    rm -f "$TMP_CFG" 2>/dev/null || true
    fail_install "SAVE_CONFIG bloğu doğrulamada kayboldu; yazma durduruldu."
fi

if [ ! -r "$LEVIQ3_CFG_DEST" ] || [ ! -r "$HOMING_CFG_DEST" ] || [ ! -r "$LIMITS_CFG_DEST" ]; then
    rm -f "$TMP_CFG" 2>/dev/null || true
    fail_install "Include edilen leviq3.cfg, open_kobra_homing.cfg veya open_kobra_limits.cfg okunabilir değil."
fi

# Validate Klipper INI syntax & include targets before overwriting printer.klipper.cfg
if [ -n "$PY_CHECK_BIN" ]; then
    if ! "$PY_CHECK_BIN" - "$TMP_CFG" "$CONFIG_DIR" <<'PY'
import configparser
import glob
import sys
import os

tmp_cfg, cfg_dir = sys.argv[1], sys.argv[2]
raw = open(tmp_cfg, encoding="utf-8").read()
marker = "#*# <---------------------- SAVE_CONFIG ---------------------->"
regular = raw.split(marker, 1)[0] if marker in raw else raw

cp = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
cp.read_string(regular)
cp.read(os.path.join(cfg_dir, "leviq3.cfg"), encoding="utf-8")
cp.read(os.path.join(cfg_dir, "open_kobra_homing.cfg"), encoding="utf-8")
for lim_path in sorted(glob.glob(os.path.join(cfg_dir, "open_kobra_limits*.cfg"))):
    cp.read(lim_path, encoding="utf-8")
if not cp.has_section("leviq3"):
    raise RuntimeError("Missing [leviq3] section in leviq3.cfg")
if not cp.has_section("gcode_macro G28"):
    raise RuntimeError("Missing [gcode_macro G28] section in open_kobra_homing.cfg")
if cp.has_section("safe_z_home") and cp.has_section("homing_override"):
    raise RuntimeError("homing_override and safe_z_home cannot be used simultaneously")
PY
    then
        rm -f "$TMP_CFG" 2>/dev/null || true
        fail_install "Yeni Klipper yapılandırması INI ayrıştırma doğrulamasından geçemedi."
    fi
fi

sync
maybe_crash_point "before_cfg_mv"
mv "$TMP_CFG" "$TARGET_CFG" || {
    rm -f "$TMP_CFG" 2>/dev/null || true
    fail_install "Klipper yapılandırması yerine taşınamadı: $TARGET_CFG"
}
sync
log_msg "Include satırları eklendi: $TARGET_CFG"
maybe_crash_point "after_cfg_mv"

# 11b. Install boot rescue layer (verified stock copy + start.sh pre-boot hook)
RESCUE_BASE="$ROOT/useremain/open-kobra"
RESCUE_DIR="$RESCUE_BASE/rescue"
RESCUE_LOG="$RESCUE_DIR/rescue.log"
RESCUE_MANIFEST_DST="$RESCUE_DIR/manifests"
STOCK_GK_DIR="$ROOT/userdata/app/gk"
STOCK_START_SH="$STOCK_GK_DIR/start.sh"
DEV_VER_FILE="$ROOT/useremain/dev/version"
REPAIR_DISABLED_MARKER="$RESCUE_DIR/binary_repair.disabled"

rescue_install_log() {
    log_msg "$*"
    if [ -d "$RESCUE_DIR" ] && [ -w "$RESCUE_DIR" ]; then
        printf '%s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$RESCUE_LOG" 2>/dev/null || true
    fi
}

check_rescue_block_balance() {
    ST_F="$1"
    [ -f "$ST_F" ] || return 0
    awk '
    /^# open-kobra-rescue\/begin/ { depth++; begins++ }
    /^# open-kobra-rescue\/end/ {
        if (depth <= 0) { bad = 1 }
        depth--
        ends++
    }
    END {
        if (bad || depth != 0 || begins != ends) exit 1
        exit 0
    }
    ' "$ST_F"
}

# start.sh is fit to be recorded as start.sh.orig: non-blank, no CR, valid shell syntax, balanced Rinkhals block.
start_sh_sane_for_orig() {
    SS_F="$1"
    [ -f "$SS_F" ] || return 1
    awk 'NF { f = 1; exit } END { exit !f }' "$SS_F" || return 1
    SS_ALL=$(wc -c < "$SS_F" | tr -d ' \t\r\n')
    SS_NOCR=$(tr -d '\r' < "$SS_F" | wc -c | tr -d ' \t\r\n')
    [ "$SS_ALL" = "$SS_NOCR" ] || return 1
    sh -n "$SS_F" >/dev/null 2>&1 || return 1
    awk '
    /^# Rinkhals\/begin/ { k++; if (k > 1) bad = 1 }
    /^# Rinkhals\/end/ { k--; if (k < 0) bad = 1 }
    END { if (bad || k != 0) exit 1; exit 0 }
    ' "$SS_F" || return 1
    # A cut-off but syntactically valid start.sh must never become the reference (PK-R01): the stock programs must be launched ...
    awk '
    /^[[:space:]]*\.\/gklib([[:space:]]|$)/ { a = 1 }
    /^[[:space:]]*\.\/gkapi([[:space:]]|$)/ { b = 1 }
    /^[[:space:]]*\.\/K3SysUi([[:space:]]|$)/ { c = 1 }
    END { exit !(a && b && c) }
    ' "$SS_F" || return 1
    # ... and, when the package carries the official start.sh of this firmware, the stock part must equal it.
    SS_REF="$UPDATE_PATH/rescue/manifests/K3_${FW_VER}.start.sh"
    if [ -n "$FW_VER" ] && [ -f "$SS_REF" ]; then
        SS_T1="${TMPDIR:-/tmp}/openkobra_ss_ref_$$.txt"
        SS_T2="${TMPDIR:-/tmp}/openkobra_ss_cur_$$.txt"
        start_sh_core "$SS_REF" > "$SS_T1"
        start_sh_core "$SS_F" > "$SS_T2"
        SS_RC=0
        cmp -s "$SS_T1" "$SS_T2" || SS_RC=1
        rm -f "$SS_T1" "$SS_T2" 2>/dev/null || true
        return "$SS_RC"
    fi
    return 0
}

# Lines outside the open-kobra-rescue and Rinkhals blocks, trailing blank lines dropped.
start_sh_core() {
    awk '
    /^# open-kobra-rescue\/begin/ { skip_r = 1; next }
    /^# open-kobra-rescue\/end/ { skip_r = 0; next }
    /^# Rinkhals\/begin/ { skip_k = 1; next }
    /^# Rinkhals\/end/ { skip_k = 0; next }
    skip_r || skip_k { next }
    { if ($0 ~ /^[ \t]*$/) { pend++ } else { while (pend > 0) { print ""; pend-- } print } }
    ' "$1"
}

if [ ! -d "$RESCUE_DIR" ]; then
    RESCUE_CREATED=1
else
    mkdir -p "$RUN_BAK_DIR/rescue_prev" || fail_install "Kurtarma yedek dizini oluşturulamadı."
    if [ -f "$RESCUE_DIR/rescue.sh" ]; then
        cp -PRp "$RESCUE_DIR/rescue.sh" "$RUN_BAK_DIR/rescue_prev/rescue.sh" || fail_install "Önceki rescue.sh yedeklenemedi."
    fi
    if [ -d "$RESCUE_DIR/manifests" ]; then
        cp -PRp "$RESCUE_DIR/manifests" "$RUN_BAK_DIR/rescue_prev/manifests" || fail_install "Önceki kurtarma manifestleri yedeklenemedi."
    fi
    if [ -f "$RESCUE_DIR/hook.block" ]; then
        cp -PRp "$RESCUE_DIR/hook.block" "$RUN_BAK_DIR/rescue_prev/hook.block" || fail_install "Önceki hook.block yedeklenemedi."
    fi
    if [ -d "$RESCUE_DIR/stock" ]; then
        cp -PRp "$RESCUE_DIR/stock" "$RUN_BAK_DIR/rescue_prev/stock" || fail_install "Önceki kurtarma stok kopyası yedeklenemedi."
    fi
    for RF_CUR in start.sh.orig start.sh.orig.fw binary_repair.disabled; do
        if [ -f "$RESCUE_DIR/$RF_CUR" ]; then
            cp -PRp "$RESCUE_DIR/$RF_CUR" "$RUN_BAK_DIR/rescue_prev/$RF_CUR" || fail_install "Önceki $RF_CUR yedeklenemedi."
        fi
    done
fi

mkdir -p "$RESCUE_DIR" "$RESCUE_MANIFEST_DST" || fail_install "Kurtarma dizini oluşturulamadı: $RESCUE_DIR"
atomic_copy_file "$UPDATE_PATH/rescue/rescue.sh" "$RESCUE_DIR/rescue.sh" || fail_install "rescue.sh kopyalanamadı: $RESCUE_DIR/rescue.sh"
chmod +x "$RESCUE_DIR/rescue.sh" || fail_install "rescue.sh çalıştırılabilir yapılamadı."
atomic_copy_file "$UPDATE_PATH/rescue/manifests/K3_2.4.6.7.md5" "$RESCUE_MANIFEST_DST/K3_2.4.6.7.md5" || fail_install "K3_2.4.6.7.md5 kopyalanamadı."
if [ -f "$UPDATE_PATH/rescue/manifests/K3_2.4.6.7.start.sh" ]; then
    atomic_copy_file "$UPDATE_PATH/rescue/manifests/K3_2.4.6.7.start.sh" "$RESCUE_MANIFEST_DST/K3_2.4.6.7.start.sh" || fail_install "K3_2.4.6.7.start.sh kopyalanamadı."
fi

# Requirement A: Verified stock binary copy (only when fw==2.4.6.7 and K3 and all MD5s match official manifest)
FW_VER=""
if [ -f "$DEV_VER_FILE" ]; then
    FW_VER=$(tr -d ' \t\r\n' < "$DEV_VER_FILE" 2>/dev/null || true)
fi
VER_MANIFEST="$UPDATE_PATH/rescue/manifests/K3_${FW_VER}.md5"
STOCK_DEST_DIR="$RESCUE_DIR/stock/$FW_VER"

if [ -n "$FW_VER" ] && [ "$FW_VER" = "2.4.6.7" ] && [ -f "$VER_MANIFEST" ]; then
    ALL_BIN_MATCH=1
    BIN_COUNT=0
    MISMATCH_REASON=""
    while IFS= read -r MLINE || [ -n "$MLINE" ]; do
        CLEAN_LINE=$(printf '%s\n' "$MLINE" | sed 's/#.*$//')
        EXP_MD5=$(printf '%s\n' "$CLEAN_LINE" | awk '{print $1}')
        BIN_NAME=$(printf '%s\n' "$CLEAN_LINE" | awk '{print $2}')
        [ -z "$EXP_MD5" ] && continue
        [ -z "$BIN_NAME" ] && continue
        if ! printf '%s\n' "$EXP_MD5" | awk 'length($0) == 32 && $0 ~ /^[0-9a-fA-F]+$/ { ok=1 } END { exit !ok }'; then
            ALL_BIN_MATCH=0
            MISMATCH_REASON="$BIN_NAME geçersiz manifest özeti"
            break
        fi
        BIN_COUNT=$((BIN_COUNT + 1))
        BIN_SRC="$STOCK_GK_DIR/$BIN_NAME"
        if [ -L "$BIN_SRC" ] || [ ! -f "$BIN_SRC" ]; then
            ALL_BIN_MATCH=0
            MISMATCH_REASON="$BIN_NAME bulunamadı"
            break
        fi
        ACT_MD5=$(md5sum "$BIN_SRC" 2>/dev/null | awk '{print $1}')
        if [ "$ACT_MD5" != "$EXP_MD5" ]; then
            ALL_BIN_MATCH=0
            MISMATCH_REASON="$BIN_NAME MD5 uyuşmazlığı ($ACT_MD5 != $EXP_MD5)"
            break
        fi
    done < "$VER_MANIFEST"

    if [ "$BIN_COUNT" -eq 0 ]; then
        ALL_BIN_MATCH=0
        MISMATCH_REASON="manifest boş"
    fi

    if [ "$ALL_BIN_MATCH" = "1" ]; then
        COPY_OK=1
        mkdir -p "$STOCK_DEST_DIR" || COPY_OK=0
        if [ "$COPY_OK" = "1" ]; then
            while IFS= read -r MLINE || [ -n "$MLINE" ]; do
                CLEAN_LINE=$(printf '%s\n' "$MLINE" | sed 's/#.*$//')
                EXP_MD5=$(printf '%s\n' "$CLEAN_LINE" | awk '{print $1}')
                BIN_NAME=$(printf '%s\n' "$CLEAN_LINE" | awk '{print $2}')
                [ -z "$EXP_MD5" ] && continue
                [ -z "$BIN_NAME" ] && continue
                BIN_SRC="$STOCK_GK_DIR/$BIN_NAME"
                BIN_DST="$STOCK_DEST_DIR/$BIN_NAME"
                BIN_TMP="${BIN_DST}.openkobra.tmp"
                rm -f "$BIN_TMP" 2>/dev/null || true
                if ! cp -p "$BIN_SRC" "$BIN_TMP" 2>/dev/null; then
                    rm -f "$BIN_TMP" 2>/dev/null || true
                    COPY_OK=0
                    break
                fi
                sync
                if ! mv "$BIN_TMP" "$BIN_DST" 2>/dev/null; then
                    rm -f "$BIN_TMP" 2>/dev/null || true
                    COPY_OK=0
                    break
                fi
                sync
                DST_MD5=$(md5sum "$BIN_DST" 2>/dev/null | awk '{print $1}')
                if [ "$DST_MD5" != "$EXP_MD5" ]; then
                    rm -f "$BIN_DST" 2>/dev/null || true
                    COPY_OK=0
                    break
                fi
            done < "$VER_MANIFEST"
        fi
        if [ "$COPY_OK" = "1" ]; then
            VER_MARK_TMP="$STOCK_DEST_DIR/.verified.openkobra.tmp"
            if printf 'K3_%s\n' "$FW_VER" > "$VER_MARK_TMP" 2>/dev/null && sync && mv "$VER_MARK_TMP" "$STOCK_DEST_DIR/.verified" 2>/dev/null && sync; then
                rm -f "$REPAIR_DISABLED_MARKER" 2>/dev/null || true
                rescue_install_log "Kurtarma stok ikili kopyaları doğrulandı ve kaydedildi ($BIN_COUNT ikili): $STOCK_DEST_DIR"
            else
                rm -f "$VER_MARK_TMP" 2>/dev/null || true
                rm -rf "$STOCK_DEST_DIR" 2>/dev/null || true
                printf 'copy_verify_failed\n' > "$REPAIR_DISABLED_MARKER" 2>/dev/null || true
                rescue_install_log "UYARI: Kurtarma stok doğrulama işareti yazılamadı; ikili onarım devre dışı bırakıldı (kurulum devam ediyor)."
            fi
        else
            rm -rf "$STOCK_DEST_DIR" 2>/dev/null || true
            printf 'copy_verify_failed\n' > "$REPAIR_DISABLED_MARKER" 2>/dev/null || true
            rescue_install_log "UYARI: Kurtarma stok ikili kopyası doğrulanamadı; stok kopya temizlendi ve ikili onarım devre dışı bırakıldı (kurulum devam ediyor)."
        fi
    else
        rm -f "$STOCK_DEST_DIR/.verified" 2>/dev/null || true
        printf 'md5_mismatch\n' > "$REPAIR_DISABLED_MARKER" 2>/dev/null || true
        rescue_install_log "UYARI: Stok ikili MD5 kontrolü uyuşmadı ($MISMATCH_REASON); stok kopya oluşturulmadı ve kurtarma ikili onarımı devre dışı bırakıldı (kurulum devam ediyor)."
    fi
else
    printf 'version_mismatch:%s\n' "${FW_VER:-missing}" > "$REPAIR_DISABLED_MARKER" 2>/dev/null || true
    rescue_install_log "UYARI: Firmware sürümü '${FW_VER:-bilinmiyor}' için resmi stok ikili manifesti bulunamadı; stok kopya oluşturulmadı ve ikili onarım devre dışı bırakıldı (kurulum devam ediyor)."
fi

# Requirement B: Pre-boot hook at the very top of /userdata/app/gk/start.sh (atomic + idempotent)
if [ -L "$STOCK_START_SH" ]; then
    rescue_install_log "UYARI: $STOCK_START_SH bir sembolik bağ; açılış kurtarma kancası eklenmedi."
elif [ -f "$STOCK_START_SH" ] && ! file_has_literal "$STOCK_START_SH" "# open-kobra-rescue/begin" \
    && { [ ! -f "$RESCUE_DIR/start.sh.orig" ] || [ "$(tr -d ' \t\r\n' < "$RESCUE_DIR/start.sh.orig.fw" 2>/dev/null)" != "$FW_VER" ] || ! start_sh_sane_for_orig "$RESCUE_DIR/start.sh.orig"; } \
    && ! start_sh_sane_for_orig "$STOCK_START_SH"; then
    # The reference would be recorded from this start.sh, and it does not look like a complete stock start.sh:
    # do NOT add the hook (nothing could safely repair it later) and leave start.sh alone.
    rescue_install_log "UYARI: $STOCK_START_SH tam/sağlam bir stok start.sh gibi görünmüyor (gklib/gkapi/K3SysUi başlatma satırları eksik, resmi içerikle uyuşmuyor, CRLF, sözdizimi ya da blok hatası); açılış kancası EKLENMEDİ, start.sh'a dokunulmadı."
elif [ -f "$STOCK_START_SH" ]; then
    if ! check_rescue_block_balance "$STOCK_START_SH"; then
        rescue_install_log "UYARI: $STOCK_START_SH içinde eşleşmemiş '# open-kobra-rescue/begin' veya '# open-kobra-rescue/end' var; dokunulmadı."
    else
        cp -PRp "$STOCK_START_SH" "$RUN_BAK_DIR/rescue_start.sh.bak" || fail_install "start.sh kurulum yedeği alınamadı."
        # start.sh.orig is the reference for the boot-time start.sh repair. It is (re)recorded from a start.sh that carries
        # no hook yet when it is missing OR was taken for another firmware: an Anycubic update replaces start.sh (hook
        # gone) and bumps the firmware, and a stale reference would keep the repair switched off for good (B-02).
        ORIG_FW_CUR=""
        if [ -f "$RESCUE_DIR/start.sh.orig.fw" ]; then
            ORIG_FW_CUR=$(tr -d ' \t\r\n' < "$RESCUE_DIR/start.sh.orig.fw" 2>/dev/null || true)
        fi
        NEED_ORIG_REFRESH=0
        if ! file_has_literal "$STOCK_START_SH" "# open-kobra-rescue/begin"; then
            if [ ! -f "$RESCUE_DIR/start.sh.orig" ] || [ "$ORIG_FW_CUR" != "$FW_VER" ] || ! start_sh_sane_for_orig "$RESCUE_DIR/start.sh.orig"; then
                NEED_ORIG_REFRESH=1
            fi
            if [ "$NEED_ORIG_REFRESH" = "1" ]; then
                if [ -f "$RESCUE_DIR/start.sh.orig" ]; then
                    rescue_install_log "start.sh.orig yenileniyor (kayıtlı firmware '${ORIG_FW_CUR:-yok}', şimdiki '${FW_VER:-yok}')."
                fi
                if start_sh_sane_for_orig "$STOCK_START_SH"; then
                    atomic_copy_file "$STOCK_START_SH" "$RESCUE_DIR/start.sh.orig" || fail_install "start.sh.orig yedeği alınamadı."
                    if [ -n "$FW_VER" ]; then
                        ORIG_FW_TMP="$RESCUE_DIR/start.sh.orig.fw.openkobra.tmp"
                        printf '%s\n' "$FW_VER" > "$ORIG_FW_TMP" && sync && mv "$ORIG_FW_TMP" "$RESCUE_DIR/start.sh.orig.fw" && sync || {
                            rm -f "$ORIG_FW_TMP" 2>/dev/null || true
                            fail_install "start.sh.orig.fw yazılamadı."
                        }
                    fi
                else
                    rescue_install_log "UYARI: $STOCK_START_SH sağlam görünmüyor (boş/CRLF/sözdizimi/dengesiz blok); start.sh.orig alınmadı, açılışta start.sh onarımı devre dışı kalacak."
                fi
            fi
        else
            # Kanca zaten varsa ve eski/uyumsuz start.sh.orig varsa:
            # start.sh'ın kanca DIŞI kısmından sağlam bir start.sh.orig üret (start_sh_sane_for_orig ile doğrula),
            # doğrulama başarılıysa .fw dosyasını yaz. Böylece 'start.sh.orig resmi-start.sh-ile-uyusmuyor' hatası önlensin.
            NEED_CAND_REBUILD=0
            if [ ! -f "$RESCUE_DIR/start.sh.orig" ] || [ "$ORIG_FW_CUR" != "$FW_VER" ] || ! start_sh_sane_for_orig "$RESCUE_DIR/start.sh.orig"; then
                NEED_CAND_REBUILD=1
            fi
            if [ "$NEED_CAND_REBUILD" = "1" ]; then
                CAND_ORIG="${RESCUE_DIR}/start.sh.orig.cand.$$.tmp"
                rm -f "$CAND_ORIG" 2>/dev/null || true
                if awk '
                BEGIN { skip = 0 }
                /^# open-kobra-rescue\/begin/ { skip = 1; next }
                /^# open-kobra-rescue\/end/ { skip = 0; next }
                skip == 0 { print }
                ' "$STOCK_START_SH" > "$CAND_ORIG" 2>/dev/null && start_sh_sane_for_orig "$CAND_ORIG"; then
                    atomic_copy_file "$CAND_ORIG" "$RESCUE_DIR/start.sh.orig" || fail_install "start.sh.orig yedeği alınamadı."
                    if [ -n "$FW_VER" ]; then
                        ORIG_FW_TMP="$RESCUE_DIR/start.sh.orig.fw.openkobra.tmp"
                        printf '%s\n' "$FW_VER" > "$ORIG_FW_TMP" && sync && mv "$ORIG_FW_TMP" "$RESCUE_DIR/start.sh.orig.fw" && sync || {
                            rm -f "$ORIG_FW_TMP" 2>/dev/null || true
                            fail_install "start.sh.orig.fw yazılamadı."
                        }
                    fi
                    rescue_install_log "start.sh.orig kanca dışı kısımdan sağlam olarak yeniden üretildi ve doğrulandı (firmware '$FW_VER')."
                elif [ -f "$RESCUE_DIR/start.sh.orig" ] && [ -z "$ORIG_FW_CUR" ] && [ -n "$FW_VER" ] \
                    && [ -f "$STOCK_DEST_DIR/.verified" ] && start_sh_sane_for_orig "$RESCUE_DIR/start.sh.orig"; then
                    ORIG_FW_TMP="$RESCUE_DIR/start.sh.orig.fw.openkobra.tmp"
                    printf '%s\n' "$FW_VER" > "$ORIG_FW_TMP" && sync && mv "$ORIG_FW_TMP" "$RESCUE_DIR/start.sh.orig.fw" && sync || {
                        rm -f "$ORIG_FW_TMP" 2>/dev/null || true
                        fail_install "start.sh.orig.fw yazılamadı."
                    }
                    rescue_install_log "start.sh.orig.fw eksikti; doğrulanmış stok kopyaya dayanarak $FW_VER olarak yazıldı."
                else
                    rescue_install_log "UYARI: start.sh kanca dışı kısmı sağlam görünmüyor; start.sh.orig yeniden üretilemedi."
                fi
                rm -f "$CAND_ORIG" 2>/dev/null || true
            fi
        fi

        HAD_RINKHALS_BLOCK=0
        if file_has_literal "$STOCK_START_SH" "# Rinkhals/begin"; then
            HAD_RINKHALS_BLOCK=1
        fi

        TMP_START_BASE="${STOCK_START_SH}.openkobra.base.tmp"
        TMP_START_NEW="${STOCK_START_SH}.openkobra.tmp"
        rm -f "$TMP_START_BASE" "$TMP_START_NEW" 2>/dev/null || true

        awk '
        BEGIN { skip = 0 }
        /^# open-kobra-rescue\/begin/ { skip = 1; next }
        /^# open-kobra-rescue\/end/ { skip = 0; next }
        skip == 0 { print }
        ' "$STOCK_START_SH" > "$TMP_START_BASE" || {
            rm -f "$TMP_START_BASE" 2>/dev/null || true
            fail_install "start.sh temel içeriği ayrıştırılamadı."
        }

        cp -PRp "$STOCK_START_SH" "$TMP_START_NEW" || {
            rm -f "$TMP_START_BASE" "$TMP_START_NEW" 2>/dev/null || true
            fail_install "start.sh geçici kopyası oluşturulamadı."
        }

        # The hook block is kept as a file too: rescue.sh rebuilds the expected start.sh from it (B-05).
        HOOK_TMP="$RESCUE_DIR/hook.block.openkobra.tmp"
        cat > "$HOOK_TMP" <<'EOF' || {
# open-kobra-rescue/begin
_OKR="${OPEN_KOBRA_ROOT:-}/useremain/open-kobra/rescue/rescue.sh"
if [ -f "$_OKR" ]; then
    sh "$_OKR" hook >/dev/null 2>&1
    if [ "$?" = "75" ] && [ -z "${OPEN_KOBRA_RESCUE_REEXEC:-}" ]; then
        OPEN_KOBRA_RESCUE_REEXEC=1; export OPEN_KOBRA_RESCUE_REEXEC
        sh "${OPEN_KOBRA_ROOT:-}/userdata/app/gk/start.sh" "$@"; exit $?
    fi
fi
# open-kobra-rescue/end
EOF
            rm -f "$HOOK_TMP" "$TMP_START_BASE" "$TMP_START_NEW" 2>/dev/null || true
            fail_install "hook.block oluşturulamadı."
        }
        sync && mv "$HOOK_TMP" "$RESCUE_DIR/hook.block" && sync || {
            rm -f "$HOOK_TMP" "$TMP_START_BASE" "$TMP_START_NEW" 2>/dev/null || true
            fail_install "hook.block yerine taşınamadı."
        }

        {
            cat "$RESCUE_DIR/hook.block"
            cat "$TMP_START_BASE"
        } > "$TMP_START_NEW" || {
            rm -f "$TMP_START_BASE" "$TMP_START_NEW" 2>/dev/null || true
            fail_install "start.sh kurtarma kancası yazılamadı."
        }
        rm -f "$TMP_START_BASE" 2>/dev/null || true

        BEGIN_CNT=$(awk '/^# open-kobra-rescue\/begin$/ { c++ } END { print c+0 }' "$TMP_START_NEW")
        END_CNT=$(awk '/^# open-kobra-rescue\/end$/ { c++ } END { print c+0 }' "$TMP_START_NEW")
        if [ ! -s "$TMP_START_NEW" ] || [ "$BEGIN_CNT" != "1" ] || [ "$END_CNT" != "1" ] || ! check_rescue_block_balance "$TMP_START_NEW"; then
            rm -f "$TMP_START_NEW" 2>/dev/null || true
            fail_install "Güncellenen start.sh bütünlük doğrulamasından geçemedi."
        fi
        if [ "$HAD_RINKHALS_BLOCK" = "1" ]; then
            if ! file_has_literal "$TMP_START_NEW" "# Rinkhals/begin" || ! file_has_literal "$TMP_START_NEW" "# Rinkhals/end"; then
                rm -f "$TMP_START_NEW" 2>/dev/null || true
                fail_install "start.sh içindeki Rinkhals bloğu doğrulamada kayboldu."
            fi
        fi

        sync
        if mv "$TMP_START_NEW" "$STOCK_START_SH" && sync; then
            RESCUE_START_MODIFIED=1
            rescue_install_log "Açılış kurtarma kancası eklendi (en başa): $STOCK_START_SH"
        else
            rm -f "$TMP_START_NEW" 2>/dev/null || true
            fail_install "Güncellenen start.sh yerine taşınamadı: $STOCK_START_SH"
        fi
    fi
else
    rescue_install_log "UYARI: $STOCK_START_SH bulunamadı; açılış kurtarma kancası atlandı."
fi
maybe_crash_point "after_rescue"

# 12. Commit point: flush all artifacts then write installed version marker atomically
if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_FAIL_AT:-}" = "version_marker" ]; then
    fail_install "Test arızası tetiklendi: version_marker"
fi
sync
VER_TMP="${VERSION_MARKER}.openkobra.tmp"
printf '%s\n' "$PKG_VERSION" > "$VER_TMP" && sync && mv "$VER_TMP" "$VERSION_MARKER" && sync || {
    rm -f "$VER_TMP" 2>/dev/null || true
    fail_install "Sürüm işaret dosyası yazılamadı: $VERSION_MARKER"
}
log_msg "Kurulu sürüm işaretlendi: $VERSION_MARKER ($PKG_VERSION)"

ROLLBACK_ACTIVE=0
trap - INT TERM HUP 2>/dev/null || true

# Restart stopped apps that are enabled
restart_stopped_apps 1

# Prune older dated install backups keeping newest 2 (open-kobra-baseline is never pruned here)
OLD_COUNT=0
PRUNE_LIST="$BAK_BASE/.openkobra.prune.tmp"
for BAK_CAND in "$BAK_BASE"/open-kobra-[0-9]*; do
    [ -d "$BAK_CAND" ] || continue
    printf '%s\n' "$BAK_CAND"
done | sort -r > "$PRUNE_LIST" || true
while IFS= read -r BAK_PATH; do
    [ -n "$BAK_PATH" ] && [ -d "$BAK_PATH" ] || continue
    OLD_COUNT=$((OLD_COUNT + 1))
    if [ "$OLD_COUNT" -gt 2 ]; then
        rm -rf "$BAK_PATH" 2>/dev/null || log_msg "UYARI: Eski yedek silinemedi: $BAK_PATH"
    fi
done < "$PRUNE_LIST"
rm -f "$PRUNE_LIST" 2>/dev/null || true

# Clean extracted staging folder only if UPDATE_PATH is exact /useremain/update_swu
EXPECTED_UPDATE_DIR="$ROOT/useremain/update_swu"
if [ "$UPDATE_PATH" = "$EXPECTED_UPDATE_DIR" ] && [ ! -L "$UPDATE_PATH" ]; then
    log_msg "Geçici güncelleme dizini temizleniyor: $UPDATE_PATH"
    cd "${ROOT:-/}" || cd / || true
    rm -rf "$UPDATE_PATH" || log_msg "UYARI: Geçici güncelleme dizini temizlenemedi: $UPDATE_PATH"
fi

# Remove USB update.swu only after successful install so stock updater won't re-trigger it on reboot
if [ -f "$USB_DIR/update.swu" ] && [ ! -L "$USB_DIR/update.swu" ]; then
    if rm -f "$USB_DIR/update.swu" 2>/dev/null; then
        log_msg "USB paket dosyası temizlendi: $USB_DIR/update.swu"
    else
        log_msg "UYARI: USB paket dosyası silinemedi: $USB_DIR/update.swu"
    fi
fi

sync 2>/dev/null || true
log_msg "Kurulum başarıyla tamamlandı (sürüm $PKG_VERSION)."
log_msg "NOT: kobra-screen ve ace-standalone varsayılan olarak KAPALI kuruldu. Etkinleştirmek için Rinkhals menüsünü (Manage apps) kullanın."
beep 500
exit 0

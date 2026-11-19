#!/bin/sh
# open-kobra uninstaller update.sh (runs on Kobra 3 via Rinkhals / SWU updater)
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

LOG_INTERNAL="$RINKHALS_DIR/open-kobra-uninstall.log"
LOG_USB=""
if [ -d "$USB_DIR" ]; then
    LOG_USB="$USB_DIR/open-kobra-uninstall.log"
elif [ -d "$USB_ROOT" ]; then
    LOG_USB="$USB_ROOT/open-kobra-uninstall.log"
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

strip_rescue_hook_block() {
    ST_F="$1"
    [ -f "$ST_F" ] || return 0
    if [ -L "$ST_F" ]; then
        log_msg "UYARI: $ST_F bir sembolik bağ; doğrudan üzerine yazma reddedildi."
        return 0
    fi
    if ! check_rescue_block_balance "$ST_F"; then
        log_msg "HATA: $ST_F içinde eşleşmemiş '# open-kobra-rescue/begin' veya '# open-kobra-rescue/end' var."
        return 1
    fi
    if file_has_literal "$ST_F" "# open-kobra-rescue/begin"; then
        TMP_ST="${ST_F}.openkobra.uninstall.tmp"
        cp -PRp "$ST_F" "$TMP_ST" || {
            rm -f "$TMP_ST" 2>/dev/null || true
            return 1
        }
        if ! awk '
        BEGIN { skip = 0 }
        /^# open-kobra-rescue\/begin/ { skip = 1; next }
        /^# open-kobra-rescue\/end/ { skip = 0; next }
        skip == 0 { print }
        ' "$ST_F" > "$TMP_ST"; then
            rm -f "$TMP_ST" 2>/dev/null || true
            log_msg "HATA: start.sh kurtarma bloğu temizlenemedi: $ST_F"
            return 1
        fi
        if [ ! -s "$TMP_ST" ] && [ -s "$ST_F" ]; then
            rm -f "$TMP_ST" 2>/dev/null || true
            log_msg "HATA: Temizlenen start.sh beklenmedik şekilde boş çıktı: $ST_F"
            return 1
        fi
        if file_has_literal "$TMP_ST" "# open-kobra-rescue/begin" || file_has_literal "$TMP_ST" "# open-kobra-rescue/end"; then
            rm -f "$TMP_ST" 2>/dev/null || true
            log_msg "HATA: start.sh içinden kurtarma bloğu tam temizlenemedi: $ST_F"
            return 1
        fi
        sync
        if ! mv "$TMP_ST" "$ST_F"; then
            rm -f "$TMP_ST" 2>/dev/null || true
            log_msg "HATA: Temizlenen start.sh yerine taşınamadı: $ST_F"
            return 1
        fi
        sync
        log_msg "Açılış kurtarma bloğu temizlendi: $ST_F"
    fi
    return 0
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

# kobra-screen stops the stock K3SysUi while it runs and its "stop" does not bring it back. When we stop (and then
# delete) a running kobra-screen the printer would be left without any UI (B-05): restart the stock UI.
# On a real printer only (ROOT empty); tests opt in with OPEN_KOBRA_ENSURE_STOCK_UI=1.
stock_ui_running() {
    for SU_COMM in "$ROOT"/proc/[0-9]*/comm; do
        [ -r "$SU_COMM" ] || continue
        SU_NAME=$(cat "$SU_COMM" 2>/dev/null || true)
        [ "$SU_NAME" = "K3SysUi" ] && return 0
    done
    return 1
}

ensure_stock_ui() {
    if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_ENSURE_STOCK_UI:-0}" != "1" ]; then
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
    log_msg "kobra-screen kaldırıldı/durduruldu: stok arayüz (K3SysUi) yeniden başlatıldı."
}

abort_uninstall() {
    log_msg "HATA: $*"
    log_msg "Kurtarma: Sorunu giderip aynı kaldırma paketini yeniden uygulayın; yedekler korundu."
    beep_error
    exit 1
}

is_klipper_user_cfg_rel() {
    case "$1" in
        */printer.klipper.cfg|printer.klipper.cfg|*/printer.cfg|printer.cfg)
            return 0
            ;;
    esac
    return 1
}

is_allowed_manifest_rel() {
    REL_CHK="$1"
    case "$REL_CHK" in
        ""|/*|*..*|.)
            return 1
            ;;
        useremain/home/rinkhals/*)
            return 0
            ;;
        useremain/rinkhals/*/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py \
        |useremain/rinkhals/*/home/rinkhals/apps/vanilla-klipper/klippy/extras/ace \
        |useremain/rinkhals/*/home/rinkhals/apps/vanilla-klipper/klippy/extras/temperature_ace.py \
        |useremain/rinkhals/*/home/rinkhals/apps/vanilla-klipper/ace_K3.cfg \
        |useremain/rinkhals/*/home/rinkhals/apps/vanilla-klipper/ace_K3.cfg.new)
            SUB="${REL_CHK#useremain/rinkhals/}"
            SUB="${SUB%%/home/rinkhals/apps/vanilla-klipper/*}"
            case "$SUB" in
                ""|*/*) return 1 ;;
                *) return 0 ;;
            esac
            ;;
    esac
    return 1
}

check_uninstall_free_space_kb() {
    if [ ! -w "$ROOT/useremain" ]; then
        return 1
    fi
    PRE_EST_KB=0
    for EST_P in \
        "$CONFIG_DIR/printer.klipper.cfg" \
        "$CONFIG_DIR/printer.cfg" \
        "$CONFIG_DIR/leviq3.cfg" \
        "$CONFIG_DIR/leviq3.cfg.new" \
        "$CONFIG_DIR/open_kobra_homing.cfg" \
        "$CONFIG_DIR/open_kobra_homing.cfg.new" \
        "$CONFIG_DIR/open_kobra_limits.cfg" \
        "$CONFIG_DIR/open_kobra_limits_zz_ekstruzyon_yasak.cfg" \
        "$CONFIG_DIR/leviq3_state.json" \
        "$RINKHALS_HOME/open-kobra" \
        "$USER_APPS_DIR/ace-standalone/config.json" \
        "$USER_APPS_DIR/kobra-screen/config.json"; do
        if [ -e "$EST_P" ]; then
            P_KB=$(du -sk "$EST_P" 2>/dev/null | awk '{print $1}' || true)
            case "$P_KB" in
                ''|*[!0-9]*) ;;
                *) PRE_EST_KB=$((PRE_EST_KB + P_KB)) ;;
            esac
        fi
    done
    NEED_KB=$((PRE_EST_KB + 128))
    [ "$NEED_KB" -lt 256 ] && NEED_KB=256
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

atomic_restore_target() {
    SRC="$1"
    TARGET="$2"
    if [ -d "$SRC" ] && [ ! -L "$SRC" ]; then
        # Stage next to the target, then swap with two renames: a power cut never leaves a half-copied directory.
        mkdir -p "$(dirname "$TARGET")" || return 1
        STAGE_R="${TARGET}.openkobra.restore.new"
        OLD_R="${TARGET}.openkobra.restore.old"
        rm -rf "$STAGE_R" "$OLD_R" || return 1
        cp -PRp "$SRC" "$STAGE_R" || { rm -rf "$STAGE_R" 2>/dev/null || true; return 1; }
        sync
        if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
            mv "$TARGET" "$OLD_R" || { rm -rf "$STAGE_R" 2>/dev/null || true; return 1; }
        fi
        if ! mv "$STAGE_R" "$TARGET"; then
            if [ -e "$OLD_R" ] || [ -L "$OLD_R" ]; then
                mv "$OLD_R" "$TARGET" 2>/dev/null || true
            fi
            rm -rf "$STAGE_R" 2>/dev/null || true
            return 1
        fi
        sync
        rm -rf "$OLD_R" 2>/dev/null || true
        return 0
    fi
    mkdir -p "$(dirname "$TARGET")" || return 1
    TMP_R="${TARGET}.openkobra.restore.tmp"
    rm -f "$TMP_R" 2>/dev/null || true
    cp -PRp "$SRC" "$TMP_R" && sync && mv "$TMP_R" "$TARGET" && sync
    RC_AR=$?
    if [ "$RC_AR" -ne 0 ]; then
        rm -f "$TMP_R" 2>/dev/null || true
    fi
    return "$RC_AR"
}

strip_open_kobra_block() {
    CFG="$1"
    [ -f "$CFG" ] || return 0
    if [ -L "$CFG" ]; then
        log_msg "HATA: $CFG bir sembolik bağ; doğrudan üzerine yazma reddedildi."
        return 1
    fi
    if ! check_block_balance "$CFG"; then
        log_msg "HATA: $CFG içinde eşleşmemiş '# open-kobra/begin' veya '# open-kobra/end' işareti var; SAVE_CONFIG kaybını önlemek için düzenlenmedi."
        return 1
    fi
    if file_has_literal "$CFG" "# open-kobra/begin" || file_has_literal "$CFG" "open-kobra/guard-disabled"; then
        HAD_SAVE_CFG=0
        if file_has_literal "$CFG" "#*# <---------------------- SAVE_CONFIG ---------------------->"; then
            HAD_SAVE_CFG=1
        fi
        TMP_CFG="${CFG}.openkobra.uninstall.tmp"
        if ! awk '
        BEGIN { skip = 0 }
        /^# open-kobra\/begin/ { skip = 1; next }
        /^# open-kobra\/end/ { skip = 0; next }
        skip == 1 { next }
        /open-kobra\/guard-disabled/ { next }
        { print }
        ' "$CFG" > "$TMP_CFG"; then
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_msg "HATA: awk include temizliği başarısız: $CFG"
            return 1
        fi
        if [ ! -s "$TMP_CFG" ] && [ -s "$CFG" ]; then
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_msg "HATA: Temizlenen config beklenmedik şekilde boş çıktı: $CFG"
            return 1
        fi
        if [ "$HAD_SAVE_CFG" = "1" ] && ! file_has_literal "$TMP_CFG" "#*# <---------------------- SAVE_CONFIG ---------------------->"; then
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_msg "HATA: Temizlik sırasında SAVE_CONFIG bloğu kayboldu: $CFG"
            return 1
        fi
        # Only ACTIVE leftovers count: a user's own commented-out "# [include leviq3.cfg]" outside our block is not ours (B-06).
        if awk '
            /^# open-kobra\/(begin|end)/ { bad = 1 }
            /^[[:space:]]*\[include[[:space:]]+(leviq3\.cfg|open_kobra_homing\.cfg|open_kobra_limits\*\.cfg)\][[:space:]]*(#.*|;.*)?$/ { bad = 1 }
            END { exit !bad }
        ' "$TMP_CFG"; then
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_msg "HATA: Include bloğu tam temizlenemedi: $CFG"
            return 1
        fi
        sync
        if ! mv "$TMP_CFG" "$CFG"; then
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_msg "HATA: Temizlenen config yerine taşınamadı: $CFG"
            return 1
        fi
        sync
        log_msg "Include bloğu temizlendi: $CFG"
    fi
    return 0
}

log_msg "open-kobra kaldırma işlemi başlatılıyor..."

if [ ! -d "$RINKHALS_DIR" ]; then
    abort_uninstall "Rinkhals dizini bulunamadı ($RINKHALS_DIR)."
fi

if [ ! -d "$RINKHALS_HOME" ] || [ ! -w "$RINKHALS_HOME" ]; then
    abort_uninstall "Rinkhals kullanıcı dizini yazılabilir değil: $RINKHALS_HOME"
fi

STOCK_START_SH="$ROOT/userdata/app/gk/start.sh"
if [ -f "$STOCK_START_SH" ] && [ ! -L "$STOCK_START_SH" ] && ! check_rescue_block_balance "$STOCK_START_SH"; then
    abort_uninstall "$STOCK_START_SH içinde eşleşmemiş '# open-kobra-rescue/begin' veya '# open-kobra-rescue/end' var; veri kaybını önlemek için hiçbir şeye dokunulmadı."
fi

# 1. Pre-destructive integrity checks BEFORE touching any installed files
for CHK_CFG in "$CONFIG_DIR/printer.klipper.cfg" "$CONFIG_DIR/printer.cfg"; do
    if [ -L "$CHK_CFG" ]; then
        abort_uninstall "$CHK_CFG bir sembolik bağ; veri güvenliği için hiçbir şeye dokunulmadı."
    fi
    if [ -f "$CHK_CFG" ] && ! check_block_balance "$CHK_CFG"; then
        abort_uninstall "$CHK_CFG içinde eşleşmemiş '# open-kobra/begin' veya '# open-kobra/end' var; veri kaybını önlemek için hiçbir şeye dokunulmadı."
    fi
done

HAS_VALID_BASELINE=0
if [ -d "$BASELINE_DIR" ]; then
    if [ ! -f "$BASELINE_DIR/.complete" ] || [ ! -f "$BASELINE_DIR/created.list" ] || [ ! -f "$BASELINE_DIR/modified.list" ]; then
        abort_uninstall "İlk durum yedeği bütünlüğü bozuk (.complete veya liste eksik: $BASELINE_DIR) — hiçbir dosyaya dokunulmadı."
    fi
    while IFS= read -r REL; do
        [ -z "$REL" ] && continue
        if ! is_allowed_manifest_rel "$REL"; then
            abort_uninstall "Güvensiz created.list hedef yolu reddedildi ($REL) — hiçbir dosyaya dokunulmadı."
        fi
    done < "$BASELINE_DIR/created.list"
    while IFS= read -r REL; do
        [ -z "$REL" ] && continue
        if ! is_allowed_manifest_rel "$REL"; then
            abort_uninstall "Güvensiz modified.list hedef yolu reddedildi ($REL) — hiçbir dosyaya dokunulmadı."
        fi
        if is_klipper_user_cfg_rel "$REL"; then
            continue
        fi
        SRC="$BASELINE_DIR/files/$REL"
        if [ ! -e "$SRC" ] && [ ! -L "$SRC" ]; then
            abort_uninstall "Yedek dosyası eksik ($SRC) — hiçbir uygulama veya yapılandırma silinmeden durduruldu."
        fi
    done < "$BASELINE_DIR/modified.list"
    HAS_VALID_BASELINE=1
fi

if [ -f "$TARGETS_MANIFEST" ]; then
    while IFS= read -r REL; do
        [ -z "$REL" ] && continue
        if ! is_allowed_manifest_rel "$REL"; then
            abort_uninstall "Güvensiz targets.list hedef yolu reddedildi ($REL) — hiçbir dosyaya dokunulmadı."
        fi
    done < "$TARGETS_MANIFEST"
fi

ACE_RESTORED=0
if [ "$HAS_VALID_BASELINE" = "1" ] && [ -f "$BASELINE_DIR/ace-orig.md5" ]; then
    # The saved vanilla ACE driver must be intact BEFORE anything is removed, otherwise we would swap a good driver for a bad copy.
    while IFS= read -r ACE_MLINE; do
        [ -n "$ACE_MLINE" ] || continue
        ACE_EXP=$(printf '%s\n' "$ACE_MLINE" | awk '{print $1}')
        ACE_REL_FILE=$(printf '%s\n' "$ACE_MLINE" | awk '{$1=""; sub(/^[ \t]+/, ""); print}')
        if [ ! -f "$BASELINE_DIR/files/$ACE_REL_FILE" ]; then
            abort_uninstall "ACE sürücü orijinal yedeği eksik ($BASELINE_DIR/files/$ACE_REL_FILE) — hiçbir dosyaya dokunulmadı."
        fi
        ACE_ACT=$(md5sum "$BASELINE_DIR/files/$ACE_REL_FILE" | awk '{print $1}')
        if [ "$ACE_ACT" != "$ACE_EXP" ]; then
            abort_uninstall "ACE sürücü orijinal yedeği bozuk (MD5 uyuşmuyor: $ACE_REL_FILE) — hiçbir dosyaya dokunulmadı."
        fi
    done < "$BASELINE_DIR/ace-orig.md5"
fi

if ! check_uninstall_free_space_kb; then
    abort_uninstall "/useremain üzerinde ön-kaldırma yedeği için yeterli yazılabilir alan yok."
fi

# 2. Save full pre-uninstall safety snapshot BEFORE deleting or rewriting anything
DATE_TAG=$(date '+%Y%m%d-%H%M%S')
PRE_BAK="$BAK_BASE/open-kobra-pre-uninstall-$DATE_TAG"
if [ -e "$PRE_BAK" ]; then
    N=1
    while [ -e "${PRE_BAK}-${N}" ]; do
        N=$((N + 1))
    done
    PRE_BAK="${PRE_BAK}-${N}"
fi
mkdir -p "$PRE_BAK" || abort_uninstall "Kaldırma öncesi güvenlik yedeği dizini oluşturulamadı: $PRE_BAK"
for BAK_ITEM in \
    "$CONFIG_DIR/printer.klipper.cfg" \
    "$CONFIG_DIR/printer.cfg" \
    "$CONFIG_DIR/leviq3.cfg" \
    "$CONFIG_DIR/leviq3.cfg.new" \
    "$CONFIG_DIR/open_kobra_homing.cfg" \
    "$CONFIG_DIR/open_kobra_homing.cfg.new" \
    "$CONFIG_DIR/open_kobra_limits.cfg" \
    "$CONFIG_DIR/open_kobra_limits_zz_ekstruzyon_yasak.cfg" \
    "$CONFIG_DIR/leviq3_state.json" \
    "$VERSION_MARKER" \
    "$RINKHALS_HOME/open-kobra" \
    "$USER_APPS_DIR/ace-standalone.config" \
    "$USER_APPS_DIR/kobra-screen.config" \
    "$USER_APPS_DIR/05-open-kobra-guard.config" \
    "$STOCK_START_SH"; do
    if [ -e "$BAK_ITEM" ] || [ -L "$BAK_ITEM" ]; then
        cp -PRp "$BAK_ITEM" "$PRE_BAK/" || abort_uninstall "Kaldırma öncesi güvenlik yedeği alınamadı: $BAK_ITEM"
    fi
done
if [ "$HAS_VALID_BASELINE" = "1" ]; then
    # Current (driver-modified) ACE targets, kept with their full relative path so nothing collides.
    PRE_ACE_LIST="$PRE_BAK/.ace_targets.txt"
    { cat "$BASELINE_DIR/modified.list" "$BASELINE_DIR/created.list" 2>/dev/null; [ -f "$TARGETS_MANIFEST" ] && cat "$TARGETS_MANIFEST"; } \
        | awk 'NF { print }' | sort -u > "$PRE_ACE_LIST" || abort_uninstall "ACE hedef listesi oluşturulamadı."
    while IFS= read -r ACE_REL; do
        case "$ACE_REL" in
            */klippy/extras/ace|*/klippy/extras/temperature_ace.py|*/vanilla-klipper/ace_K3.cfg|*/vanilla-klipper/ace_K3.cfg.new|*/printer_data/config/ace_K3.cfg.new) ;;
            *) continue ;;
        esac
        if [ -e "$ROOT/$ACE_REL" ] || [ -L "$ROOT/$ACE_REL" ]; then
            mkdir -p "$PRE_BAK/ace-driver/$(dirname "$ACE_REL")" || abort_uninstall "ACE güvenlik yedeği dizini oluşturulamadı."
            cp -PRp "$ROOT/$ACE_REL" "$PRE_BAK/ace-driver/$ACE_REL" || abort_uninstall "ACE güvenlik yedeği alınamadı: $ACE_REL"
        fi
    done < "$PRE_ACE_LIST"
    rm -f "$PRE_ACE_LIST" 2>/dev/null || true
fi
for APP_CFG_NAME in ace-standalone kobra-screen; do
    APP_CFG_JSON="$USER_APPS_DIR/$APP_CFG_NAME/config.json"
    if [ -f "$APP_CFG_JSON" ] || [ -L "$APP_CFG_JSON" ]; then
        mkdir -p "$PRE_BAK/$APP_CFG_NAME" || abort_uninstall "Uygulama ayar yedeği dizini oluşturulamadı: $PRE_BAK/$APP_CFG_NAME"
        cp -PRp "$APP_CFG_JSON" "$PRE_BAK/$APP_CFG_NAME/config.json" || abort_uninstall "Uygulama ayar yedeği alınamadı: $APP_CFG_JSON"
    fi
done
sync
log_msg "Kaldırma öncesi güvenlik yedeği alındı: $PRE_BAK"

# 3. Stop running apps cleanly before modifying configs or removing files
RESCUE_WATCH_PID_FILE="$ROOT/tmp/open-kobra-rescue-watch.pid"
if [ -f "$RESCUE_WATCH_PID_FILE" ]; then
    RW_PID=$(tr -d ' \t\r\n' < "$RESCUE_WATCH_PID_FILE" 2>/dev/null || true)
    case "$RW_PID" in
        ''|*[!0-9]*) ;;
        *)
            if kill -0 "$RW_PID" 2>/dev/null; then
                RW_CMDLINE="${OPEN_KOBRA_PROC_ROOT:-$ROOT/proc}/$RW_PID/cmdline"
                [ -r "$RW_CMDLINE" ] || RW_CMDLINE="/proc/$RW_PID/cmdline"
                if [ -r "$RW_CMDLINE" ] && tr '\0' ' ' < "$RW_CMDLINE" 2>/dev/null | awk 'index($0, "rescue.sh") > 0 { f=1; exit } END { exit !f }'; then
                    kill "$RW_PID" 2>/dev/null || true
                fi
            fi
            ;;
    esac
    rm -f "$RESCUE_WATCH_PID_FILE" 2>/dev/null || true
fi

KOBRA_SCREEN_WAS_RUNNING=0
for APP_NAME in kobra-screen ace-standalone 05-open-kobra-guard; do
    if [ -x "$USER_APPS_DIR/$APP_NAME/app.sh" ]; then
        APP_STAT=$(OPEN_KOBRA_ROOT="$ROOT" "$USER_APPS_DIR/$APP_NAME/app.sh" status 2>/dev/null || true)
        if printf '%s\n' "$APP_STAT" | awk 'index($0, "Status: started") > 0 { found=1; exit } END { exit !found }'; then
            if [ "$APP_NAME" = "kobra-screen" ]; then
                KOBRA_SCREEN_WAS_RUNNING=1
            fi
            OPEN_KOBRA_ROOT="$ROOT" "$USER_APPS_DIR/$APP_NAME/app.sh" stop >/dev/null 2>&1 || log_msg "UYARI: Durdurma sırasında hata: $APP_NAME"
        fi
    fi
done

# 4. Strip ONLY our include block from printer.klipper.cfg / printer.cfg FIRST
# so Klipper remains bootable at every subsequent intermediate step if interrupted.
strip_open_kobra_block "$CONFIG_DIR/printer.klipper.cfg" || abort_uninstall "printer.klipper.cfg içinden include bloğu temizlenemedi."
strip_open_kobra_block "$CONFIG_DIR/printer.cfg" || abort_uninstall "printer.cfg içinden include bloğu temizlenemedi."
if [ -n "$ROOT" ] && [ "${OPEN_KOBRA_UNINSTALL_FAIL_AT:-}" = "after_strip" ]; then
    abort_uninstall "Test arızası tetiklendi: after_strip"
fi

# 5. Remove tracked created targets (or fallback files) and runtime state
if [ "$HAS_VALID_BASELINE" = "1" ]; then
    log_msg "Oluşturulan open-kobra hedefleri temizleniyor..."

    CLEAN_LIST="${BASELINE_DIR}/merged_created.openkobra.tmp"
    {
        [ -f "$BASELINE_DIR/created.list" ] && cat "$BASELINE_DIR/created.list"
        [ -f "$TARGETS_MANIFEST" ] && cat "$TARGETS_MANIFEST"
    } | awk 'NF { print }' | sort -u > "$CLEAN_LIST" || abort_uninstall "Kaldırma hedef listesi oluşturulamadı."

    while IFS= read -r REL; do
        [ -z "$REL" ] && continue
        if ! is_allowed_manifest_rel "$REL"; then
            rm -f "$CLEAN_LIST" 2>/dev/null || true
            abort_uninstall "Güvensiz kaldırma hedefi reddedildi: $REL"
        fi
        if is_klipper_user_cfg_rel "$REL"; then
            continue
        fi
        # Skip if present in modified.list (will be restored from baseline files instead)
        if [ -f "$BASELINE_DIR/modified.list" ] && awk -v t="$REL" '$0 == t { f=1; exit } END { exit !f }' "$BASELINE_DIR/modified.list"; then
            continue
        fi
        TARGET="$ROOT/$REL"
        if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
            rm -rf "$TARGET" || {
                rm -f "$CLEAN_LIST" 2>/dev/null || true
                abort_uninstall "Oluşturulan dosya silinemedi: $TARGET"
            }
            [ ! -e "$TARGET" ] && [ ! -L "$TARGET" ] || {
                rm -f "$CLEAN_LIST" 2>/dev/null || true
                abort_uninstall "Silme sonrası dosya hala mevcut: $TARGET"
            }
            log_msg "Kaldırıldı: /$REL"
        fi
    done < "$CLEAN_LIST"
    rm -f "$CLEAN_LIST" 2>/dev/null || true
else
    log_msg "İlk durum yedeği bulunamadı, bilinen open-kobra dosyaları temizleniyor..."

    RINKHALS_VER=$(tr -d ' \t\r\n' < "$RINKHALS_DIR/.version" 2>/dev/null || true)
    RINKHALS_CURRENT=""
    if [ -d "$RINKHALS_DIR/.current" ]; then
        RINKHALS_CURRENT="$RINKHALS_DIR/.current"
    elif [ -n "$RINKHALS_VER" ] && [ -d "$RINKHALS_DIR/$RINKHALS_VER" ]; then
        RINKHALS_CURRENT="$RINKHALS_DIR/$RINKHALS_VER"
    fi

    for VKLIP in \
        "$USER_APPS_DIR/vanilla-klipper" \
        "$RINKHALS_CURRENT/home/rinkhals/apps/vanilla-klipper"; do
        if [ -f "$VKLIP/klippy/extras/leviq3.py" ]; then
            rm -f "$VKLIP/klippy/extras/leviq3.py" || abort_uninstall "leviq3.py silinemedi: $VKLIP/klippy/extras/leviq3.py"
            log_msg "Kaldırıldı: $VKLIP/klippy/extras/leviq3.py"
        fi
    done

    rm -f "$CONFIG_DIR/leviq3.cfg" "$CONFIG_DIR/leviq3.cfg.new" "$CONFIG_DIR/open_kobra_homing.cfg" "$CONFIG_DIR/open_kobra_homing.cfg.new" "$CONFIG_DIR/open_kobra_limits.cfg" "$CONFIG_DIR/open_kobra_limits_zz_ekstruzyon_yasak.cfg" || abort_uninstall "Yapılandırma ek dosyaları silinemedi."

    if [ -f "$RINKHALS_HOME/open-kobra/ace-driver.version" ]; then
        log_msg "UYARI: İlk durum yedeği yok; ACE sürücü dosyalarına dokunulmadı (orijinal vanilla sürücü bilinmiyor). Orijinali için vanilla-klipper uygulamasını yeniden kurun."
    fi
fi

# Always clean runtime state, persistent open-kobra dir, and version marker
rm -f "$CONFIG_DIR/leviq3_state.json" "$CONFIG_DIR/leviq3_state.json.tmp" "$CONFIG_DIR/leviq3.cfg.new" "$CONFIG_DIR/open_kobra_homing.cfg.new" "$CONFIG_DIR/ace_K3.cfg.new" || abort_uninstall "leviq3/homing durum dosyaları silinemedi."
rm -rf "$RINKHALS_HOME/open-kobra" || abort_uninstall "open-kobra kalıcı dizini silinemedi."
rm -f "$VERSION_MARKER" || abort_uninstall "Sürüm işaret dosyası silinemedi."

# Remove boot rescue hook and /useremain/open-kobra/rescue (only our files)
strip_rescue_hook_block "$STOCK_START_SH" || abort_uninstall "start.sh içinden kurtarma bloğu temizlenemedi."
rm -rf "$ROOT/useremain/open-kobra/rescue" || abort_uninstall "Kurtarma dizini silinemedi: $ROOT/useremain/open-kobra/rescue"
[ ! -e "$ROOT/useremain/open-kobra/rescue" ] || abort_uninstall "Kurtarma dizini silindikten sonra hala duruyor: $ROOT/useremain/open-kobra/rescue"
rmdir "$ROOT/useremain/open-kobra" 2>/dev/null || true

# 6. Remove Rinkhals app dirs and enable/disable/config/installing markers
for APP_NAME in kobra-screen ace-standalone 05-open-kobra-guard; do
    rm -rf "$USER_APPS_DIR/$APP_NAME" "${USER_APPS_DIR}/${APP_NAME}.openkobra.new" "${USER_APPS_DIR}/${APP_NAME}.openkobra.old" || abort_uninstall "Uygulama dizini silinemedi: $USER_APPS_DIR/$APP_NAME"
    [ ! -e "$USER_APPS_DIR/$APP_NAME" ] || abort_uninstall "Uygulama dizini hala duruyor: $USER_APPS_DIR/$APP_NAME"
    rm -f "$USER_APPS_DIR/${APP_NAME}.enabled" \
          "$USER_APPS_DIR/${APP_NAME}.disabled" \
          "$USER_APPS_DIR/${APP_NAME}.openkobra.installing" \
          "$USER_APPS_DIR/${APP_NAME}.openkobra.was_enabled" \
          "$USER_APPS_DIR/${APP_NAME}.openkobra.was_disabled" || abort_uninstall "Uygulama işaretçileri silinemedi: $APP_NAME"
    log_msg "Uygulama kaldırıldı: $APP_NAME"
done

# 7. Restore baseline modified targets atomically AFTER app removal so pre-existing files/symlinks remain intact
if [ "$HAS_VALID_BASELINE" = "1" ]; then
    log_msg "İlk durum yedeğinden önceki hedefler geri yükleniyor: $BASELINE_DIR"
    while IFS= read -r REL; do
        [ -z "$REL" ] && continue
        if ! is_allowed_manifest_rel "$REL"; then
            abort_uninstall "Güvensiz geri yükleme hedefi reddedildi: $REL"
        fi
        if is_klipper_user_cfg_rel "$REL"; then
            continue
        fi
        SRC="$BASELINE_DIR/files/$REL"
        TARGET="$ROOT/$REL"
        atomic_restore_target "$SRC" "$TARGET" || abort_uninstall "Yedekten geri yüklenemedi: $TARGET"
        log_msg "Yedekten geri yüklendi: /$REL"
        case "$REL" in
            */klippy/extras/ace|*/klippy/extras/temperature_ace.py|*/vanilla-klipper/ace_K3.cfg) ACE_RESTORED=1 ;;
        esac
    done < "$BASELINE_DIR/modified.list"
fi
if [ "$ACE_RESTORED" = "1" ]; then
    log_msg "ACE sürücüsünün vanilla orijinali geri yüklendi. NOT: Klipper'ın orijinal sürücüyü yüklemesi için yazıcıyı yeniden başlatın (veya Rinkhals Manage apps'ten vanilla-klipper'ı durdurup başlatın); paket Klipper'ı kendisi yeniden başlatmaz."
fi

# Remove numpy if installed by open-kobra (marker file exists).
# Locate vanilla-klipper directory (same logic as install).
VANILLA_KLIPPER_DIR_UNINST=""
if [ -f "$USER_APPS_DIR/vanilla-klipper/app.sh" ]; then
    VANILLA_KLIPPER_DIR_UNINST="$USER_APPS_DIR/vanilla-klipper"
elif [ -n "$RINKHALS_CURRENT" ] && [ -f "$RINKHALS_CURRENT/home/rinkhals/apps/vanilla-klipper/app.sh" ]; then
    VANILLA_KLIPPER_DIR_UNINST="$RINKHALS_CURRENT/home/rinkhals/apps/vanilla-klipper"
fi
if [ -n "$VANILLA_KLIPPER_DIR_UNINST" ]; then
    NP_SITE_UNINST="$VANILLA_KLIPPER_DIR_UNINST/lib/python3.11/site-packages"
    NP_MARKER="$NP_SITE_UNINST/numpy/.open-kobra-installed"
    if [ -f "$NP_MARKER" ]; then
        log_msg "numpy (open-kobra tarafından kuruldu) kaldırılıyor..."
        rm -rf "$NP_SITE_UNINST/numpy" "$NP_SITE_UNINST/numpy-1.24.4.dist-info" 2>/dev/null || true
        log_msg "numpy kaldırıldı."
    else
        log_msg "numpy işaret dosyası yok (kullanıcı tarafından kurulmuş olabilir), numpy dokunulmuyor."
    fi
else
    log_msg "UYARI: vanilla-klipper bulunamadı, numpy kaldırılamıyor."
fi

# Flush everything before deleting baseline recovery source
sync

if [ "$HAS_VALID_BASELINE" = "1" ]; then
    rm -rf "$BASELINE_DIR" || abort_uninstall "Baseline dizini silinemedi: $BASELINE_DIR"
    [ ! -e "$BASELINE_DIR" ] || abort_uninstall "Baseline dizini silindikten sonra hala duruyor: $BASELINE_DIR"
fi
rm -f "$TARGETS_MANIFEST" 2>/dev/null || true

# Prune older pre-uninstall backups keeping newest 2
OLD_PRE=0
PRUNE_PRE_LIST="$BAK_BASE/.openkobra.preprune.tmp"
for BAK_CAND in "$BAK_BASE"/open-kobra-pre-uninstall-*; do
    [ -d "$BAK_CAND" ] || continue
    printf '%s\n' "$BAK_CAND"
done | sort -r > "$PRUNE_PRE_LIST" || true
while IFS= read -r BAK_PATH; do
    [ -n "$BAK_PATH" ] && [ -d "$BAK_PATH" ] || continue
    OLD_PRE=$((OLD_PRE + 1))
    if [ "$OLD_PRE" -gt 2 ]; then
        rm -rf "$BAK_PATH" 2>/dev/null || log_msg "UYARI: Eski ön-kaldırma yedeği silinemedi: $BAK_PATH"
    fi
done < "$PRUNE_PRE_LIST"
rm -f "$PRUNE_PRE_LIST" 2>/dev/null || true

# Clean extracted staging folder only if exact expected /useremain/update_swu path
EXPECTED_UPDATE_DIR="$ROOT/useremain/update_swu"
if [ "$UPDATE_PATH" = "$EXPECTED_UPDATE_DIR" ] && [ ! -L "$UPDATE_PATH" ]; then
    log_msg "Geçici güncelleme dizini temizleniyor: $UPDATE_PATH"
    cd "${ROOT:-/}" || cd / || true
    rm -rf "$UPDATE_PATH" || log_msg "UYARI: Geçici güncelleme dizini temizlenemedi: $UPDATE_PATH"
fi

# Remove USB update.swu only after successful uninstall so stock updater won't re-trigger it on reboot
if [ -f "$USB_DIR/update.swu" ] && [ ! -L "$USB_DIR/update.swu" ]; then
    if rm -f "$USB_DIR/update.swu" 2>/dev/null; then
        log_msg "USB paket dosyası temizlendi: $USB_DIR/update.swu"
    else
        log_msg "UYARI: USB paket dosyası silinemedi: $USB_DIR/update.swu"
    fi
fi

sync 2>/dev/null || true
if [ "$KOBRA_SCREEN_WAS_RUNNING" = "1" ]; then
    ensure_stock_ui
fi
log_msg "open-kobra kaldırma işlemi tamamlandı."
beep 500
exit 0

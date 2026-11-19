#!/bin/sh
# Boot-time guard for open-kobra on Rinkhals (runs before vanilla-klipper via sort -uV).
# Verifies leviq3.py integrity via MD5 and open_kobra_homing.cfg presence/syntax,
# restoring them atomically from persistent copies if wiped or corrupted;
# otherwise disables the corresponding [include ...] line (and [leviq3] in
# open_kobra_limits.cfg) cleanly so Klipper never refuses to start.
# Always returns 0 on start/stop with a bounded timeout so Rinkhals boot is never blocked.

ROOT="${OPEN_KOBRA_ROOT:-}"
while [ -n "$ROOT" ] && [ "${ROOT%/}" != "$ROOT" ]; do
    ROOT="${ROOT%/}"
done
APP_ROOT=$(cd "$(dirname "$0")" && pwd)
RINKHALS_DIR="$ROOT/useremain/rinkhals"
RINKHALS_HOME="${RINKHALS_HOME:-$ROOT/useremain/home/rinkhals}"
RINKHALS_LOGS="${RINKHALS_LOGS:-$ROOT/tmp/rinkhals}"
APP_STATUS_STARTED="${APP_STATUS_STARTED:-started}"
APP_STATUS_STOPPED="${APP_STATUS_STOPPED:-stopped}"

if [ -n "$ROOT" ]; then
    RINKHALS_HOME="$ROOT/useremain/home/rinkhals"
    RINKHALS_LOGS="$ROOT/tmp/rinkhals"
fi

LOG_FILE="$RINKHALS_LOGS/app-05-open-kobra-guard.log"
STATE_DIR="$RINKHALS_HOME/open-kobra"
CONFIG_DIR="$RINKHALS_HOME/printer_data/config"
MAIN_CFG="$CONFIG_DIR/printer.klipper.cfg"
LIMITS_CFG="$CONFIG_DIR/open_kobra_limits.cfg"
USER_APPS_DIR="$RINKHALS_HOME/apps"

log_guard() {
    mkdir -p "$RINKHALS_LOGS" 2>/dev/null || true
    printf '%s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

resolve_rinkhals_current() {
    if [ -d "$RINKHALS_DIR/.current" ]; then
        printf '%s\n' "$RINKHALS_DIR/.current"
        return 0
    fi
    if [ -f "$RINKHALS_DIR/.version" ]; then
        VER=$(tr -d ' \t\r\n' < "$RINKHALS_DIR/.version" 2>/dev/null || true)
        if [ -n "$VER" ] && [ -d "$RINKHALS_DIR/$VER" ]; then
            printf '%s\n' "$RINKHALS_DIR/$VER"
            return 0
        fi
    fi
    return 1
}

resolve_vanilla_klipper() {
    if [ -d "$USER_APPS_DIR/vanilla-klipper/klippy/extras" ]; then
        printf '%s\n' "$USER_APPS_DIR/vanilla-klipper"
        return 0
    fi
    CURR=$(resolve_rinkhals_current || true)
    if [ -n "$CURR" ] && [ -d "$CURR/home/rinkhals/apps/vanilla-klipper/klippy/extras" ]; then
        printf '%s\n' "$CURR/home/rinkhals/apps/vanilla-klipper"
        return 0
    fi
    return 1
}

atomic_copy_file() {
    SRC_F="$1"
    DST_F="$2"
    [ -f "$SRC_F" ] || return 1
    if [ -L "$DST_F" ]; then
        log_guard "HATA: Sembolik bag uzerine yazma reddedildi: $DST_F"
        return 1
    fi
    DST_DIR=$(dirname "$DST_F")
    mkdir -p "$DST_DIR" 2>/dev/null || return 1
    TMP_F="${DST_F}.openkobra.guard.tmp"
    rm -f "$TMP_F" 2>/dev/null || true
    if cp -PRp "$SRC_F" "$TMP_F" 2>/dev/null && sync && mv "$TMP_F" "$DST_F" 2>/dev/null && sync; then
        return 0
    fi
    rm -f "$TMP_F" 2>/dev/null || true
    return 1
}

backup_cfg_before_edit() {
    CFG_SRC="$1"
    [ -f "$CFG_SRC" ] && [ ! -L "$CFG_SRC" ] || return 0
    mkdir -p "$STATE_DIR" 2>/dev/null || return 0
    BAK_DST="$STATE_DIR/$(basename "$CFG_SRC").guard.bak"
    atomic_copy_file "$CFG_SRC" "$BAK_DST" 2>/dev/null || true
}

atomic_commit_tmp_cfg() {
    TMP_CFG="$1"
    CFG="$2"
    if [ -L "$CFG" ]; then
        rm -f "$TMP_CFG" 2>/dev/null || true
        log_guard "HATA: Sembolik bag uzerine yazma reddedildi: $CFG"
        return 1
    fi
    if [ -s "$TMP_CFG" ] && sync && mv "$TMP_CFG" "$CFG" 2>/dev/null && sync; then
        return 0
    fi
    rm -f "$TMP_CFG" 2>/dev/null || true
    return 1
}

file_md5() {
    [ -f "$1" ] || return 1
    md5sum "$1" 2>/dev/null | awk '{print $1}'
}

get_expected_leviq3_md5() {
    for MD5_FILE in "$APP_ROOT/leviq3.py.md5" "$STATE_DIR/leviq3.py.md5"; do
        if [ -f "$MD5_FILE" ]; then
            CAND=$(awk 'NF { print $1; exit }' "$MD5_FILE" 2>/dev/null | tr -d ' \t\r\n' || true)
            if printf '%s\n' "$CAND" | awk 'length($0) == 32 && $0 ~ /^[0-9a-fA-F]+$/ { ok=1 } END { exit !ok }'; then
                printf '%s\n' "$CAND"
                return 0
            fi
        fi
    done
    return 1
}

verify_leviq3_py_ok() {
    PY_PATH="$1"
    EXP_HASH="$2"
    [ -s "$PY_PATH" ] || return 1
    if [ -n "$EXP_HASH" ]; then
        ACT_HASH=$(file_md5 "$PY_PATH" || true)
        [ "$ACT_HASH" = "$EXP_HASH" ] || return 1
        return 0
    fi
    if [ -s "$STATE_DIR/leviq3.py" ]; then
        cmp -s "$PY_PATH" "$STATE_DIR/leviq3.py" 2>/dev/null || return 1
    fi
    return 0
}

cfg_has_leviq3_section() {
    CFG="$1"
    [ -f "$CFG" ] || return 1
    awk '/^[[:space:]]*\[leviq3\][[:space:]]*(#.*|;.*)?$/ { found=1; exit } END { exit !found }' "$CFG"
}

# open_kobra_homing.cfg is sane when: [gcode_macro G28] with a gcode: body exists, every Jinja tag is closed
# ({% %}, {{ }}, {# #}, if/endif, for/endfor) and the file is not a cut-off (strict byte prefix) of the persistent copy.
# A half-written file used to pass (only the G28 header was checked) and made Klipper die on a TemplateSyntaxError (B-03).
cfg_has_homing_macro() {
    CFG="$1"
    [ -f "$CFG" ] || return 1
    awk '
    /^[[:space:]]*[#;]/ { next }
    /^[[:space:]]*\[gcode_macro[[:space:]]+G28\][[:space:]]*(#.*|;.*)?$/ { has_g28 = 1; next }
    /^[[:space:]]*gcode:[[:space:]]*(#.*|;.*)?$/ { has_gcode = 1; next }
    {
        src = $0
        if (index(src, "{#") == 0 && index(src, "#}") == 0) {
            sub(/[[:space:]]+[#;].*$/, "", src)
        }
        t = src; open_stmt += gsub(/\{%/, "&", t)
        t = src; close_stmt += gsub(/%\}/, "&", t)
        t = src; open_expr += gsub(/\{\{/, "&", t)
        t = src; close_expr += gsub(/\}\}/, "&", t)
        t = src; open_cmt += gsub(/\{#/, "&", t)
        t = src; close_cmt += gsub(/#\}/, "&", t)
        t = src; ifs += gsub(/\{%-?[ \t]*if[ \t]/, "&", t)
        t = src; endifs += gsub(/\{%-?[ \t]*endif[ \t]*-?%\}/, "&", t)
        t = src; fors += gsub(/\{%-?[ \t]*for[ \t]/, "&", t)
        t = src; endfors += gsub(/\{%-?[ \t]*endfor[ \t]*-?%\}/, "&", t)
    }
    END {
        if (!has_g28 || !has_gcode) exit 1
        if (open_stmt != close_stmt || open_expr != close_expr || open_cmt != close_cmt) exit 1
        if (ifs != endifs || fors != endfors) exit 1
        exit 0
    }
    ' "$CFG" || return 1
    REF_CFG="$STATE_DIR/open_kobra_homing.cfg"
    if [ -s "$REF_CFG" ] && [ "$CFG" != "$REF_CFG" ]; then
        CUR_SZ=$(wc -c < "$CFG" 2>/dev/null | tr -d ' \t\r\n')
        REF_SZ=$(wc -c < "$REF_CFG" 2>/dev/null | tr -d ' \t\r\n')
        case "$CUR_SZ$REF_SZ" in
            ''|*[!0-9]*) ;;
            *)
                if [ "$CUR_SZ" -lt "$REF_SZ" ] && head -c "$CUR_SZ" "$REF_CFG" 2>/dev/null | cmp -s - "$CFG"; then
                    return 1
                fi
                ;;
        esac
    fi
    return 0
}

disable_cfg_include() {
    CFG="$1"
    INC_NAME="$2"
    REASON="$3"
    [ -f "$CFG" ] || return 0
    if [ -L "$CFG" ]; then
        log_guard "UYARI: $CFG bir sembolik bag; guard devre disi birakma atlandi ($INC_NAME)."
        return 0
    fi
    NEED_DISABLE=$(awk -v inc="$INC_NAME" '
    {
        line = $0
        sub(/^[[:space:]]+/, "", line)
        sub(/[[:space:]]*(#.*|;.*)?$/, "", line)
        if (line == "[include " inc "]") { found=1; exit }
    }
    END { print found+0 }
    ' "$CFG" 2>/dev/null || echo 0)
    if [ "$NEED_DISABLE" = "1" ]; then
        backup_cfg_before_edit "$CFG"
        TMP_CFG="${CFG}.openkobra.guard.tmp"
        if awk -v inc="$INC_NAME" '
        {
            line = $0
            sub(/^[[:space:]]+/, "", line)
            sub(/[[:space:]]*(#.*|;.*)?$/, "", line)
            if (line == "[include " inc "]") {
                print "# [include " inc "] # open-kobra/guard-disabled"
                next
            }
            print
        }
        ' "$CFG" > "$TMP_CFG" 2>/dev/null && atomic_commit_tmp_cfg "$TMP_CFG" "$CFG"; then
            log_guard "UYARI: $REASON [include $INC_NAME] gecici olarak devre disi birakildi ($CFG)."
        else
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_guard "HATA: [include $INC_NAME] devre disi birakilamadi ($CFG)."
        fi
    fi
}

enable_cfg_include() {
    CFG="$1"
    INC_NAME="$2"
    [ -f "$CFG" ] || return 0
    if [ -L "$CFG" ]; then
        log_guard "UYARI: $CFG bir sembolik bag; guard etkinlestirme atlandi ($INC_NAME)."
        return 0
    fi
    NEED_ENABLE=$(awk -v inc="$INC_NAME" '
    /^[[:space:]]*#[[:space:]]*\[include[[:space:]]+/ && /#[[:space:]]*open-kobra\/guard-disabled[[:space:]]*$/ {
        if (index($0, "[include " inc "]") > 0) { found=1; exit }
    }
    END { print found+0 }
    ' "$CFG" 2>/dev/null || echo 0)
    if [ "$NEED_ENABLE" = "1" ]; then
        backup_cfg_before_edit "$CFG"
        TMP_CFG="${CFG}.openkobra.guard.tmp"
        if awk -v inc="$INC_NAME" '
        /^[[:space:]]*#[[:space:]]*\[include[[:space:]]+/ && /#[[:space:]]*open-kobra\/guard-disabled[[:space:]]*$/ {
            if (index($0, "[include " inc "]") > 0) {
                print "[include " inc "]"
                next
            }
        }
        { print }
        ' "$CFG" > "$TMP_CFG" 2>/dev/null && atomic_commit_tmp_cfg "$TMP_CFG" "$CFG"; then
            log_guard "Bilgi: [include $INC_NAME] yeniden etkinlestirildi ($CFG)."
        else
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_guard "HATA: [include $INC_NAME] yeniden etkinlestirilemedi ($CFG)."
        fi
    fi
}

disable_leviq3_limits() {
    CFG="$1"
    [ -f "$CFG" ] || return 0
    if [ -L "$CFG" ]; then
        log_guard "UYARI: $CFG bir sembolik bag; [leviq3] sinir kapatma atlandi."
        return 0
    fi
    if cfg_has_leviq3_section "$CFG"; then
        backup_cfg_before_edit "$CFG"
        TMP_CFG="${CFG}.openkobra.guard.tmp"
        if awk '
        BEGIN { in_leviq3 = 0 }
        /^[[:space:]]*\[leviq3\][[:space:]]*(#.*|;.*)?$/ {
            in_leviq3 = 1
            print "# open-kobra/guard-disabled-leviq3 " $0
            next
        }
        in_leviq3 == 1 && /^[[:space:]]*\[[^]]+\]/ {
            in_leviq3 = 0
            print
            next
        }
        in_leviq3 == 1 {
            print "# open-kobra/guard-disabled-leviq3 " $0
            next
        }
        { print }
        ' "$CFG" > "$TMP_CFG" 2>/dev/null && atomic_commit_tmp_cfg "$TMP_CFG" "$CFG"; then
            log_guard "UYARI: open_kobra_limits.cfg icindeki [leviq3] bolumu gecici olarak devre disi birakildi ($CFG)."
        else
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_guard "HATA: open_kobra_limits.cfg icindeki [leviq3] bolumu devre disi birakilamadi ($CFG)."
        fi
    fi
}

enable_leviq3_limits() {
    CFG="$1"
    [ -f "$CFG" ] || return 0
    if [ -L "$CFG" ]; then
        log_guard "UYARI: $CFG bir sembolik bag; [leviq3] sinir etkinlestirme atlandi."
        return 0
    fi
    HAS_DISABLED=$(awk '/^# open-kobra\/guard-disabled-leviq3 / { found=1; exit } END { print found+0 }' "$CFG" 2>/dev/null || echo 0)
    if [ "$HAS_DISABLED" = "1" ]; then
        backup_cfg_before_edit "$CFG"
        HAS_ACTIVE=0
        if cfg_has_leviq3_section "$CFG"; then
            HAS_ACTIVE=1
        fi
        TMP_CFG="${CFG}.openkobra.guard.tmp"
        if awk -v has_active="$HAS_ACTIVE" '
        /^# open-kobra\/guard-disabled-leviq3 / {
            if (has_active == 1) {
                next
            }
            sub(/^# open-kobra\/guard-disabled-leviq3 /, "", $0)
            print
            next
        }
        { print }
        ' "$CFG" > "$TMP_CFG" 2>/dev/null && atomic_commit_tmp_cfg "$TMP_CFG" "$CFG"; then
            log_guard "Bilgi: open_kobra_limits.cfg icindeki [leviq3] bolumu yeniden etkinlestirildi ($CFG)."
        else
            rm -f "$TMP_CFG" 2>/dev/null || true
            log_guard "HATA: open_kobra_limits.cfg icindeki [leviq3] bolumu yeniden etkinlestirilemedi ($CFG)."
        fi
    fi
}

status() {
    if [ -f "$RINKHALS_DIR/.current/tools.sh" ]; then
        # shellcheck disable=SC1091
        . "$RINKHALS_DIR/.current/tools.sh" 2>/dev/null || true
        if [ -n "$ROOT" ]; then
            RINKHALS_HOME="$ROOT/useremain/home/rinkhals"
            RINKHALS_LOGS="$ROOT/tmp/rinkhals"
        fi
    fi
    if command -v report_status >/dev/null 2>&1; then
        report_status "$APP_STATUS_STOPPED" "" "$LOG_FILE"
    else
        printf 'Status: %s\nPIDs: \nLog: %s\n' "$APP_STATUS_STOPPED" "$LOG_FILE"
    fi
}

ensure_cfg_restored() {
    DST_CFG="$1"
    SRC_CFG="$2"
    CHECK_FN="$3"
    CFG_LABEL="$4"
    if [ ! -s "$DST_CFG" ] || ! "$CHECK_FN" "$DST_CFG"; then
        if [ "${OPEN_KOBRA_GUARD_FAIL_COPY:-0}" = "1" ]; then
            log_guard "HATA: Test amacli $CFG_LABEL kopyalama basarisizligi tetiklendi."
        elif [ -s "$SRC_CFG" ] && "$CHECK_FN" "$SRC_CFG"; then
            if atomic_copy_file "$SRC_CFG" "$DST_CFG"; then
                log_guard "$CFG_LABEL kalici kopyadan atomik geri yuklendi: $DST_CFG"
            else
                log_guard "HATA: $CFG_LABEL hedef dizine yazilamadi: $DST_CFG"
            fi
        else
            log_guard "HATA: Gecerli kalici $CFG_LABEL bulunamadi: $SRC_CFG"
        fi
    fi
    [ -s "$DST_CFG" ] && "$CHECK_FN" "$DST_CFG"
}

guard_main() {
    if [ -n "$ROOT" ] && [ -n "${OPEN_KOBRA_GUARD_TEST_SLEEP:-}" ]; then
        if [ -n "${OPEN_KOBRA_GUARD_TRACE_FILE:-}" ]; then
            printf 'run\n' >> "$OPEN_KOBRA_GUARD_TRACE_FILE" 2>/dev/null || true
        fi
        sleep "$OPEN_KOBRA_GUARD_TEST_SLEEP" 2>/dev/null || true
    fi

    VK_DIR=$(resolve_vanilla_klipper || true)
    EXP_MD5=$(get_expected_leviq3_md5 || true)
    EXTRA_OK=0
    CFG_OK=0
    HOMING_OK=0

    if [ -n "$VK_DIR" ]; then
        TARGET_PY="$VK_DIR/klippy/extras/leviq3.py"
        if ! verify_leviq3_py_ok "$TARGET_PY" "$EXP_MD5"; then
            if [ -e "$TARGET_PY" ]; then
                log_guard "UYARI: leviq3.py butunluk (MD5) dogrulamasindan gecemedi; geri yukleme deneniyor: $TARGET_PY"
            fi
            if [ "${OPEN_KOBRA_GUARD_FAIL_COPY:-0}" = "1" ]; then
                log_guard "HATA: Test amacli kopyalama basarisizligi tetiklendi."
            elif ! verify_leviq3_py_ok "$STATE_DIR/leviq3.py" "$EXP_MD5"; then
                log_guard "HATA: Kalici leviq3.py bulunamadi veya MD5 dogrulamasindan gecemedi: $STATE_DIR/leviq3.py"
            elif atomic_copy_file "$STATE_DIR/leviq3.py" "$TARGET_PY" && verify_leviq3_py_ok "$TARGET_PY" "$EXP_MD5"; then
                log_guard "leviq3.py otomatik geri yuklendi ve MD5 dogrulandi: $TARGET_PY"
            else
                log_guard "HATA: leviq3.py hedef dizine atomik yazilamadi veya dogrulanamadi: $TARGET_PY"
            fi
        fi
        if verify_leviq3_py_ok "$TARGET_PY" "$EXP_MD5"; then
            EXTRA_OK=1
        fi
    else
        log_guard "UYARI: vanilla-klipper klippy/extras bulunamadi."
    fi

    if ensure_cfg_restored "$CONFIG_DIR/leviq3.cfg" "$STATE_DIR/leviq3.cfg" cfg_has_leviq3_section "leviq3.cfg"; then
        CFG_OK=1
    fi

    if [ "$EXTRA_OK" = "1" ] && [ "$CFG_OK" = "1" ]; then
        enable_cfg_include "$MAIN_CFG" "leviq3.cfg"
        enable_leviq3_limits "$LIMITS_CFG"
    else
        disable_cfg_include "$MAIN_CFG" "leviq3.cfg" "leviq3.py geri yuklenemedigi icin"
        disable_leviq3_limits "$LIMITS_CFG"
    fi

    if ensure_cfg_restored "$CONFIG_DIR/open_kobra_homing.cfg" "$STATE_DIR/open_kobra_homing.cfg" cfg_has_homing_macro "open_kobra_homing.cfg"; then
        HOMING_OK=1
    fi

    if [ "$HOMING_OK" = "1" ]; then
        enable_cfg_include "$MAIN_CFG" "open_kobra_homing.cfg"
    else
        disable_cfg_include "$MAIN_CFG" "open_kobra_homing.cfg" "open_kobra_homing.cfg geri yuklenemedigi icin"
    fi
    return 0
}

start() {
    # Rinkhals itself limits start_app to 5 s ("timeout -t 5"): stay clearly below it so our own warning gets logged
    # instead of the whole app being killed (B-07).
    GUARD_TIMEOUT="${OPEN_KOBRA_GUARD_TIMEOUT:-3}"
    RC=0
    if command -v timeout >/dev/null 2>&1; then
        # Detect timeout syntax ONCE (refs/Rinkhals uses BusyBox 'timeout -t SECS'); use 'sh -c :' as CONFIG_TRUE is off
        if timeout -t 1 sh -c : >/dev/null 2>&1; then
            timeout -t "$GUARD_TIMEOUT" sh "$APP_ROOT/app.sh" _guard_main || RC=$?
        elif timeout 1 sh -c : >/dev/null 2>&1; then
            timeout "$GUARD_TIMEOUT" sh "$APP_ROOT/app.sh" _guard_main || RC=$?
        else
            # No usable timeout: never run the guard unbounded in the Rinkhals boot path (B-08)
            log_guard "UYARI: Kullanilabilir timeout yok; bekci calistirilmadi (sinirsiz calisma riski)."
        fi
    else
        log_guard "UYARI: timeout komutu yok; bekci calistirilmadi (sinirsiz calisma riski)."
    fi
    if [ "$RC" -ne 0 ]; then
        log_guard "UYARI: Bekci ic hata veya zaman asimi ile sonlandi (rc=$RC); Rinkhals baslangicini bloklamamak icin 0 donuluyor."
    fi
    return 0
}

stop() {
    return 0
}

version() {
    if command -v jq >/dev/null 2>&1 && [ -f "$APP_ROOT/app.json" ]; then
        VER=$(jq -r '.version' "$APP_ROOT/app.json" 2>/dev/null || true)
        if [ -n "$VER" ] && [ "$VER" != "null" ]; then
            printf '%s\n' "$VER"
            return 0
        fi
    fi
    if [ -f "$APP_ROOT/app.json" ]; then
        VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$APP_ROOT/app.json" | head -n 1)
        if [ -n "$VER" ]; then
            printf '%s\n' "$VER"
            return 0
        fi
    fi
    printf '0.3.1\n'
}

case "${1:-}" in
    status)
        status
        ;;
    start)
        start
        ;;
    _guard_main)
        guard_main
        ;;
    stop)
        stop
        ;;
    version)
        version
        ;;
    *)
        printf 'Usage: %s {status|start|stop|version}\n' "$0" >&2
        exit 1
        ;;
esac

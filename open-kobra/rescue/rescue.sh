#!/bin/sh
# open-kobra boot rescue & health watcher for Anycubic Kobra 3 (modelId 20024).
# BusyBox ash / POSIX sh compatible. Never fails stock boot flow.

ROOT="${OPEN_KOBRA_ROOT:-}"
while [ -n "$ROOT" ] && [ "${ROOT%/}" != "$ROOT" ]; do
    ROOT="${ROOT%/}"
done

SELF_PATH=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
RESCUE_DIR="$ROOT/useremain/open-kobra/rescue"
RESCUE_LOG="$RESCUE_DIR/rescue.log"
STATE_FILE="$RESCUE_DIR/state"
STOCK_BASE="$RESCUE_DIR/stock"
MANIFEST_DIR="$RESCUE_DIR/manifests"
REPAIR_DISABLED_MARKER="$RESCUE_DIR/binary_repair.disabled"

# start.sh protection (B-05)
START_SH="$ROOT/userdata/app/gk/start.sh"
START_ORIG="$RESCUE_DIR/start.sh.orig"
START_ORIG_FW="$RESCUE_DIR/start.sh.orig.fw"
HOOK_BLOCK="$RESCUE_DIR/hook.block"
START_BAD_COPY="$RESCUE_DIR/start.sh.bad"
START_REPAIR_DISABLED_MARKER="$RESCUE_DIR/start_repair.disabled"
# The watcher never reboots the printer or disables Rinkhals unless this file exists (default: absent = log only).
AUTO_RECOVERY_MARKER="$RESCUE_DIR/auto_recovery.enabled"
START_REPAIRED=0
# Exit code of "rescue.sh boot" telling the start.sh hook that start.sh was REPLACED during this boot.
START_REPLACED_RC=75

GK_DIR="$ROOT/userdata/app/gk"
DEV_VER_FILE="$ROOT/useremain/dev/version"
API_CFG="$GK_DIR/config/api.cfg"
RINKHALS_DIR="$ROOT/useremain/rinkhals"
APPS_DIR="$ROOT/useremain/home/rinkhals/apps"
TMP_DIR="$ROOT/tmp"
WATCH_PID_FILE="$TMP_DIR/open-kobra-rescue-watch.pid"

PROC_ROOT="${OPEN_KOBRA_PROC_ROOT:-$ROOT/proc}"
MOONRAKER_URL="${OPEN_KOBRA_MOONRAKER_URL:-http://127.0.0.1:7125/printer/objects/query?print_stats}"
INITIAL_DELAY="${OPEN_KOBRA_RESCUE_INITIAL_DELAY:-240}"
RECHECK_DELAY="${OPEN_KOBRA_RESCUE_RECHECK_DELAY:-60}"
START_STABLE_DELAY="${OPEN_KOBRA_RESCUE_START_STABLE_DELAY:-1}"
LOG_MAX_BYTES="${OPEN_KOBRA_RESCUE_LOG_MAX_BYTES:-65536}"

trim_log_if_needed() {
    [ -f "$RESCUE_LOG" ] || return 0
    CUR_BYTES=$(wc -c < "$RESCUE_LOG" 2>/dev/null | tr -d ' \t\r\n' || echo 0)
    case "$CUR_BYTES" in
        ''|*[!0-9]*) return 0 ;;
    esac
    if [ "$CUR_BYTES" -gt "$LOG_MAX_BYTES" ]; then
        TMP_LOG="${RESCUE_LOG}.trim.tmp"
        if tail -n 200 "$RESCUE_LOG" > "$TMP_LOG" 2>/dev/null; then
            TRIM_BYTES=$(wc -c < "$TMP_LOG" 2>/dev/null | tr -d ' \t\r\n' || echo 0)
            case "$TRIM_BYTES" in
                ''|*[!0-9]*) TRIM_BYTES=0 ;;
            esac
            if [ "$TRIM_BYTES" -gt "$LOG_MAX_BYTES" ]; then
                KEEP_BYTES=$((LOG_MAX_BYTES / 2))
                tail -c "$KEEP_BYTES" "$TMP_LOG" > "${TMP_LOG}.2" 2>/dev/null && mv "${TMP_LOG}.2" "$TMP_LOG" 2>/dev/null || true
            fi
            mv "$TMP_LOG" "$RESCUE_LOG" 2>/dev/null || rm -f "$TMP_LOG" 2>/dev/null || true
        else
            rm -f "$TMP_LOG" 2>/dev/null || true
        fi
    fi
}

log_rescue() {
    mkdir -p "$RESCUE_DIR" 2>/dev/null || return 0
    trim_log_if_needed
    TS=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo 'unknown-time')
    printf '%s: %s\n' "$TS" "$*" >> "$RESCUE_LOG" 2>/dev/null || true
}

read_fw_version() {
    [ -f "$DEV_VER_FILE" ] || return 1
    VER=$(tr -d ' \t\r\n' < "$DEV_VER_FILE" 2>/dev/null || true)
    [ -n "$VER" ] || return 1
    printf '%s\n' "$VER"
}

read_model_id() {
    [ -f "$API_CFG" ] || return 1
    MID=$(awk 'index($0, "\"modelId\"") > 0 { print; exit }' "$API_CFG" 2>/dev/null \
        | sed -n 's/.*"modelId"[[:space:]]*:[[:space:]]*"*\([0-9][0-9]*\)"*.*/\1/p')
    [ -n "$MID" ] || return 1
    printf '%s\n' "$MID"
}

file_md5() {
    [ -f "$1" ] || return 1
    md5sum "$1" 2>/dev/null | awk '{print $1}'
}

is_valid_md5_hex() {
    printf '%s\n' "$1" | awk 'length($0) == 32 && $0 ~ /^[0-9a-fA-F]+$/ { ok=1 } END { exit !ok }'
}

log_leftover_patch_files() {
    BIN_PATH="$1"
    BIN_NAME="$2"
    for SUF in original patch tmp; do
        CAND="${BIN_PATH}.${SUF}"
        if [ -e "$CAND" ] || [ -L "$CAND" ]; then
            log_rescue "Bilgi: $BIN_NAME icin artik dosya mevcut (silinmedi): $CAND"
        fi
    done
}

same_size_and_mtime() {
    [ -f "$1" ] && [ -f "$2" ] && [ ! -L "$1" ] && [ ! -L "$2" ] || return 1
    if command -v stat >/dev/null 2>&1; then
        SM_A=$(stat -c '%s:%Y' "$1" 2>/dev/null || true)
        SM_B=$(stat -c '%s:%Y' "$2" 2>/dev/null || true)
        if [ -n "$SM_A" ] && [ -n "$SM_B" ]; then
            [ "$SM_A" = "$SM_B" ]
            return $?
        fi
    fi
    SM_A=$(ls -ln "$1" 2>/dev/null | awk '{ print $5; exit }')
    SM_B=$(ls -ln "$2" 2>/dev/null | awk '{ print $5; exit }')
    [ -n "$SM_A" ] && [ "$SM_A" = "$SM_B" ] || return 1
    [ -z "$(find "$1" -prune -newer "$2" 2>/dev/null)" ] && [ -z "$(find "$2" -prune -newer "$1" 2>/dev/null)" ]
}

repair_binaries() {
    FW_VER=$(read_fw_version || true)
    MODEL_ID=$(read_model_id || true)

    if [ "$MODEL_ID" != "20024" ] || [ -z "$FW_VER" ]; then
        log_rescue "Bilgi: Surum/model uygun degil (fw='${FW_VER:-yok}', model='${MODEL_ID:-yok}'); ikili onarim atlandi."
        return 2
    fi

    VER_STOCK_DIR="$STOCK_BASE/$FW_VER"
    MANIFEST_FILE="$MANIFEST_DIR/K3_${FW_VER}.md5"

    if [ ! -f "$MANIFEST_FILE" ]; then
        log_rescue "Bilgi: Firmware $FW_VER (model $MODEL_ID) icin manifest bulunamadi; hicbir onarim yapilmayacak."
        return 2
    fi

    if [ -f "$REPAIR_DISABLED_MARKER" ]; then
        log_rescue "Bilgi: Ikili onarim kurulum sirasinda devre disi birakildi ($REPAIR_DISABLED_MARKER); ikili onarim atlandi."
        return 3
    fi

    if [ ! -d "$VER_STOCK_DIR" ] || [ ! -f "$VER_STOCK_DIR/.verified" ]; then
        log_rescue "Bilgi: Firmware $FW_VER (model $MODEL_ID) icin dogrulanmis stok yedek yok; ikili onarim yapilmayacak."
        return 2
    fi

    VERIFIED_TAG=$(tr -d ' \t\r\n' < "$VER_STOCK_DIR/.verified" 2>/dev/null || true)
    if [ "$VERIFIED_TAG" != "K3_${FW_VER}" ]; then
        log_rescue "UYARI: Stok yedek dogrulama etiketi uyusmuyor ('$VERIFIED_TAG' != 'K3_${FW_VER}'); ikili onarim atlandi."
        return 2
    fi

    REPAIRED_ANY=0
    while IFS= read -r MLINE || [ -n "$MLINE" ]; do
        CLEAN_LINE=$(printf '%s\n' "$MLINE" | sed 's/#.*$//')
        EXP_MD5=$(printf '%s\n' "$CLEAN_LINE" | awk '{print $1}')
        BIN_NAME=$(printf '%s\n' "$CLEAN_LINE" | awk '{print $2}')
        [ -z "$EXP_MD5" ] && continue
        [ -z "$BIN_NAME" ] && continue
        if ! is_valid_md5_hex "$EXP_MD5"; then
            log_rescue "UYARI: Manifestte gecersiz MD5 satiri atlandi ($BIN_NAME)."
            continue
        fi

        TARGET_BIN="$GK_DIR/$BIN_NAME"
        STOCK_BIN="$VER_STOCK_DIR/$BIN_NAME"

        log_leftover_patch_files "$TARGET_BIN" "$BIN_NAME"

        ACT_MD5=""
        if [ -f "$TARGET_BIN" ] && [ ! -L "$TARGET_BIN" ]; then
            # Fast path (PK-R05): the verified stock copy was made with cp -p, so an untouched binary has the same
            # size and mtime. Hashing ~34 MB on every boot is only done when they differ.
            if same_size_and_mtime "$TARGET_BIN" "$STOCK_BIN"; then
                continue
            fi
            ACT_MD5=$(file_md5 "$TARGET_BIN" || true)
        fi
        if [ "$ACT_MD5" = "$EXP_MD5" ]; then
            continue
        fi

        log_rescue "UYARI: $BIN_NAME MD5 uyusmazligi (mevcut=${ACT_MD5:-yok}, beklenen=$EXP_MD5); stok kopyadan onarim baslatiliyor."

        if [ -L "$TARGET_BIN" ]; then
            log_rescue "HATA: $TARGET_BIN bir sembolik bag; uzerine yazma reddedildi."
            continue
        fi

        if [ ! -f "$STOCK_BIN" ]; then
            log_rescue "HATA: Stok yedek bulunamadi: $STOCK_BIN"
            continue
        fi

        STOCK_MD5=$(file_md5 "$STOCK_BIN" || true)
        if [ "$STOCK_MD5" != "$EXP_MD5" ]; then
            log_rescue "HATA: Stok yedek MD5 dogrulamasi basarisiz ($STOCK_BIN: $STOCK_MD5 != $EXP_MD5); onarim iptal."
            continue
        fi

        TMP_BIN="${TARGET_BIN}.openkobra.tmp"
        rm -f "$TMP_BIN" 2>/dev/null || true
        if ! cp -p "$STOCK_BIN" "$TMP_BIN" 2>/dev/null; then
            rm -f "$TMP_BIN" 2>/dev/null || true
            log_rescue "HATA: $STOCK_BIN gecici dosyaya kopyalanamadi: $TMP_BIN"
            continue
        fi
        chmod +x "$TMP_BIN" 2>/dev/null || true
        sync 2>/dev/null || true

        TMP_MD5=$(file_md5 "$TMP_BIN" || true)
        if [ "$TMP_MD5" != "$EXP_MD5" ]; then
            rm -f "$TMP_BIN" 2>/dev/null || true
            log_rescue "HATA: Gecici kopya MD5 dogrulamasi basarisiz ($TMP_BIN: $TMP_MD5 != $EXP_MD5); onarim iptal."
            continue
        fi

        if mv "$TMP_BIN" "$TARGET_BIN" 2>/dev/null; then
            chmod +x "$TARGET_BIN" 2>/dev/null || true
            sync 2>/dev/null || true
            log_rescue "ONARILDI: $BIN_NAME stok kopyadan geri yuklendi ve MD5 dogrulandi."
            REPAIRED_ANY=1
        else
            rm -f "$TMP_BIN" 2>/dev/null || true
            log_rescue "HATA: Gecici kopya hedefe tasinamadi: $TARGET_BIN"
        fi
    done < "$MANIFEST_FILE"

    if [ "$REPAIRED_ANY" = "0" ]; then
        log_rescue "Bilgi: Tum izlenen stok ikililer dogrulandi (onarim gerekmedi)."
    fi
    return 0
}

# ---------------------------------------------------------------------------
# start.sh protection (B-05)
#
# Expected content of /userdata/app/gk/start.sh = hook.block (our hook, saved at install time)
# followed by start.sh.orig (start.sh as it was before our hook; may contain the Rinkhals block).
#
# The file is only REPLACED when it looks BROKEN: missing/empty, CRLF, shell syntax error, unbalanced
# '# open-kobra-rescue' / '# Rinkhals' markers, or a truncated copy of the stock part. A start.sh that merely
# differs from the expected content (Rinkhals installed/updated/removed, manual edit) is left untouched.
# A valid start.sh whose hook is missing only gets the hook prepended (rest byte-for-byte preserved).
#
# Replacement is always tmp file (same directory) + sync + mv + sync. mv gives start.sh a NEW inode, so a
# shell that is still executing the old start.sh keeps reading the old (unchanged) inode instead of a
# half-rewritten file. That shell cannot see the repaired content, which is why "boot" exits with
# START_REPLACED_RC and the hook restarts start.sh once (exec) on the new inode.
# ---------------------------------------------------------------------------

has_nonblank() {
    [ -f "$1" ] || return 1
    awk 'NF { f = 1; exit } END { exit !f }' "$1"
}

has_carriage_return() {
    CR_ALL=$(wc -c < "$1" 2>/dev/null | tr -d ' \t\r\n')
    CR_NOCR=$(tr -d '\r' < "$1" 2>/dev/null | wc -c | tr -d ' \t\r\n')
    [ -n "$CR_ALL" ] && [ "$CR_ALL" != "$CR_NOCR" ]
}

blocks_balanced() {
    awk '
    /^# open-kobra-rescue\/begin/ { r++; if (r > 1) bad = 1 }
    /^# open-kobra-rescue\/end/ { r--; if (r < 0) bad = 1 }
    /^# Rinkhals\/begin/ { k++; if (k > 1) bad = 1 }
    /^# Rinkhals\/end/ { k--; if (k < 0) bad = 1 }
    END { if (bad || r != 0 || k != 0) exit 1; exit 0 }
    ' "$1"
}

has_rescue_block() {
    awk '/^# open-kobra-rescue\/begin/ { b = 1 } /^# open-kobra-rescue\/end/ { e = 1 } END { exit !(b && e) }' "$1"
}

# Lines outside the rescue and Rinkhals blocks, without trailing blank lines.
core_lines() {
    awk '
    /^# open-kobra-rescue\/begin/ { skip_r = 1; next }
    /^# open-kobra-rescue\/end/ { skip_r = 0; next }
    /^# Rinkhals\/begin/ { skip_k = 1; next }
    /^# Rinkhals\/end/ { skip_k = 0; next }
    skip_r || skip_k { next }
    {
        if ($0 ~ /^[ \t]*$/) { pend++ }
        else { while (pend > 0) { print ""; pend-- } print }
    }
    ' "$1"
}

# Is the stock part of $1 a strict (line or mid-line) prefix of the stock part of start.sh.orig?
core_is_truncated() {
    CT_FILE="$1"
    CT_TMP_S="$RESCUE_DIR/.core.stock.$$.tmp"
    CT_TMP_C="$RESCUE_DIR/.core.cur.$$.tmp"
    CT_RC=1
    if core_lines "$START_ORIG" > "$CT_TMP_S" 2>/dev/null && core_lines "$CT_FILE" > "$CT_TMP_C" 2>/dev/null \
        && [ -s "$CT_TMP_S" ]; then
        CT_PARTIAL=0
        if [ -s "$CT_FILE" ]; then
            CT_NL=$(tail -c 1 "$CT_FILE" 2>/dev/null | wc -l | tr -d ' \t\r\n')
            [ "$CT_NL" = "1" ] || CT_PARTIAL=1
        fi
        if awk -v partial="$CT_PARTIAL" '
        FNR == NR { s[++N] = $0; next }
        { c[++n] = $0 }
        END {
            if (N == 0 || n > N) exit 1
            for (i = 1; i < n; i++) if (c[i] != s[i]) exit 1
            if (n >= 1) {
                if (partial == 1) {
                    if (substr(s[n], 1, length(c[n])) != c[n]) exit 1
                    if (n < N || length(c[n]) < length(s[n])) exit 0
                    exit 1
                }
                if (c[n] != s[n]) exit 1
            }
            if (n < N) exit 0
            exit 1
        }
        ' "$CT_TMP_S" "$CT_TMP_C"; then
            CT_RC=0
        fi
    fi
    rm -f "$CT_TMP_S" "$CT_TMP_C" 2>/dev/null || true
    return "$CT_RC"
}

# Prints a short reason when $1 looks broken; prints nothing when it looks sane.
diagnose_start_sh() {
    DS_FILE="$1"
    if [ ! -e "$DS_FILE" ]; then
        printf 'yok\n'
    elif [ ! -f "$DS_FILE" ]; then
        printf 'duz-dosya-degil\n'
    elif ! has_nonblank "$DS_FILE"; then
        printf 'bos\n'
    elif has_carriage_return "$DS_FILE"; then
        printf 'crlf\n'
    elif ! sh -n "$DS_FILE" >/dev/null 2>&1; then
        printf 'sozdizimi-hatasi\n'
    elif ! blocks_balanced "$DS_FILE"; then
        printf 'dengesiz-blok\n'
    elif core_is_truncated "$DS_FILE"; then
        printf 'yarim\n'
    fi
    return 0
}

# A valid hook block: first line '# open-kobra-rescue/begin', last line '# open-kobra-rescue/end', exactly one pair.
hook_block_valid() {
    if [ ! -f "$1" ] || [ -L "$1" ]; then
        return 1
    fi
    awk '
    NR == 1 && $0 != "# open-kobra-rescue/begin" { bad = 1 }
    /^# open-kobra-rescue\/begin/ { b++ }
    /^# open-kobra-rescue\/end/ { e++; last = NR }
    { total = NR }
    END { if (bad || b != 1 || e != 1 || last != total) exit 1; exit 0 }
    ' "$1"
}

# Writes the hook block to stdout: hook.block when valid, else the complete block found inside start.sh
# (installs that predate hook.block). The block alone must be well-formed; the rest of start.sh may be broken.
emit_hook_block() {
    if hook_block_valid "$HOOK_BLOCK"; then
        cat "$HOOK_BLOCK"
        return $?
    fi
    if [ ! -f "$START_SH" ] || [ -L "$START_SH" ]; then
        return 1
    fi
    EH_TMP="$RESCUE_DIR/.hook.extract.$$.tmp"
    EH_RC=1
    awk '
    /^# open-kobra-rescue\/begin/ { on = 1 }
    on { print }
    /^# open-kobra-rescue\/end/ { on = 0; exit }
    ' "$START_SH" > "$EH_TMP" 2>/dev/null
    if hook_block_valid "$EH_TMP" && sh -n "$EH_TMP" >/dev/null 2>&1; then
        cat "$EH_TMP"
        EH_RC=$?
    fi
    rm -f "$EH_TMP" 2>/dev/null || true
    return "$EH_RC"
}

start_repair_fw_gate() {
    FW_NOW=$(read_fw_version || true)
    MODEL_NOW=$(read_model_id || true)
    if [ "$MODEL_NOW" != "20024" ] || [ -z "$FW_NOW" ]; then
        log_rescue "Bilgi: start.sh onarimi atlandi: surum/model uygun degil (fw='${FW_NOW:-yok}', model='${MODEL_NOW:-yok}')."
        return 1
    fi
    if [ -f "$START_ORIG_FW" ]; then
        FW_ORIG=$(tr -d ' \t\r\n' < "$START_ORIG_FW" 2>/dev/null || true)
        if [ "$FW_ORIG" != "$FW_NOW" ]; then
            log_rescue "Bilgi: start.sh onarimi atlandi: start.sh.orig firmware '${FW_ORIG:-yok}' icin alinmis, simdiki '$FW_NOW'."
            return 1
        fi
    elif [ ! -f "$STOCK_BASE/$FW_NOW/.verified" ]; then
        log_rescue "Bilgi: start.sh onarimi atlandi: start.sh.orig.fw yok ve $FW_NOW icin dogrulanmis stok yedek yok (surum eslesmesi kanitlanamadi)."
        return 1
    fi
    return 0
}

# Atomic replace: $1 = finished candidate (same directory as start.sh), $2 = description for the log.
replace_start_sh_atomic() {
    RS_CAND="$1"
    RS_WHAT="$2"
    chmod 755 "$RS_CAND" 2>/dev/null || true
    sync 2>/dev/null || true
    if mv "$RS_CAND" "$START_SH" 2>/dev/null; then
        sync 2>/dev/null || true
        START_REPAIRED=1
        return 0
    fi
    rm -f "$RS_CAND" 2>/dev/null || true
    log_rescue "HATA: start.sh yerine tasinamadi ($RS_WHAT); dokunulmadi."
    return 1
}

# Never fails the caller; sets START_REPAIRED=1 when start.sh was replaced.
repair_start_sh() {
    if [ -f "$START_REPAIR_DISABLED_MARKER" ]; then
        log_rescue "Bilgi: start.sh onarimi devre disi ($START_REPAIR_DISABLED_MARKER)."
        return 0
    fi
    if [ ! -f "$START_ORIG" ] || [ ! -s "$START_ORIG" ]; then
        log_rescue "Bilgi: start.sh onarimi atlandi: $START_ORIG yok/bos."
        return 0
    fi
    start_repair_fw_gate || return 0

    # The reference copy itself must be sane, otherwise restoring from it could make things worse.
    if [ -n "$(diagnose_start_sh_orig)" ]; then
        log_rescue "UYARI: start.sh.orig kendisi saglikli gorunmuyor ($(diagnose_start_sh_orig)); start.sh onarimi atlandi."
        return 0
    fi

    if [ -L "$START_SH" ]; then
        log_rescue "UYARI: $START_SH bir sembolik bag; onarim reddedildi, dokunulmadi."
        return 0
    fi

    EXPECTED="$RESCUE_DIR/.start.sh.expected.$$.tmp"
    CAND="${START_SH}.openkobra.restore.tmp"
    rm -f "$EXPECTED" "$CAND" 2>/dev/null || true

    if ! { emit_hook_block > "$EXPECTED.hook" 2>/dev/null && [ -s "$EXPECTED.hook" ]; }; then
        rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
        log_rescue "UYARI: kanca blogu (hook.block) bulunamadi/gecersiz; start.sh onarimi atlandi."
        return 0
    fi
    if ! cat "$EXPECTED.hook" "$START_ORIG" > "$EXPECTED" 2>/dev/null; then
        rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
        log_rescue "HATA: beklenen start.sh icerigi olusturulamadi; dokunulmadi."
        return 0
    fi

    if [ -f "$START_SH" ] && cmp -s "$EXPECTED" "$START_SH"; then
        rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
        log_rescue "Bilgi: start.sh beklenen icerikle ayni (kanca + start.sh.orig)."
        return 0
    fi

    REASON=$(diagnose_start_sh "$START_SH")
    if [ -n "$REASON" ]; then
        # Confirm the verdict on a stable file (a legitimate installer may be writing it right now).
        SUM1=$(file_md5 "$START_SH" || true)
        sleep_seconds "$START_STABLE_DELAY"
        SUM2=$(file_md5 "$START_SH" || true)
        REASON2=$(diagnose_start_sh "$START_SH")
        if [ "$SUM1" != "$SUM2" ] || [ "$REASON" != "$REASON2" ]; then
            rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
            log_rescue "Bilgi: start.sh denetim sirasinda degisiyor ($REASON -> ${REASON2:-saglikli}); bu turda dokunulmadi."
            return 0
        fi

        log_rescue "UYARI: start.sh bozuk gorunuyor ($REASON); beklenen icerikten (kanca + start.sh.orig) atomik geri yukleme yapilacak."
        # Candidate must itself pass every check before it may replace anything.
        if ! cp "$EXPECTED" "$CAND" 2>/dev/null || [ -n "$(diagnose_start_sh "$CAND")" ] || ! has_rescue_block "$CAND"; then
            rm -f "$EXPECTED" "$EXPECTED.hook" "$CAND" 2>/dev/null || true
            log_rescue "HATA: geri yuklenecek icerik dogrulamadan gecemedi; start.sh'a dokunulmadi."
            return 0
        fi
        if [ -s "$START_SH" ] && [ -f "$START_SH" ]; then
            cp "$START_SH" "$START_BAD_COPY" 2>/dev/null || true
        fi
        if replace_start_sh_atomic "$CAND" "geri yukleme"; then
            if cmp -s "$EXPECTED" "$START_SH"; then
                log_rescue "ONARILDI: start.sh beklenen icerikten geri yuklendi (neden: $REASON; bozuk kopya: $START_BAD_COPY)."
            else
                log_rescue "HATA: geri yuklenen start.sh beklenenle eslesmiyor; elle kontrol gerekli."
            fi
        fi
        rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
        return 0
    fi

    # start.sh looks sane but differs from the expected content.
    if has_rescue_block "$START_SH"; then
        log_rescue "Bilgi: start.sh beklenenden farkli ama bozuk gorunmuyor (Rinkhals/kullanici degisikligi olabilir); dokunulmadi."
        rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
        return 0
    fi

    # Sane but our hook is gone: put the hook back and keep everything else byte-for-byte.
    if grep -q '^# open-kobra-rescue/' "$START_SH" 2>/dev/null; then
        log_rescue "UYARI: start.sh icinde yarim kanca isaretleri var; dokunulmadi."
        rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
        return 0
    fi
    if ! cat "$EXPECTED.hook" "$START_SH" > "$CAND" 2>/dev/null \
        || [ -n "$(diagnose_start_sh "$CAND")" ] || ! has_rescue_block "$CAND"; then
        rm -f "$EXPECTED" "$EXPECTED.hook" "$CAND" 2>/dev/null || true
        log_rescue "HATA: kanca yeniden eklenemedi (dogrulama basarisiz); start.sh'a dokunulmadi."
        return 0
    fi
    if replace_start_sh_atomic "$CAND" "kanca yeniden ekleme"; then
        log_rescue "ONARILDI: start.sh saglikliydi ama kanca eksikti; kanca en basa yeniden eklendi (geri kalan icerik korundu)."
    fi
    rm -f "$EXPECTED" "$EXPECTED.hook" 2>/dev/null || true
    return 0
}

# The stock part must launch the stock programs: a cut-off but syntactically valid start.sh must never become the
# "golden" reference (PK-R01).
has_stock_launch_lines() {
    awk '
    /^[[:space:]]*\.\/gklib([[:space:]]|$)/ { a = 1 }
    /^[[:space:]]*\.\/gkapi([[:space:]]|$)/ { b = 1 }
    /^[[:space:]]*\.\/K3SysUi([[:space:]]|$)/ { c = 1 }
    END { exit !(a && b && c) }
    ' "$1"
}

# When the package carries the official start.sh for this firmware, the stock part must equal it.
stock_core_matches_reference() {
    SC_FW=$(read_fw_version || true)
    SC_REF="$MANIFEST_DIR/K3_${SC_FW}.start.sh"
    [ -n "$SC_FW" ] && [ -f "$SC_REF" ] || return 0
    SC_T1="$RESCUE_DIR/.sc.ref.$$.tmp"
    SC_T2="$RESCUE_DIR/.sc.cur.$$.tmp"
    core_lines "$SC_REF" > "$SC_T1" 2>/dev/null
    core_lines "$1" > "$SC_T2" 2>/dev/null
    SC_RC=0
    cmp -s "$SC_T1" "$SC_T2" || SC_RC=1
    rm -f "$SC_T1" "$SC_T2" 2>/dev/null || true
    return "$SC_RC"
}

diagnose_start_sh_orig() {
    if ! has_nonblank "$START_ORIG"; then
        printf 'bos\n'
    elif has_carriage_return "$START_ORIG"; then
        printf 'crlf\n'
    elif ! sh -n "$START_ORIG" >/dev/null 2>&1; then
        printf 'sozdizimi-hatasi\n'
    elif ! blocks_balanced "$START_ORIG"; then
        printf 'dengesiz-blok\n'
    elif has_rescue_block "$START_ORIG"; then
        printf 'kanca-iceriyor\n'
    elif ! has_stock_launch_lines "$START_ORIG"; then
        printf 'stok-satirlari-eksik\n'
    elif ! stock_core_matches_reference "$START_ORIG"; then
        printf 'resmi-start.sh-ile-uyusmuyor\n'
    fi
    return 0
}

has_pidof_proc() {
    P_NAME="$1"
    command -v pidof >/dev/null 2>&1 || return 1
    PIDS=$(pidof "$P_NAME" 2>/dev/null || true)
    [ -n "$PIDS" ]
}

# A process that is a zombie (Z), stopped (T/t) or dead (X) is not a working UI. No stat file (test fakes) = active.
proc_is_active() {
    PA_DIR="$1"
    [ -r "$PA_DIR/stat" ] || return 0
    PA_ST=$(awk '{ p = index($0, ") "); if (p > 0) print substr($0, p + 2, 1) }' "$PA_DIR/stat" 2>/dev/null || true)
    case "$PA_ST" in
        Z|T|t|X) return 1 ;;
    esac
    return 0
}

# The stock firmware BusyBox has no pidof (verified on the printer: "pidof: not found"), so K3SysUi must be found
# through /proc, like kobra_screen. pidof stays only as an extra fallback (B-01).
has_proc_k3sysui() {
    [ -d "$PROC_ROOT" ] || return 1
    for P_DIR in "$PROC_ROOT"/[0-9]*; do
        [ -d "$P_DIR" ] || continue
        PID="${P_DIR##*/}"
        [ "$PID" = "$$" ] && continue
        kill -0 "$PID" 2>/dev/null || continue
        proc_is_active "$P_DIR" || continue
        if [ -r "$P_DIR/comm" ]; then
            COMM=$(cat "$P_DIR/comm" 2>/dev/null || true)
            [ "$COMM" = "K3SysUi" ] && return 0
        fi
        if [ -r "$P_DIR/cmdline" ]; then
            ARG0=$(tr '\0' '\n' < "$P_DIR/cmdline" 2>/dev/null | head -n 1)
            [ "${ARG0##*/}" = "K3SysUi" ] && return 0
        fi
    done
    return 1
}

has_proc_kobra_screen() {
    [ -d "$PROC_ROOT" ] || return 1
    for P_DIR in "$PROC_ROOT"/[0-9]*; do
        [ -r "$P_DIR/cmdline" ] || continue
        PID="${P_DIR##*/}"
        [ "$PID" = "$$" ] && continue
        kill -0 "$PID" 2>/dev/null || continue
        proc_is_active "$P_DIR" || continue
        ARGS=$(tr '\0' '\n' < "$P_DIR/cmdline" 2>/dev/null || true)
        [ -n "$ARGS" ] || continue
        ARG0=$(printf '%s\n' "$ARGS" | head -n 1)
        BASE0="${ARG0##*/}"
        if [ "$BASE0" = "kobra_screen" ]; then
            return 0
        fi
        case "$BASE0" in
            python*|fake_python*)
                PREV=""
                for A in $ARGS; do
                    if [ "$PREV" = "-m" ] && [ "$A" = "kobra_screen" ]; then
                        return 0
                    fi
                    if [ "${A##*/}" = "kobra_screen" ]; then
                        return 0
                    fi
                    PREV="$A"
                done
                ;;
        esac
    done
    return 1
}

is_ui_healthy() {
    if has_proc_k3sysui; then
        return 0
    fi
    if has_proc_kobra_screen; then
        return 0
    fi
    if has_pidof_proc "K3SysUi"; then
        return 0
    fi
    if has_pidof_proc "kobra_screen"; then
        return 0
    fi
    return 1
}

# Prints printing | idle | unknown. Unreachable Moonraker is UNKNOWN, never "not printing" (fail-closed, PK-R04).
moonraker_activity() {
    command -v wget >/dev/null 2>&1 || { printf 'unknown\n'; return 0; }
    MR_RESP=$(wget -q -T 3 -O - "$MOONRAKER_URL" 2>/dev/null || true)
    if [ -z "$MR_RESP" ] || ! printf '%s\n' "$MR_RESP" | awk '/"state"[[:space:]]*:/ { f = 1; exit } END { exit !f }'; then
        printf 'unknown\n'
        return 0
    fi
    if printf '%s\n' "$MR_RESP" | awk '
        /"state"[[:space:]]*:[[:space:]]*"printing"/ || /"state"[[:space:]]*:[[:space:]]*"paused"/ { found=1; exit }
        END { exit !found }
    '; then
        printf 'printing\n'
    else
        printf 'idle\n'
    fi
    return 0
}

# Any live (non-zombie, non-stopped) process whose comm or argv[0] basename is $1.
has_proc_named() {
    [ -d "$PROC_ROOT" ] || return 1
    for HP_DIR in "$PROC_ROOT"/[0-9]*; do
        [ -d "$HP_DIR" ] || continue
        HP_PID="${HP_DIR##*/}"
        [ "$HP_PID" = "$$" ] && continue
        kill -0 "$HP_PID" 2>/dev/null || continue
        proc_is_active "$HP_DIR" || continue
        HP_COMM=$(cat "$HP_DIR/comm" 2>/dev/null || true)
        [ "$HP_COMM" = "$1" ] && return 0
        [ -r "$HP_DIR/cmdline" ] || continue
        HP_ARG0=$(tr '\0' '\n' < "$HP_DIR/cmdline" 2>/dev/null | head -n 1)
        [ "${HP_ARG0##*/}" = "$1" ] && return 0
    done
    return 1
}

has_proc_klippy() {
    [ -d "$PROC_ROOT" ] || return 1
    for HK_DIR in "$PROC_ROOT"/[0-9]*; do
        [ -r "$HK_DIR/cmdline" ] || continue
        HK_PID="${HK_DIR##*/}"
        [ "$HK_PID" = "$$" ] && continue
        kill -0 "$HK_PID" 2>/dev/null || continue
        proc_is_active "$HK_DIR" || continue
        if tr '\0' ' ' < "$HK_DIR/cmdline" 2>/dev/null | awk 'index($0, "klippy") > 0 { f = 1 } END { exit !f }'; then
            return 0
        fi
    done
    return 1
}

read_state_count() {
    if [ -f "$STATE_FILE" ]; then
        VAL=$(tr -d ' \t\r\n' < "$STATE_FILE" 2>/dev/null || echo 0)
        case "$VAL" in
            ''|*[!0-9]*) printf '0\n' ;;
            *) printf '%s\n' "$VAL" ;;
        esac
    else
        printf '0\n'
    fi
}

write_state_count() {
    NEW_VAL="$1"
    mkdir -p "$RESCUE_DIR" 2>/dev/null || return 1
    TMP_ST="${STATE_FILE}.openkobra.tmp"
    printf '%s\n' "$NEW_VAL" > "$TMP_ST" 2>/dev/null && sync 2>/dev/null && mv "$TMP_ST" "$STATE_FILE" 2>/dev/null && sync 2>/dev/null
    RC_WS=$?
    rm -f "$TMP_ST" 2>/dev/null || true
    return "$RC_WS"
}

disable_kobra_screen_app() {
    mkdir -p "$APPS_DIR" 2>/dev/null || return 1
    if [ -d "$APPS_DIR/kobra-screen" ]; then
        rm -f "$APPS_DIR/kobra-screen.enabled" "$APPS_DIR/kobra-screen.disabled" "$APPS_DIR/kobra-screen/.enabled" 2>/dev/null || true
        TMP_DIS="$APPS_DIR/kobra-screen/.disabled.openkobra.tmp"
        : > "$TMP_DIS" 2>/dev/null && sync 2>/dev/null && mv "$TMP_DIS" "$APPS_DIR/kobra-screen/.disabled" 2>/dev/null && sync 2>/dev/null
        rm -f "$TMP_DIS" 2>/dev/null || true
    else
        rm -f "$APPS_DIR/kobra-screen.enabled" 2>/dev/null || true
        TMP_DIS="$APPS_DIR/kobra-screen.disabled.openkobra.tmp"
        : > "$TMP_DIS" 2>/dev/null && sync 2>/dev/null && mv "$TMP_DIS" "$APPS_DIR/kobra-screen.disabled" 2>/dev/null && sync 2>/dev/null
        rm -f "$TMP_DIS" 2>/dev/null || true
    fi
    return 0
}

create_disable_rinkhals_marker() {
    mkdir -p "$RINKHALS_DIR" 2>/dev/null || return 1
    TMP_DR="$RINKHALS_DIR/.disable-rinkhals.openkobra.tmp"
    : > "$TMP_DR" 2>/dev/null && sync 2>/dev/null && mv "$TMP_DR" "$RINKHALS_DIR/.disable-rinkhals" 2>/dev/null && sync 2>/dev/null
    RC_DR=$?
    rm -f "$TMP_DR" 2>/dev/null || true
    return "$RC_DR"
}

sleep_seconds() {
    SECS="$1"
    case "$SECS" in
        ''|*[!0-9]*) SECS=0 ;;
    esac
    if [ "$SECS" -gt 0 ]; then
        sleep "$SECS" 2>/dev/null || true
    fi
}

start_watcher_bg() {
    if [ "${OPEN_KOBRA_RESCUE_NO_WATCH:-0}" = "1" ]; then
        return 0
    fi
    mkdir -p "$TMP_DIR" 2>/dev/null || true
    if [ -f "$WATCH_PID_FILE" ]; then
        OLD_PID=$(tr -d ' \t\r\n' < "$WATCH_PID_FILE" 2>/dev/null || true)
        case "$OLD_PID" in
            ''|*[!0-9]*) ;;
            *)
                if kill -0 "$OLD_PID" 2>/dev/null; then
                    if [ ! -r "/proc/$OLD_PID/cmdline" ] || tr '\0' ' ' < "/proc/$OLD_PID/cmdline" 2>/dev/null | awk 'index($0, "rescue.sh") > 0 { f=1; exit } END { exit !f }'; then
                        return 0
                    fi
                fi
                ;;
        esac
    fi
    if command -v setsid >/dev/null 2>&1; then
        setsid sh "$SELF_PATH" watch </dev/null >/dev/null 2>&1 &
    elif command -v nohup >/dev/null 2>&1; then
        nohup sh "$SELF_PATH" watch </dev/null >/dev/null 2>&1 &
    else
        sh "$SELF_PATH" watch </dev/null >/dev/null 2>&1 &
    fi
    return 0
}

cmd_boot() {
    BOOT_RC=0
    if [ -n "$ROOT" ] && [ -n "${OPEN_KOBRA_RESCUE_TEST_SLEEP:-}" ]; then
        sleep "$OPEN_KOBRA_RESCUE_TEST_SLEEP" 2>/dev/null || true
    fi
    # start.sh first: cheap, and a broken start.sh is the thing that keeps the stock programs from starting.
    repair_start_sh boot || true
    if [ "$START_REPAIRED" = "1" ]; then
        BOOT_RC="$START_REPLACED_RC"
    fi
    repair_binaries
    RC_REP=$?
    if [ "$RC_REP" = "2" ]; then
        # Firmware version or model does not match verified stock copy: do nothing and exit cleanly.
        return "$BOOT_RC"
    fi
    start_watcher_bg
    return "$BOOT_RC"
}

cmd_watch() {
    mkdir -p "$TMP_DIR" 2>/dev/null || true
    printf '%s\n' "$$" > "$WATCH_PID_FILE" 2>/dev/null || true

    sleep_seconds "$INITIAL_DELAY"

    # Exit safely if rescue layer was uninstalled while sleeping
    [ -f "$SELF_PATH" ] && [ -d "$RESCUE_DIR" ] || return 0

    # Covers the case where the hook never ran (hook lost / start.sh damaged after boot): next boot must be sane.
    repair_start_sh watch || true

    if is_ui_healthy; then
        write_state_count 0 || true
        log_rescue "Saglikli: UI sureci ayakta (ilk denetim); hata sayaci sifirlandi."
        return 0
    fi

    log_rescue "UYARI: Ilk denetimde K3SysUi ve kobra_screen bulunamadi; ${RECHECK_DELAY}s sonra tekrar denetlenecek."
    sleep_seconds "$RECHECK_DELAY"

    [ -f "$SELF_PATH" ] && [ -d "$RESCUE_DIR" ] || return 0

    if is_ui_healthy; then
        write_state_count 0 || true
        log_rescue "Saglikli: UI sureci ikinci denetimde ayakta; hata sayaci sifirlandi."
        return 0
    fi

    ACT=$(moonraker_activity)
    if [ "$ACT" = "printing" ]; then
        log_rescue "Bilgi: UI bulunamadi ancak Moonraker printing/paused bildiriyor; mudahale edilmedi."
        return 0
    fi
    if [ "$ACT" != "idle" ]; then
        log_rescue "UYARI: UI bulunamadi ve Moonraker'a erisilemiyor; yazdirmiyor oldugu KANITLANAMADI, hicbir sey yapilmadi (fail-closed)."
        return 0
    fi

    # Safe, evidence-based repairs only: verified binaries and start.sh for the NEXT boot. Never disruptive by default.
    repair_binaries || true
    repair_start_sh watch || true

    if [ ! -f "$AUTO_RECOVERY_MARKER" ]; then
        log_rescue "UYARI: UI sureci bulunamadi (Moonraker erisilebilir, baski yok). Otomatik kurtarma KAPALI (varsayilan): yeniden baslatma ve .disable-rinkhals YAPILMADI. Acmak icin: $AUTO_RECOVERY_MARKER"
        return 0
    fi
    # Opt-in cascade: only with strong evidence that nothing of the printer stack is alive.
    for ALIVE in K3SysUi kobra_screen gklib; do
        if has_proc_named "$ALIVE"; then
            log_rescue "Bilgi: UI yok ama $ALIVE ayakta; otomatik kurtarma tetiklenmedi."
            return 0
        fi
    done
    if has_proc_klippy; then
        log_rescue "Bilgi: UI yok ama klippy ayakta; otomatik kurtarma tetiklenmedi."
        return 0
    fi

    CUR_FAIL=$(read_state_count)
    NEXT_FAIL=$((CUR_FAIL + 1))
    write_state_count "$NEXT_FAIL" || true

    if [ "$NEXT_FAIL" -eq 1 ]; then
        log_rescue "KURTARMA (1. hata, otomatik kurtarma acik): kobra-screen devre disi birakiliyor ve sistem yeniden baslatiliyor."
        disable_kobra_screen_app || true
        sync 2>/dev/null || true
        reboot 2>/dev/null || true
    elif [ "$NEXT_FAIL" -eq 2 ]; then
        log_rescue "KURTARMA (2. hata, otomatik kurtarma acik): Rinkhals devre disi birakiliyor (.disable-rinkhals) ve sistem saf stoga yeniden baslatiliyor."
        create_disable_rinkhals_marker || true
        sync 2>/dev/null || true
        reboot 2>/dev/null || true
    else
        log_rescue "KRITIK (${NEXT_FAIL}. hata): Ardarda acilis hatasi; sonsuz donguyu onlemek icin yeniden baslatma YAPILMADI. Elle kurtarma gerekli."
        sync 2>/dev/null || true
    fi
    return 0
}

cmd_hook() {
    # Called by the start.sh hook (a 3-line stub). Runs "boot" bounded; answers START_REPLACED_RC only when start.sh was
    # replaced AND the new file parses, so the hook can restart start.sh once on it.
    HK_TO="${OPEN_KOBRA_RESCUE_BOOT_TIMEOUT:-15}"
    HK_RC=0
    if command -v timeout >/dev/null 2>&1 && timeout -t 1 sh -c : >/dev/null 2>&1; then
        timeout -t "$HK_TO" sh "$SELF_PATH" boot || HK_RC=$?
    elif command -v timeout >/dev/null 2>&1 && timeout 1 sh -c : >/dev/null 2>&1; then
        timeout "$HK_TO" sh "$SELF_PATH" boot || HK_RC=$?
    else
        return 0
    fi
    if [ "$HK_RC" = "$START_REPLACED_RC" ] && [ -s "$START_SH" ] && sh -n "$START_SH" >/dev/null 2>&1; then
        return "$START_REPLACED_RC"
    fi
    return 0
}

case "${1:-}" in
    boot)
        cmd_boot
        exit $?
        ;;
    hook)
        cmd_hook
        exit $?
        ;;
    watch)
        cmd_watch
        ;;
    *)
        printf 'Usage: %s {boot|watch|hook}\n' "$0" >&2
        exit 1
        ;;
esac
exit 0

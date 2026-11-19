#!/bin/sh
# Rinkhals app wrapper for ace-standalone.

RINKHALS_TOOLS=${RINKHALS_TOOLS:-/useremain/rinkhals/.current/tools.sh}
. "$RINKHALS_TOOLS"

APP_ROOT=$(dirname "$(realpath "$0")")
APP_NAME=$(basename "$APP_ROOT")
APP_LOG=${RINKHALS_LOGS:-/tmp/rinkhals}/app-ace-standalone.log
PID_FILE=${ACE_STANDALONE_PID_FILE:-/tmp/app-ace-standalone.pid}
PROC_ROOT=${ACE_PROC_ROOT:-/proc}

log_err() {
    MSG=$1
    mkdir -p "$(dirname "$APP_LOG")"
    echo "$(date): $MSG" >> "$APP_LOG"
    log "$MSG"
}

is_pid_running() {
    PID=$1
    if [ -z "$PID" ]; then
        return 1
    fi
    if ! kill -0 "$PID" 2>/dev/null; then
        return 1
    fi
    if [ -r "$PROC_ROOT/$PID/cmdline" ] && tr '\0' ' ' < "$PROC_ROOT/$PID/cmdline" 2>/dev/null | grep -q "server.py"; then
        return 0
    fi
    return 1
}

get_running_pids() {
    FOUND=""
    if [ -f "$PID_FILE" ]; then
        FILE_PID=$(cat "$PID_FILE" 2>/dev/null)
        if is_pid_running "$FILE_PID"; then
            FOUND="$FILE_PID"
        fi
    fi

    NAME_PIDS=$(get_by_name "$APP_ROOT/server.py")
    for P in $NAME_PIDS; do
        case " $FOUND " in
            *" $P "*) ;;
            *) FOUND="$FOUND $P" ;;
        esac
    done

    echo "$FOUND" | awk '{$1=$1; print}'
}

is_port_listening() {
    PORT=$1
    [ -n "$PORT" ] || return 1
    HEX_PORT=$(printf '%04X' "$PORT" 2>/dev/null) || return 1
    for TCP_FILE in "$PROC_ROOT/net/tcp" "$PROC_ROOT/net/tcp6"; do
        [ -r "$TCP_FILE" ] || continue
        if awk -v hp=":$HEX_PORT" 'toupper($2) ~ hp "$" && $4 == "0A" { found=1; exit } END { exit !found }' "$TCP_FILE"; then
            return 0
        fi
    done
    return 1
}

find_ace_sysfs_ports() {
    for PATTERN in "/sys/class/tty/ttyACM*" "/sys/class/tty/ttyUSB*"; do
        for TTY in $PATTERN; do
            [ -e "$TTY" ] || continue
            DESC=""
            for REL in "device/../product" "device/../manufacturer" "device/../../product" "device/../../manufacturer"; do
                if [ -r "$TTY/$REL" ]; then
                    DESC="$DESC $(cat "$TTY/$REL" 2>/dev/null)"
                fi
            done
            if echo "$DESC" | tr '[:upper:]' '[:lower:]' | grep -q "ace"; then
                echo "/dev/$(basename "$TTY")"
            fi
        done
    done
    for LINK in /dev/serial/by-id/*; do
        [ -e "$LINK" ] || continue
        if basename "$LINK" | tr '[:upper:]' '[:lower:]' | grep -q "ace"; then
            realpath "$LINK" 2>/dev/null || echo "$LINK"
        fi
    done
}

find_port_holder() {
    DEV=$1
    REAL_DEV=$(realpath "$DEV" 2>/dev/null || echo "$DEV")
    SELF_PID=$$

    for FD in "$PROC_ROOT"/[0-9]*/fd/*; do
        [ -L "$FD" ] || continue
        TARGET=$(readlink "$FD" 2>/dev/null) || continue
        if [ "$TARGET" = "$REAL_DEV" ] || [ "$TARGET" = "$DEV" ]; then
            HOLDER_PID=$(echo "$FD" | sed -n 's|.*/\([0-9][0-9]*\)/fd/.*|\1|p')
            if [ -n "$HOLDER_PID" ] && [ "$HOLDER_PID" != "$SELF_PID" ]; then
                echo "$HOLDER_PID"
                return 0
            fi
        fi
    done
    return 1
}

is_vanilla_klipper_enabled() {
    if command -v is_app_enabled >/dev/null 2>&1; then
        [ "$(is_app_enabled vanilla-klipper 2>/dev/null)" = "1" ]
        return $?
    fi
    VK_APPS=${USER_APP_PATH:-/useremain/home/rinkhals/apps}
    if [ -f "$VK_APPS/vanilla-klipper/.disabled" ] || [ -f "$VK_APPS/vanilla-klipper.disabled" ]; then
        return 1
    fi
    [ -f "$VK_APPS/vanilla-klipper/.enabled" ] || [ -f "$VK_APPS/vanilla-klipper.enabled" ]
}

# Rinkhals starts apps alphabetically, so ace-standalone runs BEFORE vanilla-klipper, which only launches klippy
# ~15 s later (launchwrapper). A running-klippy scan alone sees nothing at boot: also refuse when vanilla-klipper is
# enabled and its config drives the ACE (the two must never share the serial port).
check_vanilla_enabled_ace_conflict() {
    is_vanilla_klipper_enabled || return 0
    CFG_ENA=${ACE_KLIPPER_CONFIG:-/userdata/app/gk/printer_data/config/printer.klipper.cfg}
    [ -r "$CFG_ENA" ] || return 0
    if grep -qE '^[[:space:]]*\[ace\]|^[[:space:]]*\[include[[:space:]].*ace.*\.cfg' "$CFG_ENA" 2>/dev/null; then
        log_err "HATA: vanilla-klipper etkin ve ACE sürücüsüyle yapılandırılmış ($CFG_ENA); açılışta klippy bu uygulamadan ~15 sn sonra başlayıp aynı seri portu açar. Önce vanilla-klipper'ı devre dışı bırakın veya [ace] yapılandırmasını kaldırın."
        return 1
    fi
    return 0
}

check_klippy_ace_conflict() {
    check_vanilla_enabled_ace_conflict || return 1
    for CMD_FILE in "$PROC_ROOT"/[0-9]*/cmdline; do
        [ -r "$CMD_FILE" ] || continue
        CMDLINE=$(tr '\0' ' ' < "$CMD_FILE" 2>/dev/null)
        case "$CMDLINE" in
            *klippy*) ;;
            *) continue ;;
        esac
        KLIPPY_PID=$(echo "$CMD_FILE" | sed -n 's|.*/\([0-9][0-9]*\)/cmdline$|\1|p')
        [ -n "$KLIPPY_PID" ] || continue
        kill -0 "$KLIPPY_PID" 2>/dev/null || continue

        CFG_PATH=${ACE_KLIPPER_CONFIG:-}
        if [ -z "$CFG_PATH" ]; then
            for ARG in $CMDLINE; do
                case "$ARG" in
                    *.cfg) CFG_PATH=$ARG ;;
                esac
            done
        fi
        CFG_PATH=${CFG_PATH:-/userdata/app/gk/printer_data/config/printer.klipper.cfg}
        [ -r "$CFG_PATH" ] || continue

        if grep -qE '^[[:space:]]*\[ace\]|^[[:space:]]*\[include[[:space:]].*ace.*\.cfg' "$CFG_PATH" 2>/dev/null; then
            log_err "HATA: vanilla-klipper (klippy, PID: $KLIPPY_PID) ACE sürücüsü ile çalışıyor ($CFG_PATH). İki süreç aynı anda ACE'yi süremez; önce vanilla-klipper'ı veya [ace] yapılandırmasını durdurun."
            return 1
        fi
    done
    return 0
}

check_serial_conflict() {
    WANTED_PORT=$1
    PORTS_TO_CHECK=""

    if [ -z "$WANTED_PORT" ] || [ "$WANTED_PORT" = "auto" ]; then
        PORTS_TO_CHECK=$(find_ace_sysfs_ports)
        if [ -z "$PORTS_TO_CHECK" ]; then
            for DEV in /dev/ttyACM* /dev/ttyUSB*; do
                [ -e "$DEV" ] || continue
                PORTS_TO_CHECK="$PORTS_TO_CHECK $DEV"
            done
        fi
    else
        PORTS_TO_CHECK=$WANTED_PORT
    fi

    for PORT in $PORTS_TO_CHECK; do
        HOLDER_PID=$(find_port_holder "$PORT")
        if [ -n "$HOLDER_PID" ]; then
            HOLDER_NAME=$(cat "$PROC_ROOT/$HOLDER_PID/comm" 2>/dev/null)
            if [ -z "$HOLDER_NAME" ]; then
                HOLDER_NAME=$(tr '\0' ' ' < "$PROC_ROOT/$HOLDER_PID/cmdline" 2>/dev/null | awk '{print $1}')
            fi
            log_err "HATA: $PORT seri portu başka bir süreç tarafından kullanılıyor (PID: $HOLDER_PID, süreç: ${HOLDER_NAME:-bilinmiyor}). ACE portunu aynı anda yalnızca tek bir süreç kullanabilir; önce gklib/klippy veya ilgili süreci durdurun."
            return 1
        fi
    done
    return 0
}

has_rinkhals_override() {
    PROP=$1
    for CFG in "$TEMPORARY_APP_PATH/$APP_NAME.config" "$USER_APP_PATH/$APP_NAME.config"; do
        if [ -f "$CFG" ] && command -v jq >/dev/null 2>&1; then
            if jq -e "has(\"$PROP\")" "$CFG" >/dev/null 2>&1; then
                return 0
            fi
        fi
    done
    return 1
}

status() {
    PIDS=$(get_running_pids)

    if [ -z "$PIDS" ]; then
        report_status "$APP_STATUS_STOPPED" "" "$APP_LOG"
    else
        report_status "$APP_STATUS_STARTED" "$PIDS" "$APP_LOG"
    fi
}

start() {
    PIDS=$(get_running_pids)
    if [ -n "$PIDS" ]; then
        log "ace-standalone zaten çalışıyor (PID: $PIDS)"
        return 0
    fi

    SERIAL_PORT=$(get_app_property "$APP_NAME" serial_port)
    HTTP_PORT=$(get_app_property "$APP_NAME" http_port)
    BIND_HOST=$(get_app_property "$APP_NAME" bind_host)
    MAX_TEMP=$(get_app_property "$APP_NAME" max_temp)

    HTTP_PORT=${HTTP_PORT:-8890}
    BIND_HOST=${BIND_HOST:-127.0.0.1}

    if ! check_klippy_ace_conflict; then
        return 1
    fi

    if ! check_serial_conflict "$SERIAL_PORT"; then
        return 1
    fi

    if is_port_listening "$HTTP_PORT"; then
        log_err "HATA: $HTTP_PORT HTTP portu başka bir süreç tarafından kullanılıyor. ace-standalone ayarlarından http_port değerini değiştirin."
        return 1
    fi

    PYTHON_BIN=$(command -v python3 || command -v python)
    if [ -z "$PYTHON_BIN" ]; then
        log_err "HATA: python3 veya python bulunamadı"
        return 1
    fi

    mkdir -p "$(dirname "$APP_LOG")"
    cd "$APP_ROOT" || return 1

    set -- "$APP_ROOT/server.py" \
        --host "$BIND_HOST" \
        --http-port "$HTTP_PORT" \
        --config "$APP_ROOT/config.json"

    if [ -n "$SERIAL_PORT" ] && [ "$SERIAL_PORT" != "auto" ]; then
        set -- "$@" --port "$SERIAL_PORT"
    fi

    if has_rinkhals_override max_temp || [ ! -f "$APP_ROOT/config.json" ]; then
        if [ -n "$MAX_TEMP" ]; then
            set -- "$@" --max-temp "$MAX_TEMP"
        fi
    fi

    PYTHONGC=1 PYTHONGCTHRESHOLD="500,5,5" \
        "$PYTHON_BIN" "$@" >> "$APP_LOG" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_FILE"

    WAIT_STEP=0
    while [ "$WAIT_STEP" -lt 14 ]; do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            REASON=$(awk '/Error|Errno|Exception/ { last=$0 } END { print last }' "$APP_LOG" 2>/dev/null)
            rm -f "$PID_FILE"
            if [ -n "$REASON" ]; then
                log_err "HATA: ace-standalone başlatılamadı ($REASON); ayrıntılar için: $APP_LOG"
            else
                log_err "HATA: ace-standalone başlatılamadı; ayrıntılar için: $APP_LOG"
            fi
            return 1
        fi
        if is_port_listening "$HTTP_PORT"; then
            log "ace-standalone başlatıldı (PID: $SERVER_PID, http://$BIND_HOST:$HTTP_PORT)"
            return 0
        fi
        WAIT_STEP=$((WAIT_STEP + 1))
        if command -v usleep >/dev/null 2>&1; then
            usleep 250000
        else
            sleep 1
            WAIT_STEP=$((WAIT_STEP + 3))
        fi
    done

    kill -15 "$SERVER_PID" 2>/dev/null
    rm -f "$PID_FILE"
    log_err "HATA: ace-standalone $HTTP_PORT portunda dinlemeye başlamadı; ayrıntılar için: $APP_LOG"
    return 1
}

stop() {
    PIDS=$(get_running_pids)

    for PID in $PIDS; do
        kill -15 "$PID" 2>/dev/null
    done

    if [ -n "$PIDS" ]; then
        TRIES=0
        while [ "$TRIES" -lt 10 ]; do
            STILL_ALIVE=0
            for PID in $PIDS; do
                if kill -0 "$PID" 2>/dev/null; then
                    STILL_ALIVE=1
                fi
            done
            [ "$STILL_ALIVE" -eq 0 ] && break
            TRIES=$((TRIES + 1))
            sleep 1
        done

        for PID in $PIDS; do
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null
            fi
        done
    fi

    rm -f "$PID_FILE"
    log "ace-standalone durduruldu"
}

version() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '.version' "$APP_ROOT/app.json"
    else
        sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$APP_ROOT/app.json" | head -n 1
    fi
}

case "$1" in
    status)
        status
        ;;
    start)
        start
        ;;
    stop)
        stop
        ;;
    version)
        version
        ;;
    *)
        echo "Usage: $0 {status|start|stop|version}" >&2
        exit 1
        ;;
esac

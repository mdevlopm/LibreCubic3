#!/bin/sh

RINKHALS_PATH="${RINKHALS_ROOT:-/useremain/rinkhals/.current}"
RINKHALS_TOOLS="$RINKHALS_PATH/tools.sh"
if [ -f "$RINKHALS_TOOLS" ]; then
    # shellcheck disable=SC1090
    . "$RINKHALS_TOOLS"
elif [ -f /useremain/rinkhals/.current/tools.sh ]; then
    # shellcheck disable=SC1091
    . /useremain/rinkhals/.current/tools.sh
fi

APP_ROOT=$(dirname "$(realpath "$0")")
APP_NAME=$(basename "$APP_ROOT")
LOG_DIR="${RINKHALS_LOGS:-/tmp/rinkhals}"
LOG_FILE="$LOG_DIR/app-kobra-screen.log"
PID_FILE="${KOBRA_PID_FILE:-/tmp/kobra-screen.pid}"
HB_FILE="${KOBRA_HEARTBEAT_FILE:-/tmp/kobra-screen.hb}"
STOP_MARKER="${KOBRA_STOP_MARKER:-/tmp/kobra-screen.stopping}"
TRIAL_MARKER="${KOBRA_TRIAL_MARKER:-/tmp/kobra-screen.trial_expired}"
SUP_PID_FILE="${KOBRA_SUPERVISOR_PID_FILE:-/tmp/kobra-screen-sup.pid}"
PROC_ROOT="${KOBRA_PROC_ROOT:-/proc}"
SYS_NET="${KOBRA_SYS_NET:-/sys/class/net}"
STOCK_GK_DIR="${KOBRA_STOCK_GK_DIR:-/userdata/app/gk}"
WIFI_IFACE="${KOBRA_WIFI_IFACE:-wlan0}"
MAX_CRASHES="${KOBRA_MAX_CRASHES:-2}"
CRASH_WINDOW_SEC="${KOBRA_CRASH_WINDOW_SEC:-60}"

get_k3sysui_pids() {
    FOUND=""
    for p in "$PROC_ROOT"/[0-9]*; do
        [ -d "$p" ] || continue
        pid="${p##*/}"
        [ "$pid" = "$$" ] && continue
        kill -0 "$pid" 2>/dev/null || continue
        matched=0
        comm=""
        if [ -r "$p/comm" ]; then
            read -r comm < "$p/comm" 2>/dev/null || comm=""
            [ "$comm" = "K3SysUi" ] && matched=1
        fi
        if [ "$matched" -eq 0 ] && [ "$comm" = "" ] && [ -r "$p/cmdline" ]; then
            arg0=$(tr '\0' '\n' < "$p/cmdline" 2>/dev/null | head -n 1)
            [ "${arg0##*/}" = "K3SysUi" ] && matched=1
        fi
        if [ "$matched" -eq 1 ]; then
            FOUND="$FOUND $pid"
        fi
    done
    # shellcheck disable=SC2086
    echo $FOUND
}

get_rinkhals_ui_pids() {
    FOUND=""
    for p in "$PROC_ROOT"/[0-9]*; do
        [ -r "$p/cmdline" ] || continue
        pid="${p##*/}"
        [ "$pid" = "$$" ] && continue
        kill -0 "$pid" 2>/dev/null || continue
        if [ -r "$p/comm" ]; then
            read -r comm < "$p/comm" 2>/dev/null || comm=""
            if [ "$comm" != "" ]; then
                case "$comm" in
                    python*|rinkhals-ui.py) ;;
                    *) continue ;;
                esac
            fi
        fi
        args=$(tr '\0' '\n' < "$p/cmdline" 2>/dev/null)
        [ "$args" = "" ] && continue
        arg0=$(printf '%s\n' "$args" | head -n 1)
        base0="${arg0##*/}"
        matched=0
        if [ "$base0" = "rinkhals-ui.py" ]; then
            matched=1
        else
            case "$base0" in
                python*)
                    for a in $args; do
                        if [ "${a##*/}" = "rinkhals-ui.py" ]; then
                            matched=1
                            break
                        fi
                    done
                    ;;
            esac
        fi
        if [ "$matched" -eq 1 ]; then
            FOUND="$FOUND $pid"
        fi
    done
    # shellcheck disable=SC2086
    echo $FOUND
}

get_live_kobra_pids() {
    FOUND=""
    for p in "$PROC_ROOT"/[0-9]*; do
        [ -r "$p/cmdline" ] || continue
        pid="${p##*/}"
        [ "$pid" = "$$" ] && continue
        kill -0 "$pid" 2>/dev/null || continue
        if [ -r "$p/comm" ]; then
            read -r comm < "$p/comm" 2>/dev/null || comm=""
            if [ "$comm" != "" ]; then
                case "$comm" in
                    python*|kobra_screen|fake_python*) ;;
                    *) continue ;;
                esac
            fi
        fi
        args=$(tr '\0' '\n' < "$p/cmdline" 2>/dev/null)
        [ "$args" = "" ] && continue
        arg0=$(printf '%s\n' "$args" | head -n 1)
        base0="${arg0##*/}"
        matched=0
        if [ "$base0" = "kobra_screen" ]; then
            matched=1
        else
            case "$base0" in
                python*|fake_python*)
                    prev=""
                    for a in $args; do
                        if [ "$prev" = "-m" ] && [ "$a" = "kobra_screen" ]; then
                            matched=1
                            break
                        fi
                        if [ "${a##*/}" = "kobra_screen" ]; then
                            matched=1
                            break
                        fi
                        prev="$a"
                    done
                    ;;
            esac
        fi
        if [ "$matched" -eq 1 ]; then
            FOUND="$FOUND $pid"
        fi
    done
    # shellcheck disable=SC2086
    echo $FOUND
}

get_kobra_pids() {
    FOUND=$(get_live_kobra_pids)
    if [ "$FOUND" = "" ] && [ -f "$PID_FILE" ]; then
        fpid=$(tr -d '[:space:]' < "$PID_FILE" 2>/dev/null)
        case "$fpid" in
            ''|*[!0-9]*) ;;
            *)
                if kill -0 "$fpid" 2>/dev/null; then
                    FOUND="$fpid"
                fi
                ;;
        esac
    fi
    # shellcheck disable=SC2086
    echo $FOUND
}

get_wpa_supplicant_pids() {
    FOUND=""
    for p in "$PROC_ROOT"/[0-9]*; do
        [ -d "$p" ] || continue
        pid="${p##*/}"
        [ "$pid" = "$$" ] && continue
        kill -0 "$pid" 2>/dev/null || continue
        matched=0
        comm=""
        if [ -r "$p/comm" ]; then
            read -r comm < "$p/comm" 2>/dev/null || comm=""
            [ "$comm" = "wpa_supplicant" ] && matched=1
        fi
        if [ "$matched" -eq 0 ] && [ -r "$p/cmdline" ]; then
            arg0=$(tr '\0' '\n' < "$p/cmdline" 2>/dev/null | head -n 1)
            [ "${arg0##*/}" = "wpa_supplicant" ] && matched=1
        fi
        if [ "$matched" -eq 1 ]; then
            FOUND="$FOUND $pid"
        fi
    done
    # shellcheck disable=SC2086
    echo $FOUND
}

get_wpa_conf_from_proc() {
    if [ "${KOBRA_WPA_CONF:-}" != "" ]; then
        echo "$KOBRA_WPA_CONF"
        return 0
    fi
    for pid in $(get_wpa_supplicant_pids); do
        if [ -r "$PROC_ROOT/$pid/cmdline" ]; then
            args=$(tr '\0' '\n' < "$PROC_ROOT/$pid/cmdline" 2>/dev/null)
            prev=""
            for a in $args; do
                if [ "$prev" = "-c" ] && [ "$a" != "" ]; then
                    echo "$a"
                    return 0
                fi
                case "$a" in
                    -c?*)
                        echo "${a#-c}"
                        return 0
                        ;;
                esac
                prev="$a"
            done
        fi
    done
    for cand in /userdata/wifi_cfg/wpa_supplicant.conf /data/wifi_cfg/wpa_supplicant.conf /etc/wpa_supplicant.conf; do
        if [ -f "$cand" ]; then
            echo "$cand"
            return 0
        fi
    done
    echo ""
}

get_udhcpc_wlan0_pids() {
    FOUND=""
    for p in "$PROC_ROOT"/[0-9]*; do
        [ -r "$p/cmdline" ] || continue
        pid="${p##*/}"
        [ "$pid" = "$$" ] && continue
        kill -0 "$pid" 2>/dev/null || continue
        if [ -r "$p/comm" ]; then
            read -r comm < "$p/comm" 2>/dev/null || comm=""
            if [ "$comm" != "" ]; then
                case "$comm" in
                    udhcpc*) ;;
                    *) continue ;;
                esac
            fi
        fi
        args=$(tr '\0' '\n' < "$p/cmdline" 2>/dev/null)
        [ "$args" = "" ] && continue
        arg0=$(printf '%s\n' "$args" | head -n 1)
        [ "${arg0##*/}" = "udhcpc" ] || continue
        prev=""
        iface_match=0
        for a in $args; do
            if [ "$prev" = "-i" ] && [ "$a" = "$WIFI_IFACE" ]; then
                iface_match=1
                break
            fi
            if [ "$a" = "-i$WIFI_IFACE" ]; then
                iface_match=1
                break
            fi
            prev="$a"
        done
        if [ "$iface_match" -eq 1 ]; then
            FOUND="$FOUND $pid"
        fi
    done
    # shellcheck disable=SC2086
    echo $FOUND
}

resolve_cmd() {
    CMD_NAME="$1"
    ENV_VAL="$2"
    if [ "$ENV_VAL" != "" ]; then
        echo "$ENV_VAL"
        return 0
    fi
    if command -v "$CMD_NAME" >/dev/null 2>&1; then
        command -v "$CMD_NAME"
        return 0
    fi
    for d in /usr/sbin /sbin /usr/bin /bin; do
        if [ -x "$d/$CMD_NAME" ]; then
            echo "$d/$CMD_NAME"
            return 0
        fi
    done
    echo ""
}

is_wlan0_up() {
    FLAGS_FILE="$SYS_NET/$WIFI_IFACE/flags"
    if [ -r "$FLAGS_FILE" ]; then
        raw_flags=$(tr -d '[:space:]' < "$FLAGS_FILE" 2>/dev/null)
        if [ "$raw_flags" != "" ]; then
            val=$((raw_flags))
            [ $((val & 1)) -ne 0 ] && return 0
            return 1
        fi
    fi
    return 0
}

ensure_wifi_up() {
    CONF_PATH="${1:-}"
    if [ "$CONF_PATH" = "" ]; then
        CONF_PATH=$(get_wpa_conf_from_proc)
    fi

    if [ -d "$SYS_NET/$WIFI_IFACE" ] && ! is_wlan0_up; then
        IFCFG_BIN=$(resolve_cmd ifconfig "${KOBRA_IFCONFIG_BIN:-}")
        if [ "$IFCFG_BIN" != "" ]; then
            "$IFCFG_BIN" "$WIFI_IFACE" up >> "$LOG_FILE" 2>&1 || true
        fi
    fi

    if [ "$(get_wpa_supplicant_pids)" = "" ] && [ "$CONF_PATH" != "" ] && [ -f "$CONF_PATH" ]; then
        WPA_BIN=$(resolve_cmd wpa_supplicant "${KOBRA_WPA_SUPPLICANT_BIN:-}")
        if [ "$WPA_BIN" != "" ]; then
            "$WPA_BIN" -B -Dnl80211 -i "$WIFI_IFACE" -c "$CONF_PATH" >> "$LOG_FILE" 2>&1 || true
        fi
    fi

    # Respect 10-hostname-dns / dhcp_watchdog: only start udhcpc if none exists for wlan0
    if [ "$(get_udhcpc_wlan0_pids)" = "" ]; then
        if [ -d "$SYS_NET/$WIFI_IFACE" ] || [ "${KOBRA_UDHCPC_BIN:-}" != "" ]; then
            UDHCPC_BIN=$(resolve_cmd udhcpc "${KOBRA_UDHCPC_BIN:-}")
            if [ "$UDHCPC_BIN" != "" ]; then
                # same -x hostname: as Rinkhals' 10-hostname-dns, whose watchdog kills any udhcpc without it
                UDHCPC_HOST=$(hostname 2>/dev/null | tr -d '[:space:]')
                if [ "$UDHCPC_HOST" != "" ] && [ "$UDHCPC_HOST" != "localhost" ] && [ "$UDHCPC_HOST" != "(none)" ]; then
                    "$UDHCPC_BIN" -b -i "$WIFI_IFACE" -x "hostname:$UDHCPC_HOST" >/dev/null 2>&1 &
                else
                    "$UDHCPC_BIN" -b -i "$WIFI_IFACE" >/dev/null 2>&1 &
                fi
            fi
        fi
    fi
}

sleep_short_ms() {
    MS="$1"
    if command -v usleep >/dev/null 2>&1; then
        usleep $((MS * 1000)) 2>/dev/null && return 0
    fi
    SEC=$(awk -v ms="$MS" 'BEGIN { printf "%.3f", ms / 1000.0 }')
    sleep "$SEC" 2>/dev/null || sleep 1
}

stop_stock_ui() {
    WPA_CONF_ARG="${1:-}"
    if [ "$(get_rinkhals_ui_pids)" != "" ]; then
        return 0
    fi
    if [ "$WPA_CONF_ARG" = "" ]; then
        WPA_CONF_ARG=$(get_wpa_conf_from_proc)
    fi
    K3_PIDS=$(get_k3sysui_pids)
    if [ "$K3_PIDS" != "" ]; then
        for pid in $K3_PIDS; do
            kill -TERM "$pid" 2>/dev/null || true
        done
        k_i=0
        while [ "$k_i" -lt 10 ]; do
            [ "$(get_k3sysui_pids)" = "" ] && break
            sleep_short_ms 100
            k_i=$((k_i + 1))
        done
        REM_K3=$(get_k3sysui_pids)
        if [ "$REM_K3" != "" ]; then
            for pid in $REM_K3; do
                kill -KILL "$pid" 2>/dev/null || true
            done
        fi
    fi
    ensure_wifi_up "$WPA_CONF_ARG"
}

disable_named_app() {
    TARGET_APP="$1"
    if command -v disable_app >/dev/null 2>&1; then
        disable_app "$TARGET_APP"
        return 0
    fi
    APPS_DIR="${RINKHALS_HOME:-/useremain/home/rinkhals}/apps"
    mkdir -p "$APPS_DIR" 2>/dev/null || true
    if [ -e "$APPS_DIR/$TARGET_APP" ]; then
        rm -f "$APPS_DIR/$TARGET_APP.enabled" "$APPS_DIR/$TARGET_APP.disabled" "$APPS_DIR/$TARGET_APP/.enabled"
        : > "$APPS_DIR/$TARGET_APP/.disabled"
    else
        rm -f "$APPS_DIR/$TARGET_APP.enabled"
        : > "$APPS_DIR/$TARGET_APP.disabled"
    fi
}

disable_self() {
    disable_named_app "$APP_NAME"
}

disable_open_mode_apps() {
    disable_named_app "$APP_NAME"
    if [ "$APP_NAME" != "vanilla-klipper" ]; then
        disable_named_app "vanilla-klipper"
    fi
    sync 2>/dev/null || true
}

is_self_disabled() {
    APPS_DIR="${RINKHALS_HOME:-/useremain/home/rinkhals}/apps"
    [ -f "$APP_ROOT/.disabled" ] || [ -f "$APPS_DIR/$APP_NAME/.disabled" ] || [ -f "$APPS_DIR/$APP_NAME.disabled" ]
}

restart_stock_ui() {
    if [ "$(get_rinkhals_ui_pids)" != "" ]; then
        return 0
    fi
    if [ "$(get_k3sysui_pids)" != "" ]; then
        return 0
    fi
    if [ "$(get_live_kobra_pids)" != "" ]; then
        return 0
    fi
    STOCK_BIN="$STOCK_GK_DIR/K3SysUi"
    if [ -x "$STOCK_BIN" ]; then
        (
            cd "$STOCK_GK_DIR" || exit 1
            USE_MUTABLE_CONFIG=1
            LD_LIBRARY_PATH="$STOCK_GK_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            export USE_MUTABLE_CONFIG LD_LIBRARY_PATH
            ./K3SysUi >> "$LOG_DIR/K3SysUi.log" 2>&1 &
        ) </dev/null
    fi
}

fallback_to_stock_mode() {
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    printf '%s: kobra-screen failed/crashed; disabling open mode apps and restoring stock K3SysUi\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE" 2>/dev/null || true
    disable_open_mode_apps
    if command -v stop_app >/dev/null 2>&1; then
        stop_app vanilla-klipper >> "$LOG_FILE" 2>&1 || true
    fi
    restart_stock_ui
}

version() {
    VERSION=""
    if [ -f "$APP_ROOT/app.json" ]; then
        if command -v jq >/dev/null 2>&1; then
            VERSION=$(sed '/^[[:space:]]*\/\//d' "$APP_ROOT/app.json" | jq -r '.version // empty' 2>/dev/null)
        else
            VERSION=$(sed '/^[[:space:]]*\/\//d' "$APP_ROOT/app.json" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
        fi
    fi
    if [ "$VERSION" = "" ]; then
        VERSION="0.1.0"
    fi
    echo "$VERSION"
}

status() {
    PIDS=$(get_kobra_pids)

    if [ "$PIDS" = "" ]; then
        report_status "$APP_STATUS_STOPPED" "" "$LOG_FILE"
    else
        report_status "$APP_STATUS_STARTED" "$PIDS" "$LOG_FILE"
    fi
}

is_positive_number() {
    awk -v s="$1" 'BEGIN { exit !(s ~ /^[0-9]+([.][0-9]+)?$/ && s + 0 > 0) }'
}

has_rinkhals_touch_override() {
    PROP=$1
    for CFG in "${TEMPORARY_APP_PATH:-/tmp/rinkhals/apps}/$APP_NAME.config" "${USER_APP_PATH:-${RINKHALS_HOME:-/useremain/home/rinkhals}/apps}/$APP_NAME.config"; do
        if [ -f "$CFG" ]; then
            if command -v jq >/dev/null 2>&1; then
                if jq -e "has(\"$PROP\")" "$CFG" >/dev/null 2>&1; then
                    return 0
                fi
            elif awk -v p="\"$PROP\"" 'index($0, p) > 0 { f=1; exit } END { exit !f }' "$CFG" 2>/dev/null; then
                return 0
            fi
        fi
    done
    return 1
}

resolve_touch_properties() {
    TOUCH_CAL=""
    TOUCH_SWAP=""
    if command -v get_app_property >/dev/null 2>&1; then
        TOUCH_CAL=$(get_app_property "$APP_NAME" touch_calibration 2>/dev/null)
        TOUCH_SWAP=$(get_app_property "$APP_NAME" touch_swap_axes 2>/dev/null)
    fi
    if [ "$TOUCH_CAL" = "null" ]; then
        TOUCH_CAL=""
    fi
    if [ "$TOUCH_SWAP" = "null" ]; then
        TOUCH_SWAP=""
    fi
    if [ "$TOUCH_SWAP" = "false" ] && ! has_rinkhals_touch_override touch_swap_axes; then
        TOUCH_SWAP=""
    fi
    if [ "${KOBRA_TOUCH_CALIBRATION:-}" = "" ] && [ "$TOUCH_CAL" != "" ]; then
        KOBRA_TOUCH_CALIBRATION="$TOUCH_CAL"
    fi
    if [ "${KOBRA_TOUCH_SWAP_AXES:-}" = "" ] && [ "$TOUCH_SWAP" != "" ]; then
        KOBRA_TOUCH_SWAP_AXES="$TOUCH_SWAP"
    fi
}

resolve_python_bin() {
    UI_DIR="$RINKHALS_PATH/opt/rinkhals/ui"
    PYTHON_BIN="${KOBRA_PYTHON_BIN:-}"
    if [ "$PYTHON_BIN" = "" ]; then
        if command -v python3 >/dev/null 2>&1; then
            PYTHON_BIN="python3"
        else
            PYTHON_BIN="python"
        fi
    fi
}

export_runtime_env() {
    export LD_LIBRARY_PATH="/lib:/usr/lib:$RINKHALS_PATH/lib:$RINKHALS_PATH/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export PYTHONPATH="$APP_ROOT:$UI_DIR${PYTHONPATH:+:$PYTHONPATH}"
    export RINKHALS_HOME="${RINKHALS_HOME:-/useremain/home/rinkhals}"
    export KOBRA_APP_ROOT="$APP_ROOT"
    export KOBRA_APP_NAME="$APP_NAME"
    export KOBRA_LOG_FILE="$LOG_FILE"
    export MOONRAKER_HOST="$HOST"
    export MOONRAKER_PORT="$PORT"
    export KOBRA_PID_FILE="$PID_FILE"
    export KOBRA_HEARTBEAT_FILE="$HB_FILE"
    export KOBRA_STOP_MARKER="$STOP_MARKER"
    export KOBRA_PROC_ROOT="$PROC_ROOT"
    export KOBRA_SYS_NET="$SYS_NET"
    export KOBRA_WIFI_IFACE="$WIFI_IFACE"
    export KOBRA_SUPERVISED=1
    if [ "${DETECTED_WPA_CONF:-}" != "" ] && [ "${KOBRA_WPA_CONF:-}" = "" ]; then
        export KOBRA_WPA_CONF="$DETECTED_WPA_CONF"
    fi
    if [ "${KOBRA_TOUCH_CALIBRATION:-}" != "" ]; then
        export KOBRA_TOUCH_CALIBRATION
    fi
    if [ "${KOBRA_TOUCH_SWAP_AXES:-}" != "" ]; then
        export KOBRA_TOUCH_SWAP_AXES
    fi
}

cleanup_child_files() {
    OWNER_PID="$1"
    if [ -f "$PID_FILE" ]; then
        CUR_PID=$(tr -d '[:space:]' < "$PID_FILE" 2>/dev/null)
        if [ "$CUR_PID" = "" ] || [ "$CUR_PID" = "$OWNER_PID" ] || ! kill -0 "$CUR_PID" 2>/dev/null; then
            rm -f "$PID_FILE" "$HB_FILE" 2>/dev/null || true
        fi
    else
        rm -f "$HB_FILE" 2>/dev/null || true
    fi
}

is_supervisor_running() {
    if [ -f "$SUP_PID_FILE" ]; then
        spid=$(tr -d '[:space:]' < "$SUP_PID_FILE" 2>/dev/null)
        case "$spid" in
            ''|*[!0-9]*) ;;
            *)
                if [ "$spid" != "$$" ] && kill -0 "$spid" 2>/dev/null; then
                    return 0
                fi
                ;;
        esac
    fi
    return 1
}

start() {
    TRIAL_SEC="${KOBRA_SCREEN_TRIAL_SECONDS:-}"
    if [ "$TRIAL_SEC" != "" ] && [ "$TRIAL_SEC" != "0" ]; then
        if ! is_positive_number "$TRIAL_SEC"; then
            echo "Invalid KOBRA_SCREEN_TRIAL_SECONDS: $TRIAL_SEC" >&2
            return 1
        fi
    else
        TRIAL_SEC=""
    fi

    if [ "$(get_kobra_pids)" != "" ] || is_supervisor_running; then
        stop
    fi
    rm -f "$PID_FILE" "$HB_FILE" "$STOP_MARKER" "$TRIAL_MARKER" "$SUP_PID_FILE" 2>/dev/null || true
    mkdir -p "$LOG_DIR" 2>/dev/null || true

    HOST=""
    PORT=""
    if command -v get_app_property >/dev/null 2>&1; then
        HOST=$(get_app_property "$APP_NAME" moonraker_host 2>/dev/null)
        PORT=$(get_app_property "$APP_NAME" moonraker_port 2>/dev/null)
        CFG_IFACE=$(get_app_property "$APP_NAME" wifi_interface 2>/dev/null)
        if [ "$CFG_IFACE" != "" ] && [ "$CFG_IFACE" != "null" ] && [ "${KOBRA_WIFI_IFACE:-}" = "" ]; then
            WIFI_IFACE="$CFG_IFACE"
        fi
    fi
    if [ "$HOST" = "" ] || [ "$HOST" = "null" ]; then
        HOST="${MOONRAKER_HOST:-127.0.0.1}"
    fi
    if [ "$PORT" = "" ] || [ "$PORT" = "null" ]; then
        PORT="${MOONRAKER_PORT:-7125}"
    fi
    # Values the caller exported on purpose win; everything else is read again before every (re)start so a
    # calibration saved in the meantime is not hidden by the environment of the first launch.
    USER_TOUCH_CAL="${KOBRA_TOUCH_CALIBRATION:-}"
    USER_TOUCH_SWAP="${KOBRA_TOUCH_SWAP_AXES:-}"
    resolve_touch_properties

    DETECTED_WPA_CONF=$(get_wpa_conf_from_proc)

    resolve_python_bin

    # Launch the supervisor in the background immediately so `start` returns quickly.
    # The supervisor will stop K3SysUi only after kobra_screen actually starts and grabs the display.
    (
        cd "$APP_ROOT" || exit 1

        # Redirect to the log file, creating it if needed
        exec >> "$LOG_FILE" 2>&1

        CRASH_TIMES=""
        FIRST_LAUNCH=1
        trap 'rm -f "$SUP_PID_FILE" 2>/dev/null || true' EXIT

        while :; do
            if [ -f "$STOP_MARKER" ]; then
                break
            fi

            KOBRA_TOUCH_CALIBRATION="$USER_TOUCH_CAL"
            KOBRA_TOUCH_SWAP_AXES="$USER_TOUCH_SWAP"
            resolve_touch_properties
            export_runtime_env
            [ "${KOBRA_TOUCH_CALIBRATION:-}" = "" ] && unset KOBRA_TOUCH_CALIBRATION
            [ "${KOBRA_TOUCH_SWAP_AXES:-}" = "" ] && unset KOBRA_TOUCH_SWAP_AXES

            "$PYTHON_BIN" -m kobra_screen --host "$HOST" --port "$PORT" &
            CHILD_PID=$!
            echo "$CHILD_PID" > "$PID_FILE" 2>/dev/null || true

            # Give kobra_screen a moment to start and grab the display before killing K3SysUi
            GRAB_WAIT=0
            while [ "$GRAB_WAIT" -lt 20 ]; do
                if [ ! -d "$PROC_ROOT/$CHILD_PID" ]; then
                    # Child died before grabbing the display
                    break
                fi
                # Wait a bit for the process to initialize
                sleep_short_ms 100
                GRAB_WAIT=$((GRAB_WAIT + 1))
            done

            # Only stop K3SysUi if kobra_screen is still running (assumed to have grabbed the display)
            if kill -0 "$CHILD_PID" 2>/dev/null; then
                stop_stock_ui "$DETECTED_WPA_CONF"
            fi

            TIMER_PID=""
            if [ "$TRIAL_SEC" != "" ] && [ "$FIRST_LAUNCH" -eq 1 ]; then
                (
                    WHOLE_SEC=$(awk -v s="$TRIAL_SEC" 'BEGIN { printf "%d", int(s) }')
                    REM_MS=$(awk -v s="$TRIAL_SEC" -v w="$WHOLE_SEC" 'BEGIN { printf "%d", int((s - w) * 1000 + 0.5) }')
                    t_i=0
                    while [ "$t_i" -lt "$WHOLE_SEC" ]; do
                        kill -0 "$CHILD_PID" 2>/dev/null || exit 0
                        sleep 1
                        t_i=$((t_i + 1))
                    done
                    if [ "$REM_MS" -gt 0 ] && kill -0 "$CHILD_PID" 2>/dev/null; then
                        sleep_short_ms "$REM_MS"
                    fi
                    if kill -0 "$CHILD_PID" 2>/dev/null; then
                        printf '%s: kobra-screen trial timer expired (%ss); stopping and restoring stock K3SysUi\n' \
                            "$(date '+%Y-%m-%d %H:%M:%S')" "$TRIAL_SEC"
                        : > "$TRIAL_MARKER"
                        kill -TERM "$CHILD_PID" 2>/dev/null || true
                        j=0
                        while [ "$j" -lt 15 ] && kill -0 "$CHILD_PID" 2>/dev/null; do
                            sleep_short_ms 100
                            j=$((j + 1))
                        done
                        if kill -0 "$CHILD_PID" 2>/dev/null; then
                            kill -KILL "$CHILD_PID" 2>/dev/null || true
                        fi
                    fi
                ) </dev/null &
                TIMER_PID=$!
            fi

            wait "$CHILD_PID"
            EXIT_CODE=$?
            if [ "$TIMER_PID" != "" ]; then
                kill "$TIMER_PID" 2>/dev/null || true
                wait "$TIMER_PID" 2>/dev/null || true
            fi
            cleanup_child_files "$CHILD_PID"

            if [ -f "$STOP_MARKER" ] || is_self_disabled; then
                break
            fi
            if [ -f "$TRIAL_MARKER" ]; then
                rm -f "$TRIAL_MARKER" 2>/dev/null || true
                restart_stock_ui
                break
            fi
            if [ "$EXIT_CODE" -eq 0 ]; then
                break
            fi

            if [ "$FIRST_LAUNCH" -eq 1 ]; then
                # Check if child died immediately during startup window
                NOW_TS=$(date +%s)
                CRASH_TIMES="$NOW_TS"
            else
                NOW_TS=$(date +%s)
                NEW_LIST=""
                CRASH_COUNT=0
                for ts in $CRASH_TIMES; do
                    if [ $((NOW_TS - ts)) -le "$CRASH_WINDOW_SEC" ]; then
                        NEW_LIST="$NEW_LIST $ts"
                        CRASH_COUNT=$((CRASH_COUNT + 1))
                    fi
                done
                CRASH_TIMES="$NEW_LIST $NOW_TS"
                CRASH_COUNT=$((CRASH_COUNT + 1))
                if [ "$CRASH_COUNT" -ge "$MAX_CRASHES" ]; then
                    printf '%s: kobra-screen crash loop detected (%d crashes in %ss); switching to stock mode\n' \
                        "$(date '+%Y-%m-%d %H:%M:%S')" "$CRASH_COUNT" "$CRASH_WINDOW_SEC"
                    fallback_to_stock_mode
                    break
                fi
            fi

            # If this was an immediate failure on very first start (<1s before verify), wait slightly less or count
            if [ "$FIRST_LAUNCH" -eq 1 ] && [ "$MAX_CRASHES" -le 1 ]; then
                fallback_to_stock_mode
                break
            fi
            FIRST_LAUNCH=0
            printf '%s: kobra-screen exited unexpectedly (code %s); restarting in 1s\n' \
                "$(date '+%Y-%m-%d %H:%M:%S')" "$EXIT_CODE"
            sleep 1
            [ -f "$STOP_MARKER" ] && break
            ensure_wifi_up "$DETECTED_WPA_CONF"
        done
    ) </dev/null &
    echo $! > "$SUP_PID_FILE" 2>/dev/null || true

    # Return immediately after launching the supervisor — no blocking waits.
    # Rinkhals will see that app.sh start returned quickly (success).
    return 0
}

stop() {
    : > "$STOP_MARKER"
    PIDS=$(get_kobra_pids)
    if [ "$PIDS" != "" ]; then
        for pid in $PIDS; do
            kill -TERM "$pid" 2>/dev/null || true
        done
        MAX_WAIT="${KOBRA_STOP_WAIT_ITERS:-15}"
        i=0
        while [ "$i" -lt "$MAX_WAIT" ]; do
            if [ "$(get_kobra_pids)" = "" ]; then
                break
            fi
            sleep_short_ms 100
            i=$((i + 1))
        done
        REMAINING=$(get_kobra_pids)
        if [ "$REMAINING" != "" ]; then
            for pid in $REMAINING; do
                kill -KILL "$pid" 2>/dev/null || true
            done
            sleep_short_ms 50
        fi
    fi
    if [ -f "$SUP_PID_FILE" ]; then
        spid=$(tr -d '[:space:]' < "$SUP_PID_FILE" 2>/dev/null)
        case "$spid" in
            ''|*[!0-9]*) ;;
            *)
                if [ "$spid" != "$$" ] && kill -0 "$spid" 2>/dev/null; then
                    kill -TERM "$spid" 2>/dev/null || true
                    s_i=0
                    while [ "$s_i" -lt 15 ] && kill -0 "$spid" 2>/dev/null; do
                        sleep_short_ms 100
                        s_i=$((s_i + 1))
                    done
                    if kill -0 "$spid" 2>/dev/null; then
                        kill -KILL "$spid" 2>/dev/null || true
                    fi
                fi
                ;;
        esac
        rm -f "$SUP_PID_FILE" 2>/dev/null || true
    fi
    if [ "$(get_kobra_pids)" = "" ]; then
        rm -f "$PID_FILE" "$HB_FILE" 2>/dev/null || true
    fi
}

run_touch_diagnostic_cmd() {
    DIAG_CMD="$1"
    DIAG_SEC="$2"
    if ! is_positive_number "$DIAG_SEC"; then
        echo "Usage: $0 $DIAG_CMD [seconds]" >&2
        return 1
    fi
    WAS_RUNNING=0
    if [ "$(get_kobra_pids)" != "" ]; then
        WAS_RUNNING=1
        stop
    fi
    rm -f "$PID_FILE" "$HB_FILE" 2>/dev/null || true
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    HOST="${MOONRAKER_HOST:-127.0.0.1}"
    PORT="${MOONRAKER_PORT:-7125}"
    resolve_touch_properties
    resolve_python_bin
    stop_stock_ui
    (
        cd "$APP_ROOT" || exit 1
        export_runtime_env
        "$PYTHON_BIN" -m kobra_screen.render_lvgl "$DIAG_CMD" --duration "$DIAG_SEC"
    )
    DIAG_RC=$?
    # give the screen back: our own UI if it was running, the stock K3SysUi otherwise
    if [ "$WAS_RUNNING" -eq 1 ] && ! is_self_disabled; then
        start >/dev/null 2>&1 || true
    else
        restart_stock_ui
    fi
    return $DIAG_RC
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
    trial)
        TRIAL_ARG="${2:-${KOBRA_SCREEN_TRIAL_SECONDS:-60}}"
        if ! is_positive_number "$TRIAL_ARG"; then
            echo "Usage: $0 trial [seconds]" >&2
            exit 1
        fi
        KOBRA_SCREEN_TRIAL_SECONDS="$TRIAL_ARG"
        export KOBRA_SCREEN_TRIAL_SECONDS
        start
        ;;
    touch-diag)
        run_touch_diagnostic_cmd touch-diag "${2:-60}"
        ;;
    touch-raw)
        run_touch_diagnostic_cmd touch-raw "${2:-30}"
        ;;
    disable)
        stop
        disable_self
        ;;
    version)
        version
        ;;
    *)
        echo "Usage: $0 {status|start|stop|trial [seconds]|touch-diag [seconds]|touch-raw [seconds]|disable|version}" >&2
        exit 1
        ;;
esac

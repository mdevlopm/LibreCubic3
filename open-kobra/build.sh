#!/bin/sh
# Build open-kobra install and uninstall Rinkhals SWU packages without Docker.

set -eu

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
PARENT_ROOT=$(dirname "$PROJECT_ROOT")

KOBRA_SCREEN_SRC="$PARENT_ROOT/kobra-screen"
ACE_STANDALONE_SRC="$PARENT_ROOT/ace-standalone"
LEVIQ3_SRC="$PARENT_ROOT/leviq3-klipper"
# Optional ACE driver tree (klippy/extras/ace/*.py, klippy/extras/temperature_ace.py, config/ace_K3.cfg, VERSION).
# Overridable so tests can build with a fake tree or without one.
ACE_DRIVER_SRC="${OPEN_KOBRA_ACE_DRIVER_SRC:-$PARENT_ROOT/ace-driver}"

# K3/K2P/K3V2 SWU ZIP password verified in refs/Rinkhals/build/tools.sh and refs/Rinkhals.apps/build/tools.sh
K3_SWU_PASSWORD="U2FsdGVkX19deTfqpXHZnB5GeyQ/dtlbHjkUnwgCi+w="

for TOOL in tar gzip zip md5sum; do
    if ! command -v "$TOOL" >/dev/null 2>&1; then
        printf 'HATA: Gerekli derleme aracı bulunamadı: %s\n' "$TOOL" >&2
        exit 1
    fi
done

if [ ! -f "$PROJECT_ROOT/VERSION" ]; then
    printf 'HATA: VERSION dosyası bulunamadı.\n' >&2
    exit 1
fi
PKG_VERSION=$(tr -d ' \t\r\n' < "$PROJECT_ROOT/VERSION")
if [ -z "$PKG_VERSION" ]; then
    printf 'HATA: VERSION dosyası boş.\n' >&2
    exit 1
fi

# Single source of truth: open-kobra/VERSION. The guard app ships in the same package and must announce the same version (I-01).
GUARD_APP_VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PROJECT_ROOT/apps/05-open-kobra-guard/app.json" | head -n 1)
if [ "$GUARD_APP_VERSION" != "$PKG_VERSION" ]; then
    printf 'HATA: 05-open-kobra-guard/app.json sürümü (%s) VERSION dosyasıyla (%s) uyuşmuyor.\n' "$GUARD_APP_VERSION" "$PKG_VERSION" >&2
    exit 1
fi

KS_APP_VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$KOBRA_SCREEN_SRC/app.json" | head -n 1)
if [ "$KS_APP_VERSION" != "$PKG_VERSION" ]; then
    printf 'HATA: kobra-screen/app.json sürümü (%s) VERSION dosyasıyla (%s) uyuşmuyor.\n' "$KS_APP_VERSION" "$PKG_VERSION" >&2
    exit 1
fi

ACE_APP_VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ACE_STANDALONE_SRC/app.json" | head -n 1)
if [ "$ACE_APP_VERSION" != "$PKG_VERSION" ]; then
    printf 'HATA: ace-standalone/app.json sürümü (%s) VERSION dosyasıyla (%s) uyuşmuyor.\n' "$ACE_APP_VERSION" "$PKG_VERSION" >&2
    exit 1
fi

# Verify read-only input directories
for REQ in \
    "$KOBRA_SCREEN_SRC/app.json" \
    "$KOBRA_SCREEN_SRC/app.sh" \
    "$KOBRA_SCREEN_SRC/kobra_screen/__main__.py" \
    "$ACE_STANDALONE_SRC/app.json" \
    "$ACE_STANDALONE_SRC/app.sh" \
    "$ACE_STANDALONE_SRC/acelink.py" \
    "$ACE_STANDALONE_SRC/server.py" \
    "$ACE_STANDALONE_SRC/web/index.html" \
    "$PROJECT_ROOT/apps/05-open-kobra-guard/app.json" \
    "$PROJECT_ROOT/apps/05-open-kobra-guard/app.sh" \
    "$PROJECT_ROOT/rescue/rescue.sh" \
    "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.md5" \
    "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh" \
    "$PROJECT_ROOT/config/open_kobra_homing.cfg" \
    "$LEVIQ3_SRC/leviq3.py" \
    "$LEVIQ3_SRC/leviq3.cfg"; do
    if [ ! -f "$REQ" ]; then
        printf 'HATA: Girdi dosyası bulunamadı: %s\n' "$REQ" >&2
        exit 1
    fi
done

WORK_ROOT=$(mktemp -d)
cleanup() {
    rm -rf "$WORK_ROOT"
}
trap cleanup EXIT INT TERM

pack_swu() {
    STAGE_DIR="$1"
    OUT_SWU="$2"

    PACK_DIR=$(mktemp -d "$WORK_ROOT/pack.XXXXXX")
    mkdir -p "$PACK_DIR/update_swu"
    (cd "$STAGE_DIR" && tar --owner=0 --group=0 --numeric-owner -cf "$PACK_DIR/update_swu/setup.tar" .)
    gzip -n "$PACK_DIR/update_swu/setup.tar"
    md5sum "$PACK_DIR/update_swu/setup.tar.gz" | awk '{ print $1 }' > "$PACK_DIR/update_swu/setup.tar.gz.md5"

    mkdir -p "$(dirname "$OUT_SWU")"
    rm -f "$OUT_SWU"
    (cd "$PACK_DIR" && zip -0 -P "$K3_SWU_PASSWORD" -r "$OUT_SWU" update_swu >/dev/null)
}

DIST_DIR="${OPEN_KOBRA_DIST_DIR:-$PROJECT_ROOT/dist}"
# pack_swu runs zip from a temp directory: a relative output path would not resolve there (I-06)
case "$DIST_DIR" in
    /*) ;;
    *) DIST_DIR="$(pwd)/$DIST_DIR" ;;
esac
rm -rf "$DIST_DIR/install" "$DIST_DIR/uninstall"

# ---- 1. Stage install package ----
INSTALL_STAGE="$WORK_ROOT/install_stage"
mkdir -p "$INSTALL_STAGE/apps/kobra-screen/kobra_screen" \
         "$INSTALL_STAGE/apps/ace-standalone/web" \
         "$INSTALL_STAGE/apps/05-open-kobra-guard" \
         "$INSTALL_STAGE/rescue/manifests" \
         "$INSTALL_STAGE/config" \
         "$INSTALL_STAGE/leviq3"

cp -p "$PROJECT_ROOT/VERSION" "$INSTALL_STAGE/VERSION"
cp -p "$PROJECT_ROOT/swu/install/update.sh" "$INSTALL_STAGE/update.sh"
chmod +x "$INSTALL_STAGE/update.sh"

# kobra-screen (only runtime files; fonts come from Rinkhals assets, exclude preview/tests/__pycache__/README and the PC-only render_pil.py)
cp -p "$KOBRA_SCREEN_SRC/app.json" "$INSTALL_STAGE/apps/kobra-screen/app.json"
cp -p "$KOBRA_SCREEN_SRC/app.sh" "$INSTALL_STAGE/apps/kobra-screen/app.sh"
chmod +x "$INSTALL_STAGE/apps/kobra-screen/app.sh"
for PY_FILE in "$KOBRA_SCREEN_SRC/kobra_screen"/*.py; do
    [ -f "$PY_FILE" ] || continue
    PY_BASE=$(basename "$PY_FILE")
    case "$PY_BASE" in
        test_*.py|*_test.py|fake_*.py|make_preview.py|render_pil.py) continue ;;
    esac
    cp -p "$PY_FILE" "$INSTALL_STAGE/apps/kobra-screen/kobra_screen/$PY_BASE"
done

# ace-standalone (upstream app.json/app.sh + runtime files; exclude tests/fake/__pycache__/README)
cp -p "$ACE_STANDALONE_SRC/app.json" "$INSTALL_STAGE/apps/ace-standalone/app.json"
cp -p "$ACE_STANDALONE_SRC/app.sh" "$INSTALL_STAGE/apps/ace-standalone/app.sh"
chmod +x "$INSTALL_STAGE/apps/ace-standalone/app.sh"
cp -p "$ACE_STANDALONE_SRC/acelink.py" "$INSTALL_STAGE/apps/ace-standalone/acelink.py"
cp -p "$ACE_STANDALONE_SRC/server.py" "$INSTALL_STAGE/apps/ace-standalone/server.py"
cp -p "$ACE_STANDALONE_SRC/web/index.html" "$INSTALL_STAGE/apps/ace-standalone/web/index.html"

# 05-open-kobra-guard (boot guard ensuring leviq3.py / include safety before vanilla-klipper)
cp -p "$PROJECT_ROOT/apps/05-open-kobra-guard/app.json" "$INSTALL_STAGE/apps/05-open-kobra-guard/app.json"
cp -p "$PROJECT_ROOT/apps/05-open-kobra-guard/app.sh" "$INSTALL_STAGE/apps/05-open-kobra-guard/app.sh"
chmod +x "$INSTALL_STAGE/apps/05-open-kobra-guard/app.sh"

# Boot rescue layer (verified stock binary backup + start.sh pre-boot hook + health watcher)
cp -p "$PROJECT_ROOT/rescue/rescue.sh" "$INSTALL_STAGE/rescue/rescue.sh"
chmod +x "$INSTALL_STAGE/rescue/rescue.sh"
cp -p "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.md5" "$INSTALL_STAGE/rescue/manifests/K3_2.4.6.7.md5"
# Official 2.4.6.7 start.sh (reference for the start.sh repair, PK-R01); md5 of refs/stock-k3-2.4.6.7/start.sh
STOCK_START_MD5="b29684d20aa7e5d73bdfdab543d72d9f"
if [ "$(md5sum "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh" | awk '{ print $1 }')" != "$STOCK_START_MD5" ]; then
    printf 'HATA: rescue/manifests/K3_2.4.6.7.start.sh resmi stok start.sh değil (md5 %s bekleniyordu).\n' "$STOCK_START_MD5" >&2
    exit 1
fi
cp -p "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh" "$INSTALL_STAGE/rescue/manifests/K3_2.4.6.7.start.sh"

# leviq3 (extra + config + expected MD5 for boot guard integrity check)
cp -p "$LEVIQ3_SRC/leviq3.py" "$INSTALL_STAGE/leviq3/leviq3.py"
cp -p "$LEVIQ3_SRC/leviq3.cfg" "$INSTALL_STAGE/leviq3/leviq3.cfg"
cp -p "$PROJECT_ROOT/config/open_kobra_homing.cfg" "$INSTALL_STAGE/config/open_kobra_homing.cfg"
md5sum "$LEVIQ3_SRC/leviq3.py" | awk '{ print $1 }' > "$INSTALL_STAGE/apps/05-open-kobra-guard/leviq3.py.md5"

# ace-driver (optional): replaces vanilla-klipper's ACE driver on install.
#  - directory absent            -> package is built without it (warning, exit 0)
#  - directory present but incomplete or syntactically broken -> build FAILS: a silently driver-less package would
#    leave an old driver on the printer while the developer believes the new one shipped (I-04).
#    OPEN_KOBRA_ALLOW_NO_ACE_DRIVER=1 turns that failure into the warning + driver-less package.
ACE_DRIVER_NOTE=""
ace_driver_skip() {
    ACE_DRIVER_NOTE="atlandı ($1)"
    printf 'UYARI: ace-driver pakete eklenmedi: %s\n' "$1" >&2
}
ace_driver_broken() {
    if [ "${OPEN_KOBRA_ALLOW_NO_ACE_DRIVER:-0}" = "1" ]; then
        ace_driver_skip "$1"
    else
        printf 'HATA: ace-driver paketlenemedi: %s\n(Sürücüsüz paket üretmek için OPEN_KOBRA_ALLOW_NO_ACE_DRIVER=1 verin.)\n' "$1" >&2
        exit 1
    fi
}
ACE_DRIVER_STAGE="$WORK_ROOT/ace_driver_stage"
if [ ! -d "$ACE_DRIVER_SRC" ]; then
    ace_driver_skip "dizin yok: $ACE_DRIVER_SRC"
elif [ ! -f "$ACE_DRIVER_SRC/klippy/extras/ace/__init__.py" ] || [ ! -f "$ACE_DRIVER_SRC/klippy/extras/temperature_ace.py" ]; then
    ace_driver_broken "eksik: klippy/extras/ace/__init__.py veya klippy/extras/temperature_ace.py yok"
else
    mkdir -p "$ACE_DRIVER_STAGE/klippy/extras/ace"
    for PY_FILE in "$ACE_DRIVER_SRC/klippy/extras/ace"/*.py; do
        [ -f "$PY_FILE" ] || continue
        PY_BASE=$(basename "$PY_FILE")
        case "$PY_BASE" in
            test_*.py|*_test.py|fake_*.py) continue ;;
        esac
        cp -p "$PY_FILE" "$ACE_DRIVER_STAGE/klippy/extras/ace/$PY_BASE"
    done
    cp -p "$ACE_DRIVER_SRC/klippy/extras/temperature_ace.py" "$ACE_DRIVER_STAGE/klippy/extras/temperature_ace.py"
    if [ -s "$ACE_DRIVER_SRC/config/ace_K3.cfg" ]; then
        mkdir -p "$ACE_DRIVER_STAGE/config"
        cp -p "$ACE_DRIVER_SRC/config/ace_K3.cfg" "$ACE_DRIVER_STAGE/config/ace_K3.cfg"
    fi
    if [ -s "$ACE_DRIVER_SRC/VERSION" ]; then
        cp -p "$ACE_DRIVER_SRC/VERSION" "$ACE_DRIVER_STAGE/VERSION"
    fi

    ACE_SYNTAX_OK=1
    if command -v python3 >/dev/null 2>&1; then
        # The printer runs Python 3.11: ask the parser for 3.11 grammar where the host Python supports it.
        if ! python3 - "$ACE_DRIVER_STAGE" <<'PY'
import ast
import os
import sys

root = sys.argv[1]
failed = False
for dirpath, _dirs, files in os.walk(root):
    for name in sorted(files):
        if not name.endswith(".py"):
            continue
        path = os.path.join(dirpath, name)
        with open(path, "rb") as fh:
            src = fh.read()
        try:
            ast.parse(src, filename=path, feature_version=(3, 11))
        except SyntaxError as exc:
            print("sözdizimi hatası: %s: %s" % (os.path.relpath(path, root), exc), file=sys.stderr)
            failed = True
sys.exit(1 if failed else 0)
PY
        then
            ACE_SYNTAX_OK=0
        fi
    fi
    if [ "$ACE_SYNTAX_OK" = "1" ]; then
        ACE_PY_COUNT=$(find "$ACE_DRIVER_STAGE" -name '*.py' -type f | wc -l | tr -d ' ')
        ACE_VER="yok"
        [ -f "$ACE_DRIVER_STAGE/VERSION" ] && ACE_VER=$(tr -d ' \t\r\n' < "$ACE_DRIVER_STAGE/VERSION")
        mkdir -p "$INSTALL_STAGE/ace-driver"
        cp -PRp "$ACE_DRIVER_STAGE"/. "$INSTALL_STAGE/ace-driver"/
        ACE_DRIVER_NOTE="dahil ($ACE_PY_COUNT .py dosyası, sürüm $ACE_VER)"
    else
        ace_driver_broken "Python sözdizimi denetimi başarısız"
    fi
fi

# numpy (optional): uClibc için derlenmiş wheel varsa pakete ekle (python/ altına tek dosya).
NUMPY_WHEEL="${OPEN_KOBRA_NUMPY_WHEEL:-$PROJECT_ROOT/vendor/numpy-1.24.4-cp311-cp311-linux_armv7l.whl}"
NUMPY_NOTE=""
if [ -f "$NUMPY_WHEEL" ]; then
    NUMPY_WHEEL_BASE=$(basename "$NUMPY_WHEEL")
    mkdir -p "$INSTALL_STAGE/python"
    cp -p "$NUMPY_WHEEL" "$INSTALL_STAGE/python/$NUMPY_WHEEL_BASE"
    NUMPY_NOTE="dahil (1.24.4)"
else
    NUMPY_NOTE="atlandı (dosya yok: $NUMPY_WHEEL)"
fi

# Payload integrity manifest (verified by swu/install/update.sh before mutating target root)
(cd "$INSTALL_STAGE" && find . -type f ! -name 'MANIFEST.md5' | LC_ALL=C sort | while IFS= read -r F; do
    md5sum "$F"
done > "$INSTALL_STAGE/MANIFEST.md5")

INSTALL_SWU="$DIST_DIR/install/aGVscF9zb3Nf/update.swu"
pack_swu "$INSTALL_STAGE" "$INSTALL_SWU"

# ---- 2. Stage uninstall package ----
UNINSTALL_STAGE="$WORK_ROOT/uninstall_stage"
mkdir -p "$UNINSTALL_STAGE"
cp -p "$PROJECT_ROOT/VERSION" "$UNINSTALL_STAGE/VERSION"
cp -p "$PROJECT_ROOT/swu/uninstall/update.sh" "$UNINSTALL_STAGE/update.sh"
chmod +x "$UNINSTALL_STAGE/update.sh"

UNINSTALL_SWU="$DIST_DIR/uninstall/aGVscF9zb3Nf/update.swu"
pack_swu "$UNINSTALL_STAGE" "$UNINSTALL_SWU"

printf 'Derleme tamamlandı (open-kobra %s, model K3):\n' "$PKG_VERSION"
printf '  Kurulum paketi  : %s (%s bayt)\n' "$INSTALL_SWU" "$(wc -c < "$INSTALL_SWU" | tr -d ' ')"
printf '  Kaldırma paketi : %s (%s bayt)\n' "$UNINSTALL_SWU" "$(wc -c < "$UNINSTALL_SWU" | tr -d ' ')"
printf '  ACE sürücüsü    : %s\n' "$ACE_DRIVER_NOTE"
printf '  numpy           : %s\n' "$NUMPY_NOTE"

#!/bin/sh
# End-to-end verification suite for open-kobra build, SWU format, POSIX/BusyBox checks,
# power-loss recovery, LeviQ3 guard, and install/reinstall/uninstall lifecycle.

set -eu

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
PARENT_ROOT=$(dirname "$PROJECT_ROOT")
K3_SWU_PASSWORD="U2FsdGVkX19deTfqpXHZnB5GeyQ/dtlbHjkUnwgCi+w="

pass() {
    printf '  [OK] %s\n' "$1"
}

fail() {
    printf '  [FAIL] %s\n' "$1" >&2
    exit 1
}

has_lit() {
    awk -v t="$2" 'index($0, t) > 0 { f=1; exit } END { exit !f }' "$1"
}

has_line() {
    awk -v t="$2" '$0 == t { f=1; exit } END { exit !f }' "$1"
}

count_lit() {
    awk -v t="$2" 'index($0, t) > 0 { c++ } END { print c+0 }' "$1"
}

WORK=$(mktemp -d)
cleanup() {
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

echo "1. Derleme testi (./build.sh)..."
"$PROJECT_ROOT/build.sh"

INSTALL_SWU="$PROJECT_ROOT/dist/install/aGVscF9zb3Nf/update.swu"
UNINSTALL_SWU="$PROJECT_ROOT/dist/uninstall/aGVscF9zb3Nf/update.swu"
[ -f "$INSTALL_SWU" ] || fail "Kurulum SWU bulunamadı: $INSTALL_SWU"
[ -f "$UNINSTALL_SWU" ] || fail "Kaldırma SWU bulunamadı: $UNINSTALL_SWU"
pass "Her iki SWU paketi üretildi"

echo "2. SWU açma, MD5 ve paket içeriği doğrulama..."
extract_swu() {
    SWU="$1"
    DEST="$2"
    mkdir -p "$DEST"
    unzip -q -P "$K3_SWU_PASSWORD" "$SWU" -d "$DEST"
    [ -f "$DEST/update_swu/setup.tar.gz" ] || fail "setup.tar.gz eksik: $SWU"
    [ -f "$DEST/update_swu/setup.tar.gz.md5" ] || fail "setup.tar.gz.md5 eksik: $SWU"
    # refs/Rinkhals/build/tools.sh:14 writes bare 32-hex digest via awk '{ print $1 }'
    ACTUAL_MD5=$(md5sum "$DEST/update_swu/setup.tar.gz" | awk '{print $1}')
    EXPECTED_MD5=$(tr -d ' \t\r\n' < "$DEST/update_swu/setup.tar.gz.md5")
    [ "$ACTUAL_MD5" = "$EXPECTED_MD5" ] || fail "MD5 uyuşmazlığı: $ACTUAL_MD5 != $EXPECTED_MD5"
    tar -xzf "$DEST/update_swu/setup.tar.gz" -C "$DEST/update_swu"
}

extract_swu "$INSTALL_SWU" "$WORK/unpack_install"
extract_swu "$UNINSTALL_SWU" "$WORK/unpack_uninstall"

for F in \
    "$WORK/unpack_install/update_swu/update.sh" \
    "$WORK/unpack_install/update_swu/VERSION" \
    "$WORK/unpack_install/update_swu/apps/kobra-screen/app.sh" \
    "$WORK/unpack_install/update_swu/apps/kobra-screen/app.json" \
    "$WORK/unpack_install/update_swu/apps/kobra-screen/kobra_screen/__main__.py" \
    "$WORK/unpack_install/update_swu/apps/ace-standalone/app.sh" \
    "$WORK/unpack_install/update_swu/apps/ace-standalone/app.json" \
    "$WORK/unpack_install/update_swu/apps/ace-standalone/server.py" \
    "$WORK/unpack_install/update_swu/apps/ace-standalone/acelink.py" \
    "$WORK/unpack_install/update_swu/apps/ace-standalone/web/index.html" \
    "$WORK/unpack_install/update_swu/apps/05-open-kobra-guard/app.sh" \
    "$WORK/unpack_install/update_swu/apps/05-open-kobra-guard/app.json" \
    "$WORK/unpack_install/update_swu/apps/05-open-kobra-guard/leviq3.py.md5" \
    "$WORK/unpack_install/update_swu/rescue/rescue.sh" \
    "$WORK/unpack_install/update_swu/rescue/manifests/K3_2.4.6.7.md5" \
    "$WORK/unpack_install/update_swu/config/open_kobra_homing.cfg" \
    "$WORK/unpack_install/update_swu/leviq3/leviq3.py" \
    "$WORK/unpack_install/update_swu/leviq3/leviq3.cfg" \
    "$WORK/unpack_uninstall/update_swu/update.sh" \
    "$WORK/unpack_uninstall/update_swu/VERSION"; do
    [ -f "$F" ] || fail "Paket içi dosya eksik: $F"
done

python3 - "$WORK/unpack_install/update_swu/setup.tar.gz" "$WORK/unpack_uninstall/update_swu/setup.tar.gz" <<'PY'
import sys, tarfile
for p in sys.argv[1:]:
    with tarfile.open(p, "r:gz") as tf:
        for m in tf.getmembers():
            assert m.uid == 0 and m.gid == 0, f"{p}: {m.name} uid={m.uid} gid={m.gid}"
PY
EXP_LEVIQ_MD5=$(md5sum "$PARENT_ROOT/leviq3-klipper/leviq3.py" | awk '{print $1}')
PACK_LEVIQ_MD5=$(tr -d ' \t\r\n' < "$WORK/unpack_install/update_swu/apps/05-open-kobra-guard/leviq3.py.md5")
[ "$EXP_LEVIQ_MD5" = "$PACK_LEVIQ_MD5" ] || fail "leviq3.py.md5 paket özeti uyuşmuyor: $PACK_LEVIQ_MD5 != $EXP_LEVIQ_MD5"
if awk '!/^[[:space:]]*#/ && index($0, "/userdata/") > 0 { f=1; exit } END { exit !f }' "$WORK/unpack_install/update_swu/leviq3/leviq3.cfg"; then
    fail "leviq3.cfg mutlak /userdata/ yolu içermemeli (B-07)"
fi
has_line "$WORK/unpack_install/update_swu/leviq3/leviq3.cfg" "state_file: /useremain/home/rinkhals/printer_data/config/leviq3_state.json" || fail "leviq3.cfg state_file Rinkhals kalıcı yolunu göstermeli (B-07)"

[ ! -e "$WORK/unpack_install/update_swu/apps/kobra-screen/.enabled" ] || fail "kobra-screen .enabled içermemeli"
[ ! -e "$WORK/unpack_install/update_swu/apps/ace-standalone/.enabled" ] || fail "ace-standalone .enabled içermemeli"
[ ! -e "$WORK/unpack_install/update_swu/apps/kobra-screen/fonts" ] || fail "kobra-screen/fonts pakete dahil edilmemeli"

LEAKED=$(find "$WORK/unpack_install/update_swu" \( \
    -name 'fonts' -o -name 'preview' -o -name '__pycache__' -o -name '*.pyc' \
    -o -name 'test_*.py' -o -name '*_test.py' -o -name 'fake_*.py' \
    -o -name 'make_preview.py' -o -name 'render_pil.py' -o -name 'README.md' \
\) | head -n 1 || true)
[ -z "$LEAKED" ] || fail "Pakette istenmeyen dosya/dizin sızdı: $LEAKED"

cmp -s "$WORK/unpack_install/update_swu/apps/ace-standalone/app.sh" "$PARENT_ROOT/ace-standalone/app.sh" || fail "Paket içi ace-standalone/app.sh güncel değil"
cmp -s "$WORK/unpack_install/update_swu/apps/ace-standalone/app.json" "$PARENT_ROOT/ace-standalone/app.json" || fail "Paket içi ace-standalone/app.json güncel değil"
cmp -s "$WORK/unpack_install/update_swu/apps/kobra-screen/app.json" "$PARENT_ROOT/kobra-screen/app.json" || fail "Paket içi kobra-screen/app.json güncel değil"
CURRENT_PKG_VER=$(tr -d ' \t\r\n' < "$PROJECT_ROOT/VERSION")
[ "$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/kobra-screen/app.json" | head -n 1)" = "$CURRENT_PKG_VER" ] || fail "Paket içi kobra-screen/app.json sürümü VERSION ($CURRENT_PKG_VER) ile eşleşmiyor"
[ "$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/ace-standalone/app.json" | head -n 1)" = "$CURRENT_PKG_VER" ] || fail "Paket içi ace-standalone/app.json sürümü VERSION ($CURRENT_PKG_VER) ile eşleşmiyor"
[ "$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/05-open-kobra-guard/app.json" | head -n 1)" = "$CURRENT_PKG_VER" ] || fail "Paket içi 05-open-kobra-guard/app.json sürümü VERSION ($CURRENT_PKG_VER) ile eşleşmiyor"
cmp -s "$WORK/unpack_install/update_swu/leviq3/leviq3.py" "$PARENT_ROOT/leviq3-klipper/leviq3.py" || fail "Paket içi leviq3.py güncel değil"
cmp -s "$WORK/unpack_install/update_swu/leviq3/leviq3.cfg" "$PARENT_ROOT/leviq3-klipper/leviq3.cfg" || fail "Paket içi leviq3.cfg güncel değil"
cmp -s "$WORK/unpack_install/update_swu/config/open_kobra_homing.cfg" "$PROJECT_ROOT/config/open_kobra_homing.cfg" || fail "Paket içi open_kobra_homing.cfg güncel değil"
pass "Kurulum ve kaldırma SWU paketleri K3 parolasıyla açıldı, içerikler doğrulandı"

# real tree (if packaged) must be byte-identical to the dist package
if [ -d "$WORK/unpack_install/update_swu/ace-driver" ]; then
    for RF in "$WORK/unpack_install/update_swu/ace-driver/klippy/extras/ace"/*.py; do
        [ -f "$RF" ] || continue
        cmp -s "$RF" "$PARENT_ROOT/ace-driver/klippy/extras/ace/$(basename "$RF")" || fail "dist paketindeki ace/$(basename "$RF") gerçek ace-driver ile aynı değil"
    done
    cmp -s "$WORK/unpack_install/update_swu/ace-driver/klippy/extras/temperature_ace.py" "$PARENT_ROOT/ace-driver/klippy/extras/temperature_ace.py" || fail "dist paketindeki temperature_ace.py gerçek ace-driver ile aynı değil"
else
    echo "  [INFO] dist paketinde ace-driver yok (ace-driver/ eksik ya da derleme uyarı verip atladı)"
fi

# Steps 4-19 exercise installer/rescue logic with a package built WITHOUT the (possibly still changing) real
# ace-driver tree; the ACE step has its own tests (20) that use a fake tree. dist/ keeps the normal build.
NOACE_DIST="$WORK/dist_noace"
OPEN_KOBRA_ACE_DRIVER_SRC="$WORK/no_such_ace_driver" OPEN_KOBRA_DIST_DIR="$NOACE_DIST" "$PROJECT_ROOT/build.sh" >/dev/null 2>&1 || fail "Sürücüsüz paket derlenemedi"
INSTALL_SWU="$NOACE_DIST/install/aGVscF9zb3Nf/update.swu"
UNINSTALL_SWU="$NOACE_DIST/uninstall/aGVscF9zb3Nf/update.swu"

cat > "$WORK/posix_scan.py" <<'PY'
import re
import sys

CHECKS = [
    ("bash shebang", re.compile(r"^#!.*\bbash\b")),
    ("[[ ... ]] bashism", re.compile(r"(^|[ \t;|&(])\[\[[ \t]")),
    ("<<< here-string", re.compile(r"<<<")),
    ("process substitution", re.compile(r"[<>]\(")),
    ("declare/typeset bashism", re.compile(r"(^|[ \t;|&])(declare|typeset)\b")),
    ("function keyword", re.compile(r"(^|[ \t;|&])function[ \t]+[A-Za-z_]")),
    ("source keyword (use '.')", re.compile(r"(^|[ \t;|&])source[ \t]+")),
    ("'==' inside [ ] (use '=')", re.compile(r"\[[^\]]*[ \t]==[ \t][^\]]*\]")),
    ("echo -e (use printf)", re.compile(r"(^|[ \t;|&])echo[ \t]+-[neE]*e")),
    ("brace sequence {a..b}", re.compile(r"\{[0-9a-zA-Z]+\.\.[0-9a-zA-Z]+\}")),
    ("GNU cp -a / --long", re.compile(r"(^|[ \t;|&])cp[ \t]+(-[a-zA-Z]*a|--)")),
    ("GNU sed -i / --long", re.compile(r"(^|[ \t;|&])sed[ \t]+(-[a-zA-Z]*i|--)")),
    ("GNU grep -P / -F / -x / --long", re.compile(r"(^|[ \t;|&])grep[ \t]+(-[a-zA-Z]*[PFx]|--)")),
    ("local keyword", re.compile(r"(^|[ \t;|&])local[ \t]+")),
    ("${var//pattern} (bash)", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*//")),
    ("${var:offset} substring (bash)", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:[0-9]")),
    ("${var^^} / ${var,,} (bash)", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(\^\^|,,)")),
    ("${!var} indirection (bash)", re.compile(r"\$\{![A-Za-z_]")),
    ("array syntax [@] / =( (bash)", re.compile(r"\[@\]|\[\*\]|[A-Za-z_][A-Za-z0-9_]*=\(")),
    ("var+= (bash)", re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+=")),
    ("&> / |& redirection (bash)", re.compile(r"(^|[ \t;|])&>|\|&")),
    ("$'...' quoting (bash)", re.compile(r"\$'")),
    ("(( )) arithmetic command (bash)", re.compile(r"(^|[;&|{ \t])\(\([^(]")),
    ("[ -v var ] (bash)", re.compile(r"\[[ \t]+-v[ \t]")),
    ("read -p/-a/-s/-n/-t (not POSIX)", re.compile(r"(^|[ \t;|&])read[ \t]+-[a-zA-Z]*[pasnt]")),
    ("$RANDOM/$SECONDS/$BASH*/$OSTYPE/$EUID/$PIPESTATUS (bash)", re.compile(r"\$\{?(RANDOM|SECONDS|BASH[A-Z_]*|OSTYPE|EUID|PIPESTATUS)\b")),
    ("let / select / pushd / popd / shopt / mapfile / readarray / disown", re.compile(r"(^|[ \t;|&])(let|select|pushd|popd|shopt|mapfile|readarray|disown)[ \t]")),
    ("brace expansion {a,b}", re.compile(r"[ \t=/]\{[A-Za-z0-9_.-]+,[A-Za-z0-9_.,-]+\}")),
    ("printf -v / %q (bash)", re.compile(r"printf[ \t]+-v[ \t]|printf[^|;&]*%q")),
    ("test ==/=~ (bash)", re.compile(r"[ \t]=~[ \t]")),
]

errors = []
for path in sys.argv[1:]:
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if not lines or not lines[0].startswith("#!/bin/sh"):
        errors.append(f"{path}:1: missing '#!/bin/sh' shebang")
    in_heredoc = False
    heredoc_end = ""
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if in_heredoc:
            if line.strip() == heredoc_end:
                in_heredoc = False
            continue
        m_hd = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if m_hd and m_hd.group(1) in ("PY", "EOF"):
            in_heredoc = True
            heredoc_end = m_hd.group(1)
            continue
        stripped = line.lstrip()
        if stripped.startswith("#") and lineno > 1:
            continue
        for label, pat in CHECKS:
            if pat.search(line):
                errors.append(f"{path}:{lineno}: {label}: {stripped}")

if errors:
    for err in errors:
        print(err, file=sys.stderr)
    sys.exit(1)
PY

echo "3. open-kobra kabuk betikleri sözdizimi, POSIX/BusyBox ve shellcheck denetimi..."
find "$PROJECT_ROOT" "$WORK/unpack_install/update_swu" "$WORK/unpack_uninstall/update_swu" -name '*.sh' -type f | sort > "$WORK/sh_files.list"
while IFS= read -r SH; do
    [ -z "$SH" ] && continue
    sh -n "$SH" || fail "sh -n başarısız: $SH"
    if command -v busybox >/dev/null 2>&1; then
        busybox sh -n "$SH" || fail "busybox sh -n başarısız: $SH"
    fi
done < "$WORK/sh_files.list"
pass "Tüm kabuk betikleri sh -n kontrolünden geçti"

python3 "$WORK/posix_scan.py" \
    "$PROJECT_ROOT/build.sh" \
    "$PROJECT_ROOT/test.sh" \
    "$PROJECT_ROOT/apps/05-open-kobra-guard/app.sh" \
    "$PROJECT_ROOT/rescue/rescue.sh" \
    "$PROJECT_ROOT/swu/install/update.sh" \
    "$PROJECT_ROOT/swu/uninstall/update.sh" \
    "$WORK/unpack_install/update_swu/apps/ace-standalone/app.sh" || fail "open-kobra POSIX/BusyBox taraması başarısız"
pass "open-kobra ve ace-standalone POSIX / BusyBox uyumluluk taraması geçti"

if command -v shellcheck >/dev/null 2>&1; then
    for SH in \
        "$PROJECT_ROOT/build.sh" \
        "$PROJECT_ROOT/test.sh" \
        "$PROJECT_ROOT/apps/05-open-kobra-guard/app.sh" \
        "$PROJECT_ROOT/rescue/rescue.sh" \
        "$WORK/unpack_install/update_swu/apps/ace-standalone/app.sh" \
        "$PROJECT_ROOT/swu/install/update.sh" \
        "$PROJECT_ROOT/swu/uninstall/update.sh"; do
        shellcheck "$SH" || fail "shellcheck başarısız: $SH"
    done
    pass "shellcheck kontrolü geçti"
else
    echo "  [INFO] shellcheck ve busybox kurulu değil; sh -n ve statik POSIX/BusyBox taraması çalıştırıldı"
fi

make_fake_root() {
    R="$1"
    WITH_PRINTER_CFG="${2:-1}"
    rm -rf "$R"
    mkdir -p \
        "$R/userdata/app/gk/config" \
        "$R/useremain/rinkhals/20250409_01/home/rinkhals/apps" \
        "$R/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras" \
        "$R/useremain/home/rinkhals/apps/40-moonraker" \
        "$R/useremain/home/rinkhals/printer_data/config" \
        "$R/tmp/rinkhals" \
        "$R/mnt/udisk/aGVscF9zb3Nf"

    printf '{"modelId": "20024"}\n' > "$R/userdata/app/gk/config/api.cfg"
    printf '# stock printer.cfg sentinel\n[printer]\nkinematics: cartesian\n' > "$R/userdata/app/gk/printer.cfg"
    printf '20250409_01\n' > "$R/useremain/rinkhals/.version"
    cat > "$R/useremain/rinkhals/20250409_01/tools.sh" <<'EOF'
#!/bin/sh
APP_STATUS_STARTED=started
APP_STATUS_STOPPED=stopped
report_status() {
    echo "Status: $1"
    echo "PIDs: $2"
    echo "Log: $3"
}
EOF
    printf '#!/bin/sh\n' > "$R/useremain/rinkhals/20250409_01/start.sh"
    ln -s "20250409_01" "$R/useremain/rinkhals/.current"

    printf '#!/bin/sh\n' > "$R/useremain/home/rinkhals/apps/vanilla-klipper/app.sh"
    printf '# klipper\n' > "$R/useremain/home/rinkhals/apps/vanilla-klipper/klippy/klippy.py"
    printf '# probe_ks1\n' > "$R/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/probe_ks1.py"
    printf '#!/bin/sh\n' > "$R/useremain/home/rinkhals/apps/40-moonraker/app.sh"

    cat > "$R/useremain/home/rinkhals/apps/vanilla-klipper/printer.klipper_K3.cfg" <<'EOF'
[printer]
kinematics: cartesian
max_velocity: 300
max_accel: 5000
max_z_velocity: 15

[extruder]
max_temp: 260
min_extrude_temp: 170

[heater_bed]
max_temp: 110

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW.
#*#
#*# [probe]
#*# z_offset = 1.250
EOF

    if [ "$WITH_PRINTER_CFG" = "1" ]; then
        cp "$R/useremain/home/rinkhals/apps/vanilla-klipper/printer.klipper_K3.cfg" \
           "$R/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg"
    fi
}

run_swu_on_fake_root() {
    SWU="$1"
    R="$2"
    rm -rf "$R/useremain/update_swu"
    unzip -q -P "$K3_SWU_PASSWORD" "$SWU" -d "$R/useremain"
    tar -xzf "$R/useremain/update_swu/setup.tar.gz" -C "$R/useremain/update_swu"
    chmod +x "$R/useremain/update_swu/update.sh"
    OPEN_KOBRA_ROOT="$R" "$R/useremain/update_swu/update.sh"
}

echo "4. Ön koşul kontrolleri (hatalı ve sayısal JSON modelId durumları)..."
BAD1="$WORK/bad_no_rinkhals"
make_fake_root "$BAD1"
rm -rf "$BAD1/useremain/rinkhals"
if run_swu_on_fake_root "$INSTALL_SWU" "$BAD1" >/dev/null 2>&1; then
    fail "Rinkhals yokken kurulum başarısız olmalıydı"
fi
[ ! -d "$BAD1/useremain/rinkhals" ] || fail "Rinkhals yokken /useremain/rinkhals oluşturulmamalıydı"
[ ! -d "$BAD1/useremain/bak" ] || fail "Ön koşul hatasında yedek dizini oluşturulmamalıydı"
[ -f "$BAD1/mnt/udisk/aGVscF9zb3Nf/open-kobra-install.log" ] || fail "USB günlüğü yazılmalıydı"

BAD2="$WORK/bad_wrong_model_str"
make_fake_root "$BAD2"
printf '{"modelId": "20025"}\n' > "$BAD2/userdata/app/gk/config/api.cfg"
cp "$BAD2/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_bad2.cfg"
if run_swu_on_fake_root "$INSTALL_SWU" "$BAD2" >/dev/null 2>&1; then
    fail "Yanlış string modelde kurulum başarısız olmalıydı"
fi
cmp -s "$BAD2/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_bad2.cfg" || fail "Yanlış modelde cfg değişmemeliydi"

BAD2_NUM="$WORK/bad_wrong_model_num"
make_fake_root "$BAD2_NUM"
printf '{"modelId": 20025}\n' > "$BAD2_NUM/userdata/app/gk/config/api.cfg"
if run_swu_on_fake_root "$INSTALL_SWU" "$BAD2_NUM" >/dev/null 2>&1; then
    fail "Yanlış sayısal modelId (20025) reddedilmeliydi"
fi

GOOD_NUM="$WORK/good_num_model"
make_fake_root "$GOOD_NUM"
printf '{"modelId": 20024}\n' > "$GOOD_NUM/userdata/app/gk/config/api.cfg"
run_swu_on_fake_root "$INSTALL_SWU" "$GOOD_NUM" >/dev/null || fail "Tırnaksız sayısal modelId (20024) kabul edilmeliydi"

BAD3="$WORK/bad_no_vklipper"
make_fake_root "$BAD3"
rm -rf "$BAD3/useremain/home/rinkhals/apps/vanilla-klipper"
if run_swu_on_fake_root "$INSTALL_SWU" "$BAD3" >/dev/null 2>&1; then
    fail "vanilla-klipper yokken kurulum başarısız olmalıydı"
fi

BAD4="$WORK/bad_no_moonraker"
make_fake_root "$BAD4"
rm -rf "$BAD4/useremain/home/rinkhals/apps/40-moonraker"
if run_swu_on_fake_root "$INSTALL_SWU" "$BAD4" >/dev/null 2>&1; then
    fail "Moonraker yokken kurulum başarısız olmalıydı"
fi
pass "Tüm ön koşul reddetme ve tırnaksız modelId=20024 testleri geçti"

echo "5. Hata anında geri alma (rollback) testi..."
RB="$WORK/rollback_root"
make_fake_root "$RB"
cp "$RB/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_rb.cfg"
if OPEN_KOBRA_FAIL_AT=config_edit run_swu_on_fake_root "$INSTALL_SWU" "$RB" >/dev/null 2>&1; then
    fail "Simüle edilen config_edit hatası başarısız olmalıydı"
fi
for ABSENT in \
    "$RB/useremain/home/rinkhals/apps/kobra-screen" \
    "$RB/useremain/home/rinkhals/apps/ace-standalone" \
    "$RB/useremain/home/rinkhals/apps/05-open-kobra-guard" \
    "$RB/useremain/home/rinkhals/open-kobra" \
    "$RB/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" \
    "$RB/useremain/home/rinkhals/printer_data/config/leviq3.cfg" \
    "$RB/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg" \
    "$RB/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg" \
    "$RB/useremain/home/rinkhals/.open-kobra-version" \
    "$RB/useremain/bak/open-kobra-baseline"; do
    [ ! -e "$ABSENT" ] || fail "Rollback temizlemedi: $ABSENT"
done
cmp -s "$RB/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_rb.cfg" || fail "Rollback printer.klipper.cfg'yi geri yüklemedi"
pass "Yarıda hata durumunda tam geri alma doğrulandı"

echo "6. Şablondan oluşturma (CFG_FROM_TEMPLATE=1) ve yanlış printer.cfg'ye dokunmama testi..."
TPL="$WORK/template_root"
make_fake_root "$TPL" 0
printf '# DO NOT TOUCH printer.cfg\n[printer]\nkinematics: corexy\n' > "$TPL/useremain/home/rinkhals/printer_data/config/printer.cfg"
cp "$TPL/useremain/home/rinkhals/printer_data/config/printer.cfg" "$WORK/orig_printer_cfg"

run_swu_on_fake_root "$INSTALL_SWU" "$TPL" >/dev/null
cmp -s "$TPL/useremain/home/rinkhals/printer_data/config/printer.cfg" "$WORK/orig_printer_cfg" || fail "vanilla-klipper okumadığı printer.cfg dosyasını yamalamamalıydı"
[ -s "$TPL/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" ] || fail "Şablondan printer.klipper.cfg oluşturulmalıydı"
has_lit "$TPL/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include leviq3.cfg]" || fail "Şablondan oluşturulan printer.klipper.cfg include içermiyor"
has_lit "$TPL/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include open_kobra_homing.cfg]" || fail "Şablondan oluşturulan printer.klipper.cfg homing include içermiyor"

# User adds calibration after install; uninstall MUST preserve printer.klipper.cfg and user additions!
printf '\n[gcode_macro USER_AFTER_TPL]\ngcode: M117 OK\n' >> "$TPL/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg"
run_swu_on_fake_root "$UNINSTALL_SWU" "$TPL" >/dev/null
[ -f "$TPL/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" ] || fail "Şablondan oluşturulan printer.klipper.cfg kaldırmada silinmemeliydi!"
has_lit "$TPL/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "USER_AFTER_TPL" || fail "Kaldırma sonrası kullanıcı makrosu silindi!"
if has_lit "$TPL/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "# open-kobra/begin"; then
    fail "Kaldırma sonrasında open-kobra bloğu kalmış!"
fi
cmp -s "$TPL/useremain/home/rinkhals/printer_data/config/printer.cfg" "$WORK/orig_printer_cfg" || fail "Kaldırma sonrası printer.cfg değişmiş!"
pass "Şablondan oluşturma, yanlış printer.cfg koruması ve kaldırmada printer.klipper.cfg korunması geçti"

echo "7. Uçtan uca yaşam döngüsü (SAVE_CONFIG koruma, config.json, budama, leviq3 bekçisi)..."
E2E="$WORK/e2e_root"
make_fake_root "$E2E"
cp "$E2E/userdata/app/gk/printer.cfg" "$WORK/orig_stock_cfg"

# 7a. First install
run_swu_on_fake_root "$INSTALL_SWU" "$E2E" >/dev/null

[ -x "$E2E/useremain/home/rinkhals/apps/kobra-screen/app.sh" ] || fail "kobra-screen app.sh eksik"
[ -x "$E2E/useremain/home/rinkhals/apps/ace-standalone/app.sh" ] || fail "ace-standalone app.sh eksik"
[ -x "$E2E/useremain/home/rinkhals/apps/05-open-kobra-guard/app.sh" ] || fail "05-open-kobra-guard app.sh eksik"
[ ! -e "$E2E/useremain/home/rinkhals/apps/kobra-screen/.enabled" ] || fail "kobra-screen otomatik etkinleştirilmemeli"
[ ! -e "$E2E/useremain/home/rinkhals/apps/ace-standalone/.enabled" ] || fail "ace-standalone otomatik etkinleştirilmemeli"
[ -e "$E2E/useremain/home/rinkhals/apps/05-open-kobra-guard/.enabled" ] || fail "05-open-kobra-guard açılış bekçisi olarak etkin kurulmalıydı"
[ ! -e "$E2E/useremain/home/rinkhals/apps/kobra-screen/fonts" ] || fail "Kurulan kobra-screen fonts dizini içermemeli"
[ -f "$E2E/useremain/home/rinkhals/open-kobra/leviq3.py" ] || fail "Kalıcı open-kobra/leviq3.py eksik"
[ -f "$E2E/useremain/home/rinkhals/open-kobra/leviq3.cfg" ] || fail "Kalıcı open-kobra/leviq3.cfg eksik"
[ -f "$E2E/useremain/home/rinkhals/open-kobra/open_kobra_homing.cfg" ] || fail "Kalıcı open-kobra/open_kobra_homing.cfg eksik"
[ -f "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg" ] || fail "Kurulan open_kobra_homing.cfg eksik"
[ -x "$E2E/useremain/home/rinkhals/open-kobra/restore-leviq3.sh" ] || fail "restore-leviq3.sh eksik"
[ ! -e "$E2E/useremain/update_swu" ] || fail "update_swu temizlenmeliydi"

# Verify Rinkhals sort -uV places 05-open-kobra-guard strictly BEFORE vanilla-klipper
FIRST_POS=$(printf '25-mainsail\n40-moonraker\nace-standalone\nkobra-screen\nvanilla-klipper\n05-open-kobra-guard\n' | sort -uV | awk '/05-open-kobra-guard/{print NR}')
VK_POS=$(printf '25-mainsail\n40-moonraker\nace-standalone\nkobra-screen\nvanilla-klipper\n05-open-kobra-guard\n' | sort -uV | awk '/vanilla-klipper/{print NR}')
[ "$FIRST_POS" -lt "$VK_POS" ] || fail "sort -uV sırası 05-open-kobra-guard'ı vanilla-klipper önüne koymadı"

# Verify guard app.sh version / status / stop / self-healing / fail-safe disable
GUARD_SH="$E2E/useremain/home/rinkhals/apps/05-open-kobra-guard/app.sh"
[ "$(OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" version)" = "$(tr -d ' \t\r\n' < "$PROJECT_ROOT/VERSION")" ] || fail "Guard version çıktısı beklenenden farklı"
OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" status > "$WORK/guard_status.txt"
has_lit "$WORK/guard_status.txt" "Status: stopped" || fail "Guard status raporu hatalı"

# Scenario 7a-1: vanilla-klipper update wipes leviq3.py -> guard start restores it before Klipper boots
rm -f "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py"
OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" start
cmp -s "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" "$PARENT_ROOT/leviq3-klipper/leviq3.py" || fail "Guard leviq3.py'yi geri yüklemedi"
has_line "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include leviq3.cfg]" || fail "leviq3.cfg include satırı aktif kalmalıydı"

# Scenario 7a-1b: corrupted/half-written non-empty leviq3.py -> guard detects MD5 mismatch and restores atomically (B-04)
printf '# half-written leviq3\n' > "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py"
OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" start
cmp -s "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" "$PARENT_ROOT/leviq3-klipper/leviq3.py" || fail "Guard bozuk/yarım leviq3.py'yi MD5 ile yakalayıp geri yüklemedi"
[ ! -e "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py.openkobra.guard.tmp" ] || fail "Guard geçici dosyası kaldı"

# Scenario 7a-1c: missing or corrupt open_kobra_homing.cfg -> guard restores it atomically (H-04)
rm -f "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg"
OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" start
cmp -s "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg" "$PROJECT_ROOT/config/open_kobra_homing.cfg" || fail "Guard silinen open_kobra_homing.cfg'yi geri yüklemedi"
has_line "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include open_kobra_homing.cfg]" || fail "open_kobra_homing.cfg include satırı aktif kalmalıydı"

# Scenario 7a-2: target corrupt/missing and copy fails -> guard disables [include leviq3.cfg], [include open_kobra_homing.cfg], AND [leviq3] in open_kobra_limits.cfg (A2-01, B-04, H-04)
cat > "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg" <<'EOF'
[extruder]
max_temp: 295

[leviq3]
max_auto_travel: 3.0
noise_diff: 3000

[ace]
max_temp: 55
EOF
printf '# half-written leviq3\n' > "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py"
rm -f "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg"
OPEN_KOBRA_GUARD_FAIL_COPY=1 OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" start
if awk '/^[[:space:]]*\[include[[:space:]]+leviq3\.cfg\][[:space:]]*$/ { f=1; exit } END { exit !f }' "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg"; then
    fail "Kopyalama başarısızlığında [include leviq3.cfg] devre dışı bırakılmalıydı!"
fi
if awk '/^[[:space:]]*\[include[[:space:]]+open_kobra_homing\.cfg\][[:space:]]*$/ { f=1; exit } END { exit !f }' "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg"; then
    fail "Kopyalama başarısızlığında [include open_kobra_homing.cfg] devre dışı bırakılmalıydı (H-04)!"
fi
has_lit "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "open-kobra/guard-disabled" || fail "Devre dışı işareti yazılmadı"
has_lit "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg" "open-kobra/guard-disabled-leviq3" || fail "open_kobra_limits.cfg içindeki [leviq3] kapatılmadı (A2-01)"
[ -f "$E2E/useremain/home/rinkhals/open-kobra/printer.klipper.cfg.guard.bak" ] || fail "Guard düzenleme öncesi .guard.bak almadı"
python3 - "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg" "${KLIPPER_SRC:-$PARENT_ROOT/refs/klipper}/klippy" "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" <<'PY'
import configparser, sys
cp = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
cp.read(sys.argv[1], encoding="utf-8")
assert cp.has_section("extruder") and cp.getint("extruder", "max_temp") == 295
assert cp.has_section("ace") and cp.getint("ace", "max_temp") == 55
assert not cp.has_section("leviq3"), "[leviq3] still active in open_kobra_limits.cfg!"
import os
if os.path.isdir(sys.argv[2]):  # the Klipper parser check needs a Klipper checkout
    sys.path.insert(0, sys.argv[2])
    import configfile
    cfgrdr = configfile.ConfigFileReader()
    fc = cfgrdr.build_fileconfig_with_includes(open(sys.argv[3], encoding="utf-8").read(), sys.argv[3])
    assert not fc.has_section("leviq3")
    assert not fc.has_section("gcode_macro G28")
PY

# Scenario 7a-3: guard restores leviq3.py & open_kobra_homing.cfg on next boot and re-enables includes AND [leviq3] limits
OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" start
cmp -s "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" "$PARENT_ROOT/leviq3-klipper/leviq3.py" || fail "Guard iyileştirmede leviq3.py geri gelmedi"
cmp -s "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg" "$PROJECT_ROOT/config/open_kobra_homing.cfg" || fail "Guard iyileştirmede open_kobra_homing.cfg geri gelmedi"
has_line "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include leviq3.cfg]" || fail "Guard [include leviq3.cfg] satırını yeniden etkinleştirmeliydi"
has_line "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include open_kobra_homing.cfg]" || fail "Guard [include open_kobra_homing.cfg] satırını yeniden etkinleştirmeliydi (H-04)"
python3 - "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg" <<'PY'
import configparser, sys
cp = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
cp.read(sys.argv[1], encoding="utf-8")
assert cp.has_section("leviq3"), "[leviq3] was not re-enabled!"
assert cp.getfloat("leviq3", "max_auto_travel") == 3.0
assert cp.getint("leviq3", "noise_diff") == 3000
PY

# Scenario 7a-4: guard internal timeout/failure always exits 0, logs, and runs _guard_main ONLY ONCE (B-03, Y2-01)
OPEN_KOBRA_GUARD_TRACE_FILE="$WORK/guard_trace.log" \
    OPEN_KOBRA_GUARD_TIMEOUT=1 OPEN_KOBRA_GUARD_TEST_SLEEP=3 OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" start || fail "Bekçi zaman aşımında bile 0 dönmeliydi"
has_lit "$E2E/tmp/rinkhals/app-05-open-kobra-guard.log" "zaman asimi" || fail "Bekçi zaman aşımı günlüğe yazılmadı"
TRACE_CNT=$(wc -l < "$WORK/guard_trace.log" | tr -d ' ')
[ "$TRACE_CNT" = "1" ] || fail "Zaman aşımında _guard_main tam 1 kez çalışmalıydı (gelen: $TRACE_CNT)"

# Also simulate BusyBox timeout (accepting both -t SECS and SECS) returning 143 on _guard_main -> must run ONLY ONCE!
mkdir -p "$WORK/bb_timeout_bin"
cat > "$WORK/bb_timeout_bin/timeout" <<EOF
#!/bin/sh
if [ "\$1" = "-t" ]; then
    shift
fi
shift
if [ "\$1" = "sh" ] && [ "\$2" = "-c" ] && [ "\$3" = ":" ]; then
    exit 0
fi
printf 'call:%s\n' "\$*" >> "$WORK/bb_timeout_calls.log"
exit 143
EOF
chmod +x "$WORK/bb_timeout_bin/timeout"
PATH="$WORK/bb_timeout_bin:$PATH" OPEN_KOBRA_GUARD_TIMEOUT=1 OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" start || fail "BusyBox timeout simülasyonunda 0 dönmeliydi"
BB_CALLS=$(wc -l < "$WORK/bb_timeout_calls.log" | tr -d ' ')
[ "$BB_CALLS" = "1" ] || fail "BusyBox timeout zaman aşımında _guard_main ikinci kez çağrıldı ($BB_CALLS)"
OPEN_KOBRA_ROOT="$E2E" "$GUARD_SH" stop

# Verify open-kobra scripts never invoke tools.sh disable_app/enable_app (Y2-02)
if awk 'index($0, "disable_app") > 0 || index($0, "enable_app") > 0 { f=1; exit } END { exit !f }' \
    "$PROJECT_ROOT/apps/05-open-kobra-guard/app.sh" \
    "$PROJECT_ROOT/swu/install/update.sh" \
    "$PROJECT_ROOT/swu/uninstall/update.sh"; then
    fail "open-kobra betikleri tools.sh disable_app/enable_app çağırmamalı (Y2-02)"
fi

# Verify manual restore-leviq3.sh syntax, POSIX compliance, and behavior
sh -n "$E2E/useremain/home/rinkhals/open-kobra/restore-leviq3.sh" || fail "restore-leviq3.sh sh -n başarısız"
python3 "$WORK/posix_scan.py" "$E2E/useremain/home/rinkhals/open-kobra/restore-leviq3.sh" || fail "restore-leviq3.sh POSIX taraması başarısız"
rm -f "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py"
OPEN_KOBRA_ROOT="$E2E" "$E2E/useremain/home/rinkhals/open-kobra/restore-leviq3.sh" >/dev/null
cmp -s "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" "$PARENT_ROOT/leviq3-klipper/leviq3.py" || fail "restore-leviq3.sh çalışmadı"

# 7b. Simulate real user work after install + 3 more installs (test backup pruning, config.json, sidecars, SAVE_CONFIG)
cat > "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" <<'EOF'
[printer]
kinematics: cartesian
max_velocity: 300
max_accel: 5000
max_z_velocity: 15

[extruder]
max_temp: 260
min_extrude_temp: 170

[heater_bed]
max_temp: 110

[gcode_macro USER_POST_INSTALL]
gcode:
    G28

# open-kobra/begin
[include leviq3.cfg]
[include open_kobra_limits*.cfg]
# open-kobra/end

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW.
#*#
#*# [probe]
#*# z_offset = 0.875
#*#
#*# [bed_mesh default]
#*# version = 1
EOF

printf '[extruder]\nmax_temp: 295\n' > "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg"
printf '{"max_temp": 52}\n' > "$E2E/useremain/home/rinkhals/apps/ace-standalone/config.json"
: > "$E2E/useremain/home/rinkhals/apps/ace-standalone.enabled"
printf '{"z_offset": 0.875}\n' > "$E2E/useremain/home/rinkhals/printer_data/config/leviq3_state.json"

# Reinstall once while leviq3.cfg and open_kobra_homing.cfg are unedited -> no .new created
run_swu_on_fake_root "$INSTALL_SWU" "$E2E" >/dev/null
[ ! -e "$E2E/useremain/home/rinkhals/printer_data/config/leviq3.cfg.new" ] || fail "Düzenlenmemiş leviq3.cfg için .new oluşmamalıydı"
[ ! -e "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg.new" ] || fail "Düzenlenmemiş open_kobra_homing.cfg için .new oluşmamalıydı"

# User customizes leviq3.cfg & open_kobra_homing.cfg -> subsequent reinstall MUST preserve them and write .new!
printf '[leviq3]\nbed_temp: 75\n' > "$E2E/useremain/home/rinkhals/printer_data/config/leviq3.cfg"
printf '[stepper_x]\nhoming_retract_dist: 0\n[gcode_macro G28]\nrename_existing: G28.1\nvariable_backoff_mm: 15.0\ngcode:\n    G28.1\n' > "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg"
run_swu_on_fake_root "$INSTALL_SWU" "$E2E" >/dev/null
run_swu_on_fake_root "$INSTALL_SWU" "$E2E" >/dev/null

has_lit "$E2E/useremain/home/rinkhals/printer_data/config/leviq3.cfg" "bed_temp: 75" || fail "Kullanıcının düzenlediği leviq3.cfg ezildi!"
[ -f "$E2E/useremain/home/rinkhals/printer_data/config/leviq3.cfg.new" ] || fail "Kullanıcı leviq3.cfg'si korunduğunda leviq3.cfg.new bırakılmalıydı!"
has_lit "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg" "variable_backoff_mm: 15.0" || fail "Kullanıcının düzenlediği open_kobra_homing.cfg ezildi!"
[ -f "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg.new" ] || fail "Kullanıcı open_kobra_homing.cfg'si korunduğunda open_kobra_homing.cfg.new bırakılmalıydı!"
cp "$WORK/orig_leviq3_placeholder" "$WORK/orig_leviq3_placeholder" 2>/dev/null || true
# Restore valid default leviq3.cfg & open_kobra_homing.cfg for Python INI check
cp "$PARENT_ROOT/leviq3-klipper/leviq3.cfg" "$E2E/useremain/home/rinkhals/printer_data/config/leviq3.cfg"
cp "$PROJECT_ROOT/config/open_kobra_homing.cfg" "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg"

[ -f "$E2E/useremain/home/rinkhals/apps/ace-standalone/config.json" ] || fail "Yeniden kurulumda ace-standalone/config.json silindi!"
has_lit "$E2E/useremain/home/rinkhals/apps/ace-standalone/config.json" '"max_temp": 52' || fail "ace-standalone/config.json içeriği bozuldu"
[ -f "$E2E/useremain/home/rinkhals/apps/ace-standalone.enabled" ] || fail "Yan işaretçi ace-standalone.enabled korunmalıydı"
has_lit "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg" "max_temp: 295" || fail "open_kobra_limits.cfg korunmadı"
has_lit "$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "z_offset = 0.875" || fail "Yeniden kurulum SAVE_CONFIG z_offset'i bozdu"

BAK_COUNT=$(find "$E2E/useremain/bak" -maxdepth 1 -type d -name 'open-kobra-[0-9]*' | wc -l)
[ "$BAK_COUNT" -le 2 ] || fail "Tarihli yedek sayısı 2 ile budanmalıydı (mevcut: $BAK_COUNT)"
[ -d "$E2E/useremain/bak/open-kobra-baseline" ] || fail "open-kobra-baseline silinmemeliydi"

# Validate Klipper INI merge before SAVE_CONFIG
python3 - "$E2E/useremain/home/rinkhals/printer_data/config" <<'PY'
import configparser, os, sys
cfg_dir = sys.argv[1]
text = open(os.path.join(cfg_dir, "printer.klipper.cfg"), encoding="utf-8").read()
assert text.count("[include leviq3.cfg]") == 1
assert text.count("[include open_kobra_homing.cfg]") == 1
assert text.count("[include open_kobra_limits*.cfg]") == 1
assert text.index("[include leviq3.cfg]") < text.index("[include open_kobra_homing.cfg]") < text.index("[include open_kobra_limits*.cfg]") < text.index("#*# <---------------------- SAVE_CONFIG ---------------------->")
PY

# 7c. Uninstall MUST preserve USER_POST_INSTALL, z_offset = 0.875, bed_mesh default, and remove all open-kobra files
run_swu_on_fake_root "$UNINSTALL_SWU" "$E2E" >/dev/null

for GONE in \
    "$E2E/useremain/home/rinkhals/apps/kobra-screen" \
    "$E2E/useremain/home/rinkhals/apps/ace-standalone" \
    "$E2E/useremain/home/rinkhals/apps/ace-standalone.enabled" \
    "$E2E/useremain/home/rinkhals/apps/05-open-kobra-guard" \
    "$E2E/useremain/home/rinkhals/open-kobra" \
    "$E2E/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" \
    "$E2E/useremain/home/rinkhals/printer_data/config/leviq3.cfg" \
    "$E2E/useremain/home/rinkhals/printer_data/config/leviq3.cfg.new" \
    "$E2E/useremain/home/rinkhals/printer_data/config/leviq3_state.json" \
    "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg" \
    "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg.new" \
    "$E2E/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg" \
    "$E2E/useremain/home/rinkhals/.open-kobra-version" \
    "$E2E/useremain/bak/open-kobra-baseline"; do
    [ ! -e "$GONE" ] || fail "Kaldırma sonrası temizlenmedi: $GONE"
done

CFG_AFTER="$E2E/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg"
[ -f "$CFG_AFTER" ] || fail "Kaldırma sonrasında printer.klipper.cfg silinmemeli!"
if has_lit "$CFG_AFTER" "# open-kobra/begin" || has_lit "$CFG_AFTER" "[include leviq3.cfg]" || has_lit "$CFG_AFTER" "[include open_kobra_homing.cfg]"; then
    fail "Kaldırma sonrasında open-kobra include bloğu kalmış!"
fi
has_lit "$CFG_AFTER" "[gcode_macro USER_POST_INSTALL]" || fail "Kaldırma sonrasında kullanıcı makrosu silindi!"
has_lit "$CFG_AFTER" "z_offset = 0.875" || fail "Kaldırma sonrasında SAVE_CONFIG z_offset silindi (eski baseline üzerine yazılmış)!"
has_lit "$CFG_AFTER" "[bed_mesh default]" || fail "Kaldırma sonrasında SAVE_CONFIG bed_mesh silindi!"
cmp -s "$E2E/userdata/app/gk/printer.cfg" "$WORK/orig_stock_cfg" || fail "Stok /userdata/app/gk/printer.cfg değiştirilmiş!"
pass "Yaşam döngüsü, SAVE_CONFIG koruma, bekçi ve budama testleri geçti"

echo "8. Elektrik kesintisi (her adımda SIGKILL sonrası yeniden kurulum) benzetimi..."
for STAGE in \
    after_backup \
    after_kobra_screen \
    after_ace_standalone \
    after_guard \
    after_leviq3_py \
    after_leviq3_cfg \
    after_limits_cfg \
    after_homing_cfg \
    before_cfg_mv \
    after_cfg_mv; do
    PWR="$WORK/power_${STAGE}"
    make_fake_root "$PWR"
    mkdir -p "$PWR/useremain/home/rinkhals/apps/ace-standalone"
    printf '{"max_temp": 48}\n' > "$PWR/useremain/home/rinkhals/apps/ace-standalone/config.json"

    # Trigger sudden mid-run death (kill -9 -> no rollback)
    if OPEN_KOBRA_CRASH_AFTER="$STAGE" run_swu_on_fake_root "$INSTALL_SWU" "$PWR" >/dev/null 2>&1; then
        fail "Kesinti benzetimi ($STAGE) başarısız dönmeliydi"
    fi

    # Re-run installation cleanly after power returns
    run_swu_on_fake_root "$INSTALL_SWU" "$PWR" >/dev/null || fail "Kesinti sonrası ($STAGE) yeniden kurulum başarısız"

    [ -f "$PWR/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" ] || fail "$STAGE: leviq3.py eksik"
    [ -x "$PWR/useremain/home/rinkhals/apps/05-open-kobra-guard/app.sh" ] || fail "$STAGE: guard eksik"
    has_lit "$PWR/useremain/home/rinkhals/apps/ace-standalone/config.json" '"max_temp": 48' || fail "$STAGE: ace-standalone/config.json kayboldu"
    INC_N=$(count_lit "$PWR/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include leviq3.cfg]")
    [ "$INC_N" = "1" ] || fail "$STAGE: kesinti sonrası include çoğaldı ($INC_N)"
    HINC_N=$(count_lit "$PWR/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include open_kobra_homing.cfg]")
    [ "$HINC_N" = "1" ] || fail "$STAGE: kesinti sonrası homing include çoğaldı ($HINC_N)"
    LEFT_TMP=$(find "$PWR" -name '*.openkobra.*' | head -n 1 || true)
    [ -z "$LEFT_TMP" ] || fail "$STAGE: geçici dosya kalmış: $LEFT_TMP"

    # Also verify uninstall works cleanly even when first run crashed midway!
    run_swu_on_fake_root "$UNINSTALL_SWU" "$PWR" >/dev/null || fail "$STAGE: kesinti sonrası uninstall başarısız"
    [ ! -e "$PWR/useremain/home/rinkhals/apps/05-open-kobra-guard" ] || fail "$STAGE: uninstall sonrası guard kaldı"
    [ -f "$PWR/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" ] || fail "$STAGE: uninstall sonrası printer.klipper.cfg silindi"
done
pass "Tüm elektrik kesintisi (9 ara aşama) ve kurtarma testleri geçti"

echo "9. Sinyal/trap geri alması ve geri alma hatası yakalama (exit 2) testleri..."
SIG_ROOT="$WORK/sig_root"
make_fake_root "$SIG_ROOT"
rm -rf "$SIG_ROOT/useremain/update_swu"
unzip -q -P "$K3_SWU_PASSWORD" "$INSTALL_SWU" -d "$SIG_ROOT/useremain"
tar -xzf "$SIG_ROOT/useremain/update_swu/setup.tar.gz" -C "$SIG_ROOT/useremain/update_swu"
chmod +x "$SIG_ROOT/useremain/update_swu/update.sh"
OPEN_KOBRA_ROOT="$SIG_ROOT" OPEN_KOBRA_PAUSE_AFTER=after_guard OPEN_KOBRA_READY_FILE="$WORK/sig.ready" \
    "$SIG_ROOT/useremain/update_swu/update.sh" >/dev/null 2>&1 &
SIG_PID=$!
WAIT_T=0
while [ ! -f "$WORK/sig.ready" ] && [ "$WAIT_T" -lt 50 ]; do
    sleep 0.1 2>/dev/null || sleep 1
    WAIT_T=$((WAIT_T + 1))
done
[ -f "$WORK/sig.ready" ] || fail "Sinyal testi senkronizasyon dosyası oluşmadı"
kill -TERM "$SIG_PID" 2>/dev/null || true
SIG_RC=0
wait "$SIG_PID" || SIG_RC=$?
[ "$SIG_RC" -ne 0 ] || fail "Sinyal kesintisinde kurulum 0 dönmemeliydi"
[ ! -e "$SIG_ROOT/useremain/home/rinkhals/apps/kobra-screen" ] || fail "Sinyal geri alması kobra-screen'i temizlemedi"
[ ! -e "$SIG_ROOT/useremain/home/rinkhals/apps/05-open-kobra-guard" ] || fail "Sinyal geri alması guard'ı temizlemedi"
[ ! -e "$SIG_ROOT/useremain/bak/open-kobra-baseline" ] || fail "Sinyal geri alması baseline'ı temizlemedi"
has_lit "$SIG_ROOT/mnt/udisk/aGVscF9zb3Nf/open-kobra-install.log" "SİNYAL (TERM)" || fail "Sinyal günlüğe yazılmadı"

RB_FAIL_ROOT="$WORK/rb_fail_root"
make_fake_root "$RB_FAIL_ROOT"
mkdir -p "$RB_FAIL_ROOT/useremain/home/rinkhals/apps/ace-standalone"
printf 'keep\n' > "$RB_FAIL_ROOT/useremain/home/rinkhals/apps/ace-standalone/sentinel.txt"
RB_RC=0
OPEN_KOBRA_FAIL_AT=config_edit OPEN_KOBRA_BREAK_ROLLBACK=1 run_swu_on_fake_root "$INSTALL_SWU" "$RB_FAIL_ROOT" >/dev/null 2>&1 || RB_RC=$?
[ "$RB_RC" = "2" ] || fail "Geri alma hatasında çıkış kodu 2 olmalıydı (gelen: $RB_RC)"
has_lit "$RB_FAIL_ROOT/mnt/udisk/aGVscF9zb3Nf/open-kobra-install.log" "GERİ ALMA BAŞARISIZ" || fail "Geri alma hatası günlüğe yazılmadı"
pass "Sinyal/trap geri alması ve yutulmayan geri alma hatası testleri geçti"

echo "10. Eşleşmemiş '# open-kobra/begin' koruması (install & uninstall) testi..."
UNB="$WORK/unbalanced_root"
make_fake_root "$UNB"
cat > "$UNB/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" <<'EOF'
[printer]
kinematics: cartesian

# open-kobra/begin
[include leviq3.cfg]

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# [probe]
#*# z_offset = 1.111
EOF
cp "$UNB/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_unb.cfg"

if run_swu_on_fake_root "$INSTALL_SWU" "$UNB" >/dev/null 2>&1; then
    fail "Eşleşmemiş begin bloğu varken kurulum başarısız olmalıydı"
fi
cmp -s "$UNB/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_unb.cfg" || fail "Eşleşmemiş begin durumunda install SAVE_CONFIG'i bozdu!"

if run_swu_on_fake_root "$UNINSTALL_SWU" "$UNB" >/dev/null 2>&1; then
    fail "Eşleşmemiş begin bloğu varken kaldırma başarısız olmalıydı"
fi
cmp -s "$UNB/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_unb.cfg" || fail "Eşleşmemiş begin durumunda uninstall SAVE_CONFIG'i bozdu!"
pass "Eşleşmemiş '# open-kobra/begin' veri kaybı koruması geçti"

echo "11. Payload MANIFEST.md5 bozulması ve geçersiz INI doğrulama testi..."
CORRUPT_ROOT="$WORK/corrupt_payload_root"
make_fake_root "$CORRUPT_ROOT"
rm -rf "$CORRUPT_ROOT/useremain/update_swu"
unzip -q -P "$K3_SWU_PASSWORD" "$INSTALL_SWU" -d "$CORRUPT_ROOT/useremain"
tar -xzf "$CORRUPT_ROOT/useremain/update_swu/setup.tar.gz" -C "$CORRUPT_ROOT/useremain/update_swu"
chmod +x "$CORRUPT_ROOT/useremain/update_swu/update.sh"
printf '\n# corrupted\n' >> "$CORRUPT_ROOT/useremain/update_swu/leviq3/leviq3.py"
if OPEN_KOBRA_ROOT="$CORRUPT_ROOT" "$CORRUPT_ROOT/useremain/update_swu/update.sh" >/dev/null 2>&1; then
    fail "MANIFEST.md5 uyuşmazlığında kurulum reddedilmeliydi"
fi
[ ! -e "$CORRUPT_ROOT/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py" ] || fail "Bozuk payload hedefe yazılmamalıydı"

BAD_INI="$WORK/bad_ini_root"
make_fake_root "$BAD_INI"
printf '[broken_section_no_close_bracket\nmax_temp: 1\n' > "$BAD_INI/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg"
cp "$BAD_INI/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_bad_ini.cfg"
if run_swu_on_fake_root "$INSTALL_SWU" "$BAD_INI" >/dev/null 2>&1; then
    fail "Geçersiz INI doğrulamasında kurulum başarısız olmalıydı"
fi
cmp -s "$BAD_INI/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_bad_ini.cfg" || fail "INI hatasında rollback config'i geri yüklemedi"
pass "Payload MANIFEST.md5 ve config INI doğrulama testleri geçti"

echo "12. Durdurulan uygulamaların başarıda ve geri almada yeniden başlatılması testi..."
MOCK_APP_ROOT="$WORK/mock_running_app_root"
make_fake_root "$MOCK_APP_ROOT"
mkdir -p "$MOCK_APP_ROOT/useremain/home/rinkhals/apps/ace-standalone"
: > "$MOCK_APP_ROOT/useremain/home/rinkhals/apps/ace-standalone/.enabled"
: > "$MOCK_APP_ROOT/tmp/ace_is_running"
cat > "$MOCK_APP_ROOT/useremain/home/rinkhals/apps/ace-standalone/app.sh" <<'EOF'
#!/bin/sh
R="${OPEN_KOBRA_ROOT:-}"
case "${1:-}" in
    status)
        if [ -f "$R/tmp/ace_is_running" ]; then
            printf 'Status: started\nPIDs: 9999\nLog: %s/tmp/ace.log\n' "$R"
        else
            printf 'Status: stopped\nPIDs: \nLog: %s/tmp/ace.log\n' "$R"
        fi
        ;;
    stop)
        printf 'stop\n' >> "$R/tmp/ace_events.log"
        rm -f "$R/tmp/ace_is_running"
        ;;
    start)
        printf 'start\n' >> "$R/tmp/ace_events.log"
        : > "$R/tmp/ace_is_running"
        ;;
esac
EOF
chmod +x "$MOCK_APP_ROOT/useremain/home/rinkhals/apps/ace-standalone/app.sh"

# Rollback path: stop old running app, fail on config_edit, rollback restores old app.sh and calls start!
if OPEN_KOBRA_FAIL_AT=config_edit run_swu_on_fake_root "$INSTALL_SWU" "$MOCK_APP_ROOT" >/dev/null 2>&1; then
    fail "Mock running rollback başarısız olmalıydı"
fi
[ -f "$MOCK_APP_ROOT/tmp/ace_is_running" ] || fail "Geri alma sonrası durdurulan uygulama yeniden başlatılmadı"
has_lit "$MOCK_APP_ROOT/tmp/ace_events.log" "stop" || fail "Kurulum çalışan uygulamayı durdurmadı"
has_lit "$MOCK_APP_ROOT/tmp/ace_events.log" "start" || fail "Geri alma sonrası durdurulan uygulama başlatılmadı"
pass "Çalışan uygulamaların durdurulup geri alma sonrasında eski durumuna döndürülmesi geçti"

echo "13. Sonda '/' içeren OPEN_KOBRA_ROOT ve sembolik bağ koruma testi..."
SL_ROOT="$WORK/slash_sym_root"
make_fake_root "$SL_ROOT"
ln -s "printer.klipper_K3.cfg" "$SL_ROOT/useremain/home/rinkhals/apps/vanilla-klipper/extra_link.cfg"
mkdir -p "$SL_ROOT/useremain/home/rinkhals/apps/kobra-screen"
ln -s "/nonexistent/target" "$SL_ROOT/useremain/home/rinkhals/apps/kobra-screen/sym_keep"
rm -rf "$SL_ROOT/useremain/update_swu"
unzip -q -P "$K3_SWU_PASSWORD" "$INSTALL_SWU" -d "$SL_ROOT/useremain"
tar -xzf "$SL_ROOT/useremain/update_swu/setup.tar.gz" -C "$SL_ROOT/useremain/update_swu"
chmod +x "$SL_ROOT/useremain/update_swu/update.sh"
OPEN_KOBRA_ROOT="${SL_ROOT}/" "$SL_ROOT/useremain/update_swu/update.sh" >/dev/null || fail "Sonda / olan ROOT ile kurulum başarısız"
[ -L "$SL_ROOT/useremain/bak/open-kobra-baseline/files/useremain/home/rinkhals/apps/kobra-screen/sym_keep" ] || fail "cp -PRp sembolik bağı koruyamadı!"
rm -rf "$SL_ROOT/useremain/update_swu"
unzip -q -P "$K3_SWU_PASSWORD" "$UNINSTALL_SWU" -d "$SL_ROOT/useremain"
tar -xzf "$SL_ROOT/useremain/update_swu/setup.tar.gz" -C "$SL_ROOT/useremain/update_swu"
chmod +x "$SL_ROOT/useremain/update_swu/update.sh"
OPEN_KOBRA_ROOT="${SL_ROOT}/" "$SL_ROOT/useremain/update_swu/update.sh" >/dev/null || fail "Sonda / olan ROOT ile kaldırma başarısız"
[ -L "$SL_ROOT/useremain/home/rinkhals/apps/kobra-screen/sym_keep" ] || fail "Kaldırma sonrası sembolik bağ korunmadı!"
pass "Sonda '/' normalizasyonu ve sembolik bağ yedek/geri yükleme testi geçti"

echo "14. Kaldırmada bozuk/eksik baseline ön kontrolü ve baseline yokken güvenlik yedeği testi..."
CORR_UN="$WORK/corrupt_uninst"
make_fake_root "$CORR_UN"
printf '# existing leviq3\n' > "$CORR_UN/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py"
run_swu_on_fake_root "$INSTALL_SWU" "$CORR_UN" >/dev/null

# 14a. Corrupt baseline: missing modified backup file -> MUST fail BEFORE deleting any app!
rm -f "$CORR_UN/useremain/bak/open-kobra-baseline/files/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/leviq3.py"
if run_swu_on_fake_root "$UNINSTALL_SWU" "$CORR_UN" >/dev/null 2>&1; then
    fail "Eksik yedek dosyası varken uninstall durmalıydı"
fi
[ -d "$CORR_UN/useremain/home/rinkhals/apps/kobra-screen" ] || fail "Eksik yedekte kobra-screen silinmemeliydi!"
[ -d "$CORR_UN/useremain/home/rinkhals/apps/ace-standalone" ] || fail "Eksik yedekte ace-standalone silinmemeliydi!"
[ -d "$CORR_UN/useremain/home/rinkhals/apps/05-open-kobra-guard" ] || fail "Eksik yedekte guard silinmemeliydi!"

# 14b. Corrupt baseline: missing .complete -> MUST fail BEFORE deleting any app!
rm -f "$CORR_UN/useremain/bak/open-kobra-baseline/.complete"
if run_swu_on_fake_root "$UNINSTALL_SWU" "$CORR_UN" >/dev/null 2>&1; then
    fail ".complete yokken uninstall durmalıydı"
fi
[ -d "$CORR_UN/useremain/home/rinkhals/apps/kobra-screen" ] || fail ".complete yokken hiçbir uygulama silinmemeliydi!"

# 14c. No baseline (removed completely) -> takes full pre-uninstall backup including printer.klipper.cfg & cleans safely
rm -rf "$CORR_UN/useremain/bak/open-kobra-baseline"
run_swu_on_fake_root "$UNINSTALL_SWU" "$CORR_UN" >/dev/null || fail "Baseline olmadan kaldırma başarısız"
PRE_CFG=$(find "$CORR_UN/useremain/bak" -maxdepth 2 -path '*open-kobra-pre-uninstall-*/printer.klipper.cfg' | head -n 1 || true)
[ -n "$PRE_CFG" ] && [ -f "$PRE_CFG" ] || fail "Baseline yok dalında printer.klipper.cfg ön-yedek içine alınmalıydı!"
pass "Yedek bütünlük doğrulaması ve yıkıcı işlem öncesi reddetme testleri geçti"

echo "15. LD_LIBRARY_PATH normalizasyonu (A1-01), bip bekleme süresi (12c-6) ve USB update.swu temizliği (B-10) testi..."
ENV_ROOT="$WORK/env_usb_root"
make_fake_root "$ENV_ROOT"
mkdir -p "$ENV_ROOT/sys/class/pwm/pwmchip0/pwm0" "$WORK/mock_bin"
: > "$ENV_ROOT/sys/class/pwm/pwmchip0/pwm0/enable"
REAL_PY3=$(command -v python3)
cat > "$WORK/mock_bin/python3" <<EOF
#!/bin/sh
printf '%s\n' "\${LD_LIBRARY_PATH:-}" >> "$WORK/py3_ld.log"
exec "$REAL_PY3" "\$@"
EOF
cat > "$WORK/mock_bin/usleep" <<EOF
#!/bin/sh
printf '%s\n' "\$1" >> "$WORK/usleep.log"
EOF
chmod +x "$WORK/mock_bin/python3" "$WORK/mock_bin/usleep"

# Failed install with writable PWM + USB update.swu -> beep_error inserts pauses, USB update.swu stays
printf 'dummy' > "$ENV_ROOT/mnt/udisk/aGVscF9zb3Nf/update.swu"
printf '{"modelId": "20025"}\n' > "$ENV_ROOT/userdata/app/gk/config/api.cfg"
if PATH="$WORK/mock_bin:$PATH" LD_LIBRARY_PATH="/userdata/app/gk" run_swu_on_fake_root "$INSTALL_SWU" "$ENV_ROOT" >/dev/null 2>&1; then
    fail "Yanlış modelde kurulum başarısız olmalıydı"
fi
[ -f "$ENV_ROOT/mnt/udisk/aGVscF9zb3Nf/update.swu" ] || fail "Başarısız kurulumda USB update.swu silinmemeliydi"
USLEEP_N=$(wc -l < "$WORK/usleep.log" | tr -d ' ')
[ "$USLEEP_N" -ge 5 ] || fail "beep_error çağrıları arasında bekleme (usleep) yok (çağrı sayısı: $USLEEP_N)"

# Successful install -> LD_LIBRARY_PATH normalized before python3 & USB update.swu removed
printf '{"modelId": "20024"}\n' > "$ENV_ROOT/userdata/app/gk/config/api.cfg"
: > "$WORK/py3_ld.log"
PATH="$WORK/mock_bin:$PATH" LD_LIBRARY_PATH="/userdata/app/gk" run_swu_on_fake_root "$INSTALL_SWU" "$ENV_ROOT" >/dev/null || fail "Mock PATH ile kurulum başarısız"
[ ! -e "$ENV_ROOT/mnt/udisk/aGVscF9zb3Nf/update.swu" ] || fail "Başarılı kurulum sonrasında USB update.swu temizlenmeliydi (B-10)"
[ -f "$ENV_ROOT/mnt/udisk/aGVscF9zb3Nf/open-kobra-install.log" ] || fail "Kurulum USB günlüğü korunmalıydı"
PY3_CALLS=$(wc -l < "$WORK/py3_ld.log" | tr -d ' ')
[ "$PY3_CALLS" -ge 2 ] || fail "python3 doğrulamaları beklenen sayıda çalışmadı ($PY3_CALLS)"
while IFS= read -r LD_VAL; do
    case "$LD_VAL" in
        /lib:/usr/lib:*) ;;
        *) fail "LD_LIBRARY_PATH /lib:/usr/lib ile normalize edilmedi: $LD_VAL" ;;
    esac
done < "$WORK/py3_ld.log"

# Successful uninstall -> USB update.swu removed
printf 'dummy' > "$ENV_ROOT/mnt/udisk/aGVscF9zb3Nf/update.swu"
run_swu_on_fake_root "$UNINSTALL_SWU" "$ENV_ROOT" >/dev/null || fail "USB temizlik kaldırma testi başarısız"
[ ! -e "$ENV_ROOT/mnt/udisk/aGVscF9zb3Nf/update.swu" ] || fail "Başarılı kaldırma sonrasında USB update.swu temizlenmeliydi (B-10)"
pass "LD_LIBRARY_PATH, bip bekleme ve USB update.swu temizliği testleri geçti"

echo "16. Atomik uygulama dizini değişimi (.openkobra.old kurtarma) ve version_marker rollback (B-05) testi..."
AT_ROOT="$WORK/atomic_app_root"
make_fake_root "$AT_ROOT"
mkdir -p "$AT_ROOT/useremain/home/rinkhals/apps/kobra-screen.openkobra.old"
printf '{"recovered": true}\n' > "$AT_ROOT/useremain/home/rinkhals/apps/kobra-screen.openkobra.old/config.json"
run_swu_on_fake_root "$INSTALL_SWU" "$AT_ROOT" >/dev/null || fail "Yarım .openkobra.old kurtarımı başarısız"
has_lit "$AT_ROOT/useremain/home/rinkhals/apps/kobra-screen/config.json" '"recovered": true' || fail ".openkobra.old kurtarılmadı"
[ ! -e "$AT_ROOT/useremain/home/rinkhals/apps/kobra-screen.openkobra.old" ] || fail ".openkobra.old kalıntısı temizlenmedi"

RB_VM="$WORK/rb_version_marker"
make_fake_root "$RB_VM"
cp "$RB_VM/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_rb_vm.cfg"
if OPEN_KOBRA_FAIL_AT=version_marker run_swu_on_fake_root "$INSTALL_SWU" "$RB_VM" >/dev/null 2>&1; then
    fail "version_marker test arızası başarısız dönmeliydi"
fi
cmp -s "$RB_VM/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/orig_rb_vm.cfg" || fail "version_marker rollback printer.klipper.cfg'yi geri almadı"
[ ! -e "$RB_VM/useremain/home/rinkhals/printer_data/config/leviq3.cfg" ] || fail "version_marker rollback leviq3.cfg'yi silmedi"
pass "Atomik uygulama değişimi ve geri alma sırası testleri geçti"

echo "17. Kaldırma sırası (include önce çıkar), PRE_BAK uygulama ayarları (A1-02) ve manifest beyaz listesi (B-06) testi..."
UN_ORD="$WORK/uninstall_order_root"
make_fake_root "$UN_ORD"
run_swu_on_fake_root "$INSTALL_SWU" "$UN_ORD" >/dev/null
printf '{"max_temp": 51}\n' > "$UN_ORD/useremain/home/rinkhals/apps/ace-standalone/config.json"
printf '{"http_port": 8091}\n' > "$UN_ORD/useremain/home/rinkhals/apps/ace-standalone.config"
printf '{"foo": 1}\n' > "$UN_ORD/useremain/home/rinkhals/apps/kobra-screen.config"

if OPEN_KOBRA_UNINSTALL_FAIL_AT=after_strip run_swu_on_fake_root "$UNINSTALL_SWU" "$UN_ORD" >/dev/null 2>&1; then
    fail "after_strip simülasyon hatası başarısız dönmeliydi"
fi
if has_lit "$UN_ORD/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include leviq3.cfg]"; then
    fail "Kaldırma önce printer.klipper.cfg include bloğunu çıkarmalıydı (B-06)"
fi
[ -f "$UN_ORD/useremain/home/rinkhals/printer_data/config/leviq3.cfg" ] || fail "Include çıkarıldıktan sonraki ara durumda dosyalar henüz durmalıydı"
[ -d "$UN_ORD/useremain/home/rinkhals/apps/05-open-kobra-guard" ] || fail "Include çıkarıldıktan sonraki ara durumda guard henüz durmalıydı"

PRE_DIR=$(find "$UN_ORD/useremain/bak" -maxdepth 1 -type d -name 'open-kobra-pre-uninstall-*' | sort -r | head -n 1 || true)
[ -n "$PRE_DIR" ] || fail "PRE_BAK dizini bulunamadı"
has_lit "$PRE_DIR/ace-standalone/config.json" '"max_temp": 51' || fail "PRE_BAK ace-standalone/config.json almadı (A1-02)"
has_lit "$PRE_DIR/ace-standalone.config" '"http_port": 8091' || fail "PRE_BAK ace-standalone.config almadı (A1-02)"
has_lit "$PRE_DIR/kobra-screen.config" '"foo": 1' || fail "PRE_BAK kobra-screen.config almadı (A1-02)"

# Unsafe manifest entry must abort BEFORE deleting apps
cp "$UN_ORD/useremain/bak/open-kobra-targets.list" "$WORK/orig_targets.list"
printf '../etc/passwd\n' >> "$UN_ORD/useremain/bak/open-kobra-targets.list"
if run_swu_on_fake_root "$UNINSTALL_SWU" "$UN_ORD" >/dev/null 2>&1; then
    fail "Güvensiz manifest girdisi kaldırmayı durdurmalıydı (B-06)"
fi
[ -d "$UN_ORD/useremain/home/rinkhals/apps/05-open-kobra-guard" ] || fail "Güvensiz manifest durumunda hiçbir uygulama silinmemeliydi"
cp "$WORK/orig_targets.list" "$UN_ORD/useremain/bak/open-kobra-targets.list"

run_swu_on_fake_root "$UNINSTALL_SWU" "$UN_ORD" >/dev/null || fail "after_strip sonrası tekrar kaldırma başarısız"
[ ! -e "$UN_ORD/useremain/home/rinkhals/apps/05-open-kobra-guard" ] || fail "Son kaldırma guard'ı silmedi"
pass "Kaldırma sırası, PRE_BAK uygulama ayarları ve manifest beyaz listesi testleri geçti"

echo "18. Klipper config ayrıştırıcısı (refs/klipper) ile open_kobra_homing.cfg ve safe_z_home/homing_override çakışma testi..."
# Needs a Klipper checkout and the vanilla-klipper app sources; skipped when they are absent
KLIP_REF="${KLIPPER_SRC:-$PARENT_ROOT/refs/klipper}/klippy"
VK_REF="${VANILLA_KLIPPER_SRC:-$PARENT_ROOT/refs/vanilla-klipper-app}"
if [ ! -d "$KLIP_REF" ] || [ ! -f "$VK_REF/printer.klipper_K3.cfg" ]; then
    echo "   atlandı: Klipper kaynağı ($KLIP_REF) veya vanilla-klipper uygulaması ($VK_REF) yok; KLIPPER_SRC / VANILLA_KLIPPER_SRC ile verin"
else

KLIP_ROOT="$WORK/klipper_load_root"
make_fake_root "$KLIP_ROOT" 0
cp "$VK_REF/printer.klipper_K3.cfg" "$KLIP_ROOT/useremain/home/rinkhals/apps/vanilla-klipper/printer.klipper_K3.cfg"
cp "$VK_REF/mainsail.cfg" "$KLIP_ROOT/useremain/home/rinkhals/printer_data/config/mainsail.cfg"
cp "$VK_REF/printer_generic_macros.cfg" "$KLIP_ROOT/useremain/home/rinkhals/printer_data/config/printer_generic_macros.cfg"
run_swu_on_fake_root "$INSTALL_SWU" "$KLIP_ROOT" >/dev/null || fail "Gerçek K3 şablonuyla kurulum başarısız"

python3 - "$KLIP_REF" "$KLIP_ROOT/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" <<'PY'
import ast
import contextlib
import os
import re
import sys

klippy_dir, cfg_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, klippy_dir)
import configfile
import gcode
from extras import safe_z_home

class StubReactor:
    def mutex(self):
        return contextlib.nullcontext()

class StubPrinter:
    config_error = configfile.error
    command_error = gcode.CommandError

    def __init__(self, path):
        self.start_args = {"config_file": path}
        self.reactor = StubReactor()
        self.objs = {"gcode": gcode.GCodeDispatch(self)}

    def get_start_args(self):
        return self.start_args

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objs.get(name, default)

    def load_object(self, cfg, section, default=configfile.sentinel):
        return self.objs.get(section, object())

    def register_event_handler(self, event, cb):
        pass

printer = StubPrinter(cfg_path)
pconfig = configfile.PrinterConfig(printer)
printer.objs["configfile"] = pconfig
cfg = pconfig.read_main_config()

# 1. Verify merged sections and override precedence
assert cfg.getsection("stepper_x").getfloat("homing_retract_dist") == 0.0
assert cfg.getsection("stepper_y").getfloat("homing_retract_dist") == 0.0
assert cfg.has_section("safe_z_home")
assert not cfg.has_section("homing_override"), "open_kobra_homing.cfg [homing_override] tanimlamamali"
assert cfg.has_section("gcode_macro G28")
assert cfg.has_section("gcode_macro FORCE_HOME")

g28_sec = cfg.getsection("gcode_macro G28")
assert g28_sec.get("rename_existing") == "G28.1"
macro_vars = {}
for opt in g28_sec.get_prefix_options("variable_"):
    macro_vars[opt[len("variable_"):]] = ast.literal_eval(g28_sec.get(opt))
assert macro_vars["skip_homed_xy"] is True
assert macro_vars["skip_homed_z"] is False, "H-01: skip_homed_z False olmali"
assert macro_vars["tmc_override_active"] is False

# Verify Jinja2 delimiters are balanced and SAVE_GCODE_STATE never spans G28.1
g28_script = g28_sec.get("gcode")
assert g28_script.count("{%") == g28_script.count("%}")
assert g28_script.count("{{") == g28_script.count("}}")
assert re.search(r"SAVE_GCODE_STATE\s+NAME=(\S+)[\s\S]*?G28\.1[\s\S]*?RESTORE_GCODE_STATE\s+NAME=\1\b", g28_script) is None, (
    "SAVE_GCODE_STATE/RESTORE_GCODE_STATE G28.1 etrafini sarmamali"
)

# Stdlib Jinja-subset renderer to verify G28 macro branches on the real template text
class _JinjaObj(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

class _Sentinel: pass
_SENT = _Sentinel()

def _eval_jinja_expr(expr, env):
    # Support Jinja filters/tests used in open_kobra_homing.cfg
    def _split_top_char(s, sep):
        parts, cur, depth, q = [], [], 0, None
        i = 0
        while i < len(s):
            ch = s[i]
            if q:
                cur.append(ch)
                if ch == "\\" and i + 1 < len(s):
                    cur.append(s[i + 1]); i += 2; continue
                if ch == q:
                    q = None
            else:
                if ch in ("'", '"'): q = ch
                elif ch in "([{": depth += 1
                elif ch in ")]}": depth -= 1
                elif ch == sep and depth == 0:
                    parts.append("".join(cur).strip()); cur = []; i += 1; continue
                cur.append(ch)
            i += 1
        parts.append("".join(cur).strip())
        return parts

    def _replace_is_defined(s):
        return re.sub(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s+is\s+defined\b",
                      lambda m: "_is_defined(%r)" % (m.group(1),), s)

    def _is_defined(dotted):
        cur = env
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return False
        return True

    def _split_top_kw(s, kw):
        parts, cur, depth, q = [], [], 0, None
        i = 0
        kw_len = len(kw)
        while i < len(s):
            ch = s[i]
            if q:
                cur.append(ch)
                if ch == "\\" and i + 1 < len(s):
                    cur.append(s[i + 1]); i += 2; continue
                if ch == q:
                    q = None
            else:
                if ch in ("'", '"'): q = ch
                elif ch in "([{": depth += 1
                elif ch in ")]}": depth -= 1
                elif depth == 0 and s[i:i + kw_len] == kw and (i == 0 or s[i - 1].isspace() or s[i - 1] in ")]}") and (i + kw_len == len(s) or s[i + kw_len].isspace() or s[i + kw_len] in "([{"):
                    parts.append("".join(cur).strip()); cur = []; i += kw_len; continue
                cur.append(ch)
            i += 1
        parts.append("".join(cur).strip())
        return parts

    def _resolve_base(base):
        or_parts = _split_top_kw(base, "or")
        if len(or_parts) > 1:
            res = False
            for p in or_parts:
                res = _resolve_base(p)
                if res:
                    return res
            return res
        and_parts = _split_top_kw(base, "and")
        if len(and_parts) > 1:
            res = True
            for p in and_parts:
                res = _resolve_base(p)
                if not res:
                    return res
            return res
        base = _replace_is_defined(base)
        # Replace innermost (...) or [...] containing '|' at top level (supports nested calls like szh.get(...)|float)
        while True:
            replaced = False
            stack_pos = []
            q = None
            i = 0
            while i < len(base):
                ch = base[i]
                if q:
                    if ch == "\\" and i + 1 < len(base):
                        i += 2; continue
                    if ch == q:
                        q = None
                else:
                    if ch in ("'", '"'):
                        q = ch
                    elif ch in "([":
                        stack_pos.append((ch, i))
                    elif ch in ")]" and stack_pos:
                        open_ch, start = stack_pos.pop()
                        inner = base[start + 1:i]
                        # Check for '|' at depth 0 inside this (...) or [...]
                        if any(_split_top_char(part, "|")[1:] for part in (_split_top_char(inner, ",") if open_ch == "[" else [inner])):
                            if open_ch == "[":
                                rep = repr([_eval_jinja_expr(it, env) for it in _split_top_char(inner, ",")])
                            else:
                                # Only evaluate as Jinja pipe expression if it isn't a tuple/arg list with top-level commas
                                if len(_split_top_char(inner, ",")) == 1:
                                    rep = repr(_eval_jinja_expr(inner, env))
                                else:
                                    rep = None
                            if rep is not None:
                                base = base[:start] + rep + base[i + 1:]
                                replaced = True
                                break
                i += 1
            if not replaced:
                break
        return eval(base, {"__builtins__": {"min": min, "max": max, "abs": abs, "float": float, "int": int, "str": str, "len": len, "True": True, "False": False, "None": None}, "_is_defined": _is_defined}, env)

    segs = _split_top_char(expr, "|")
    val = _resolve_base(segs[0])
    for flt in segs[1:]:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\((.*)\))?$", flt)
        assert m, f"Unsupported filter: {flt}"
        fname, fargs = m.group(1), m.group(2)
        args = eval(f"[{fargs}]", {"__builtins__": {}}, env) if fargs else []
        if fname == "default":
            if val is _SENT or val is None:
                val = args[0]
        elif fname == "lower": val = str(val).lower()
        elif fname == "float": val = float(val)
        elif fname == "int":
            try: val = int(val)
            except (ValueError, TypeError): val = args[0] if args else 0
        elif fname == "abs": val = abs(val)
        elif fname == "string": val = str(val)
        elif fname == "replace": val = str(val).replace(args[0], args[1])
        elif fname == "min": val = min(val)
        elif fname == "max": val = max(val)
        else: raise AssertionError(f"Unknown filter: {fname}")
    return val

def render_g28(homed_axes="", pos=(0.0, 0.0, 0.0, 0.0), params=None, enable_force_move=True, vars_override=None):
    params = params or {}
    mv = dict(macro_vars)
    if vars_override:
        mv.update(vars_override)
    settings = _JinjaObj({
        "stepper_x": _JinjaObj({"position_endstop": 278.5, "position_min": -19.5, "position_max": 278.5}),
        "stepper_y": _JinjaObj({"position_endstop": -6.0, "position_min": -6.0, "position_max": 277.0}),
        "stepper_z": _JinjaObj({"position_max": 262.0}),
        "safe_z_home": _JinjaObj({"home_xy_position": (127.0, 127.0), "speed": 200.0, "z_hop": 15.0, "z_hop_speed": 8.0}),
        "force_move": _JinjaObj({"enable_force_move": enable_force_move}),
        "tmc2209 stepper_x": _JinjaObj({"run_current": 1.5, "hold_current": 1.1}),
        "tmc2209 stepper_y": _JinjaObj({"run_current": 1.7, "hold_current": 0.8}),
    })
    env = dict(mv)
    env["params"] = _JinjaObj(params)
    env["printer"] = _JinjaObj({
        "toolhead": _JinjaObj({"homed_axes": homed_axes, "position": list(pos)}),
        "configfile": _JinjaObj({"settings": settings}),
    })
    tokens = re.split(r"(\{%[\s\S]*?%\}|\{[\s\S]*?\})", g28_script)
    out = []
    stack = [True]
    def _stack_ok():
        return all((s if isinstance(s, bool) else s[1]) for s in stack)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("{%") and tok.endswith("%}"):
            stmt = tok[2:-2].strip()
            if stmt.startswith("set "):
                if _stack_ok():
                    lhs, rhs = stmt[4:].split("=", 1)
                    env[lhs.strip()] = _eval_jinja_expr(rhs.strip(), env)
            elif stmt.startswith("if "):
                parent_ok = _stack_ok()
                cond = bool(_eval_jinja_expr(stmt[3:].strip(), env)) if parent_ok else False
                stack.append([parent_ok, cond, cond])  # [parent, active, ever_matched]
            elif stmt.startswith("elif "):
                parent_ok, _, ever = stack[-1]
                cond = (bool(_eval_jinja_expr(stmt[5:].strip(), env)) if (parent_ok and not ever) else False)
                stack[-1] = [parent_ok, cond, ever or cond]
            elif stmt == "else":
                parent_ok, _, ever = stack[-1]
                stack[-1] = [parent_ok, parent_ok and (not ever), True]
            elif stmt == "endif":
                stack.pop()
            else:
                raise AssertionError(f"Unsupported tag: {stmt}")
        elif tok.startswith("{") and tok.endswith("}"):
            if _stack_ok():
                out.append(str(_eval_jinja_expr(tok[1:-1].strip(), env)))
        else:
            if _stack_ok():
                out.append(tok)
    return [ln.strip() for ln in "".join(out).splitlines() if ln.strip() and not ln.strip().startswith("#")]

# Test A (H-01): Plain G28 when XYZ already homed -> homes Z ONLY (G28.1 Z), does NOT move/home XY or pause 2s
lines_a = render_g28(homed_axes="xyz", pos=(127.0, 127.0, 15.0))
assert "G28.1 Z" in lines_a, f"Duz G28 xyz home'dayken Z'yi home etmeliydi: {lines_a}"
assert not any(l.startswith(("G28.1 X", "G28.1 Y")) or l == "G28.1" for l in lines_a), f"XY surulmemeliydi: {lines_a}"
assert not any(l.startswith("G4 ") or l.startswith("SET_TMC_CURRENT") for l in lines_a), f"Gereksiz TMC/G4: {lines_a}"

# Test B (H-02): G28 X Y when Z NOT homed & enable_force_move=False -> NO XY pre-move before G28.1
lines_b = render_g28(homed_axes="xy", pos=(278.5, -6.0, 0.0), params={"X": "", "Y": ""}, enable_force_move=False)
idx_g28_b = lines_b.index("G28.1 X Y")
assert not any(l.startswith("G1 ") for l in lines_b[:idx_g28_b]), f"Z home degilken (force_move=False) XY on hareketi yapilmamaliydi: {lines_b}"

# Test C (H-02, H-03, H-05, S-01): G28 X Y when Z NOT homed & pos[2] < z_hop & enable_force_move=True -> FORCE_MOVE Z (with ACCEL=300.0) + XY pre-backoff + SET_STEPPER_ENABLE + M400 + G4 P2000 + M400 + G28.1 X Y + post-backoff + restore
lines_c = render_g28(homed_axes="xy", pos=(278.5, -6.0, 0.0), params={"X": "", "Y": ""}, enable_force_move=True)
idx_fm = lines_c.index("FORCE_MOVE STEPPER=stepper_z DISTANCE=5.0 VELOCITY=8.0 ACCEL=300.0")
idx_pre = next(i for i, l in enumerate(lines_c) if l.startswith("G1 X-10.000 Y10.000"))
idx_en_x = lines_c.index("SET_STEPPER_ENABLE STEPPER=stepper_x ENABLE=1")
idx_en_y = lines_c.index("SET_STEPPER_ENABLE STEPPER=stepper_y ENABLE=1")
idx_g4 = lines_c.index("G4 P2000")
idx_g28_c = lines_c.index("G28.1 X Y")
assert idx_fm < idx_pre < idx_en_x < idx_en_y < idx_g4 < idx_g28_c, lines_c
assert lines_c[idx_g4 - 1].startswith("SET_TMC_CURRENT")
assert lines_c[idx_g4 + 1] == "M400", f"H-03: G4 P2000 sonrasinda M400 olmali: {lines_c}"
assert lines_c[idx_en_y + 1] == "M400", f"H-03: SET_STEPPER_ENABLE sonrasinda M400 olmali: {lines_c}"
assert any(l == "G1 X-10.000 Y10.000 F6000.0" for l in lines_c[idx_g28_c + 1:]), lines_c
assert any(l == "SET_TMC_CURRENT STEPPER=stepper_y CURRENT=1.700 HOLDCURRENT=0.800" for l in lines_c[idx_g28_c + 1:]), lines_c

# Test G (S-01): G28 X Y when Z NOT homed BUT pos[2] >= z_hop (already lifted 15mm by SafeZHoming) -> skips redundant FORCE_MOVE while keeping XY pre-backoff
lines_g = render_g28(homed_axes="xy", pos=(278.5, -6.0, 15.0), params={"X": "", "Y": ""}, enable_force_move=True)
assert not any(l.startswith("FORCE_MOVE") for l in lines_g), f"S-01: pos[2] >= z_hop iken FORCE_MOVE yapilmamaliydi: {lines_g}"
assert any(l.startswith("G1 X-10.000 Y10.000") for l in lines_g[:lines_g.index("G28.1 X Y")]), lines_g

# Test D (H-03): Cold boot G28 (homed_axes="") -> enables steppers, M400, G4 P2000, M400, G28.1
lines_d = render_g28(homed_axes="")
assert "SET_STEPPER_ENABLE STEPPER=stepper_x ENABLE=1" in lines_d
assert "SET_STEPPER_ENABLE STEPPER=stepper_y ENABLE=1" in lines_d
assert "G4 P2000" in lines_d and "G28.1" in lines_d
assert lines_d[lines_d.index("G4 P2000") + 1] == "M400"

# Test E (H-05): After failed G28.1 (tmc_override_active=True), next G28 Z restores TMC currents & clears flag
lines_e = render_g28(homed_axes="xyz", params={"Z": ""}, vars_override={"tmc_override_active": True})
assert "G28.1 Z" in lines_e
assert "SET_TMC_CURRENT STEPPER=stepper_x CURRENT=1.500 HOLDCURRENT=1.100" in lines_e
assert "SET_TMC_CURRENT STEPPER=stepper_y CURRENT=1.700 HOLDCURRENT=0.800" in lines_e
assert "SET_GCODE_VARIABLE MACRO=G28 VARIABLE=tmc_override_active VALUE=False" in lines_e

# Test F (H-07): _ok_home_idle (when skip_homed_z=True) has M400 before RESTORE_GCODE_STATE
lines_f = render_g28(homed_axes="xyz", pos=(50.0, 50.0, 5.0), vars_override={"skip_homed_z": True})
idx_rest = lines_f.index("RESTORE_GCODE_STATE NAME=_ok_home_idle MOVE=0")
assert lines_f[idx_rest - 1] == "M400", f"H-07: _ok_home_idle icinde M400 olmali: {lines_f}"

# 2. Verify real safe_z_home loads cleanly without conflict
szh = safe_z_home.load_config(cfg.getsection("safe_z_home"))
assert (szh.home_x_pos, szh.home_y_pos) == (127.0, 127.0)
assert szh.z_hop == 15.0

# 3. Verify SAVE_CONFIG conflict rule passes
regular_fc, _ = pconfig.autosave.load_main_config()
pconfig.autosave._disallow_include_conflicts(regular_fc)

# 4. Negative test: homing_override + safe_z_home together MUST fail in Klipper
homing_cfg = os.path.join(os.path.dirname(cfg_path), "open_kobra_homing.cfg")
with open(homing_cfg, "a", encoding="utf-8") as fh:
    fh.write("\n[homing_override]\ngcode:\n    G28\n")
bad_cfg = pconfig.read_main_config()
try:
    safe_z_home.load_config(bad_cfg.getsection("safe_z_home"))
    raise AssertionError("homing_override ve safe_z_home birlikte hata uretmeliydi")
except configfile.error as exc:
    msg = str(exc)
    if "homing_override and safe_z_homing cannot be used simultaneously" not in msg:
        raise AssertionError("Beklenmeyen hata metni: %r" % (msg,))
PY
pass "refs/klipper ayrıştırıcı yükleme ve safe_z_home/homing_override çakışma testleri geçti"
fi

echo "19. Açılış kurtarma (rescue) katmanı: stok kopya, start.sh kancası, onarım ve kademeli izleyici testleri..."
RES_BIN="$WORK/rescue_mock_bin"
mkdir -p "$RES_BIN"

REAL_MD5SUM=$(command -v md5sum)
cat > "$RES_BIN/md5sum" <<EOF
#!/bin/sh
TARGET="\${1:-}"
[ -n "\${MD5_CALL_LOG:-}" ] && printf '%s\\n' "\$TARGET" >> "\$MD5_CALL_LOG"
if [ -f "\$TARGET" ]; then
    HEAD_VAL=\$(head -n 1 "\$TARGET" 2>/dev/null || true)
    case "\$HEAD_VAL" in
        STOCK_VALID_gkapi) printf '25b5e89b7f25ebcb0e45f26dac5fabe6  %s\n' "\$TARGET"; exit 0 ;;
        STOCK_VALID_K3SysUi) printf '61ef528ac60075a4c2faf5fa0e30a90f  %s\n' "\$TARGET"; exit 0 ;;
        STOCK_VALID_gklib) printf '004813e067410f0feb9838957d96a387  %s\n' "\$TARGET"; exit 0 ;;
        STOCK_VALID_gkcam) printf '8e82e25a315621d552e3b39436516fb1  %s\n' "\$TARGET"; exit 0 ;;
        STOCK_VALID_gkcam) printf '8e82e25a315621d552e3b39436516fb1  %s\n' "\$TARGET"; exit 0 ;;
    esac
fi
exec "$REAL_MD5SUM" "\$@"
EOF
chmod +x "$RES_BIN/md5sum"

# 19a. Install idempotency, stock content + Rinkhals block preservation, verified stock copy, byte-for-byte uninstall
RES_ROOT="$WORK/rescue_root"
make_fake_root "$RES_ROOT"
mkdir -p "$RES_ROOT/useremain/dev" "$RES_ROOT/userdata/app/gk"
printf '2.4.6.7\n' > "$RES_ROOT/useremain/dev/version"
if [ -f "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh" ]; then
    cp "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh" "$RES_ROOT/userdata/app/gk/start.sh"
else
    printf 'export LD_LIBRARY_PATH=/userdata/app/gk:$LD_LIBRARY_PATH\n./gklib -a /tmp/unix_uds1 printer.cfg &\n./K3SysUi &\n' > "$RES_ROOT/userdata/app/gk/start.sh"
fi
if [ -f "$PARENT_ROOT/refs/Rinkhals/files/start.sh.patch" ]; then
    cat "$PARENT_ROOT/refs/Rinkhals/files/start.sh.patch" >> "$RES_ROOT/userdata/app/gk/start.sh"
else
    # Same shape as Rinkhals' files/start.sh.patch
    printf '# Rinkhals/begin\nif [ -f /useremain/rinkhals/start-rinkhals.sh ]; then\n    echo Rinkhals is installed, starting...\n    chmod +x /useremain/rinkhals/start-rinkhals.sh\n    /useremain/rinkhals/start-rinkhals.sh\nfi\n# Rinkhals/end\n' >> "$RES_ROOT/userdata/app/gk/start.sh"
fi
chmod +x "$RES_ROOT/userdata/app/gk/start.sh"
cp "$RES_ROOT/userdata/app/gk/start.sh" "$WORK/orig_start_sh"

printf 'STOCK_VALID_gkapi\n' > "$RES_ROOT/userdata/app/gk/gkapi"
printf 'STOCK_VALID_K3SysUi\n' > "$RES_ROOT/userdata/app/gk/K3SysUi"
printf 'STOCK_VALID_gklib\n' > "$RES_ROOT/userdata/app/gk/gklib"
printf 'STOCK_VALID_gkcam\n' > "$RES_ROOT/userdata/app/gk/gkcam"
chmod +x "$RES_ROOT/userdata/app/gk/gkcam"
printf 'STOCK_VALID_gkcam\n' > "$RES_ROOT/userdata/app/gk/gkcam"
chmod +x "$RES_ROOT/userdata/app/gk/gkcam"
chmod +x "$RES_ROOT/userdata/app/gk/gkapi" "$RES_ROOT/userdata/app/gk/K3SysUi" "$RES_ROOT/userdata/app/gk/gklib"

PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$RES_ROOT" >/dev/null || fail "Kurtarma katmanı ilk kurulum başarısız"
[ -x "$RES_ROOT/useremain/open-kobra/rescue/rescue.sh" ] || fail "rescue.sh kurulmadı"
[ -f "$RES_ROOT/useremain/open-kobra/rescue/start.sh.orig" ] || fail "start.sh.orig yedeği alınmadı"
cmp -s "$RES_ROOT/useremain/open-kobra/rescue/start.sh.orig" "$WORK/orig_start_sh" || fail "start.sh.orig stok içerikle eşleşmiyor"
[ -f "$RES_ROOT/useremain/open-kobra/rescue/stock/2.4.6.7/.verified" ] || fail "Stok kopya .verified işareti oluşturulmadı"
[ ! -e "$RES_ROOT/useremain/open-kobra/rescue/binary_repair.disabled" ] || fail "Doğrulanmış kurulumda binary_repair.disabled olmamalıydı"
cmp -s "$RES_ROOT/useremain/open-kobra/rescue/stock/2.4.6.7/gkapi" "$RES_ROOT/userdata/app/gk/gkapi" || fail "Stok gkapi kopyası eşleşmiyor"
cmp -s "$RES_ROOT/useremain/open-kobra/rescue/stock/2.4.6.7/K3SysUi" "$RES_ROOT/userdata/app/gk/K3SysUi" || fail "Stok K3SysUi kopyası eşleşmiyor"
cmp -s "$RES_ROOT/useremain/open-kobra/rescue/stock/2.4.6.7/gklib" "$RES_ROOT/userdata/app/gk/gklib" || fail "Stok gklib kopyası eşleşmiyor"

FIRST_LINE=$(head -n 1 "$RES_ROOT/userdata/app/gk/start.sh")
[ "$FIRST_LINE" = "# open-kobra-rescue/begin" ] || fail "start.sh kancası en başa eklenmemiş: $FIRST_LINE"
has_lit "$RES_ROOT/userdata/app/gk/start.sh" "# Rinkhals/begin" || fail "Rinkhals/begin korunmalıydı"
has_lit "$RES_ROOT/userdata/app/gk/start.sh" "# Rinkhals/end" || fail "Rinkhals/end korunmalıydı"
has_lit "$RES_ROOT/userdata/app/gk/start.sh" "./K3SysUi &" || fail "Stok K3SysUi satırı korunmalıydı"

# Second install must be idempotent (single rescue block)
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$RES_ROOT" >/dev/null || fail "Kurtarma katmanı ikinci kurulum başarısız"
BLK_CNT=$(count_lit "$RES_ROOT/userdata/app/gk/start.sh" "# open-kobra-rescue/begin")
[ "$BLK_CNT" = "1" ] || fail "İki kurulum sonrası start.sh içinde tam 1 blok olmalıydı (gelen: $BLK_CNT)"
cmp -s "$RES_ROOT/useremain/open-kobra/rescue/start.sh.orig" "$WORK/orig_start_sh" || fail "İkinci kurulum start.sh.orig dosyasını bozmamalıydı"

# 19b. Boot binary repair (truncated gkapi + leftover .original/.patch preserved and logged)
printf 'HALF_WRITTEN_CORRUPT' > "$RES_ROOT/userdata/app/gk/gkapi"
chmod -x "$RES_ROOT/userdata/app/gk/gkapi" 2>/dev/null || true
printf 'LEFT_ORIG\n' > "$RES_ROOT/userdata/app/gk/gkapi.original"
printf 'LEFT_PATCH\n' > "$RES_ROOT/userdata/app/gk/gkapi.patch"
PATH="$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$RES_ROOT" OPEN_KOBRA_RESCUE_NO_WATCH=1 \
    sh "$RES_ROOT/useremain/open-kobra/rescue/rescue.sh" boot || fail "rescue.sh boot başarısız"
has_line "$RES_ROOT/userdata/app/gk/gkapi" "STOCK_VALID_gkapi" || fail "Kesilmiş gkapi boot sırasında onarılmadı"
[ -x "$RES_ROOT/userdata/app/gk/gkapi" ] || fail "Onarılan gkapi çalıştırılabilir (+x) olmalı"
[ -f "$RES_ROOT/userdata/app/gk/gkapi.original" ] || fail "Artık gkapi.original silinmemeliydi"
has_lit "$RES_ROOT/useremain/open-kobra/rescue/rescue.log" "gkapi.original" || fail "Artık gkapi.original günlüğe yazılmalıydı"

# 19c. Version mismatch on boot must NOT overwrite binaries (Anycubic update protection)
printf '2.4.7.0\n' > "$RES_ROOT/useremain/dev/version"
printf 'NEW_FIRMWARE_GKAPI\n' > "$RES_ROOT/userdata/app/gk/gkapi"
PATH="$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$RES_ROOT" OPEN_KOBRA_RESCUE_NO_WATCH=1 \
    sh "$RES_ROOT/useremain/open-kobra/rescue/rescue.sh" boot || fail "Sürüm farkında rescue.sh boot 0 dönmeliydi"
has_line "$RES_ROOT/userdata/app/gk/gkapi" "NEW_FIRMWARE_GKAPI" || fail "Farklı firmware sürümünde eski stok ikili üzerine yazılmamalıydı!"
has_lit "$RES_ROOT/useremain/open-kobra/rescue/rescue.log" "2.4.7.0" || fail "Sürüm farkı günlüğe yazılmalıydı"
printf '2.4.6.7\n' > "$RES_ROOT/useremain/dev/version"
printf 'STOCK_VALID_gkapi\n' > "$RES_ROOT/userdata/app/gk/gkapi"

# 19d. Uninstall removes rescue block and restores start.sh byte-for-byte + removes /useremain/open-kobra/rescue
run_swu_on_fake_root "$UNINSTALL_SWU" "$RES_ROOT" >/dev/null || fail "Kurtarma katmanı kaldırma başarısız"
if has_lit "$RES_ROOT/userdata/app/gk/start.sh" "# open-kobra-rescue/begin"; then
    fail "Kaldırma sonrası start.sh içinde kurtarma bloğu kalmış"
fi
cmp -s "$RES_ROOT/userdata/app/gk/start.sh" "$WORK/orig_start_sh" || fail "Kaldırma sonrası start.sh bayt bayt orijinaline dönmedi"
[ ! -e "$RES_ROOT/useremain/open-kobra/rescue" ] || fail "Kaldırma sonrası /useremain/open-kobra/rescue temizlenmedi"

# 19e. MD5 mismatch on install: no stock copy created, binary repair disabled, warning logged, install STILL succeeds
RES_BAD_MD5="$WORK/rescue_bad_md5"
make_fake_root "$RES_BAD_MD5"
mkdir -p "$RES_BAD_MD5/useremain/dev" "$RES_BAD_MD5/userdata/app/gk"
printf '2.4.6.7\n' > "$RES_BAD_MD5/useremain/dev/version"
cp "$WORK/orig_start_sh" "$RES_BAD_MD5/userdata/app/gk/start.sh"
printf 'MODIFIED_OR_PATCHED_GKAPI\n' > "$RES_BAD_MD5/userdata/app/gk/gkapi"
printf 'STOCK_VALID_K3SysUi\n' > "$RES_BAD_MD5/userdata/app/gk/K3SysUi"
printf 'STOCK_VALID_gklib\n' > "$RES_BAD_MD5/userdata/app/gk/gklib"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$RES_BAD_MD5" >/dev/null || fail "MD5 uyuşmazlığında kurulum başarısız sayılmamalıydı"
[ ! -e "$RES_BAD_MD5/useremain/open-kobra/rescue/stock/2.4.6.7/gkapi" ] || fail "MD5 uyuşmazlığında stok kopya oluşturulmamalıydı"
[ ! -e "$RES_BAD_MD5/useremain/open-kobra/rescue/stock/2.4.6.7/.verified" ] || fail "MD5 uyuşmazlığında .verified olmamalıydı"
[ -f "$RES_BAD_MD5/useremain/open-kobra/rescue/binary_repair.disabled" ] || fail "MD5 uyuşmazlığında binary_repair.disabled işaretlenmeliydi"
has_lit "$RES_BAD_MD5/useremain/rinkhals/open-kobra-install.log" "ikili onarımı devre dışı" || fail "MD5 uyuşmazlığı kurulum günlüğüne yazılmalıydı"
has_lit "$RES_BAD_MD5/useremain/open-kobra/rescue/rescue.log" "ikili onarımı devre dışı" || fail "MD5 uyuşmazlığı rescue.log'a yazılmalıydı"

# 19f. Watcher tests (healthy K3SysUi, healthy kobra_screen via /proc, 1st/2nd/3rd failure, printing/paused, log trim)
W_ROOT="$WORK/rescue_watch_root"
make_fake_root "$W_ROOT"
mkdir -p "$W_ROOT/useremain/dev" "$W_ROOT/userdata/app/gk" "$W_ROOT/proc"
printf '2.4.6.7\n' > "$W_ROOT/useremain/dev/version"
cp "$WORK/orig_start_sh" "$W_ROOT/userdata/app/gk/start.sh"
printf 'STOCK_VALID_gkapi\n' > "$W_ROOT/userdata/app/gk/gkapi"
printf 'STOCK_VALID_K3SysUi\n' > "$W_ROOT/userdata/app/gk/K3SysUi"
printf 'STOCK_VALID_gklib\n' > "$W_ROOT/userdata/app/gk/gklib"
printf 'STOCK_VALID_gkcam\n' > "$W_ROOT/userdata/app/gk/gkcam"
chmod +x "$W_ROOT/userdata/app/gk/gkcam"
printf 'STOCK_VALID_gkcam\n' > "$W_ROOT/userdata/app/gk/gkcam"
chmod +x "$W_ROOT/userdata/app/gk/gkcam"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$W_ROOT" >/dev/null

W_BIN="$WORK/watch_mock_bin"
mkdir -p "$W_BIN"
cat > "$W_BIN/pidof" <<'EOF'
#!/bin/sh
case "${1:-}" in
    K3SysUi)
        [ "${MOCK_K3SYSUI_ALIVE:-0}" = "1" ] && { printf '1234\n'; exit 0; }
        ;;
    kobra_screen)
        [ "${MOCK_KOBRA_ALIVE:-0}" = "1" ] && { printf '2345\n'; exit 0; }
        ;;
esac
exit 1
EOF
cat > "$W_BIN/reboot" <<'EOF'
#!/bin/sh
printf 'reboot\n' >> "${MOCK_REBOOT_LOG:?}"
exit 0
EOF
cat > "$W_BIN/wget" <<'EOF'
#!/bin/sh
printf '%s\n' "${MOCK_MOONRAKER_JSON:-}"
exit 0
EOF
chmod +x "$W_BIN/pidof" "$W_BIN/reboot" "$W_BIN/wget"

REBOOT_LOG="$WORK/mock_reboots.log"
WATCH_SH="$W_ROOT/useremain/open-kobra/rescue/rescue.sh"

# Healthy K3SysUi -> resets state from 2 to 0, no reboot
printf '2\n' > "$W_ROOT/useremain/open-kobra/rescue/state"
: > "$REBOOT_LOG"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" \
    OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 \
    OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_K3SYSUI_ALIVE=1 \
    MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch || fail "Sağlıklı izleyici başarısız"
[ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "0" ] || fail "Sağlıklı durumda state sıfırlanmalıydı"
[ ! -s "$REBOOT_LOG" ] || fail "Sağlıklı durumda reboot çağrılmamalıydı"

# Healthy kobra_screen detected via /proc/<pid>/cmdline (python3 -m kobra_screen)
sleep 30 &
LIVE_PID=$!
mkdir -p "$W_ROOT/proc/$LIVE_PID"
printf 'python3\0-m\0kobra_screen\0--host\0127.0.0.1\0' > "$W_ROOT/proc/$LIVE_PID/cmdline"
printf '1\n' > "$W_ROOT/useremain/open-kobra/rescue/state"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" \
    OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 \
    OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_K3SYSUI_ALIVE=0 \
    MOCK_KOBRA_ALIVE=0 \
    MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch || fail "kobra_screen /proc sağlıklı izleyici başarısız"
kill "$LIVE_PID" 2>/dev/null || true
wait "$LIVE_PID" 2>/dev/null || true
rm -rf "$W_ROOT/proc/$LIVE_PID"
[ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "0" ] || fail "kobra_screen ayakta iken state sıfırlanmalıydı"
[ ! -s "$REBOOT_LOG" ] || fail "kobra_screen ayakta iken reboot çağrılmamalıydı"

# Active print (printing & paused) -> NO action, NO reboot
: > "$W_ROOT/useremain/home/rinkhals/apps/kobra-screen/.enabled"
for PSTATE in printing paused; do
    : > "$REBOOT_LOG"
    PATH="$W_BIN:$RES_BIN:$PATH" \
        OPEN_KOBRA_ROOT="$W_ROOT" \
        OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 \
        OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
        MOCK_K3SYSUI_ALIVE=0 \
        MOCK_KOBRA_ALIVE=0 \
        MOCK_MOONRAKER_JSON="{\"result\":{\"status\":{\"print_stats\":{\"state\":\"$PSTATE\"}}}}" \
        MOCK_REBOOT_LOG="$REBOOT_LOG" \
        sh "$WATCH_SH" watch
    [ ! -s "$REBOOT_LOG" ] || fail "$PSTATE durumunda reboot çağrılmamalıydı"
    [ ! -e "$W_ROOT/useremain/home/rinkhals/apps/kobra-screen/.disabled" ] || fail "$PSTATE durumunda kobra-screen kapatılmamalıydı"
    [ ! -e "$W_ROOT/useremain/rinkhals/.disable-rinkhals" ] || fail "$PSTATE durumunda .disable-rinkhals oluşturulmamalıydı"
    [ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "0" ] || fail "$PSTATE durumunda state değişmemeliydi"
done

# PK-R04: default (no auto_recovery.enabled): UI missing + Moonraker reachable + not printing -> repair what is safe, but
# NEVER reboot and NEVER touch Rinkhals; the failure counter does not move.
printf '0\n' > "$W_ROOT/useremain/open-kobra/rescue/state"
: > "$REBOOT_LOG"
printf 'BROKEN_GKAPI' > "$W_ROOT/userdata/app/gk/gkapi"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_MOONRAKER_JSON='{"result":{"status":{"print_stats":{"state":"standby"}}}}' MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch
[ ! -s "$REBOOT_LOG" ] || fail "PK-R04: varsayılanda izleyici reboot ÇAĞIRMAMALI"
[ ! -e "$W_ROOT/useremain/rinkhals/.disable-rinkhals" ] || fail "PK-R04: varsayılanda .disable-rinkhals oluşturulmamalı"
[ ! -e "$W_ROOT/useremain/home/rinkhals/apps/kobra-screen/.disabled" ] || fail "PK-R04: varsayılanda kobra-screen kapatılmamalı"
[ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "0" ] || fail "PK-R04: varsayılanda hata sayacı artmamalı"
has_line "$W_ROOT/userdata/app/gk/gkapi" "STOCK_VALID_gkapi" || fail "PK-R04: güvenli ikili onarımı yine yapılmalı"
has_lit "$W_ROOT/useremain/open-kobra/rescue/rescue.log" "Otomatik kurtarma KAPALI" || fail "PK-R04: günlükte kapalı bilgisi yok"
# opt-in marker present but Moonraker unreachable (empty answer): fail-closed, nothing happens
: > "$W_ROOT/useremain/open-kobra/rescue/auto_recovery.enabled"
for MRJ in "" "garbage"; do
    PATH="$W_BIN:$RES_BIN:$PATH" \
        OPEN_KOBRA_ROOT="$W_ROOT" OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
        MOCK_MOONRAKER_JSON="$MRJ" MOCK_REBOOT_LOG="$REBOOT_LOG" \
        sh "$WATCH_SH" watch
    [ ! -s "$REBOOT_LOG" ] || fail "PK-R04: Moonraker erişilemezken reboot çağrılmamalı ('$MRJ')"
    [ ! -e "$W_ROOT/useremain/rinkhals/.disable-rinkhals" ] || fail "PK-R04: Moonraker erişilemezken .disable-rinkhals oluşturulmamalı"
    [ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "0" ] || fail "PK-R04: Moonraker erişilemezken sayaç artmamalı"
done
has_lit "$W_ROOT/useremain/open-kobra/rescue/rescue.log" "fail-closed" || fail "PK-R04: fail-closed günlüğü yok"
# opt-in + reachable + idle, but gklib / klippy is alive: strong evidence missing -> no action
sleep 30 &
ALIVE_PID=$!
mkdir -p "$W_ROOT/proc/$ALIVE_PID"
printf 'gklib\n' > "$W_ROOT/proc/$ALIVE_PID/comm"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_MOONRAKER_JSON='{"result":{"status":{"print_stats":{"state":"standby"}}}}' MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch
[ ! -s "$REBOOT_LOG" ] || fail "PK-R04: gklib ayaktayken otomatik kurtarma tetiklenmemeli"
printf 'python\0-m\0klippy\0-a\0/tmp/unix_uds1\0' > "$W_ROOT/proc/$ALIVE_PID/cmdline"
printf 'python3\n' > "$W_ROOT/proc/$ALIVE_PID/comm"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_MOONRAKER_JSON='{"result":{"status":{"print_stats":{"state":"standby"}}}}' MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch
[ ! -s "$REBOOT_LOG" ] || fail "PK-R04: klippy ayaktayken otomatik kurtarma tetiklenmemeli"
kill "$ALIVE_PID" 2>/dev/null || true
wait "$ALIVE_PID" 2>/dev/null || true
rm -rf "$W_ROOT/proc/$ALIVE_PID"

# 1st failure -> re-repairs binary, disables kobra-screen, calls reboot (opt-in cascade only)
: > "$REBOOT_LOG"
printf 'BROKEN_GKAPI' > "$W_ROOT/userdata/app/gk/gkapi"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" \
    OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 \
    OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_MOONRAKER_JSON='{"result":{"status":{"print_stats":{"state":"standby"}}}}' \
    MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch
has_line "$W_ROOT/userdata/app/gk/gkapi" "STOCK_VALID_gkapi" || fail "1. hatada ikili onarım çalışmalıydı"
[ -f "$W_ROOT/useremain/home/rinkhals/apps/kobra-screen/.disabled" ] || fail "1. hatada kobra-screen/.disabled oluşturulmalıydı"
[ ! -e "$W_ROOT/useremain/home/rinkhals/apps/kobra-screen/.enabled" ] || fail "1. hatada kobra-screen/.enabled kaldırılmalıydı"
[ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "1" ] || fail "1. hatada state=1 olmalıydı"
[ "$(wc -l < "$REBOOT_LOG" | tr -d ' ')" = "1" ] || fail "1. hatada 1 kez reboot çağrılmalıydı"
[ ! -e "$W_ROOT/useremain/rinkhals/.disable-rinkhals" ] || fail "1. hatada .disable-rinkhals henüz oluşturulmamalıydı"

# 2nd failure -> creates .disable-rinkhals and calls reboot
: > "$REBOOT_LOG"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" \
    OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 \
    OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_MOONRAKER_JSON='{"result":{"status":{"print_stats":{"state":"standby"}}}}' \
    MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch
[ -f "$W_ROOT/useremain/rinkhals/.disable-rinkhals" ] || fail "2. hatada .disable-rinkhals oluşturulmalıydı"
[ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "2" ] || fail "2. hatada state=2 olmalıydı"
[ "$(wc -l < "$REBOOT_LOG" | tr -d ' ')" = "1" ] || fail "2. hatada 1 kez reboot çağrılmalıydı"

# 3rd failure -> does NOT call reboot (no bootloop)
: > "$REBOOT_LOG"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" \
    OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 \
    OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_MOONRAKER_JSON='{"result":{"status":{"print_stats":{"state":"standby"}}}}' \
    MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch
[ "$(tr -d ' \t\r\n' < "$W_ROOT/useremain/open-kobra/rescue/state")" = "3" ] || fail "3. hatada state=3 olmalıydı"
[ ! -s "$REBOOT_LOG" ] || fail "3. hatada reboot ÇAĞRILMAMALIYDI"

# Log size cap (64 KB)
python3 -c 'import sys; open(sys.argv[1], "w").write(("2026-11-16 00:00:00: log line padding\n") * 2500)' "$W_ROOT/useremain/open-kobra/rescue/rescue.log"
PATH="$W_BIN:$RES_BIN:$PATH" \
    OPEN_KOBRA_ROOT="$W_ROOT" \
    OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 \
    OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
    MOCK_K3SYSUI_ALIVE=1 \
    MOCK_REBOOT_LOG="$REBOOT_LOG" \
    sh "$WATCH_SH" watch
LOG_SZ=$(wc -c < "$W_ROOT/useremain/open-kobra/rescue/rescue.log" | tr -d ' ')
[ "$LOG_SZ" -le 65536 ] || fail "rescue.log 64 KB sinirini asti ($LOG_SZ bayt)"

# 19g. start.sh hook never stops stock flow when rescue.sh is missing, broken, or times out
HOOK_ROOT="$WORK/rescue_hook_root"
make_fake_root "$HOOK_ROOT"
mkdir -p "$HOOK_ROOT/useremain/dev" "$HOOK_ROOT/userdata/app/gk"
printf '9.9.9.9\n' > "$HOOK_ROOT/useremain/dev/version"
STUBS="$WORK/rt_stubs"
mkdir -p "$STUBS"
for STB in gklib gkapi K3SysUi; do
    printf '#!/bin/sh\nexit 0\n' > "$STUBS/$STB"
    chmod +x "$STUBS/$STB"
done
cat > "$HOOK_ROOT/userdata/app/gk/start.sh" <<'EOF'
: > "${HOOK_STOCK_REACHED:?}"
./gklib -a /tmp/unix_uds1 printer.cfg &
./gkapi &
./K3SysUi &
EOF
printf 'STOCK_VALID_gkapi\n' > "$HOOK_ROOT/userdata/app/gk/gkapi"
printf 'STOCK_VALID_K3SysUi\n' > "$HOOK_ROOT/userdata/app/gk/K3SysUi"
printf 'STOCK_VALID_gklib\n' > "$HOOK_ROOT/userdata/app/gk/gklib"
printf 'STOCK_VALID_gkcam\n' > "$HOOK_ROOT/userdata/app/gk/gkcam"
chmod +x "$HOOK_ROOT/userdata/app/gk/gkcam"
printf 'STOCK_VALID_gkcam\n' > "$HOOK_ROOT/userdata/app/gk/gkcam"
chmod +x "$HOOK_ROOT/userdata/app/gk/gkcam"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$HOOK_ROOT" >/dev/null

# Case 1: broken syntax in rescue.sh
printf 'if [ broken\n' > "$HOOK_ROOT/useremain/open-kobra/rescue/rescue.sh"
rm -f "$WORK/hook_reached"
(cd "$STUBS" && OPEN_KOBRA_ROOT="$HOOK_ROOT" HOOK_STOCK_REACHED="$WORK/hook_reached" sh "$HOOK_ROOT/userdata/app/gk/start.sh") || fail "Bozuk rescue.sh start.sh'i durdurmamalıydı"
[ -f "$WORK/hook_reached" ] || fail "Bozuk rescue.sh durumunda stok akış devam etmedi"

# Case 2: a hanging "boot" is cut by the bounded wrapper (real rescue.sh, boot made slow through the test knob)
if command -v timeout >/dev/null 2>&1; then
    cp "$PROJECT_ROOT/rescue/rescue.sh" "$HOOK_ROOT/useremain/open-kobra/rescue/rescue.sh"
    rm -f "$WORK/hook_reached"
    HOOK_T0=$(date +%s)
    (cd "$STUBS" && OPEN_KOBRA_ROOT="$HOOK_ROOT" OPEN_KOBRA_RESCUE_BOOT_TIMEOUT=1 OPEN_KOBRA_RESCUE_TEST_SLEEP=20 HOOK_STOCK_REACHED="$WORK/hook_reached" \
        sh "$HOOK_ROOT/userdata/app/gk/start.sh") || fail "Zaman aşımında start.sh durmamalıydı"
    [ -f "$WORK/hook_reached" ] || fail "Zaman aşımı durumunda stok akış devam etmedi"
    [ $(( $(date +%s) - HOOK_T0 )) -lt 12 ] || fail "Takılan boot sınırsız beklenmemeli"
fi

# Case 3: missing rescue.sh
rm -f "$HOOK_ROOT/useremain/open-kobra/rescue/rescue.sh"
rm -f "$WORK/hook_reached"
(cd "$STUBS" && OPEN_KOBRA_ROOT="$HOOK_ROOT" HOOK_STOCK_REACHED="$WORK/hook_reached" sh "$HOOK_ROOT/userdata/app/gk/start.sh") || fail "Eksik rescue.sh start.sh'i durdurmamalıydı"
[ -f "$WORK/hook_reached" ] || fail "Eksik rescue.sh durumunda stok akış devam etmedi"

pass "Açılış kurtarma (rescue) katmanı kurulum, onarım, izleyici ve dayanıklılık testleri geçti"

echo "20. ACE sürücüsü (ace-driver) kurulum/kaldırma: yedek, atomik kopya, bayt bayt geri alma, sürücüsüz paket..."
FAKE_ACE="$WORK/fake_ace_driver"
mkdir -p "$FAKE_ACE/klippy/extras/ace/__pycache__" "$FAKE_ACE/config"
printf '# fake ace driver package\nVALUE = 1\n' > "$FAKE_ACE/klippy/extras/ace/__init__.py"
printf 'import os\n\n\ndef run():\n    return os.getcwd()\n' > "$FAKE_ACE/klippy/extras/ace/manager.py"
printf 'raise SystemExit(1)  # tests must never be packaged\n' > "$FAKE_ACE/klippy/extras/ace/test_manager.py"
printf 'junk' > "$FAKE_ACE/klippy/extras/ace/__pycache__/manager.cpython-311.pyc"
printf '# fake temperature_ace\n' > "$FAKE_ACE/klippy/extras/temperature_ace.py"
printf '# NEW ace_K3.cfg from driver\n[ace]\ntangle_detection: True\n' > "$FAKE_ACE/config/ace_K3.cfg"
printf '9.9.9-test\n' > "$FAKE_ACE/VERSION"

ACE_DIST="$WORK/dist_ace"
OPEN_KOBRA_ACE_DRIVER_SRC="$FAKE_ACE" OPEN_KOBRA_DIST_DIR="$ACE_DIST" "$PROJECT_ROOT/build.sh" > "$WORK/build_ace.log" 2>&1 || fail "Sahte ace-driver ile derleme başarısız"
has_lit "$WORK/build_ace.log" "dahil (3 .py dosyası, sürüm 9.9.9-test)" || fail "Derleme çıktısı ace-driver dahil bilgisini vermedi: $(cat "$WORK/build_ace.log")"
ACE_INSTALL_SWU="$ACE_DIST/install/aGVscF9zb3Nf/update.swu"
extract_swu "$ACE_INSTALL_SWU" "$WORK/unpack_ace"
PAY="$WORK/unpack_ace/update_swu/ace-driver"
cmp -s "$PAY/klippy/extras/ace/manager.py" "$FAKE_ACE/klippy/extras/ace/manager.py" || fail "Paketteki ace/manager.py kaynakla aynı değil"
cmp -s "$PAY/klippy/extras/temperature_ace.py" "$FAKE_ACE/klippy/extras/temperature_ace.py" || fail "Paketteki temperature_ace.py kaynakla aynı değil"
cmp -s "$PAY/config/ace_K3.cfg" "$FAKE_ACE/config/ace_K3.cfg" || fail "Paketteki ace_K3.cfg kaynakla aynı değil"
[ ! -e "$PAY/klippy/extras/ace/test_manager.py" ] || fail "test_*.py pakete sızdı"
[ ! -e "$PAY/klippy/extras/ace/__pycache__" ] || fail "__pycache__ pakete sızdı"
has_lit "$WORK/unpack_ace/update_swu/MANIFEST.md5" "./ace-driver/klippy/extras/ace/manager.py" || fail "MANIFEST.md5 ace-driver dosyalarını kapsamıyor"

ace_make_root() {
    AR="$1"
    make_fake_root "$AR"
    AVK="$AR/useremain/home/rinkhals/apps/vanilla-klipper"
    mkdir -p "$AVK/klippy/extras/ace/__pycache__"
    printf 'ORIGINAL ace __init__\n' > "$AVK/klippy/extras/ace/__init__.py"
    printf 'ORIGINAL file that the driver does not ship\n' > "$AVK/klippy/extras/ace/vanilla_only.py"
    printf 'orig-pyc\000bytes' > "$AVK/klippy/extras/ace/__pycache__/x.pyc"
    printf 'ORIGINAL temperature_ace\n' > "$AVK/klippy/extras/temperature_ace.py"
    printf '# ORIGINAL template ace_K3.cfg\n[ace]\n' > "$AVK/ace_K3.cfg"
    printf '# USER EDITED live ace_K3.cfg\n[ace]\ntangle_detection: False\n' > "$AR/useremain/home/rinkhals/printer_data/config/ace_K3.cfg"
    rm -rf "$AR.snap"
    mkdir -p "$AR.snap"
    cp -PRp "$AVK" "$AR.snap/vanilla-klipper"
    cp -PRp "$AR/useremain/home/rinkhals/printer_data/config/ace_K3.cfg" "$AR.snap/ace_K3.live.cfg"
}

ace_check_installed() {
    AR="$1"
    AVK="$AR/useremain/home/rinkhals/apps/vanilla-klipper"
    ACFG="$AR/useremain/home/rinkhals/printer_data/config"
    cmp -s "$AVK/klippy/extras/ace/__init__.py" "$FAKE_ACE/klippy/extras/ace/__init__.py" || fail "$2: ace/__init__.py sürücüyle aynı değil"
    cmp -s "$AVK/klippy/extras/ace/manager.py" "$FAKE_ACE/klippy/extras/ace/manager.py" || fail "$2: ace/manager.py sürücüyle aynı değil"
    [ ! -e "$AVK/klippy/extras/ace/vanilla_only.py" ] || fail "$2: sürücünün taşımadığı eski dosya kalmış (dizin bütün olarak değişmeli)"
    [ ! -e "$AVK/klippy/extras/ace/__pycache__" ] || fail "$2: eski __pycache__ kalmış"
    cmp -s "$AVK/klippy/extras/temperature_ace.py" "$FAKE_ACE/klippy/extras/temperature_ace.py" || fail "$2: temperature_ace.py sürücüyle aynı değil"
    cmp -s "$AVK/ace_K3.cfg" "$FAKE_ACE/config/ace_K3.cfg" || fail "$2: vanilla ace_K3.cfg şablonu değişmedi"
    cmp -s "$ACFG/ace_K3.cfg" "$AR.snap/ace_K3.live.cfg" || fail "$2: kullanıcının canlı ace_K3.cfg dosyasına dokunulmamalıydı"
    cmp -s "$ACFG/ace_K3.cfg.new" "$FAKE_ACE/config/ace_K3.cfg" || fail "$2: ace_K3.cfg.new sürücü varsayılanlarını içermeli"
    [ "$(tr -d ' \t\r\n' < "$AR/useremain/home/rinkhals/open-kobra/ace-driver.version")" = "9.9.9-test" ] || fail "$2: ace-driver.version yanlış"
    LEFT_ACE_TMP=$(find "$AR/useremain" "$AR/userdata" -name '*.openkobra.*' | head -n 1 || true)
    [ -z "$LEFT_ACE_TMP" ] || fail "$2: geçici dosya kalmış: $LEFT_ACE_TMP"
}

ace_check_restored() {
    AR="$1"
    diff -r "$AR/useremain/home/rinkhals/apps/vanilla-klipper" "$AR.snap/vanilla-klipper" > "$WORK/ace_diff.txt" 2>&1 || fail "$2: vanilla-klipper dizini bayt bayt geri gelmedi: $(cat "$WORK/ace_diff.txt")"
    cmp -s "$AR/useremain/home/rinkhals/printer_data/config/ace_K3.cfg" "$AR.snap/ace_K3.live.cfg" || fail "$2: canlı ace_K3.cfg değişmiş"
    [ ! -e "$AR/useremain/home/rinkhals/printer_data/config/ace_K3.cfg.new" ] || fail "$2: ace_K3.cfg.new temizlenmedi"
    [ ! -e "$AR/useremain/home/rinkhals/open-kobra" ] || fail "$2: open-kobra durum dizini kalmış"
    LEFT_ACE_TMP=$(find "$AR/useremain" -name '*.openkobra.*' | head -n 1 || true)
    [ -z "$LEFT_ACE_TMP" ] || fail "$2: geçici dosya kalmış: $LEFT_ACE_TMP"
}

# 20a. install -> reinstall -> uninstall: originals come back byte-for-byte
ACE_R="$WORK/ace_root"
ace_make_root "$ACE_R"
run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_R" > "$WORK/ace_install1.log" || fail "ACE sürücülü kurulum başarısız"
ace_check_installed "$ACE_R" "kurulum"
has_lit "$ACE_R/useremain/rinkhals/open-kobra-install.log" "yeniden başlatın" || fail "Kurulum günlüğü Klipper yeniden başlatma notunu içermeli"
has_lit "$ACE_R/useremain/rinkhals/open-kobra-install.log" "kendisi yeniden başlatmaz" || fail "Kurulum günlüğü paketin Klipper'ı zorla başlatmadığını söylemeli"
[ -f "$ACE_R/useremain/bak/open-kobra-baseline/ace-orig.md5" ] || fail "Baseline ACE orijinal MD5 manifesti yok"
has_lit "$ACE_R/useremain/bak/open-kobra-baseline/ace-orig.md5" "klippy/extras/ace/vanilla_only.py" || fail "ACE MD5 manifesti orijinal dosyaları içermiyor"
run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_R" > /dev/null || fail "ACE sürücülü yeniden kurulum başarısız"
ace_check_installed "$ACE_R" "yeniden kurulum"
[ -f "$ACE_R/useremain/bak/open-kobra-baseline/files/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/ace/vanilla_only.py" ] || fail "Yeniden kurulum baseline'daki vanilla orijinalini ezmemeli"
run_swu_on_fake_root "$UNINSTALL_SWU" "$ACE_R" > "$WORK/ace_uninstall1.log" || fail "ACE sürücülü kaldırma başarısız"
ace_check_restored "$ACE_R" "kaldırma"
has_lit "$WORK/ace_uninstall1.log" "yeniden başlatın" || fail "Kaldırma günlüğü Klipper yeniden başlatma notunu içermeli"

# 20b. baseline taken by a package WITHOUT the ACE step: the vanilla driver must still come back (never be deleted)
ACE_OLDBASE="$WORK/ace_oldbase_root"
ace_make_root "$ACE_OLDBASE"
run_swu_on_fake_root "$INSTALL_SWU" "$ACE_OLDBASE" > /dev/null || fail "Sürücüsüz kurulum başarısız"
[ ! -f "$ACE_OLDBASE/useremain/bak/open-kobra-baseline/ace-orig.md5" ] || fail "Sürücüsüz kurulum ACE manifesti üretmemeli"
run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_OLDBASE" > /dev/null || fail "Eski baseline üstüne ACE sürücülü kurulum başarısız"
ace_check_installed "$ACE_OLDBASE" "eski baseline kurulum"
[ -f "$ACE_OLDBASE/useremain/bak/open-kobra-baseline/ace-orig.md5" ] || fail "Eski baseline ACE orijinalleriyle genişletilmedi"
run_swu_on_fake_root "$UNINSTALL_SWU" "$ACE_OLDBASE" > /dev/null || fail "Eski baseline ile kaldırma başarısız"
ace_check_restored "$ACE_OLDBASE" "eski baseline kaldırma"

# 20c. injected failure inside the ACE step: full rollback, originals intact
ACE_RB="$WORK/ace_rollback_root"
ace_make_root "$ACE_RB"
if OPEN_KOBRA_FAIL_AT=ace_driver run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_RB" >/dev/null 2>&1; then
    fail "ace_driver adımında simüle edilen hata başarısız olmalıydı"
fi
diff -r "$ACE_RB/useremain/home/rinkhals/apps/vanilla-klipper" "$ACE_RB.snap/vanilla-klipper" > "$WORK/ace_diff.txt" 2>&1 || fail "Geri alma sonrası vanilla-klipper değişmiş: $(cat "$WORK/ace_diff.txt")"
cmp -s "$ACE_RB/useremain/home/rinkhals/printer_data/config/ace_K3.cfg" "$ACE_RB.snap/ace_K3.live.cfg" || fail "Geri alma sonrası canlı ace_K3.cfg değişmiş"
[ ! -e "$ACE_RB/useremain/home/rinkhals/printer_data/config/ace_K3.cfg.new" ] || fail "Geri alma ace_K3.cfg.new bırakmamalıydı"

# 20d. power cut inside the ACE step (SIGKILL): reinstall converges, uninstall still restores the originals
for ACE_STAGE in after_ace_dir after_ace_temp after_ace_cfg; do
    ACE_PWR="$WORK/ace_power_$ACE_STAGE"
    ace_make_root "$ACE_PWR"
    if OPEN_KOBRA_CRASH_AFTER="$ACE_STAGE" run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_PWR" >/dev/null 2>&1; then
        fail "ACE kesinti benzetimi ($ACE_STAGE) başarısız dönmeliydi"
    fi
    run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_PWR" >/dev/null || fail "$ACE_STAGE: kesinti sonrası yeniden kurulum başarısız"
    ace_check_installed "$ACE_PWR" "$ACE_STAGE kesinti sonrası"
    run_swu_on_fake_root "$UNINSTALL_SWU" "$ACE_PWR" >/dev/null || fail "$ACE_STAGE: kesinti sonrası kaldırma başarısız"
    ace_check_restored "$ACE_PWR" "$ACE_STAGE kesinti sonrası kaldırma"
done

# 20e. damaged baseline copy of the vanilla driver: uninstall must refuse BEFORE touching anything
ACE_BAD="$WORK/ace_badbase_root"
ace_make_root "$ACE_BAD"
run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_BAD" >/dev/null || fail "Bozuk baseline testi için kurulum başarısız"
printf 'CORRUPTED' > "$ACE_BAD/useremain/bak/open-kobra-baseline/files/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/ace/vanilla_only.py"
cp "$ACE_BAD/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/ace_bad_before.cfg"
if run_swu_on_fake_root "$UNINSTALL_SWU" "$ACE_BAD" >/dev/null 2>&1; then
    fail "Bozuk ACE orijinal yedeğiyle kaldırma reddedilmeliydi"
fi
cmp -s "$ACE_BAD/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/ace/manager.py" "$FAKE_ACE/klippy/extras/ace/manager.py" || fail "Reddedilen kaldırma sürücü dizinine dokunmamalıydı"
cmp -s "$ACE_BAD/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "$WORK/ace_bad_before.cfg" || fail "Reddedilen kaldırma printer.klipper.cfg'ye dokunmamalıydı"
[ -d "$ACE_BAD/useremain/home/rinkhals/apps/kobra-screen" ] || fail "Reddedilen kaldırma uygulamaları silmemeliydi"

# 20f. vanilla-klipper only as the built-in Rinkhals copy: uninstall allow-list must accept the ACE targets
ACE_BI="$WORK/ace_builtin_root"
ace_make_root "$ACE_BI"
BI_VK="$ACE_BI/useremain/rinkhals/20250409_01/home/rinkhals/apps/vanilla-klipper"
mkdir -p "$BI_VK/klippy"
cp -PRp "$ACE_BI/useremain/home/rinkhals/apps/vanilla-klipper"/. "$BI_VK"/
rm -rf "$ACE_BI/useremain/home/rinkhals/apps/vanilla-klipper"
rm -rf "$ACE_BI.snap"
mkdir -p "$ACE_BI.snap"
cp -PRp "$BI_VK" "$ACE_BI.snap/vanilla-klipper"
cp -PRp "$ACE_BI/useremain/home/rinkhals/printer_data/config/ace_K3.cfg" "$ACE_BI.snap/ace_K3.live.cfg"
run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_BI" >/dev/null || fail "Yerleşik vanilla-klipper ile ACE kurulumu başarısız"
cmp -s "$BI_VK/klippy/extras/ace/manager.py" "$FAKE_ACE/klippy/extras/ace/manager.py" || fail "Yerleşik vanilla-klipper'a ACE sürücüsü kopyalanmadı"
run_swu_on_fake_root "$UNINSTALL_SWU" "$ACE_BI" >/dev/null || fail "Yerleşik vanilla-klipper ile ACE kaldırma başarısız"
diff -r "$BI_VK" "$ACE_BI.snap/vanilla-klipper" > "$WORK/ace_diff.txt" 2>&1 || fail "Yerleşik vanilla-klipper bayt bayt geri gelmedi: $(cat "$WORK/ace_diff.txt")"

# 20g. no ace-driver dir: build succeeds without it; incomplete/broken tree: build fails unless OPEN_KOBRA_ALLOW_NO_ACE_DRIVER=1 (I-04, tested in 21b)
NO_ACE_LOG="$WORK/build_no_ace.log"
OPEN_KOBRA_ACE_DRIVER_SRC="$WORK/definitely_missing_ace_driver" OPEN_KOBRA_DIST_DIR="$WORK/dist_none" "$PROJECT_ROOT/build.sh" > "$NO_ACE_LOG" 2>&1 || fail "ace-driver yokken derleme başarısız oldu"
has_lit "$NO_ACE_LOG" "atlandı" || fail "ace-driver yokken derleme atlama bilgisini vermedi"
extract_swu "$WORK/dist_none/install/aGVscF9zb3Nf/update.swu" "$WORK/unpack_none"
[ ! -e "$WORK/unpack_none/update_swu/ace-driver" ] || fail "ace-driver yokken pakette ace-driver dizini olmamalı"

INC_ACE="$WORK/incomplete_ace_driver"
mkdir -p "$INC_ACE/klippy/extras/ace"
printf 'X = 1\n' > "$INC_ACE/klippy/extras/ace/__init__.py"
OPEN_KOBRA_ALLOW_NO_ACE_DRIVER=1 OPEN_KOBRA_ACE_DRIVER_SRC="$INC_ACE" OPEN_KOBRA_DIST_DIR="$WORK/dist_inc" "$PROJECT_ROOT/build.sh" > "$WORK/build_inc.log" 2>&1 || fail "İzinli eksik ace-driver ile derleme başarısız oldu"
has_lit "$WORK/build_inc.log" "eksik" || fail "Eksik ace-driver uyarısı verilmedi"
extract_swu "$WORK/dist_inc/install/aGVscF9zb3Nf/update.swu" "$WORK/unpack_inc"
[ ! -e "$WORK/unpack_inc/update_swu/ace-driver" ] || fail "Eksik ace-driver pakete girmemeli"

BRK_ACE="$WORK/broken_ace_driver"
mkdir -p "$BRK_ACE/klippy/extras/ace"
printf 'def broken(:\n' > "$BRK_ACE/klippy/extras/ace/__init__.py"
printf '# t\n' > "$BRK_ACE/klippy/extras/temperature_ace.py"
OPEN_KOBRA_ALLOW_NO_ACE_DRIVER=1 OPEN_KOBRA_ACE_DRIVER_SRC="$BRK_ACE" OPEN_KOBRA_DIST_DIR="$WORK/dist_brk" "$PROJECT_ROOT/build.sh" > "$WORK/build_brk.log" 2>&1 || fail "İzinli sözdizimi hatalı ace-driver ile derleme başarısız oldu"
has_lit "$WORK/build_brk.log" "sözdizimi" || fail "Sözdizimi hatalı ace-driver uyarısı verilmedi"
extract_swu "$WORK/dist_brk/install/aGVscF9zb3Nf/update.swu" "$WORK/unpack_brk"
[ ! -e "$WORK/unpack_brk/update_swu/ace-driver" ] || fail "Sözdizimi hatalı ace-driver pakete girmemeli"

# driver-less package installs fine, logs the skip and leaves the vanilla ACE driver alone
ACE_NONE="$WORK/ace_none_root"
ace_make_root "$ACE_NONE"
run_swu_on_fake_root "$WORK/dist_none/install/aGVscF9zb3Nf/update.swu" "$ACE_NONE" >/dev/null || fail "Sürücüsüz paketle kurulum başarısız"
has_lit "$ACE_NONE/useremain/rinkhals/open-kobra-install.log" "ACE sürücü paketi yok" || fail "Sürücüsüz kurulum günlüğü atlama bilgisini vermedi"
diff -r "$ACE_NONE/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/ace" "$ACE_NONE.snap/vanilla-klipper/klippy/extras/ace" >/dev/null 2>&1 || fail "Sürücüsüz kurulum vanilla ACE sürücüsüne dokunmamalıydı"
cmp -s "$ACE_NONE/useremain/home/rinkhals/apps/vanilla-klipper/ace_K3.cfg" "$ACE_NONE.snap/vanilla-klipper/ace_K3.cfg" || fail "Sürücüsüz kurulum ace_K3.cfg şablonuna dokunmamalıydı"
[ ! -e "$ACE_NONE/useremain/home/rinkhals/open-kobra/ace-driver.version" ] || fail "Sürücüsüz kurulum ace-driver.version yazmamalı"

pass "ACE sürücüsü: kurulum, yeniden kurulum, kaldırma (bayt bayt), eski baseline, geri alma, kesinti, bozuk yedek, yerleşik klipper ve sürücüsüz paket testleri geçti"

echo "21. start.sh koruması (B-05): bozuk start.sh atomik onarılır, meşru değişikliğe dokunulmaz, çalışan betik etkilenmez..."
RT_BODY="$WORK/rt_stock_body"
cat > "$RT_BODY" <<'EOF'
echo STOCK_LINE_1 >> "${HOOK_LOG:?}"
sleep 0
echo STOCK_LINE_2 >> "$HOOK_LOG"
if [ -n "$HOOK_LOG" ]; then
    echo STOCK_LINE_3 >> "$HOOK_LOG"
fi
echo STOCK_LINE_4 >> "$HOOK_LOG"
./gklib -a /tmp/unix_uds1 printer.cfg &
./gkapi &
./K3SysUi &
EOF
RT_PATCH="$WORK/rt_rinkhals_patch"
printf '\n# Rinkhals/begin\nif [ -f /nonexistent/start-rinkhals.sh ]; then\n    echo Rinkhals is installed, starting...\nfi\n# Rinkhals/end\n' > "$RT_PATCH"

rt_make_root() {
    RTR="$1"
    RTWITH="$2"
    RTFW="${3:-9.9.9.9}"
    RTBODYF="${4:-$RT_BODY}"
    make_fake_root "$RTR"
    mkdir -p "$RTR/useremain/dev" "$RTR/userdata/app/gk"
    printf '%s\n' "$RTFW" > "$RTR/useremain/dev/version"
    cp "$RTBODYF" "$RTR/userdata/app/gk/start.sh"
    if [ "$RTWITH" = "1" ]; then
        cat "$RT_PATCH" >> "$RTR/userdata/app/gk/start.sh"
    fi
    chmod +x "$RTR/userdata/app/gk/start.sh"
    for RTB in gkapi K3SysUi gklib gkcam; do
        printf 'STOCK_VALID_%s\n' "$RTB" > "$RTR/userdata/app/gk/$RTB"
        chmod +x "$RTR/userdata/app/gk/$RTB"
    done
    PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$RTR" >/dev/null || fail "start.sh koruma testi için kurulum başarısız ($RTR)"
}

rt_boot() {
    RT_RC=0
    PATH="$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$1" OPEN_KOBRA_RESCUE_NO_WATCH=1 OPEN_KOBRA_RESCUE_START_STABLE_DELAY=0 \
        sh "$1/useremain/open-kobra/rescue/rescue.sh" boot || RT_RC=$?
}

# Fresh install with and without a Rinkhals block already in start.sh
RT_RK="$WORK/rt_with_rinkhals"
RT_NORK="$WORK/rt_without_rinkhals"
rt_make_root "$RT_RK" 1
rt_make_root "$RT_NORK" 0
for RTX in "$RT_RK" "$RT_NORK"; do
    RTRES="$RTX/useremain/open-kobra/rescue"
    [ -f "$RTRES/hook.block" ] || fail "hook.block kurulmadı: $RTX"
    [ "$(tr -d ' \t\r\n' < "$RTRES/start.sh.orig.fw")" = "9.9.9.9" ] || fail "start.sh.orig.fw yazılmadı: $RTX"
    cat "$RTRES/hook.block" "$RTRES/start.sh.orig" > "$WORK/rt_expected"
    cmp -s "$WORK/rt_expected" "$RTX/userdata/app/gk/start.sh" || fail "Kurulu start.sh = hook.block + start.sh.orig olmalı: $RTX"
    [ "$(head -n 1 "$RTRES/hook.block")" = "# open-kobra-rescue/begin" ] || fail "hook.block ilk satırı yanlış"
    [ "$(tail -n 1 "$RTRES/hook.block")" = "# open-kobra-rescue/end" ] || fail "hook.block son satırı yanlış"
done
# second install keeps the invariant and does not touch start.sh.orig / .fw
cp "$RT_RK/useremain/open-kobra/rescue/start.sh.orig" "$WORK/rt_orig_before"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$RT_RK" >/dev/null || fail "start.sh koruma: ikinci kurulum başarısız"
cmp -s "$WORK/rt_orig_before" "$RT_RK/useremain/open-kobra/rescue/start.sh.orig" || fail "İkinci kurulum start.sh.orig'i değiştirdi"
cat "$RT_RK/useremain/open-kobra/rescue/hook.block" "$RT_RK/useremain/open-kobra/rescue/start.sh.orig" | cmp -s - "$RT_RK/userdata/app/gk/start.sh" || fail "İkinci kurulum sonrası start.sh = hook.block + orig bozuldu"

# Pristine file: nothing happens
rt_boot "$RT_RK"
[ "$RT_RC" = "0" ] || fail "Sağlam start.sh'ta boot çıkış kodu 0 olmalı (gelen: $RT_RC)"
[ ! -e "$RT_RK/useremain/open-kobra/rescue/start.sh.bad" ] || fail "Sağlam start.sh için .bad kopyası oluşmamalı"
has_lit "$RT_RK/useremain/open-kobra/rescue/rescue.log" "start.sh beklenen icerikle ayni" || fail "Sağlam start.sh günlüğü yok"

# --- Broken variants are restored atomically ---------------------------------------------------------------------
rt_expect_restored() {
    RTX="$1"
    RTLABEL="$2"
    RTRES="$RTX/useremain/open-kobra/rescue"
    cat "$RTRES/hook.block" "$RTRES/start.sh.orig" > "$WORK/rt_expected"
    cp "$RTX/userdata/app/gk/start.sh" "$WORK/rt_broken_copy" 2>/dev/null || : > "$WORK/rt_broken_copy"
    rt_boot "$RTX"
    [ "$RT_RC" = "75" ] || fail "$RTLABEL: start.sh değiştirildiğinde boot çıkış kodu 75 olmalı (gelen: $RT_RC)"
    cmp -s "$WORK/rt_expected" "$RTX/userdata/app/gk/start.sh" || fail "$RTLABEL: start.sh beklenen içeriğe (hook.block + orig) dönmedi"
    [ -x "$RTX/userdata/app/gk/start.sh" ] || fail "$RTLABEL: onarılan start.sh çalıştırılabilir (+x) olmalı"
    has_lit "$RTRES/rescue.log" "ONARILDI: start.sh beklenen icerikten geri yuklendi" || fail "$RTLABEL: ONARILDI günlüğü yok"
    LEFT_RT=$(find "$RTX/userdata" "$RTRES" -name '*.openkobra.*' -o -name '.start.sh.expected.*' -o -name '.core.*' -o -name '.hook.extract.*' | head -n 1 || true)
    [ -z "$LEFT_RT" ] || fail "$RTLABEL: geçici dosya kalmış: $LEFT_RT"
    if [ -s "$WORK/rt_broken_copy" ]; then
        cmp -s "$WORK/rt_broken_copy" "$RTRES/start.sh.bad" || fail "$RTLABEL: bozuk kopya start.sh.bad olarak saklanmadı"
    fi
    rt_boot "$RTX"
    [ "$RT_RC" = "0" ] || fail "$RTLABEL: onarım sonrası ikinci boot 0 dönmeli (gelen: $RT_RC)"
}

rt_variant() {
    # $1 = label; $2 = root to copy; remaining stdin = new start.sh content
    RTV="$WORK/rt_v_$1"
    rm -rf "$RTV"
    cp -PRp "$2" "$RTV"
    cat > "$RTV/userdata/app/gk/start.sh"
    chmod +x "$RTV/userdata/app/gk/start.sh"
}

HOOKB="$RT_RK/useremain/open-kobra/rescue/hook.block"

# empty file
rt_variant empty "$RT_RK" < /dev/null
rt_expect_restored "$WORK/rt_v_empty" "boş start.sh"
# truncated at a line boundary inside the stock part (still valid shell syntax)
{ cat "$HOOKB"; head -n 2 "$RT_BODY"; } | rt_variant trunc_line "$RT_RK"
rt_expect_restored "$WORK/rt_v_trunc_line" "satır sınırında kesik start.sh"
# truncated inside the if-block (syntax error)
{ cat "$HOOKB"; head -n 4 "$RT_BODY"; } | rt_variant trunc_if "$RT_RK"
rt_expect_restored "$WORK/rt_v_trunc_if" "if bloğu içinde kesik start.sh"
# truncated mid-line without trailing newline
{ cat "$HOOKB"; head -n 1 "$RT_BODY"; printf 'sleep 0\necho STOCK_LI'; } | rt_variant trunc_mid "$RT_RK"
rt_expect_restored "$WORK/rt_v_trunc_mid" "satır ortasında kesik start.sh"
# only the hook survived
cat "$HOOKB" | rt_variant hook_only "$RT_RK"
rt_expect_restored "$WORK/rt_v_hook_only" "yalnız kancası kalan start.sh"
# Rinkhals block started but never finished (interrupted append)
{ cat "$HOOKB" "$RT_BODY"; printf '\n# Rinkhals/begin\nif [ -f /nonexistent/start-rinkhals.sh ]; then\n    echo hi\n'; } | rt_variant rk_half "$RT_NORK"
rt_expect_restored "$WORK/rt_v_rk_half" "yarım Rinkhals bloğu"
# Rinkhals block cut after its last 'fi' (end marker missing)
{ cat "$HOOKB" "$RT_BODY"; printf '\n# Rinkhals/begin\nif [ -f /nonexistent/start-rinkhals.sh ]; then\n    echo hi\nfi\n'; } | rt_variant rk_noend "$RT_NORK"
rt_expect_restored "$WORK/rt_v_rk_noend" "end işaretsiz Rinkhals bloğu"
# CRLF line endings
{ cat "$HOOKB" "$RT_BODY"; } | sed 's/$/\r/' | rt_variant crlf "$RT_NORK"
rt_expect_restored "$WORK/rt_v_crlf" "CRLF start.sh"
# file removed entirely
rm -rf "$WORK/rt_v_gone"
cp -PRp "$RT_RK" "$WORK/rt_v_gone"
rm -f "$WORK/rt_v_gone/userdata/app/gk/start.sh"
rt_expect_restored "$WORK/rt_v_gone" "silinmiş start.sh"
# legacy install without hook.block: the hook found inside start.sh is used
rm -rf "$WORK/rt_v_legacy"
cp -PRp "$RT_RK" "$WORK/rt_v_legacy"
rm -f "$WORK/rt_v_legacy/useremain/open-kobra/rescue/hook.block"
cp "$HOOKB" "$WORK/rt_hook_legacy_copy"
{ cat "$HOOKB"; head -n 2 "$RT_BODY"; } > "$WORK/rt_v_legacy/userdata/app/gk/start.sh"
RT_RC=0
PATH="$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$WORK/rt_v_legacy" OPEN_KOBRA_RESCUE_NO_WATCH=1 OPEN_KOBRA_RESCUE_START_STABLE_DELAY=0 \
    sh "$WORK/rt_v_legacy/useremain/open-kobra/rescue/rescue.sh" boot || RT_RC=$?
[ "$RT_RC" = "75" ] || fail "hook.block'suz eski kurulum: onarım çıkış kodu 75 olmalı ($RT_RC)"
cat "$WORK/rt_hook_legacy_copy" "$WORK/rt_v_legacy/useremain/open-kobra/rescue/start.sh.orig" | cmp -s - "$WORK/rt_v_legacy/userdata/app/gk/start.sh" || fail "hook.block'suz eski kurulum: start.sh içindeki kanca ile geri yükleme yapılmadı"

# --- Legitimate changes are left alone --------------------------------------------------------------------------
rt_expect_untouched() {
    RTX="$1"
    RTLABEL="$2"
    RTEXPECT_LOG="$3"
    cp "$RTX/userdata/app/gk/start.sh" "$WORK/rt_before"
    rt_boot "$RTX"
    [ "$RT_RC" = "0" ] || fail "$RTLABEL: dokunulmaması gereken start.sh için boot çıkış kodu 0 olmalı (gelen: $RT_RC)"
    cmp -s "$WORK/rt_before" "$RTX/userdata/app/gk/start.sh" || fail "$RTLABEL: start.sh'a dokunulmamalıydı"
    [ ! -e "$RTX/useremain/open-kobra/rescue/start.sh.bad" ] || fail "$RTLABEL: .bad oluşmamalıydı"
    has_lit "$RTX/useremain/open-kobra/rescue/rescue.log" "$RTEXPECT_LOG" || fail "$RTLABEL: günlükte '$RTEXPECT_LOG' yok"
}

# Rinkhals installed AFTER open-kobra (block appended, orig has none)
rm -rf "$WORK/rt_v_rk_added"
cp -PRp "$RT_NORK" "$WORK/rt_v_rk_added"
cat "$RT_PATCH" >> "$WORK/rt_v_rk_added/userdata/app/gk/start.sh"
rt_expect_untouched "$WORK/rt_v_rk_added" "sonradan eklenen meşru Rinkhals bloğu" "bozuk gorunmuyor"
# Rinkhals removed by its own uninstaller (orig HAS the block; sed leaves a blank line behind)
rm -rf "$WORK/rt_v_rk_removed"
cp -PRp "$RT_RK" "$WORK/rt_v_rk_removed"
awk '/# Rinkhals\/begin/ { skip = 1 } !skip { print } /# Rinkhals\/end/ { skip = 0 }' "$RT_RK/userdata/app/gk/start.sh" > "$WORK/rt_v_rk_removed/userdata/app/gk/start.sh"
rt_expect_untouched "$WORK/rt_v_rk_removed" "Rinkhals kaldırıldı (orig'de blok var)" "bozuk gorunmuyor"
# Rinkhals block with different content (new Rinkhals version)
rm -rf "$WORK/rt_v_rk_changed"
cp -PRp "$RT_RK" "$WORK/rt_v_rk_changed"
{ cat "$HOOKB" "$RT_BODY"; printf '\n# Rinkhals/begin\nif [ -f /nonexistent/other.sh ]; then\n    echo newer loader\nfi\n# Rinkhals/end\n'; } > "$WORK/rt_v_rk_changed/userdata/app/gk/start.sh"
rt_expect_untouched "$WORK/rt_v_rk_changed" "değişmiş Rinkhals bloğu" "bozuk gorunmuyor"
# manual edit that keeps the file valid
rm -rf "$WORK/rt_v_edit"
cp -PRp "$RT_NORK" "$WORK/rt_v_edit"
{ cat "$HOOKB"; sed 's/STOCK_LINE_2/STOCK_LINE_2_EDITED/' "$RT_BODY"; printf 'echo EXTRA >> "$HOOK_LOG"\n'; } > "$WORK/rt_v_edit/userdata/app/gk/start.sh"
rt_expect_untouched "$WORK/rt_v_edit" "elle düzenlenmiş geçerli start.sh" "bozuk gorunmuyor"
# harmless partial comment fragment at EOF (cannot break boot): not treated as broken
{ cat "$HOOKB" "$RT_BODY"; printf '# Rinkha'; } > "$WORK/rt_v_frag_src"
rm -rf "$WORK/rt_v_frag"
cp -PRp "$RT_NORK" "$WORK/rt_v_frag"
cp "$WORK/rt_v_frag_src" "$WORK/rt_v_frag/userdata/app/gk/start.sh"
rt_expect_untouched "$WORK/rt_v_frag" "zararsız yorum parçası" "bozuk gorunmuyor"

# --- Valid start.sh whose hook disappeared: hook is put back, everything else preserved byte-for-byte -------------
rm -rf "$WORK/rt_v_nohook"
cp -PRp "$RT_NORK" "$WORK/rt_v_nohook"
{ cat "$RT_BODY"; cat "$RT_PATCH"; } > "$WORK/rt_v_nohook/userdata/app/gk/start.sh"
cp "$WORK/rt_v_nohook/userdata/app/gk/start.sh" "$WORK/rt_nohook_before"
rt_boot "$WORK/rt_v_nohook"
cat "$WORK/rt_v_nohook/useremain/open-kobra/rescue/hook.block" "$WORK/rt_nohook_before" | cmp -s - "$WORK/rt_v_nohook/userdata/app/gk/start.sh" || fail "Kancası kaybolan geçerli start.sh: kanca en başa eklenmeli, kalan içerik aynı kalmalı"
has_lit "$WORK/rt_v_nohook/useremain/open-kobra/rescue/rescue.log" "kanca en basa yeniden eklendi" || fail "Kanca yeniden ekleme günlüğü yok"

# --- Gates: nothing is touched when the safety conditions are not met ---------------------------------------------
rt_gate_case() {
    # $1 label, $2 root; start.sh is set to an empty (broken) file and must STAY empty
    : > "$2/userdata/app/gk/start.sh"
    chmod +x "$2/userdata/app/gk/start.sh"
    rt_boot "$2"
    [ "$RT_RC" = "0" ] || fail "$1: boot çıkış kodu 0 olmalı ($RT_RC)"
    [ ! -s "$2/userdata/app/gk/start.sh" ] || fail "$1: kapıya takılan durumda start.sh'a dokunulmamalıydı"
}
rm -rf "$WORK/rt_g_fw"; cp -PRp "$RT_RK" "$WORK/rt_g_fw"; printf '2.4.9.9\n' > "$WORK/rt_g_fw/useremain/dev/version"
rt_gate_case "firmware sürümü farklı" "$WORK/rt_g_fw"
rm -rf "$WORK/rt_g_orig"; cp -PRp "$RT_RK" "$WORK/rt_g_orig"; rm -f "$WORK/rt_g_orig/useremain/open-kobra/rescue/start.sh.orig"
rt_gate_case "start.sh.orig yok" "$WORK/rt_g_orig"
rm -rf "$WORK/rt_g_off"; cp -PRp "$RT_RK" "$WORK/rt_g_off"; : > "$WORK/rt_g_off/useremain/open-kobra/rescue/start_repair.disabled"
rt_gate_case "start_repair.disabled işareti" "$WORK/rt_g_off"
rm -rf "$WORK/rt_g_badorig"; cp -PRp "$RT_RK" "$WORK/rt_g_badorig"; printf 'if [ broken\n' > "$WORK/rt_g_badorig/useremain/open-kobra/rescue/start.sh.orig"
rt_gate_case "bozuk start.sh.orig" "$WORK/rt_g_badorig"
rm -rf "$WORK/rt_g_nofwfile"; cp -PRp "$RT_RK" "$WORK/rt_g_nofwfile"; rm -f "$WORK/rt_g_nofwfile/useremain/open-kobra/rescue/start.sh.orig.fw"
rm -rf "$WORK/rt_g_nofwfile/useremain/open-kobra/rescue/stock"
rt_gate_case "sürüm eşleşmesi kanıtlanamıyor" "$WORK/rt_g_nofwfile"
rm -rf "$WORK/rt_g_nohookblock"; cp -PRp "$RT_RK" "$WORK/rt_g_nohookblock"; rm -f "$WORK/rt_g_nohookblock/useremain/open-kobra/rescue/hook.block"
rt_gate_case "kanca blogu hiçbir yerde yok" "$WORK/rt_g_nohookblock"
# legacy install (no .fw) falls back to the verified stock copy of the current firmware: repair works
RT_REAL="$WORK/rt_real"
rt_make_root "$RT_REAL" 1 2.4.6.7 "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh"
rm -rf "$WORK/rt_g_legacyfw"; cp -PRp "$RT_REAL" "$WORK/rt_g_legacyfw"; rm -f "$WORK/rt_g_legacyfw/useremain/open-kobra/rescue/start.sh.orig.fw"
cat "$WORK/rt_g_legacyfw/useremain/open-kobra/rescue/hook.block" "$WORK/rt_g_legacyfw/useremain/open-kobra/rescue/start.sh.orig" > "$WORK/rt_expected"
: > "$WORK/rt_g_legacyfw/userdata/app/gk/start.sh"
rt_boot "$WORK/rt_g_legacyfw"
cmp -s "$WORK/rt_expected" "$WORK/rt_g_legacyfw/userdata/app/gk/start.sh" || fail ".fw dosyası olmayan eski kurulum doğrulanmış stok kopyayla onarılmalı"
# symlink is never replaced
rm -rf "$WORK/rt_g_link"; cp -PRp "$RT_RK" "$WORK/rt_g_link"
rm -f "$WORK/rt_g_link/userdata/app/gk/start.sh"
: > "$WORK/rt_link_target"
ln -s "$WORK/rt_link_target" "$WORK/rt_g_link/userdata/app/gk/start.sh"
rt_boot "$WORK/rt_g_link"
[ -L "$WORK/rt_g_link/userdata/app/gk/start.sh" ] || fail "Sembolik bağ olan start.sh değiştirilmemeliydi"
# an unwritable rescue.log / dir never makes boot fail
rm -rf "$WORK/rt_g_ro"; cp -PRp "$RT_RK" "$WORK/rt_g_ro"
: > "$WORK/rt_g_ro/userdata/app/gk/start.sh"
rt_boot "$WORK/rt_g_ro"
[ "$RT_RC" = "75" ] || fail "Normal onarım çıkış kodu 75 olmalı"

# --- Running the real hook: broken start.sh is repaired AND this very boot continues on the repaired file ----------
rt_run_start() {
    # $1 root, $2 log file
    : > "$2"
    (cd "$STUBS" && PATH="$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$1" OPEN_KOBRA_RESCUE_NO_WATCH=1 OPEN_KOBRA_RESCUE_START_STABLE_DELAY=0 HOOK_LOG="$2" \
        sh "$1/userdata/app/gk/start.sh") || fail "start.sh çalıştırması başarısız oldu"
}
printf 'STOCK_LINE_1\nSTOCK_LINE_2\nSTOCK_LINE_3\nSTOCK_LINE_4\n' > "$WORK/rt_all_lines"
# pristine: stock lines run exactly once (no re-exec)
rm -rf "$WORK/rt_x_ok"; cp -PRp "$RT_RK" "$WORK/rt_x_ok"
rt_run_start "$WORK/rt_x_ok" "$WORK/rt_x_ok.log"
cmp -s "$WORK/rt_all_lines" "$WORK/rt_x_ok.log" || fail "Sağlam start.sh stok satırları tam bir kez çalıştırmalıydı: $(cat "$WORK/rt_x_ok.log")"
# broken: the old inode is not fully usable, hook restarts once on the repaired file
for RTCASE in trunc_if empty; do
    rm -rf "$WORK/rt_x_$RTCASE"
    cp -PRp "$WORK/rt_v_$RTCASE" "$WORK/rt_x_$RTCASE"
    # rt_v_* were repaired by the earlier boot tests: break them again
    case "$RTCASE" in
        trunc_if) { cat "$HOOKB"; head -n 4 "$RT_BODY"; } > "$WORK/rt_x_$RTCASE/userdata/app/gk/start.sh" ;;
        empty) { cat "$HOOKB"; } > "$WORK/rt_x_$RTCASE/userdata/app/gk/start.sh" ;;
    esac
    rt_run_start "$WORK/rt_x_$RTCASE" "$WORK/rt_x_$RTCASE.log"
    cat "$WORK/rt_x_$RTCASE/useremain/open-kobra/rescue/hook.block" "$WORK/rt_x_$RTCASE/useremain/open-kobra/rescue/start.sh.orig" | cmp -s - "$WORK/rt_x_$RTCASE/userdata/app/gk/start.sh" || fail "$RTCASE: start.sh kanca üzerinden onarılmadı"
    # trunc_if has STOCK_LINE_1..2 already run by the old shell before it hit the broken part? No: the hook is first,
    # the repair happens before any stock line runs, so the repaired file runs each line exactly once.
    cmp -s "$WORK/rt_all_lines" "$WORK/rt_x_$RTCASE.log" || fail "$RTCASE: onarım sonrası aynı açılış onarılmış dosyayla tam bir kez ilerlemeli: $(cat "$WORK/rt_x_$RTCASE.log")"
done
# the re-exec guard stops loops even if rescue.sh keeps reporting a replacement
rm -rf "$WORK/rt_x_loop"; cp -PRp "$RT_RK" "$WORK/rt_x_loop"
cat > "$WORK/rt_x_loop/useremain/open-kobra/rescue/rescue.sh" <<'EOF'
#!/bin/sh
echo run >> "${HOOK_LOG:?}.rescue"
exit 75
EOF
: > "$WORK/rt_x_loop.log.rescue"
rt_run_start "$WORK/rt_x_loop" "$WORK/rt_x_loop.log"
[ "$(wc -l < "$WORK/rt_x_loop.log.rescue" | tr -d ' ')" = "2" ] || fail "Yeniden exec en çok bir kez olmalı (rescue.sh 2 kez çalışmalı): $(cat "$WORK/rt_x_loop.log.rescue")"
cmp -s "$WORK/rt_all_lines" "$WORK/rt_x_loop.log" || fail "Döngü koruması: stok satırları bir kez çalışmalı"
# --- Why mv: a shell that is still reading start.sh keeps its old inode when the file is replaced by rename --------
MV_DIR="$WORK/mv_inode"
mkdir -p "$MV_DIR"
printf 'echo NEW >> "$MV_LOG"\n' > "$MV_DIR/new.sh"
cat > "$MV_DIR/running.sh" <<'EOF'
echo A >> "$MV_LOG"
mv "$MV_DIR/new.sh" "$MV_DIR/running.sh"
echo B >> "$MV_LOG"
echo C >> "$MV_LOG"
EOF
: > "$MV_DIR/log"
MV_DIR="$MV_DIR" MV_LOG="$MV_DIR/log" sh "$MV_DIR/running.sh"
printf 'A\nB\nC\n' | cmp -s - "$MV_DIR/log" || fail "mv ile değiştirilen çalışan betik eski inode'dan okumaya devam etmeliydi: $(cat "$MV_DIR/log")"
has_lit "$MV_DIR/running.sh" 'echo NEW' || fail "mv sonrası yeni içerik yolda olmalı"

# --- Watcher: corrupt start.sh is repaired even when the hook never ran; uninstall removes every new artefact ------
rm -rf "$WORK/rt_w"; cp -PRp "$RT_RK" "$WORK/rt_w"
: > "$WORK/rt_w/userdata/app/gk/start.sh"
mkdir -p "$WORK/rt_w_bin"
cat > "$WORK/rt_w_bin/pidof" <<'EOF'
#!/bin/sh
[ "${1:-}" = "K3SysUi" ] && { printf '1234\n'; exit 0; }
exit 1
EOF
chmod +x "$WORK/rt_w_bin/pidof"
PATH="$WORK/rt_w_bin:$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$WORK/rt_w" OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 OPEN_KOBRA_RESCUE_START_STABLE_DELAY=0 \
    sh "$WORK/rt_w/useremain/open-kobra/rescue/rescue.sh" watch || fail "watch başarısız"
cat "$WORK/rt_w/useremain/open-kobra/rescue/hook.block" "$WORK/rt_w/useremain/open-kobra/rescue/start.sh.orig" | cmp -s - "$WORK/rt_w/userdata/app/gk/start.sh" || fail "watch modu boş start.sh'ı onarmadı"

run_swu_on_fake_root "$UNINSTALL_SWU" "$RT_RK" >/dev/null || fail "start.sh koruma testi: kaldırma başarısız"
[ ! -e "$RT_RK/useremain/open-kobra" ] || fail "Kaldırma sonrası open-kobra/rescue (hook.block, orig.fw dahil) kalmamalı"
cat "$RT_BODY" "$RT_PATCH" | cmp -s - "$RT_RK/userdata/app/gk/start.sh" || fail "Kaldırma sonrası start.sh bayt bayt kurulum öncesine dönmeli"
pass "start.sh koruması: bozuk start.sh onarılır, meşru Rinkhals değişikliğine dokunulmaz, kapılar çalışır, kanca yeniden exec ile açılışı sürdürür"

echo "21b. Birleşik hata raporu düzeltmeleri (docs/32): B-01 B-02 B-03 B-05 B-06 B-07 B-08 I-01 I-04 I-05 I-06..."
# the step-21 root with Rinkhals was uninstalled at its end: build a fresh installed one for these checks
RT_B="$WORK/rt_b21"
rt_make_root "$RT_B" 1
HOOKB="$RT_B/useremain/open-kobra/rescue/hook.block"

# ---- B-01: K3SysUi must be found through /proc (the printer has no pidof); zombie / stopped is not healthy ----------
B1_BIN="$WORK/b1_bin"
mkdir -p "$B1_BIN"
printf '#!/bin/sh\nexit 1\n' > "$B1_BIN/pidof"
printf '#!/bin/sh\nprintf "reboot\\n" >> "${MOCK_REBOOT_LOG:?}"\n' > "$B1_BIN/reboot"
printf '#!/bin/sh\nprintf "%%s\\n" "{\\"result\\":{\\"status\\":{\\"print_stats\\":{\\"state\\":\\"standby\\"}}}}"\n' > "$B1_BIN/wget"
chmod +x "$B1_BIN/pidof" "$B1_BIN/reboot" "$B1_BIN/wget"
b1_case() {
    # $1 label, $2 comm-or-empty, $3 cmdline-or-empty, $4 stat state letter-or-empty, $5 expected: healthy|dead
    B1R="$WORK/b1_$1"
    rm -rf "$B1R"
    cp -PRp "$RT_B" "$B1R"
    mkdir -p "$B1R/proc"
    sleep 30 &
    B1PID=$!
    mkdir -p "$B1R/proc/$B1PID"
    [ -z "$2" ] || printf '%s\n' "$2" > "$B1R/proc/$B1PID/comm"
    [ -z "$3" ] || printf '%b' "$3" > "$B1R/proc/$B1PID/cmdline"
    [ -z "$4" ] || printf '%s (K3SysUi) %s 1 1 1 0 -1\n' "$B1PID" "$4" > "$B1R/proc/$B1PID/stat"
    printf '2\n' > "$B1R/useremain/open-kobra/rescue/state"
    : > "$WORK/b1_reboots"
    PATH="$B1_BIN:$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$B1R" OPEN_KOBRA_RESCUE_INITIAL_DELAY=0 OPEN_KOBRA_RESCUE_RECHECK_DELAY=0 \
        OPEN_KOBRA_RESCUE_START_STABLE_DELAY=0 MOCK_REBOOT_LOG="$WORK/b1_reboots" \
        sh "$B1R/useremain/open-kobra/rescue/rescue.sh" watch || fail "B-01 $1: watch başarısız"
    kill "$B1PID" 2>/dev/null || true
    wait "$B1PID" 2>/dev/null || true
    B1ST=$(tr -d ' \t\r\n' < "$B1R/useremain/open-kobra/rescue/state")
    if [ "$5" = "healthy" ]; then
        [ "$B1ST" = "0" ] || fail "B-01 $1: pidof yokken /proc'ta çalışan K3SysUi sağlıklı sayılmalıydı (state=$B1ST)"
        [ ! -s "$WORK/b1_reboots" ] || fail "B-01 $1: sağlıklı UI için reboot çağrılmamalıydı"
    else
        # not healthy: the watcher logs it; with auto recovery off (default) it changes nothing (PK-R04)
        [ "$B1ST" = "2" ] || fail "B-01 $1: ölü/durmuş K3SysUi sağlıklı sayılmamalı, sayaç da değişmemeli (state=$B1ST)"
        [ ! -s "$WORK/b1_reboots" ] || fail "B-01 $1: varsayılanda reboot yok"
        has_lit "$B1R/useremain/open-kobra/rescue/rescue.log" "UI sureci bulunamadi" || fail "B-01 $1: sağlıksız UI günlüğe yazılmalı"
    fi
}
b1_case comm K3SysUi "" "" healthy
b1_case cmdline "" './K3SysUi\0' "" healthy
b1_case sleeping K3SysUi "" S healthy
b1_case zombie K3SysUi "" Z dead
b1_case stopped K3SysUi "" T dead
b1_case other "otherprog" "" "" dead

# ---- B-02: firmware update then reinstall refreshes start.sh.orig / .fw; legacy install without .fw gets it backfilled
B2R="$WORK/b2_fwupdate"
rm -rf "$B2R"
cp -PRp "$RT_NORK" "$B2R"
printf '2.4.7.0\n' > "$B2R/useremain/dev/version"
{ cat "$RT_BODY"; printf 'echo NEW_FW_2470 >> "$HOOK_LOG"\n'; } > "$B2R/userdata/app/gk/start.sh"   # Anycubic update: fresh start.sh, no hook
chmod +x "$B2R/userdata/app/gk/start.sh"
cp "$B2R/userdata/app/gk/start.sh" "$WORK/b2_new_stock"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$B2R" >/dev/null || fail "B-02: firmware güncellemesi sonrası yeniden kurulum başarısız"
cmp -s "$WORK/b2_new_stock" "$B2R/useremain/open-kobra/rescue/start.sh.orig" || fail "B-02: start.sh.orig yeni firmware'in start.sh'ından yenilenmedi"
[ "$(tr -d ' \t\r\n' < "$B2R/useremain/open-kobra/rescue/start.sh.orig.fw")" = "2.4.7.0" ] || fail "B-02: start.sh.orig.fw 2.4.7.0 olmalı"
cat "$B2R/useremain/open-kobra/rescue/hook.block" "$WORK/b2_new_stock" | cmp -s - "$B2R/userdata/app/gk/start.sh" || fail "B-02: kurulu start.sh = hook.block + yeni orig olmalı"
: > "$B2R/userdata/app/gk/start.sh"
rt_boot "$B2R"
[ "$RT_RC" = "75" ] || fail "B-02: yenilenen referansla boş start.sh onarılmalı (rc=$RT_RC)"
has_lit "$B2R/userdata/app/gk/start.sh" "NEW_FW_2470" || fail "B-02: onarılan start.sh yeni firmware içeriğini taşımalı"
# same firmware, hook present: orig is NOT touched on reinstall
cp "$B2R/useremain/open-kobra/rescue/start.sh.orig" "$WORK/b2_orig_keep"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$B2R" >/dev/null || fail "B-02: aynı firmware yeniden kurulum başarısız"
cmp -s "$WORK/b2_orig_keep" "$B2R/useremain/open-kobra/rescue/start.sh.orig" || fail "B-02: aynı firmware'de start.sh.orig değişmemeli"
# legacy: hook + orig present, .fw missing, verified stock copy of this firmware exists
B2L="$WORK/b2_legacy"
rm -rf "$B2L"
cp -PRp "$RT_REAL" "$B2L"
rm -f "$B2L/useremain/open-kobra/rescue/start.sh.orig.fw"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$B2L" >/dev/null || fail "B-02: eski kurulum üstüne yeniden kurulum başarısız"
[ "$(tr -d ' \t\r\n' < "$B2L/useremain/open-kobra/rescue/start.sh.orig.fw" 2>/dev/null)" = "2.4.6.7" ] || fail "B-02: eksik start.sh.orig.fw doğrulanmış stok kopyaya dayanarak yazılmalı"

# ---- ERR-05: hook present + stale/invalid start.sh.orig gets candidate rebuilt from start.sh non-hook body
STOCK_REF="$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh"
B5_ERR="$WORK/b5_err05"
rm -rf "$B5_ERR"
cp -PRp "$RT_REAL" "$B5_ERR"
# Simulate older 0.1.0/0.2.0 install: hook already in start.sh, but start.sh.orig is stale/corrupt (e.g. truncated)
head -n 5 "$STOCK_REF" > "$B5_ERR/useremain/open-kobra/rescue/start.sh.orig"
rm -f "$B5_ERR/useremain/open-kobra/rescue/start.sh.orig.fw"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$B5_ERR" >/dev/null || fail "ERR-05: kanca var + eski start.sh.orig ile kurulum başarısız"
[ "$(tr -d ' \t\r\n' < "$B5_ERR/useremain/open-kobra/rescue/start.sh.orig.fw")" = "2.4.6.7" ] || fail "ERR-05: start.sh.orig.fw güncellenmeli"
cat "$STOCK_REF" "$RT_PATCH" > "$WORK/b5_orig_expected"
cmp -s "$WORK/b5_orig_expected" "$B5_ERR/useremain/open-kobra/rescue/start.sh.orig" || fail "ERR-05: start.sh.orig kanca dışı gövdeden yeniden üretilip beklenen stokla eşleşmeli"
cat "$B5_ERR/useremain/open-kobra/rescue/hook.block" "$WORK/b5_orig_expected" > "$WORK/b5_expected"
: > "$B5_ERR/userdata/app/gk/start.sh"
rt_boot "$B5_ERR"
[ "$RT_RC" = "75" ] || fail "ERR-05: boş start.sh yenilenen orig referansıyla onarılmalı (rc=$RT_RC)"
cmp -s "$WORK/b5_expected" "$B5_ERR/userdata/app/gk/start.sh" || fail "ERR-05: onarılan start.sh beklenen içeriğe dönmedi"
pass "ERR-05: kanca var + eski start.sh.orig senaryosunda referans yenilendi ve boot onarımı başarılı"

# ---- B-03: guard rejects half-written open_kobra_homing.cfg ---------------------------------------------------------
B3R="$WORK/b3_root"
make_fake_root "$B3R"
run_swu_on_fake_root "$INSTALL_SWU" "$B3R" >/dev/null || fail "B-03: kurulum başarısız"
B3CFG="$B3R/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg"
B3MAIN="$B3R/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg"
B3GUARD="$B3R/useremain/home/rinkhals/apps/05-open-kobra-guard/app.sh"
B3SHIP="$PROJECT_ROOT/config/open_kobra_homing.cfg"
b3_guard() { OPEN_KOBRA_ROOT="$B3R" "$B3GUARD" start || fail "B-03: guard 0 dönmeli"; }
b3_guard
cmp -s "$B3CFG" "$B3SHIP" || fail "B-03: sağlam dosyaya dokunulmamalı"
head -n 60 "$B3SHIP" > "$B3CFG"
b3_guard
cmp -s "$B3CFG" "$B3SHIP" || fail "B-03: ilk 60 satıra kesilen dosya kalıcı kopyadan geri yüklenmeli"
has_line "$B3MAIN" "[include open_kobra_homing.cfg]" || fail "B-03: geri yüklenince include etkin kalmalı"
# cut exactly at a section boundary (balanced Jinja, still a strict prefix of the shipped file)
FORCE_LN=$(awk '/^\[gcode_macro FORCE_HOME\]/ { print NR; exit }' "$B3SHIP")
head -n $((FORCE_LN - 1)) "$B3SHIP" > "$B3CFG"
b3_guard
cmp -s "$B3CFG" "$B3SHIP" || fail "B-03: bölüm sınırında kesilen dosya geri yüklenmeli"
# user-edited but intact: untouched
sed 's/variable_backoff_mm: 10.0/variable_backoff_mm: 12.5/' "$B3SHIP" > "$B3CFG"
cp "$B3CFG" "$WORK/b3_user_edit"
b3_guard
cmp -s "$B3CFG" "$WORK/b3_user_edit" || fail "B-03: kullanıcının sağlam düzenlemesi ezilmemeli"
# no persistent copy: broken file cannot be restored -> include is switched off instead of crashing Klipper
head -n 60 "$B3SHIP" > "$B3CFG"
rm -f "$B3R/useremain/home/rinkhals/open-kobra/open_kobra_homing.cfg"
b3_guard
has_lit "$B3MAIN" "# [include open_kobra_homing.cfg] # open-kobra/guard-disabled" || fail "B-03: geri yüklenemeyen bozuk dosyada include kapatılmalı"

# ---- B-07 / B-08: guard timeout default is clearly below Rinkhals' 5 s; no usable timeout -> never unbounded ---------
B7BIN="$WORK/b7_bin"
mkdir -p "$B7BIN"
cat > "$B7BIN/timeout" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${MOCK_TIMEOUT_LOG:?}"
if [ "${1:-}" = "-t" ]; then shift 2; else shift 1; fi
exec "$@"
EOF
chmod +x "$B7BIN/timeout"
: > "$WORK/b7_timeout.log"
PATH="$B7BIN:$PATH" MOCK_TIMEOUT_LOG="$WORK/b7_timeout.log" OPEN_KOBRA_ROOT="$B3R" "$B3GUARD" start || fail "B-07: guard 0 dönmeli"
has_lit "$WORK/b7_timeout.log" "-t 3 sh " || fail "B-07: varsayılan bekçi zaman aşımı 3 sn olmalı: $(cat "$WORK/b7_timeout.log")"
: > "$WORK/b7_timeout.log"
PATH="$B7BIN:$PATH" MOCK_TIMEOUT_LOG="$WORK/b7_timeout.log" OPEN_KOBRA_GUARD_TIMEOUT=2 OPEN_KOBRA_ROOT="$B3R" "$B3GUARD" start || fail "B-07: guard 0 dönmeli"
has_lit "$WORK/b7_timeout.log" "-t 2 sh " || fail "B-07: OPEN_KOBRA_GUARD_TIMEOUT geçersiz kılma çalışmalı"

B8BIN="$WORK/b8_bin"
mkdir -p "$B8BIN"
printf '#!/bin/sh\nexit 127\n' > "$B8BIN/timeout"
chmod +x "$B8BIN/timeout"
rm -f "$WORK/b8_trace"
PATH="$B8BIN:$PATH" OPEN_KOBRA_GUARD_TEST_SLEEP=0 OPEN_KOBRA_GUARD_TRACE_FILE="$WORK/b8_trace" OPEN_KOBRA_ROOT="$B3R" "$B3GUARD" start || fail "B-08: kullanılabilir timeout yokken guard 0 dönmeli"
[ ! -e "$WORK/b8_trace" ] || fail "B-08: kullanılabilir timeout yokken guard_main sınırsız çalıştırılmamalı"
has_lit "$B3R/tmp/rinkhals/app-05-open-kobra-guard.log" "timeout" || fail "B-08: guard günlüğü timeout uyarısını içermeli"
# the start.sh hook: no usable timeout -> rescue.sh is not run unbounded, stock flow continues
B8R="$WORK/b8_hook"
rm -rf "$B8R"
cp -PRp "$RT_B" "$B8R"
{ cat "$HOOKB"; head -n 2 "$RT_BODY"; } > "$B8R/userdata/app/gk/start.sh"
cp "$B8R/userdata/app/gk/start.sh" "$WORK/b8_start_before"
: > "$WORK/b8_hook.log"
PATH="$B8BIN:$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$B8R" OPEN_KOBRA_RESCUE_NO_WATCH=1 HOOK_LOG="$WORK/b8_hook.log" \
    sh "$B8R/userdata/app/gk/start.sh" || fail "B-08: timeout yokken start.sh durmamalıydı"
cmp -s "$WORK/b8_start_before" "$B8R/userdata/app/gk/start.sh" || fail "B-08: timeout yokken rescue.sh çalıştırılmamalıydı (start.sh değişti)"
printf 'STOCK_LINE_1\n' | cmp -s - "$WORK/b8_hook.log" || fail "B-08: timeout yokken stok akış sürmeliydi: $(cat "$WORK/b8_hook.log")"

# ---- B-06: a user's commented-out include outside our block must not abort the uninstall ---------------------------
B6R="$WORK/b6_root"
make_fake_root "$B6R"
run_swu_on_fake_root "$INSTALL_SWU" "$B6R" >/dev/null || fail "B-06: kurulum başarısız"
B6CFG="$B6R/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg"
awk '/^#\*# <---/ && !done { print "# [include leviq3.cfg]"; print "#[include open_kobra_limits*.cfg]"; done = 1 } { print }' "$B6CFG" > "$B6CFG.new" && mv "$B6CFG.new" "$B6CFG"
has_line "$B6CFG" "# [include leviq3.cfg]" || fail "B-06: test hazırlığı başarısız"
run_swu_on_fake_root "$UNINSTALL_SWU" "$B6R" >/dev/null || fail "B-06: yorum satırındaki include kaldırmayı iptal etmemeli"
has_line "$B6CFG" "# [include leviq3.cfg]" || fail "B-06: kullanıcının yorum satırına dokunulmamalı"
[ ! -e "$B6R/useremain/home/rinkhals/apps/05-open-kobra-guard" ] || fail "B-06: kaldırma tamamlanmalıydı"
if has_lit "$B6CFG" "# open-kobra/begin"; then
    fail "B-06: open-kobra bloğu temizlenmeli"
fi

# ---- B-05: a running kobra-screen is stopped by install/uninstall -> the stock UI must come back -------------------
b5_prepare() {
    B5R="$1"
    make_fake_root "$B5R"
    mkdir -p "$B5R/userdata/app/gk" "$B5R/useremain/home/rinkhals/apps/kobra-screen"
    printf '#!/bin/sh\n: > "%s/tmp/k3sysui_started"\n' "$B5R" > "$B5R/userdata/app/gk/K3SysUi"
    chmod +x "$B5R/userdata/app/gk/K3SysUi"
    b5_mock_screen "$B5R"
}
b5_mock_screen() {
    : > "$1/tmp/ks_is_running"
    cat > "$1/useremain/home/rinkhals/apps/kobra-screen/app.sh" <<'EOF'
#!/bin/sh
R="${OPEN_KOBRA_ROOT:-}"
case "${1:-}" in
    status)
        if [ -f "$R/tmp/ks_is_running" ]; then printf 'Status: started\nPIDs: 9999\nLog: x\n'; else printf 'Status: stopped\nPIDs: \nLog: x\n'; fi
        ;;
    stop) rm -f "$R/tmp/ks_is_running" ;;
    start) : > "$R/tmp/ks_is_running" ;;
esac
EOF
    chmod +x "$1/useremain/home/rinkhals/apps/kobra-screen/app.sh"
}
b5_wait_marker() {
    B5I=0
    while [ ! -f "$1/tmp/k3sysui_started" ] && [ "$B5I" -lt 30 ]; do
        sleep 0.1
        B5I=$((B5I + 1))
    done
    [ -f "$1/tmp/k3sysui_started" ]
}
# install: kobra-screen was started by hand (no .enabled) -> it is stopped for the update and not restarted
b5_prepare "$WORK/b5_install"
OPEN_KOBRA_ENSURE_STOCK_UI=1 run_swu_on_fake_root "$INSTALL_SWU" "$WORK/b5_install" >/dev/null || fail "B-05: kurulum başarısız"
b5_wait_marker "$WORK/b5_install" || fail "B-05: kurulum kobra-screen'i durdurup geri başlatmadığında stok K3SysUi başlatılmalıydı"
# install, nothing was running: stock UI must NOT be touched
b5_prepare "$WORK/b5_idle"
rm -f "$WORK/b5_idle/tmp/ks_is_running"
OPEN_KOBRA_ENSURE_STOCK_UI=1 run_swu_on_fake_root "$INSTALL_SWU" "$WORK/b5_idle" >/dev/null || fail "B-05: boşta kurulum başarısız"
[ ! -e "$WORK/b5_idle/tmp/k3sysui_started" ] || fail "B-05: kobra-screen çalışmıyorken K3SysUi başlatılmamalı"
# uninstall with kobra-screen running
b5_prepare "$WORK/b5_uninstall"
run_swu_on_fake_root "$INSTALL_SWU" "$WORK/b5_uninstall" >/dev/null || fail "B-05: kurulum başarısız (kaldırma testi)"
rm -f "$WORK/b5_uninstall/tmp/k3sysui_started"
b5_mock_screen "$WORK/b5_uninstall"
OPEN_KOBRA_ENSURE_STOCK_UI=1 run_swu_on_fake_root "$UNINSTALL_SWU" "$WORK/b5_uninstall" >/dev/null || fail "B-05: kaldırma başarısız"
b5_wait_marker "$WORK/b5_uninstall" || fail "B-05: çalışan kobra-screen kaldırılınca stok K3SysUi başlatılmalıydı"
# (the baseline restores the pre-existing kobra-screen dir, as for any pre-existing app)
# tests never launch the real binary unless asked to
b5_prepare "$WORK/b5_off"
run_swu_on_fake_root "$INSTALL_SWU" "$WORK/b5_off" >/dev/null || fail "B-05: varsayılan kurulum başarısız"
[ ! -e "$WORK/b5_off/tmp/k3sysui_started" ] || fail "B-05: ENSURE_STOCK_UI olmadan test kökünde K3SysUi başlatılmamalı"

# ---- I-01: one version everywhere ------------------------------------------------------------------------------------
PKG_V=$(tr -d ' \t\r\n' < "$PROJECT_ROOT/VERSION")
GUARD_V=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PROJECT_ROOT/apps/05-open-kobra-guard/app.json" | head -n 1)
[ "$PKG_V" = "$GUARD_V" ] || fail "I-01: VERSION ($PKG_V) ile guard app.json ($GUARD_V) aynı olmalı"
[ "$(tr -d ' \t\r\n' < "$WORK/unpack_install/update_swu/VERSION")" = "$PKG_V" ] || fail "I-01: paket VERSION'ı kaynak VERSION ile aynı olmalı"
[ "$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/kobra-screen/app.json" | head -n 1)" = "$PKG_V" ] || fail "I-01: paket kobra-screen/app.json sürümü ($PKG_V) olmalı"
[ "$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/ace-standalone/app.json" | head -n 1)" = "$PKG_V" ] || fail "I-01: paket ace-standalone/app.json sürümü ($PKG_V) olmalı"
I1R="$WORK/i1_root"
make_fake_root "$I1R"
printf '0.1.0\n' > "$I1R/useremain/home/rinkhals/.open-kobra-version"
run_swu_on_fake_root "$INSTALL_SWU" "$I1R" > "$WORK/i1_install.log" || fail "I-01: yükseltme kurulumu başarısız"
[ "$(tr -d ' \t\r\n' < "$I1R/useremain/home/rinkhals/.open-kobra-version")" = "$PKG_V" ] || fail "I-01: .open-kobra-version paket sürümüne güncellenmeli"
has_lit "$I1R/useremain/rinkhals/open-kobra-install.log" "sürüm değişikliği: 0.1.0 -> $PKG_V" || fail "I-01: yükseltme günlüğü eski -> yeni sürümü göstermeli"
[ "$(OPEN_KOBRA_ROOT="$I1R" "$I1R/useremain/home/rinkhals/apps/05-open-kobra-guard/app.sh" version)" = "$PKG_V" ] || fail "I-01: guard sürümü paket sürümüyle aynı olmalı"
# build refuses a guard/VERSION mismatch
VT="$WORK/vt"
rm -rf "$VT"
mkdir -p "$VT/open-kobra"
(cd "$PROJECT_ROOT" && tar -cf - --exclude=./dist .) | (cd "$VT/open-kobra" && tar -xf -)
for LNK in kobra-screen ace-standalone leviq3-klipper ace-driver; do
    ln -s "$PARENT_ROOT/$LNK" "$VT/$LNK"
done
sed 's/"version": "[^"]*"/"version": "9.9.9"/' "$VT/open-kobra/apps/05-open-kobra-guard/app.json" > "$VT/guard_app.json.new"
mv "$VT/guard_app.json.new" "$VT/open-kobra/apps/05-open-kobra-guard/app.json"
if (cd "$VT/open-kobra" && OPEN_KOBRA_DIST_DIR="$VT/out" ./build.sh >/dev/null 2>&1); then
    fail "I-01: VERSION ile guard app.json uyuşmazken derleme başarısız olmalıydı"
fi

# build refuses a kobra-screen/VERSION mismatch
VT_KS="$WORK/vt_ks"
rm -rf "$VT_KS"
mkdir -p "$VT_KS/open-kobra" "$VT_KS/fake_kobra_screen"
(cd "$PROJECT_ROOT" && tar -cf - --exclude=./dist .) | (cd "$VT_KS/open-kobra" && tar -xf -)
for LNK in ace-standalone leviq3-klipper ace-driver; do
    ln -s "$PARENT_ROOT/$LNK" "$VT_KS/$LNK"
done
(cd "$PARENT_ROOT/kobra-screen" && tar -cf - .) | (cd "$VT_KS/fake_kobra_screen" && tar -xf -)
sed 's/"version": "[^"]*"/"version": "9.9.9"/' "$VT_KS/fake_kobra_screen/app.json" > "$VT_KS/ks_app.json.new"
mv "$VT_KS/ks_app.json.new" "$VT_KS/fake_kobra_screen/app.json"
ln -s "$VT_KS/fake_kobra_screen" "$VT_KS/kobra-screen"
if (cd "$VT_KS/open-kobra" && OPEN_KOBRA_DIST_DIR="$VT_KS/out" ./build.sh >/dev/null 2>&1); then
    fail "I-01: VERSION ile kobra-screen app.json uyuşmazken derleme başarısız olmalıydı"
fi

# build refuses an ace-standalone/VERSION mismatch
VT_AS="$WORK/vt_as"
rm -rf "$VT_AS"
mkdir -p "$VT_AS/open-kobra" "$VT_AS/fake_ace_standalone"
(cd "$PROJECT_ROOT" && tar -cf - --exclude=./dist .) | (cd "$VT_AS/open-kobra" && tar -xf -)
for LNK in kobra-screen leviq3-klipper ace-driver; do
    ln -s "$PARENT_ROOT/$LNK" "$VT_AS/$LNK"
done
(cd "$PARENT_ROOT/ace-standalone" && tar -cf - .) | (cd "$VT_AS/fake_ace_standalone" && tar -xf -)
sed 's/"version": "[^"]*"/"version": "9.9.9"/' "$VT_AS/fake_ace_standalone/app.json" > "$VT_AS/as_app.json.new"
mv "$VT_AS/as_app.json.new" "$VT_AS/fake_ace_standalone/app.json"
ln -s "$VT_AS/fake_ace_standalone" "$VT_AS/ace-standalone"
if (cd "$VT_AS/open-kobra" && OPEN_KOBRA_DIST_DIR="$VT_AS/out" ./build.sh >/dev/null 2>&1); then
    fail "I-01: VERSION ile ace-standalone app.json uyuşmazken derleme başarısız olmalıydı"
fi

# ---- I-04: a present-but-broken ace-driver fails the build (explicit opt-out only); missing dir still builds -------
if OPEN_KOBRA_ACE_DRIVER_SRC="$INC_ACE" OPEN_KOBRA_DIST_DIR="$WORK/dist_i4a" "$PROJECT_ROOT/build.sh" > "$WORK/i4a.log" 2>&1; then
    fail "I-04: eksik ace-driver ile derleme başarısız olmalıydı"
fi
has_lit "$WORK/i4a.log" "HATA: ace-driver paketlenemedi" || fail "I-04: eksik ace-driver hata mesajı yok"
if OPEN_KOBRA_ACE_DRIVER_SRC="$BRK_ACE" OPEN_KOBRA_DIST_DIR="$WORK/dist_i4b" "$PROJECT_ROOT/build.sh" > "$WORK/i4b.log" 2>&1; then
    fail "I-04: sözdizimi hatalı ace-driver ile derleme başarısız olmalıydı"
fi
OPEN_KOBRA_ALLOW_NO_ACE_DRIVER=1 OPEN_KOBRA_ACE_DRIVER_SRC="$BRK_ACE" OPEN_KOBRA_DIST_DIR="$WORK/dist_i4c" "$PROJECT_ROOT/build.sh" > "$WORK/i4c.log" 2>&1 || fail "I-04: OPEN_KOBRA_ALLOW_NO_ACE_DRIVER=1 ile sürücüsüz derleme başarılı olmalı"
has_lit "$WORK/i4c.log" "atlandı" || fail "I-04: izinli atlama bilgisi yok"
# real tree: when it is there and complete, the dist package must carry it
if [ -f "$PARENT_ROOT/ace-driver/klippy/extras/ace/__init__.py" ] && [ -f "$PARENT_ROOT/ace-driver/klippy/extras/temperature_ace.py" ]; then
    [ -d "$WORK/unpack_install/update_swu/ace-driver" ] || fail "I-04: gerçek ace-driver tam ama dist paketinde yok"
fi
# driver-less package over an installed driver: driver stays, log says so
ACE_UP="$WORK/ace_upgrade_root"
ace_make_root "$ACE_UP"
run_swu_on_fake_root "$ACE_INSTALL_SWU" "$ACE_UP" >/dev/null || fail "I-04: sürücülü kurulum başarısız"
run_swu_on_fake_root "$WORK/dist_none/install/aGVscF9zb3Nf/update.swu" "$ACE_UP" >/dev/null || fail "I-04: sürücüsüz yükseltme başarısız"
has_lit "$ACE_UP/useremain/rinkhals/open-kobra-install.log" "olduğu gibi bırakıldı" || fail "I-04: sürücüsüz yükseltme günlüğü mevcut sürücüyü belirtmeli"
cmp -s "$ACE_UP/useremain/home/rinkhals/apps/vanilla-klipper/klippy/extras/ace/manager.py" "$FAKE_ACE/klippy/extras/ace/manager.py" || fail "I-04: sürücüsüz yükseltme kurulu sürücüye dokunmamalı"
run_swu_on_fake_root "$UNINSTALL_SWU" "$ACE_UP" >/dev/null || fail "I-04: yükseltme sonrası kaldırma başarısız"
ace_check_restored "$ACE_UP" "sürücüsüz yükseltme sonrası kaldırma"

# ---- I-05: PC-only render_pil.py is not shipped -----------------------------------------------------------------------
[ ! -e "$WORK/unpack_install/update_swu/apps/kobra-screen/kobra_screen/render_pil.py" ] || fail "I-05: render_pil.py pakete girmemeli"

# ---- I-06: relative OPEN_KOBRA_DIST_DIR ------------------------------------------------------------------------------
mkdir -p "$WORK/rel_cwd"
(cd "$WORK/rel_cwd" && OPEN_KOBRA_ACE_DRIVER_SRC="$FAKE_ACE" OPEN_KOBRA_DIST_DIR="rel_out/dist" "$PROJECT_ROOT/build.sh" >/dev/null 2>&1) || fail "I-06: göreli OPEN_KOBRA_DIST_DIR ile derleme başarısız"
[ -f "$WORK/rel_cwd/rel_out/dist/install/aGVscF9zb3Nf/update.swu" ] || fail "I-06: göreli hedefte kurulum paketi yok"
[ -f "$WORK/rel_cwd/rel_out/dist/uninstall/aGVscF9zb3Nf/update.swu" ] || fail "I-06: göreli hedefte kaldırma paketi yok"
pass "Hata raporu düzeltmeleri (B-01 B-02 B-03 B-05 B-06 B-07 B-08 I-01 I-04 I-05 I-06) testleri geçti"

echo "21c. PK-R01..R05 (denetçi): altın kopya doğrulaması, küçük kanca, exec yok, hızlı ikili denetimi..."

# ---- PK-R01: the official start.sh travels with the package; a cut-off start.sh never becomes the reference ---------
[ "$(md5sum "$WORK/unpack_install/update_swu/rescue/manifests/K3_2.4.6.7.start.sh" | awk '{print $1}')" = "b29684d20aa7e5d73bdfdab543d72d9f" ] || fail "PK-R01: paketteki resmi start.sh md5 uyuşmuyor"
pr1_root() {
    # $1 dir, $2 fw, $3 start.sh source file
    PR1R="$1"
    make_fake_root "$PR1R"
    mkdir -p "$PR1R/useremain/dev" "$PR1R/userdata/app/gk"
    printf '%s\n' "$2" > "$PR1R/useremain/dev/version"
    cp "$3" "$PR1R/userdata/app/gk/start.sh"
    chmod +x "$PR1R/userdata/app/gk/start.sh"
    for PRB in gkapi K3SysUi gklib gkcam; do
        printf 'STOCK_VALID_%s\n' "$PRB" > "$PR1R/userdata/app/gk/$PRB"
        chmod +x "$PR1R/userdata/app/gk/$PRB"
    done
}
pr1_expect_refused() {
    cmp -s "$WORK/pr1_src" "$1/userdata/app/gk/start.sh" || fail "PK-R01 $2: start.sh'a dokunulmamalıydı"
    if has_lit "$1/userdata/app/gk/start.sh" "# open-kobra-rescue/begin"; then
        fail "PK-R01 $2: doğrulama başarısızken kanca EKLENMEMELİYDİ"
    fi
    [ ! -e "$1/useremain/open-kobra/rescue/start.sh.orig" ] || fail "PK-R01 $2: bozuk start.sh referans (orig) olarak kaydedilmemeli"
    has_lit "$1/useremain/rinkhals/open-kobra-install.log" "EKLENMEDİ" || fail "PK-R01 $2: kurulum günlüğü kancanın eklenmediğini söylemeli"
}
STOCK_REF="$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh"
# the auditor's "badorig": first 14 lines of the stock start.sh (valid syntax, no launch lines)
head -n 14 "$STOCK_REF" > "$WORK/pr1_src"
sh -n "$WORK/pr1_src" || fail "PK-R01: badorig senaryosu geçerli sözdizimli olmalı"
pr1_root "$WORK/pr1_badorig" 2.4.6.7 "$WORK/pr1_src"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$WORK/pr1_badorig" >/dev/null || fail "PK-R01: kurulum başarısız (kesik stok)"
pr1_expect_refused "$WORK/pr1_badorig" "ilk 14 satır"
# launch lines present but the stock part differs from the official one (a line missing)
awk '$0 != "killall adbd"' "$STOCK_REF" > "$WORK/pr1_src"
pr1_root "$WORK/pr1_altered" 2.4.6.7 "$WORK/pr1_src"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$WORK/pr1_altered" >/dev/null || fail "PK-R01: kurulum başarısız (değişmiş stok)"
pr1_expect_refused "$WORK/pr1_altered" "resmi içerikten farklı"
# unknown firmware without launch lines
printf 'echo hello\n' > "$WORK/pr1_src"
pr1_root "$WORK/pr1_nolaunch" 9.9.9.9 "$WORK/pr1_src"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$WORK/pr1_nolaunch" >/dev/null || fail "PK-R01: kurulum başarısız (başlatma satırsız)"
pr1_expect_refused "$WORK/pr1_nolaunch" "başlatma satırı yok"
# the official file itself is accepted (and with the Rinkhals block appended)
for PRWITH in 0 1; do
    cp "$STOCK_REF" "$WORK/pr1_src"
    [ "$PRWITH" = "0" ] || cat "$RT_PATCH" >> "$WORK/pr1_src"
    pr1_root "$WORK/pr1_ok$PRWITH" 2.4.6.7 "$WORK/pr1_src"
    PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$WORK/pr1_ok$PRWITH" >/dev/null || fail "PK-R01: resmi start.sh ile kurulum başarısız"
    [ -f "$WORK/pr1_ok$PRWITH/useremain/open-kobra/rescue/start.sh.orig" ] || fail "PK-R01: resmi start.sh referans olarak kaydedilmeli ($PRWITH)"
    [ "$(head -n 1 "$WORK/pr1_ok$PRWITH/userdata/app/gk/start.sh")" = "# open-kobra-rescue/begin" ] || fail "PK-R01: resmi start.sh'a kanca eklenmeli ($PRWITH)"
done
# boot-time: a damaged reference is never used to "repair" anything
rt_gate_orig() {
    rm -rf "$WORK/pr1_g"
    cp -PRp "$RT_REAL" "$WORK/pr1_g"
    cat "$2" > "$WORK/pr1_g/useremain/open-kobra/rescue/start.sh.orig"
    rt_gate_case "$1" "$WORK/pr1_g"
}
head -n 14 "$STOCK_REF" > "$WORK/pr1_src"
rt_gate_orig "PK-R01 orig ilk 14 satır" "$WORK/pr1_src"
awk '$0 != "killall adbd"' "$STOCK_REF" > "$WORK/pr1_src"
rt_gate_orig "PK-R01 orig resmi içerikten farklı" "$WORK/pr1_src"

# ---- PK-R02 / PK-R03: tiny hook, no exec, same stock lines once ---------------------------------------------------------
HK_N=$(wc -l < "$RT_B/useremain/open-kobra/rescue/hook.block" | tr -d ' ')
[ "$HK_N" -le 14 ] || fail "PK-R02: kanca küçük kalmalı (satır sayısı $HK_N)"
for NOPE in "exec " "timeout" "command -v"; do
    if has_lit "$RT_B/useremain/open-kobra/rescue/hook.block" "$NOPE"; then
        fail "PK-R02/R03: kanca '$NOPE' içermemeli (mantık rescue.sh'ta; exec yok)"
    fi
done
if command -v dash >/dev/null 2>&1; then
    for DSH in "$PROJECT_ROOT/rescue/rescue.sh" "$PROJECT_ROOT/swu/install/update.sh" "$PROJECT_ROOT/swu/uninstall/update.sh" \
        "$PROJECT_ROOT/apps/05-open-kobra-guard/app.sh" "$RT_B/useremain/open-kobra/rescue/hook.block" "$RT_B/userdata/app/gk/start.sh"; do
        dash -n "$DSH" || fail "PK-R02: dash -n başarısız: $DSH"
    done
    rm -rf "$WORK/pk2_dash"
    cp -PRp "$RT_B" "$WORK/pk2_dash"
    { cat "$RT_B/useremain/open-kobra/rescue/hook.block"; head -n 4 "$RT_BODY"; } > "$WORK/pk2_dash/userdata/app/gk/start.sh"
    : > "$WORK/pk2_dash.log"
    (cd "$STUBS" && PATH="$RES_BIN:$PATH" OPEN_KOBRA_ROOT="$WORK/pk2_dash" OPEN_KOBRA_RESCUE_NO_WATCH=1 OPEN_KOBRA_RESCUE_START_STABLE_DELAY=0 HOOK_LOG="$WORK/pk2_dash.log" \
        dash "$WORK/pk2_dash/userdata/app/gk/start.sh") || fail "PK-R02: dash altında onarım+yeniden çalıştırma başarısız"
    cmp -s "$WORK/rt_all_lines" "$WORK/pk2_dash.log" || fail "PK-R02: dash altında stok satırlar bir kez çalışmalı: $(cat "$WORK/pk2_dash.log")"
else
    echo "  [INFO] dash yok: kanca/rescue.sh yalnızca bash (sh) altında çalıştırıldı; gerçek BusyBox ash doğrulaması yazıcıda yapılmalı"
fi

# ---- PK-R05: untouched binaries are not hashed at boot; truncated / same-size-different-mtime ones are --------------
PR5="$WORK/pr5_root"
rm -rf "$PR5"
cp -PRp "$RT_REAL" "$PR5"
pr5_boot() {
    : > "$WORK/pr5_md5.log"
    PATH="$RES_BIN:$PATH" MD5_CALL_LOG="$WORK/pr5_md5.log" OPEN_KOBRA_ROOT="$PR5" OPEN_KOBRA_RESCUE_NO_WATCH=1 OPEN_KOBRA_RESCUE_START_STABLE_DELAY=0 \
        sh "$PR5/useremain/open-kobra/rescue/rescue.sh" boot || fail "PK-R05: boot başarısız"
}
pr5_boot
if awk '/userdata\/app\/gk\/(gkapi|K3SysUi|gklib|gkcam)$/ { f = 1 } END { exit !f }' "$WORK/pr5_md5.log"; then
    fail "PK-R05: dokunulmamış ikililer her açılışta md5'lenmemeli: $(cat "$WORK/pr5_md5.log")"
fi
printf 'XXXXXXXXXXXXXXXXXX' > "$PR5/userdata/app/gk/gkapi"          # same size as 'STOCK_VALID_gkapi\n'
touch -d '2001-01-01 00:00:00' "$PR5/userdata/app/gk/gkapi"
pr5_boot
has_line "$PR5/userdata/app/gk/gkapi" "STOCK_VALID_gkapi" || fail "PK-R05: aynı boyutlu ama mtime'ı farklı bozuk ikili onarılmalı"
pr5_boot
if has_lit "$WORK/pr5_md5.log" "userdata/app/gk/gkapi"; then
    fail "PK-R05: onarım sonrası ikili (mtime korunarak) tekrar md5'lenmemeli"
fi
printf 'HALF' > "$PR5/userdata/app/gk/K3SysUi"
pr5_boot
has_line "$PR5/userdata/app/gk/K3SysUi" "STOCK_VALID_K3SysUi" || fail "PK-R05: kesik ikili onarılmalı"
pass "PK-R01..R05: altın kopya doğrulaması, küçük/exec'siz kanca, hızlı ikili denetimi testleri geçti"

echo "22. ERR-05: start.sh.orig güvenli yeniden üretimi (kanca var + eski/uyumsuz start.sh.orig)..."
# 22a. Scenario: Hook already exists in start.sh, but start.sh.orig is stale/incompatible (e.g. from 0.1.0/0.2.0).
# Underlying start.sh has intact stock 2.4.6.7 content (outside the hook).
# update.sh must regenerate start.sh.orig from the hook-free part of start.sh, verify it against official 2.4.6.7,
# and write start.sh.orig.fw so 'start.sh.orig resmi-start.sh-ile-uyusmuyor' is prevented and repair works.
ERR5_ROOT="$WORK/err05_root"
rm -rf "$ERR5_ROOT"
rt_make_root "$ERR5_ROOT" 1 2.4.6.7 "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh"
printf 'echo OLD_STALE_ORIG\n' > "$ERR5_ROOT/useremain/open-kobra/rescue/start.sh.orig"
printf '0.1.0\n' > "$ERR5_ROOT/useremain/open-kobra/rescue/start.sh.orig.fw"

PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$ERR5_ROOT" >/dev/null || fail "ERR-05: kanca varken yeniden kurulum başarısız"

[ -f "$ERR5_ROOT/useremain/open-kobra/rescue/start.sh.orig" ] || fail "ERR-05: start.sh.orig oluşturulmadı"
[ "$(tr -d ' \t\r\n' < "$ERR5_ROOT/useremain/open-kobra/rescue/start.sh.orig.fw")" = "2.4.6.7" ] || fail "ERR-05: start.sh.orig.fw 2.4.6.7 olmalı"
if has_lit "$ERR5_ROOT/useremain/open-kobra/rescue/start.sh.orig" "OLD_STALE_ORIG"; then
    fail "ERR-05: eski start.sh.orig yenilenmedi"
fi

awk '
/^# open-kobra-rescue\/begin/ { skip = 1; next }
/^# open-kobra-rescue\/end/ { skip = 0; next }
skip == 0 { print }
' "$ERR5_ROOT/userdata/app/gk/start.sh" > "$WORK/err05_hook_free_expected"
cmp -s "$WORK/err05_hook_free_expected" "$ERR5_ROOT/useremain/open-kobra/rescue/start.sh.orig" || fail "ERR-05: start.sh.orig kanca dışı içerikle birebir eşleşmeli"

# Verify rescue boot diagnoses start.sh.orig as healthy (no "resmi-start.sh-ile-uyusmuyor")
# and successfully repairs a damaged start.sh:
: > "$ERR5_ROOT/userdata/app/gk/start.sh"
rt_boot "$ERR5_ROOT"
[ "$RT_RC" = "75" ] || fail "ERR-05: onarım sonrası boot 75 dönmeli (dönen: $RT_RC)"
cat "$ERR5_ROOT/useremain/open-kobra/rescue/hook.block" "$ERR5_ROOT/useremain/open-kobra/rescue/start.sh.orig" > "$WORK/err05_repaired_expected"
cmp -s "$WORK/err05_repaired_expected" "$ERR5_ROOT/userdata/app/gk/start.sh" || fail "ERR-05: start.sh hook + yenilenen orig ile onarılmalı"

# Verify rescue boot does not reject intact start.sh (does not log resmi-start.sh-ile-uyusmuyor)
rt_boot "$ERR5_ROOT"
[ "$RT_RC" = "0" ] || fail "ERR-05: onarılan start.sh ile rescue boot 0 dönmeli (dönen: $RT_RC)"
[ -f "$ERR5_ROOT/useremain/open-kobra/rescue/rescue.log" ] || fail "ERR-05: rescue.log oluşturulmalı"
if has_lit "$ERR5_ROOT/useremain/open-kobra/rescue/rescue.log" "resmi-start.sh-ile-uyusmuyor"; then
    fail "ERR-05: rescue boot start.sh.orig için resmi-start.sh-ile-uyusmuyor hatası vermemeli"
fi
has_lit "$ERR5_ROOT/useremain/open-kobra/rescue/rescue.log" "start.sh beklenen icerikle ayni" || fail "ERR-05: rescue boot sağlam start.sh için beklenen günlük kaydını yazmalı"

# 22b. Scenario: Hook already exists, but the hook-free part is ITSELF broken/insane
# (e.g. truncated or syntax error). update.sh must NOT produce a broken start.sh.orig or write .fw.
ERR5_BROKEN="$WORK/err05_broken"
rm -rf "$ERR5_BROKEN"
rt_make_root "$ERR5_BROKEN" 1 2.4.6.7 "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh"
{
    cat "$ERR5_BROKEN/useremain/open-kobra/rescue/hook.block"
    printf 'if [ broken_syntax_no_fi\n'
} > "$ERR5_BROKEN/userdata/app/gk/start.sh"
rm -f "$ERR5_BROKEN/useremain/open-kobra/rescue/start.sh.orig"
rm -f "$ERR5_BROKEN/useremain/open-kobra/rescue/start.sh.orig.fw"

PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$ERR5_BROKEN" >/dev/null || fail "ERR-05: bozuk start.sh ile kurulum başarısız olmamalı (uyarı vermeli)"
[ ! -f "$ERR5_BROKEN/useremain/open-kobra/rescue/start.sh.orig" ] || fail "ERR-05: bozuk kanca dışı içerik start.sh.orig olarak kaydedilmemeli"
[ ! -f "$ERR5_BROKEN/useremain/open-kobra/rescue/start.sh.orig.fw" ] || fail "ERR-05: doğrulanamayan orig için .fw yazılmamalı"

pass "ERR-05: kanca varken eski/uyumsuz start.sh.orig güvenli yeniden üretimi ve doğrulama testleri geçti"

echo "23. ERR-07: build.sh VERSION=0.3.1 iken app.json version alanları 0.3.1 olmalı..."
# (1) Check root VERSION file
[ -f "$PROJECT_ROOT/VERSION" ] || fail "ERR-07: VERSION dosyası bulunamadı"
EXP_VER=$(tr -d ' \t\r\n' < "$PROJECT_ROOT/VERSION")
[ "$EXP_VER" = "0.3.1" ] || fail "ERR-07: VERSION dosyası 0.3.1 olmalı (mevcut: $EXP_VER)"

# (2) Check source app.json files
SRC_KS_VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PARENT_ROOT/kobra-screen/app.json" | head -n 1)
[ "$SRC_KS_VER" = "0.3.1" ] || fail "ERR-07: kobra-screen/app.json version 0.3.1 olmalı (mevcut: $SRC_KS_VER)"

SRC_ACE_VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PARENT_ROOT/ace-standalone/app.json" | head -n 1)
[ "$SRC_ACE_VER" = "0.3.1" ] || fail "ERR-07: ace-standalone/app.json version 0.3.1 olmalı (mevcut: $SRC_ACE_VER)"

SRC_GUARD_VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PROJECT_ROOT/apps/05-open-kobra-guard/app.json" | head -n 1)
[ "$SRC_GUARD_VER" = "0.3.1" ] || fail "ERR-07: apps/05-open-kobra-guard/app.json version 0.3.1 olmalı (mevcut: $SRC_GUARD_VER)"

# (3) Check unpack_install packaged app.json files
PKG_KS_VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/kobra-screen/app.json" | head -n 1)
[ "$PKG_KS_VER" = "0.3.1" ] || fail "ERR-07: paket kobra-screen/app.json version 0.3.1 olmalı (mevcut: $PKG_KS_VER)"

PKG_ACE_VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/ace-standalone/app.json" | head -n 1)
[ "$PKG_ACE_VER" = "0.3.1" ] || fail "ERR-07: paket ace-standalone/app.json version 0.3.1 olmalı (mevcut: $PKG_ACE_VER)"

PKG_GUARD_VER=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/unpack_install/update_swu/apps/05-open-kobra-guard/app.json" | head -n 1)
[ "$PKG_GUARD_VER" = "0.3.1" ] || fail "ERR-07: paket 05-open-kobra-guard/app.json version 0.3.1 olmalı (mevcut: $PKG_GUARD_VER)"

# (4) Verify that build.sh strictly enforces version match for all three app.json files
ERR7_VT="$WORK/err07_vt"
rm -rf "$ERR7_VT"
mkdir -p "$ERR7_VT/open-kobra" "$ERR7_VT/fake_ks" "$ERR7_VT/fake_as"
(cd "$PROJECT_ROOT" && tar -cf - --exclude=./dist .) | (cd "$ERR7_VT/open-kobra" && tar -xf -)
for LNK in leviq3-klipper ace-driver; do
    ln -s "$PARENT_ROOT/$LNK" "$ERR7_VT/$LNK"
done
(cd "$PARENT_ROOT/kobra-screen" && tar -cf - .) | (cd "$ERR7_VT/fake_ks" && tar -xf -)
(cd "$PARENT_ROOT/ace-standalone" && tar -cf - .) | (cd "$ERR7_VT/fake_as" && tar -xf -)
ln -s "$ERR7_VT/fake_ks" "$ERR7_VT/kobra-screen"
ln -s "$ERR7_VT/fake_as" "$ERR7_VT/ace-standalone"

# Test build fails if kobra-screen/app.json is mismatched
sed 's/"version": "[^"]*"/"version": "0.1.0"/' "$ERR7_VT/fake_ks/app.json" > "$ERR7_VT/fake_ks/app.json.tmp"
mv "$ERR7_VT/fake_ks/app.json.tmp" "$ERR7_VT/fake_ks/app.json"
if (cd "$ERR7_VT/open-kobra" && OPEN_KOBRA_DIST_DIR="$ERR7_VT/out" ./build.sh >/dev/null 2>&1); then
    fail "ERR-07: kobra-screen/app.json sürüm uyuşmazlığında build.sh başarısız olmalıydı"
fi

# Restore kobra-screen version and test ace-standalone mismatch
sed 's/"version": "[^"]*"/"version": "0.3.1"/' "$ERR7_VT/fake_ks/app.json" > "$ERR7_VT/fake_ks/app.json.tmp"
mv "$ERR7_VT/fake_ks/app.json.tmp" "$ERR7_VT/fake_ks/app.json"
sed 's/"version": "[^"]*"/"version": "0.1"/' "$ERR7_VT/fake_as/app.json" > "$ERR7_VT/fake_as/app.json.tmp"
mv "$ERR7_VT/fake_as/app.json.tmp" "$ERR7_VT/fake_as/app.json"
if (cd "$ERR7_VT/open-kobra" && OPEN_KOBRA_DIST_DIR="$ERR7_VT/out" ./build.sh >/dev/null 2>&1); then
    fail "ERR-07: ace-standalone/app.json sürüm uyuşmazlığında build.sh başarısız olmalıydı"
fi

# Restore ace-standalone version and test 05-open-kobra-guard mismatch
sed 's/"version": "[^"]*"/"version": "0.3.1"/' "$ERR7_VT/fake_as/app.json" > "$ERR7_VT/fake_as/app.json.tmp"
mv "$ERR7_VT/fake_as/app.json.tmp" "$ERR7_VT/fake_as/app.json"
sed 's/"version": "[^"]*"/"version": "0.2.1"/' "$ERR7_VT/open-kobra/apps/05-open-kobra-guard/app.json" > "$ERR7_VT/guard.tmp"
mv "$ERR7_VT/guard.tmp" "$ERR7_VT/open-kobra/apps/05-open-kobra-guard/app.json"
if (cd "$ERR7_VT/open-kobra" && OPEN_KOBRA_DIST_DIR="$ERR7_VT/out" ./build.sh >/dev/null 2>&1); then
    fail "ERR-07: 05-open-kobra-guard/app.json sürüm uyuşmazlığında build.sh başarısız olmalıydı"
fi

pass "ERR-07: VERSION=0.3.1 iken tüm app.json dosyalarının 0.3.1 olduğu ve uyuşmazlıkta build.sh'ın reddettiği doğrulandı"

echo "24. ERR-08: rescue.sh, 05-open-kobra-guard/app.sh ve update.sh içinde {32} regex kalmamalı..."
# (1) grep check across the 3 updated files: grep -n '{32}' MUST return empty (exit code 1)
ERR8_MATCHES=""
if ERR8_MATCHES=$(grep -n '{32}' \
    "$PROJECT_ROOT/rescue/rescue.sh" \
    "$PROJECT_ROOT/apps/05-open-kobra-guard/app.sh" \
    "$PROJECT_ROOT/swu/install/update.sh" 2>/dev/null); then
    fail "ERR-08: dosyalarda {32} regex ifadesi bulundu: $ERR8_MATCHES"
fi
[ -z "$ERR8_MATCHES" ] || fail "ERR-08: {32} regex boş dönmeli"

# (2) Unit-test the BusyBox-compatible awk validation expression
test_md5_regex() {
    TEST_STR="$1"
    printf '%s\n' "$TEST_STR" | awk 'length($0) == 32 && $0 ~ /^[0-9a-fA-F]+$/ { ok=1 } END { exit !ok }'
}

# Valid 32-hex digests (lower, upper, mixed) must pass
test_md5_regex "b29684d20aa7e5d73bdfdab543d72d9f" || fail "ERR-08: geçerli küçük harf 32 hex reddedildi"
test_md5_regex "B29684D20AA7E5D73BDFDAB543D72D9F" || fail "ERR-08: geçerli büyük harf 32 hex reddedildi"
test_md5_regex "B29684d20aa7e5d73bdfdab543d72d9F" || fail "ERR-08: geçerli karışık harf 32 hex reddedildi"

# Invalid digests must be rejected
if test_md5_regex "b29684d20aa7e5d73bdfdab543d72d9"; then
    fail "ERR-08: 31 karakter kabul edilmemeli"
fi
if test_md5_regex "b29684d20aa7e5d73bdfdab543d72d9fa"; then
    fail "ERR-08: 33 karakter kabul edilmemeli"
fi
if test_md5_regex "b29684d20aa7e5d73bdfdab543d72d9g"; then
    fail "ERR-08: hex dışı karakter kabul edilmemeli"
fi
if test_md5_regex "b29684d2 0aa7e5d73bdfdab543d72d9"; then
    fail "ERR-08: boşluk içeren metin kabul edilmemeli"
fi
if test_md5_regex ""; then
    fail "ERR-08: boş metin kabul edilmemeli"
fi

pass "ERR-08: 3 dosyada {32} regex kalmadığı ve BusyBox uyumlu awk MD5 kontrolünün doğruluğu onaylandı"

echo "24b. Paket 5: REV-04 (yeni davranış), REV-26, REV-27, tek Python derleme, rescue -nt/-ot ve guard yorum toleransı..."
# (1) Yeni davranış: Temiz kurulum open_kobra_limits_zz_ekstruzyon_yasak.cfg OLUŞTURMAZ
# ve kobra-screen.config extrusion_lock OLMAZ (boş {} veya extrusion_lock: false).
P5_R="$WORK/p5_rev04_clean"
make_fake_root "$P5_R"
run_swu_on_fake_root "$INSTALL_SWU" "$P5_R" >/dev/null || fail "Yeni davranış: temiz kurulum başarısız"
ZZ_CFG="$P5_R/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg"
KS_CFG="$P5_R/useremain/home/rinkhals/apps/kobra-screen.config"
[ ! -e "$ZZ_CFG" ] || fail "Yeni davranış: temiz kurulumda open_kobra_limits_zz_ekstruzyon_yasak.cfg oluşturulmamalı"
[ -f "$KS_CFG" ] || fail "Yeni davranış: kobra-screen.config oluşturulmalı"
python3 - "$KS_CFG" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
# extrusion_lock yok veya false olmalı
assert d.get("extrusion_lock") is not True, d
PY

# (2) Eski şablon dosyası (open-kobra varsayilan guvenlik kilidi yorumu + min_extrude_temp: 320)
# yükseltmede silinmeli.
P5_UPG_OLD="$WORK/p5_upg_old_template"
make_fake_root "$P5_UPG_OLD"
# Eski şablon dosyasını oluştur (install.sh'n eski mantığıyla üretilmiş gibi)
printf '# open-kobra varsayilan guvenlik kilidi (hotend sogutma fani yokken nozuldan ekstruzyon yasak)\n[extruder]\nmin_extrude_temp: 320\n' > "$P5_UPG_OLD/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg"
# kobra-screen.config'te extrusion_lock yok
printf '{"language": "en"}\n' > "$P5_UPG_OLD/useremain/home/rinkhals/apps/kobra-screen.config"
run_swu_on_fake_root "$INSTALL_SWU" "$P5_UPG_OLD" >/dev/null || fail "Yeni davranış: eski şablon dosyası olan yükseltme başarısız"
# Eski şablon dosyası silinmeli
[ ! -e "$P5_UPG_OLD/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg" ] || fail "Yeni davranış: eski şablon _zz_ dosyası yükseltmede silinmeli"
# Log'da silme mesajı olmalı (test için sadece dosya yokluğunu kontrol ediyoruz)
# kobra-screen.config extrusion_lock eklenmemeli
python3 - "$P5_UPG_OLD/useremain/home/rinkhals/apps/kobra-screen.config" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d.get("extrusion_lock") is not True, d
assert d.get("language") == "en", d
PY

# (2b) Elle konmuş geçici kilit ("open-kobra GECICI GUVENLIK" yorumu, yazıcıdaki gerçek dosya) silinmeli;
# aynı yorumla ama değeri değiştirilmiş dosya korunmalı
P5_GEC="$WORK/p5_gecici"
make_fake_root "$P5_GEC"
ZZ_GEC="$P5_GEC/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg"
printf '# open-kobra GECICI GUVENLIK: hotend sogutma fani takili degil -> nozuldan ekstruzyon yasak (2026-11-18).\n# Fan takilinca bu dosyayi silin ve FIRMWARE_RESTART yapin.\n[extruder]\nmin_extrude_temp: 320\n' > "$ZZ_GEC"
run_swu_on_fake_root "$INSTALL_SWU" "$P5_GEC" >/dev/null || fail "GECICI kilit dosyası olan yükseltme başarısız"
[ ! -e "$ZZ_GEC" ] || fail "GECICI kilit dosyası yükseltmede silinmeli"
P5_GEC2="$WORK/p5_gecici_degisik"
make_fake_root "$P5_GEC2"
ZZ_GEC2="$P5_GEC2/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg"
printf '# open-kobra varsayilan guvenlik kilidi\n[extruder]\nmin_extrude_temp: 250\n' > "$ZZ_GEC2"
run_swu_on_fake_root "$INSTALL_SWU" "$P5_GEC2" >/dev/null || fail "Değiştirilmiş şablon dosyası olan yükseltme başarısız"
has_lit "$ZZ_GEC2" "min_extrude_temp: 250" || fail "Değeri değiştirilmiş şablon dosyası korunmalı"

# (3) Kullanıcı düzenlemesi olan _zz_ dosyası (şablon dışı) korunmalı
P5_KEEP="$WORK/p5_keep_user"
make_fake_root "$P5_KEEP"
# Kullanıcının kendi min_extrude_temp ve yorumu
printf '# kullanıcı kendi ayarı\n[extruder]\nmin_extrude_temp: 175\n' > "$P5_KEEP/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg"
# extrusion_lock=false olan kobra-screen.config
printf '{"extrusion_lock": false, "language": "en"}\n' > "$P5_KEEP/useremain/home/rinkhals/apps/kobra-screen.config"
run_swu_on_fake_root "$INSTALL_SWU" "$P5_KEEP" >/dev/null || fail "Yeni davranış: kullanıcı düzenlemesi koruma kurulumu başarısız"
# _zz_ dosyası korunmalı
has_lit "$P5_KEEP/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg" "min_extrude_temp: 175" || fail "Yeni davranış: kullanıcı _zz_ dosyası ezilmemeli"
# kobra-screen.config extrusion_lock=false korunmalı
python3 - "$P5_KEEP/useremain/home/rinkhals/apps/kobra-screen.config" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d.get("extrusion_lock") is False, d
assert d.get("language") == "en", d
PY

# (4) REV-27: Uninstall must preserve kobra-screen.config (and ace-standalone.config), and clean _zz_ file.
run_swu_on_fake_root "$UNINSTALL_SWU" "$P5_KEEP" >/dev/null || fail "REV-27: kaldırma başarısız"
[ -f "$P5_KEEP/useremain/home/rinkhals/apps/kobra-screen.config" ] || fail "REV-27: kaldırma kobra-screen.config dosyasını silmemeliydi"
python3 - "$P5_KEEP/useremain/home/rinkhals/apps/kobra-screen.config" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d.get("extrusion_lock") is False and d.get("language") == "en", d
PY
# Temiz kurulum rootunda _zz_ dosyası zaten yok, uninstall da temiz
run_swu_on_fake_root "$UNINSTALL_SWU" "$P5_R" >/dev/null || fail "Yeni davranış: temiz kaldırma başarısız"
[ ! -e "$ZZ_CFG" ] || fail "Yeni davranış: kaldırma sonrası open_kobra_limits_zz_ekstruzyon_yasak.cfg temizlenmeliydi"

# (3) REV-27 & REV-30: No -nt/-ot in rescue.sh, no find -maxdepth/-path in update.sh/uninstall/update.sh
if grep -nE '\[.*-(nt|ot)[[:space:]]' "$PROJECT_ROOT/rescue/rescue.sh"; then
    fail "REV-27: rescue.sh içinde POSIX dışı -nt/-ot bulundu"
fi
if grep -nE 'find[[:space:]].*-(maxdepth|path)' "$PROJECT_ROOT/swu/install/update.sh" "$PROJECT_ROOT/swu/uninstall/update.sh"; then
    fail "REV-30: update.sh içinde BusyBox dışı find -maxdepth/-path bulundu"
fi

# (4) REV-27: guard/app.sh tolerates user comments (# {% if ..., gcode: # note) in open_kobra_homing.cfg
P5_GRD="$WORK/p5_guard_cmt"
make_fake_root "$P5_GRD"
run_swu_on_fake_root "$INSTALL_SWU" "$P5_GRD" >/dev/null || fail "REV-27 guard: kurulum başarısız"
HOMING_LIVE="$P5_GRD/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg"
printf '\n# user comment with unclosed {% if foo\n' >> "$HOMING_LIVE"
awk '/^[[:space:]]*gcode:[[:space:]]*$/ { print "gcode: # user note"; next } { print }' "$HOMING_LIVE" > "$HOMING_LIVE.tmp" && mv "$HOMING_LIVE.tmp" "$HOMING_LIVE"
cp -p "$HOMING_LIVE" "$WORK/p5_homing_expected"
OPEN_KOBRA_ROOT="$P5_GRD" "$P5_GRD/useremain/home/rinkhals/apps/05-open-kobra-guard/app.sh" start || fail "REV-27 guard start başarısız"
cmp -s "$HOMING_LIVE" "$WORK/p5_homing_expected" || fail "REV-27: guard yorumlu open_kobra_homing.cfg dosyasını bozuk sanıp ezdi"
has_lit "$P5_GRD/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg" "[include open_kobra_homing.cfg]" || fail "REV-27: guard homing include satırını kapattı"

# (5) REV-27: Upgrade rollback restores stock/$FW_VER, targets.list, and kobra-screen.config;
# and update.sh refuses hook when existing start.sh.orig is corrupt & live start.sh is bad.
P5_UPG="$WORK/p5_upg_rb"
rt_make_root "$P5_UPG" 0 2.4.6.7 "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh"
printf '{"language": "tr"}\n' > "$P5_UPG/useremain/home/rinkhals/apps/kobra-screen.config"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$P5_UPG" >/dev/null || fail "REV-27: ilk kurulum başarısız"
cp -p "$P5_UPG/useremain/bak/open-kobra-targets.list" "$WORK/p5_targets_before"
cp -p "$P5_UPG/useremain/open-kobra/rescue/stock/2.4.6.7/gkapi" "$WORK/p5_gkapi_before"
# Trigger rollback at version_marker on upgrade when binary check fails (modifies rescue/stock + binary_repair.disabled + targets)
printf 'CORRUPT_NOW\n' > "$P5_UPG/userdata/app/gk/gkapi"
if OPEN_KOBRA_FAIL_AT=version_marker PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$P5_UPG" >/dev/null 2>&1; then
    fail "REV-27: version_marker hatası başarısız olmalıydı"
fi
[ -f "$P5_UPG/useremain/open-kobra/rescue/stock/2.4.6.7/.verified" ] || fail "REV-27: yükseltme rollback stock/2.4.6.7/.verified geri yüklemedi"
cmp -s "$P5_UPG/useremain/open-kobra/rescue/stock/2.4.6.7/gkapi" "$WORK/p5_gkapi_before" || fail "REV-27: yükseltme rollback stock/2.4.6.7/gkapi geri yüklemedi"
[ ! -e "$P5_UPG/useremain/open-kobra/rescue/binary_repair.disabled" ] || fail "REV-27: yükseltme rollback binary_repair.disabled temizlemedi"
cmp -s "$P5_UPG/useremain/bak/open-kobra-targets.list" "$WORK/p5_targets_before" || fail "REV-27: yükseltme rollback open-kobra-targets.list geri yüklemedi"

# Corrupt start.sh.orig with matching .fw + unhooked truncated start.sh -> update.sh:1506 must NOT add hook!
P5_ORIG="$WORK/p5_bad_orig_1506"
rt_make_root "$P5_ORIG" 0 2.4.6.7 "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh"
mkdir -p "$P5_ORIG/useremain/open-kobra/rescue"
printf 'echo CORRUPT_ORIG\n' > "$P5_ORIG/useremain/open-kobra/rescue/start.sh.orig"
printf '2.4.6.7\n' > "$P5_ORIG/useremain/open-kobra/rescue/start.sh.orig.fw"
head -n 8 "$PROJECT_ROOT/rescue/manifests/K3_2.4.6.7.start.sh" > "$P5_ORIG/userdata/app/gk/start.sh"
cp -p "$P5_ORIG/userdata/app/gk/start.sh" "$WORK/p5_orig_start_saved"
PATH="$RES_BIN:$PATH" run_swu_on_fake_root "$INSTALL_SWU" "$P5_ORIG" >/dev/null || fail "REV-27: bad_orig kurulumu başarısız olmamalı"
cmp -s "$P5_ORIG/userdata/app/gk/start.sh" "$WORK/p5_orig_start_saved" || fail "REV-27: bozuk start.sh.orig + yarım start.sh varken kanca EKLENMEMELİYDİ"

# (6) Single Python compilation process during install (18 separate spawns avoided)
P5_ACE="$WORK/p5_ace_fake"
mkdir -p "$P5_ACE/klippy/extras/ace"
printf '# __init__\n' > "$P5_ACE/klippy/extras/ace/__init__.py"
for MOD_I in 1 2 3 4 5; do
    printf 'X = %d\n' "$MOD_I" > "$P5_ACE/klippy/extras/ace/mod_${MOD_I}.py"
done
printf '# temp\n' > "$P5_ACE/klippy/extras/temperature_ace.py"
printf '0.3.0\n' > "$P5_ACE/VERSION"
OPEN_KOBRA_ACE_DRIVER_SRC="$P5_ACE" OPEN_KOBRA_DIST_DIR="$WORK/p5_ace_dist" "$PROJECT_ROOT/build.sh" >/dev/null 2>&1 || fail "Tek Python testi için build başarısız"
P5_PY_SWU="$WORK/p5_ace_dist/install/aGVscF9zb3Nf/update.swu"
P5_PY_BIN="$WORK/p5_py_bin"
P5_PY_LOG="$WORK/p5_py_calls.log"
mkdir -p "$P5_PY_BIN"
: > "$P5_PY_LOG"
REAL_PY3=$(command -v python3)
cat > "$P5_PY_BIN/python3" <<EOF
#!/bin/sh
printf 'call:%s\n' "\$*" >> "$P5_PY_LOG"
exec "$REAL_PY3" "\$@"
EOF
chmod +x "$P5_PY_BIN/python3"
P5_PY_R="$WORK/p5_py_root"
make_fake_root "$P5_PY_R"
PATH="$P5_PY_BIN:$PATH" run_swu_on_fake_root "$P5_PY_SWU" "$P5_PY_R" >/dev/null || fail "Tek Python testi kurulumu başarısız"
PY_COMPILE_CALLS=$(awk -v p="$P5_PY_R/useremain/update_swu/leviq3/leviq3.py" 'index($0, p) > 0 { c++ } END { print c+0 }' "$P5_PY_LOG")
[ "$PY_COMPILE_CALLS" -eq 1 ] || fail "update.sh leviq3 + ace-driver derleme denetimini tek Python sürecinde yapmalıydı (çağrı: $PY_COMPILE_CALLS)"

# (7) REV-26: kobra-screen/app.sh stop/supervisor race and touch_swap_axes default override
KS_APP_SH="$PARENT_ROOT/kobra-screen/app.sh"
P5_KS="$WORK/p5_ks_test"
mkdir -p "$P5_KS/rinkhals/opt/rinkhals/ui" "$P5_KS/home/apps/kobra-screen" "$P5_KS/proc" "$P5_KS/logs" "$P5_KS/gk"
K3_FLAG="$P5_KS/k3_started.flag"
K3_BIN="$P5_KS/gk/K3SysUi"
printf '#!/bin/sh\n: > "%s"\n' "$K3_FLAG" > "$K3_BIN"
chmod +x "$K3_BIN"
cat > "$P5_KS/rinkhals/tools.sh" <<'EOF'
APP_STATUS_STARTED='started'
APP_STATUS_STOPPED='stopped'
log() { :; }
report_status() { echo "Status: $1,$2,$3"; }
get_app_property() {
    if [ "$2" = "touch_swap_axes" ]; then
        echo "false"
    else
        echo ""
    fi
}
EOF
KS_ENV_OUT="$P5_KS/ks_env.out"
KS_RUN_COUNT="$P5_KS/ks_run.count"
KS_FAKE_PY="$P5_KS/fake_py.sh"
cat > "$KS_FAKE_PY" <<EOF
#!/bin/sh
echo "run" >> "$KS_RUN_COUNT"
printf 'SWAP=%s\n' "\${KOBRA_TOUCH_SWAP_AXES:-UNSET}" > "$KS_ENV_OUT"
if [ "\${KS_CRASH_ONCE:-0}" = "1" ] && [ "\$(wc -l < "$KS_RUN_COUNT" | tr -d ' ')" -eq 1 ]; then
    exit 1
fi
trap 'exit 143' TERM
while :; do sleep 0.1; done
EOF
chmod +x "$KS_FAKE_PY"

ks_run_sh() {
    RINKHALS_ROOT="$P5_KS/rinkhals" \
    RINKHALS_HOME="$P5_KS/home" \
    RINKHALS_LOGS="$P5_KS/logs" \
    TEMPORARY_APP_PATH="$P5_KS/tmp_apps" \
    USER_APP_PATH="$P5_KS/home/apps" \
    KOBRA_PID_FILE="$P5_KS/ks.pid" \
    KOBRA_HEARTBEAT_FILE="$P5_KS/ks.hb" \
    KOBRA_STOP_MARKER="$P5_KS/ks.stopping" \
    KOBRA_TRIAL_MARKER="$P5_KS/ks.trial" \
    KOBRA_SUPERVISOR_PID_FILE="$P5_KS/ks.sup.pid" \
    KOBRA_PROC_ROOT="$P5_KS/proc" \
    KOBRA_SYS_NET="$P5_KS/sys_net" \
    KOBRA_STOCK_GK_DIR="$P5_KS/gk" \
    KOBRA_PYTHON_BIN="$KS_FAKE_PY" \
    KOBRA_MAX_CRASHES="3" \
    sh "$KS_APP_SH" "$@"
}

# 7a) app.json default touch_swap_axes="false" without .config entry must NOT export KOBRA_TOUCH_SWAP_AXES
ks_run_sh start || fail "REV-26: kobra-screen app.sh start başarısız"
sleep 0.4
has_line "$KS_ENV_OUT" "SWAP=UNSET" || fail "REV-26: varsayılan touch_swap_axes='false' KOBRA_TOUCH_SWAP_AXES dışa aktarmamalıydı ($(cat "$KS_ENV_OUT"))"

# 7b) app.sh stop when child exits non-zero (143 on TERM) must NOT start K3SysUi
ks_run_sh stop || fail "REV-26: kobra-screen app.sh stop başarısız"
sleep 0.3
[ ! -e "$K3_FLAG" ] || fail "REV-26: app.sh stop sırasında K3SysUi başlatılmamalıydı"

# 7c) app.sh start while supervisor is in 1s restart sleep window must stop old supervisor cleanly
: > "$KS_RUN_COUNT"
KS_CRASH_ONCE=1 ks_run_sh start || fail "REV-26: crash-once start başarısız"
sleep 0.3
# Now supervisor #1 is sleeping 1s before relaunch; call start again!
KS_CRASH_ONCE=0 ks_run_sh start || fail "REV-26: ikinci start başarısız"
sleep 1.4
SUP_ALIVE=0
if [ -f "$P5_KS/ks.sup.pid" ]; then
    SP_ID=$(tr -d '[:space:]' < "$P5_KS/ks.sup.pid")
    kill -0 "$SP_ID" 2>/dev/null && SUP_ALIVE=1
fi
[ "$SUP_ALIVE" -eq 1 ] || fail "REV-26: yeni supervisor ayakta olmalı"
LIVE_PY_CNT=$(wc -l < "$KS_RUN_COUNT" | tr -d ' ')
[ "$LIVE_PY_CNT" -eq 2 ] || fail "REV-26: eski supervisor sleep penceresinde durdurulmalıydı (beklenen 2 çalıştırma, gerçek: $LIVE_PY_CNT)"
ks_run_sh stop || fail "REV-26: temizlik stop başarısız"

# 7d) Explicit .config touch_swap_axes="false" IS exported
printf '{"touch_swap_axes": "false"}\n' > "$P5_KS/home/apps/kobra-screen.config"
ks_run_sh start || fail "REV-26: explicit config start başarısız"
sleep 0.4
has_line "$KS_ENV_OUT" "SWAP=false" || fail "REV-26: açık kobra-screen.config touch_swap_axes='false' dışa aktarılmalıydı"
ks_run_sh stop || fail "REV-26: final stop başarısız"

pass "Paket 5 (REV-04, REV-26, REV-27, tek Python, POSIX rescue/guard) testleri geçti"

echo "25. numpy (isteğe bağlı) paketleme, kurulum ve kaldırma testleri..."
# 25a. Build includes numpy wheel and MANIFEST.md5 entry
[ -f "$WORK/unpack_install/update_swu/python/numpy-1.24.4-cp311-cp311-linux_armv7l.whl" ] || fail "numpy wheel pakette yok"
has_lit "$WORK/unpack_install/update_swu/MANIFEST.md5" "./python/numpy-1.24.4-cp311-cp311-linux_armv7l.whl" || fail "MANIFEST.md5 numpy wheel'i kapsamıyor"

# 25b. Build without numpy (OPEN_KOBRA_NUMPY_WHEEL=/yok) succeeds and summary says "atlandı"
NO_NUMPY_DIST="$WORK/dist_no_numpy"
OPEN_KOBRA_NUMPY_WHEEL=/yok OPEN_KOBRA_DIST_DIR="$NO_NUMPY_DIST" "$PROJECT_ROOT/build.sh" > "$WORK/build_no_numpy.log" 2>&1 || fail "numpy yokken derleme başarısız"
has_lit "$WORK/build_no_numpy.log" "numpy" && has_lit "$WORK/build_no_numpy.log" "atlandı" || fail "Derleme özeti 'numpy' ve 'atlandı' içermiyor: $(cat "$WORK/build_no_numpy.log")"
# Check the package doesn't have numpy
rm -rf "$WORK/unpack_no_numpy"
extract_swu "$NO_NUMPY_DIST/install/aGVscF9zb3Nf/update.swu" "$WORK/unpack_no_numpy"
[ ! -e "$WORK/unpack_no_numpy/update_swu/python/numpy-1.24.4-cp311-cp311-linux_armv7l.whl" ] || fail "numpy atlandığında pakette numpy wheel olmamalı"

# 25c. Install creates numpy files and marker in fake vanilla-klipper site-packages
NP_TEST="$WORK/numpy_test_root"
make_fake_root "$NP_TEST"
# Create the site-packages directory that the installer expects
mkdir -p "$NP_TEST/useremain/home/rinkhals/apps/vanilla-klipper/lib/python3.11/site-packages"
run_swu_on_fake_root "$INSTALL_SWU" "$NP_TEST" >/dev/null || fail "numpy test kurulumu başarısız"
NP_SITE="$NP_TEST/useremain/home/rinkhals/apps/vanilla-klipper/lib/python3.11/site-packages"
[ -f "$NP_SITE/numpy/__init__.py" ] || fail "numpy/__init__.py oluşturulmadı"
[ -f "$NP_SITE/numpy-1.24.4.dist-info/METADATA" ] || fail "numpy-1.24.4.dist-info/METADATA oluşturulmadı"
[ -f "$NP_SITE/numpy/.open-kobra-installed" ] || fail "numpy/.open-kobra-installed işaret dosyası oluşturulmadı"

# 25d. Second install says "zaten kurulu" and leaves files intact
run_swu_on_fake_root "$INSTALL_SWU" "$NP_TEST" >/dev/null || fail "numpy ikinci kurulum başarısız"
[ -f "$NP_SITE/numpy/__init__.py" ] || fail "İkinci kurulum numpy/__init__.py'yi silmedi"
[ -f "$NP_SITE/numpy-1.24.4.dist-info/METADATA" ] || fail "İkinci kurulum numpy-1.24.4.dist-info'yi silmedi"

# 25e. Install succeeds with warning when site-packages dir is missing
NP_NOSITE="$WORK/numpy_nosite_root"
make_fake_root "$NP_NOSITE"
rm -rf "$NP_NOSITE/useremain/home/rinkhals/apps/vanilla-klipper/lib"
run_swu_on_fake_root "$INSTALL_SWU" "$NP_NOSITE" >/dev/null || fail "site-packages yokken kurulum başarısız olmamalı (sadece uyarı)"
has_lit "$NP_NOSITE/useremain/rinkhals/open-kobra-install.log" "numpy hedef dizini yok" || fail "site-packages yokken uyarı loglanmalı"

# 25f. Uninstall removes numpy when marker exists, leaves user-installed numpy alone
run_swu_on_fake_root "$UNINSTALL_SWU" "$NP_TEST" >/dev/null || fail "numpy işaretli kaldırma başarısız"
[ ! -e "$NP_SITE/numpy" ] || fail "Kaldırma numpy/ dizinini silmedi"
[ ! -e "$NP_SITE/numpy-1.24.4.dist-info" ] || fail "Kaldırma numpy-1.24.4.dist-info'yi silmedi"

# User-installed numpy (no marker) should not be touched
NP_USER="$WORK/numpy_user_root"
make_fake_root "$NP_USER"
mkdir -p "$NP_USER/useremain/home/rinkhals/apps/vanilla-klipper/lib/python3.11/site-packages"
run_swu_on_fake_root "$INSTALL_SWU" "$NP_USER" >/dev/null || fail "kullanıcı numpy testi kurulumu başarısız"
USER_NP_SITE="$NP_USER/useremain/home/rinkhals/apps/vanilla-klipper/lib/python3.11/site-packages"
# Remove marker to simulate user-installed numpy
rm -f "$USER_NP_SITE/numpy/.open-kobra-installed"
run_swu_on_fake_root "$UNINSTALL_SWU" "$NP_USER" >/dev/null || fail "kullanıcı numpy'suz kaldırma başarısız"
[ -f "$USER_NP_SITE/numpy/__init__.py" ] || fail "Kullanıcının numpy'su işaretsiz kaldırılmamalıydı"

pass "numpy paketleme, kurulum ve kaldırma testleri geçti"

echo "26. kobra-screen/app.sh harici girdi POSIX/BusyBox/shellcheck denetimi (kapsam dışı dosya)..."
KS_SCAN_OK=1
if ! python3 "$WORK/posix_scan.py" "$WORK/unpack_install/update_swu/apps/kobra-screen/app.sh"; then
    KS_SCAN_OK=0
fi
if command -v shellcheck >/dev/null 2>&1; then
    if ! shellcheck "$WORK/unpack_install/update_swu/apps/kobra-screen/app.sh"; then
        KS_SCAN_OK=0
    fi
fi

if [ "$KS_SCAN_OK" = "1" ]; then
    pass "kobra-screen/app.sh POSIX/BusyBox denetimi geçti"
    echo "Tüm testler (kobra-screen/app.sh dahil) başarıyla geçti."
    exit 0
else
    printf '  [FAIL] kobra-screen/app.sh POSIX/BusyBox taraması başarısız (harici girdi - open-kobra kapsamı dışı; diğer 1-14. adımların tamamı geçti)\n' >&2
    exit 1
fi

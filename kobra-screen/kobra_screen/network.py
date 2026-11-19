"""Wi-Fi management and supervision for open mode (wpa_supplicant / wpa_cli / udhcpc)."""

from dataclasses import dataclass
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time

DEFAULT_INTERFACE = "wlan0"
DEFAULT_CTRL_INTERFACE = "/var/run/wpa_supplicant"
WPA_CONF_CANDIDATES = (
    "/userdata/wifi_cfg/wpa_supplicant.conf",
    "/data/wifi_cfg/wpa_supplicant.conf",
    "/etc/wpa_supplicant.conf",
)
BIN_SEARCH_DIRS = ("/usr/sbin", "/sbin", "/usr/bin", "/bin")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_KV_QUOTED_RE = re.compile(r'^([A-Za-z0-9_]+)\s*=\s*"((?:[^"\\]|\\.)*)"(?:\s*#.*)?$')
_KV_PLAIN_RE = re.compile(r"^([A-Za-z0-9_]+)\s*=\s*([^\s#]+)(?:\s*#.*)?$")
_INET_ADDR_RE = re.compile(r"\binet(?:\s+addr:|\s+)(\d+\.\d+\.\d+\.\d+)")


class NetworkError(Exception):
    """Wi-Fi operation failure; messages never contain credentials."""


@dataclass
class WifiStatus:
    interface: str = DEFAULT_INTERFACE
    iface_up: bool = False
    wpa_running: bool = False
    udhcpc_running: bool = False
    state: str = "DISCONNECTED"
    ssid: str = ""
    bssid: str = ""
    ip_address: str = ""
    signal_dbm: object = None
    saved_ssids: tuple = ()
    wpa_conf_path: str = ""
    error: str = ""


@dataclass
class WifiNetwork:
    ssid: str
    bssid: str = ""
    frequency: int = 0
    signal_dbm: object = None
    flags: str = ""
    secured: bool = False
    saved: bool = False
    connected: bool = False


class NullWifiManager:
    """No-op Wi-Fi manager used in unit tests and offline previews when no backend is supplied."""

    def get_status(self):
        return WifiStatus(state="UNAVAILABLE", error="Wi-Fi yöneticisi etkin değil")

    def scan(self):
        return []

    def scan_results(self):
        return []

    def connect(self, ssid, password=None, flags=""):
        raise NetworkError("Wi-Fi yöneticisi etkin değil")

    def forget(self, ssid):
        raise NetworkError("Wi-Fi yöneticisi etkin değil")

    def ensure_connected(self):
        return False


def decode_wpa_ssid(raw):
    """Decode wpa_cli \\xNN UTF-8 escapes into readable text."""
    if not raw:
        return ""
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i:i + 2] == "\\x" and i + 4 <= len(raw):
            hex_part = raw[i + 2:i + 4]
            try:
                out.append(int(hex_part, 16))
                i += 4
                continue
            except ValueError:
                pass
        if raw[i:i + 2] == "\\\\" and i + 2 <= len(raw):
            out.extend(b"\\")
            i += 2
            continue
        if raw[i:i + 2] == '\\"' and i + 2 <= len(raw):
            out.extend(b'"')
            i += 2
            continue
        out.extend(raw[i].encode("utf-8", errors="replace"))
        i += 1
    return out.decode("utf-8", errors="replace")


def _escape_wpa_quoted(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unescape_wpa_quoted(value):
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def _read_proc_cmdline(proc_dir):
    try:
        with open(os.path.join(proc_dir, "cmdline"), "rb") as f:
            raw = f.read()
        return [p.decode("utf-8", "ignore") for p in raw.split(b"\x00") if p]
    except OSError:
        return []


def _read_proc_comm(proc_dir):
    try:
        with open(os.path.join(proc_dir, "comm"), "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


def _cmdline_has_iface(args, interface):
    for i, arg in enumerate(args):
        if arg == "-i" and i + 1 < len(args) and args[i + 1] == interface:
            return True
        if arg == "-i" + interface:
            return True
    return False


def _cmdline_has_any_iface(args):
    for i, arg in enumerate(args):
        if arg == "-i" and i + 1 < len(args):
            return True
        if arg.startswith("-i") and len(arg) > 2:
            return True
    return False


def find_wpa_supplicant_pids(proc_root="/proc", interface=DEFAULT_INTERFACE):
    """Return PIDs of wpa_supplicant processes handling `interface`."""
    pids = []
    if not os.path.isdir(proc_root):
        return pids
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return pids
    for entry in entries:
        if not entry.isdigit():
            continue
        pdir = os.path.join(proc_root, entry)
        comm = _read_proc_comm(pdir)
        args = _read_proc_cmdline(pdir)
        base0 = os.path.basename(args[0]) if args else ""
        if comm != "wpa_supplicant" and base0 != "wpa_supplicant":
            continue
        if _cmdline_has_iface(args, interface) or not _cmdline_has_any_iface(args):
            pids.append(int(entry))
    return sorted(pids)


def find_udhcpc_pids(proc_root="/proc", interface=DEFAULT_INTERFACE):
    """Return PIDs of udhcpc processes bound specifically to `interface` (ignores e.g. eth0)."""
    pids = []
    if not os.path.isdir(proc_root):
        return pids
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return pids
    for entry in entries:
        if not entry.isdigit():
            continue
        pdir = os.path.join(proc_root, entry)
        comm = _read_proc_comm(pdir)
        args = _read_proc_cmdline(pdir)
        base0 = os.path.basename(args[0]) if args else ""
        if comm != "udhcpc" and base0 != "udhcpc":
            continue
        if _cmdline_has_iface(args, interface):
            pids.append(int(entry))
    return sorted(pids)


def detect_wpa_conf_from_proc(proc_root="/proc", interface=DEFAULT_INTERFACE):
    """Extract `-c <conf_path>` from a running wpa_supplicant cmdline if present."""
    for pid in find_wpa_supplicant_pids(proc_root=proc_root, interface=interface):
        args = _read_proc_cmdline(os.path.join(proc_root, str(pid)))
        for i, arg in enumerate(args):
            if arg == "-c" and i + 1 < len(args) and args[i + 1]:
                return args[i + 1]
            if arg.startswith("-c") and len(arg) > 2:
                return arg[2:]
    return ""


def resolve_wpa_conf_path(proc_root="/proc", interface=DEFAULT_INTERFACE, env=None, candidates=WPA_CONF_CANDIDATES):
    """Determine the wpa_supplicant.conf path used on the device."""
    env = os.environ if env is None else env
    explicit = env.get("KOBRA_WPA_CONF", "").strip()
    if explicit:
        return explicit
    from_proc = detect_wpa_conf_from_proc(proc_root=proc_root, interface=interface)
    if from_proc:
        return from_proc
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


def resolve_binary(name, env_var=None, env=None):
    """Locate a system binary from env override, PATH, or standard sbin/bin directories."""
    env = os.environ if env is None else env
    if env_var:
        override = env.get(env_var, "").strip()
        if override:
            return override
    found = shutil.which(name)
    if found:
        return found
    for directory in BIN_SEARCH_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def validate_ssid(ssid):
    if not isinstance(ssid, str) or not ssid:
        raise NetworkError("Ağ adı (SSID) boş olamaz")
    if any(ch in ssid for ch in ("\r", "\n", "\x00")):
        raise NetworkError("Ağ adı geçersiz karakter içeriyor")
    if len(ssid.encode("utf-8")) > 32:
        raise NetworkError("Ağ adı en fazla 32 bayt olabilir")


def validate_password(password, flags=""):
    if password is None:
        return
    if not isinstance(password, str) or not password:
        raise NetworkError("Şifre boş olamaz")
    if any(ch in password for ch in ("\r", "\n", "\x00")):
        raise NetworkError("Şifre geçersiz karakter içeriyor")
    upper_flags = str(flags or "").upper()
    is_wep_only = "WEP" in upper_flags and "WPA" not in upper_flags and "RSN" not in upper_flags
    if is_wep_only:
        return
    if _HEX_RE.match(password):
        return
    if len(password) < 8 or len(password) > 63:
        raise NetworkError("WPA şifresi 8 ile 63 karakter arasında olmalıdır")


def _brace_delta(line):
    """`{` minus `}` in one wpa_supplicant.conf line, ignoring "quoted text" (SSIDs, PSKs) and # comments."""
    delta = 0
    quoted = False
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
        elif quoted:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == "#":
            break
        elif ch == '"':
            quoted = True
        elif ch == "{":
            delta += 1
        elif ch == "}":
            delta -= 1
    return delta


def parse_wpa_conf(text):
    """Split wpa_supplicant.conf text into (header_lines, networks).

    Each item in `networks` is `(ssid_or_none, block_lines)` preserving formatting.
    """
    header = []
    networks = []
    in_block = False
    block_lines = []
    block_ssid = None
    brace_depth = 0

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not in_block:
            if re.match(r"^network\s*=\s*\{", stripped):
                in_block = True
                block_lines = [line]
                block_ssid = None
                brace_depth = _brace_delta(line)
                if brace_depth <= 0:
                    networks.append((block_ssid, block_lines))
                    in_block = False
                    block_lines = []
                continue
            header.append(line)
        else:
            block_lines.append(line)
            m_q = _KV_QUOTED_RE.match(stripped)
            if m_q and m_q.group(1) == "ssid":
                block_ssid = _unescape_wpa_quoted(m_q.group(2))
            else:
                m_p = _KV_PLAIN_RE.match(stripped)
                if m_p and m_p.group(1) == "ssid" and block_ssid is None:
                    block_ssid = m_p.group(2)
            brace_depth += _brace_delta(line)
            if brace_depth <= 0:
                networks.append((block_ssid, block_lines))
                in_block = False
                block_lines = []

    if in_block and block_lines:
        networks.append((block_ssid, block_lines))
    return header, networks


def list_saved_ssids(conf_path):
    if not conf_path or not os.path.isfile(conf_path):
        return []
    try:
        with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    _, networks = parse_wpa_conf(text)
    seen = []
    for ssid, _ in networks:
        if ssid and ssid not in seen:
            seen.append(ssid)
    return seen


def _atomic_write_private(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        if hasattr(os, "sync"):
            try:
                os.sync()
            except OSError:
                pass
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise NetworkError("Wi-Fi yapılandırması yazılamadı: %s" % exc) from exc


def backup_wpa_conf(conf_path):
    """Back up existing wpa_supplicant.conf to `<conf_path>.bak` matching stock layout."""
    if not conf_path or not os.path.isfile(conf_path):
        return ""
    try:
        with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
            existing = f.read()
    except OSError as exc:
        raise NetworkError("Mevcut Wi-Fi yapılandırması okunamadı: %s" % exc) from exc
    if not existing:
        return ""
    bak_path = conf_path + ".bak"
    _atomic_write_private(bak_path, existing)
    return bak_path


def _ensure_ctrl_interface(header_lines):
    has_ctrl = any(
        line.strip().startswith("ctrl_interface=")
        for line in header_lines
        if line.strip() and not line.strip().startswith("#")
    )
    out = list(header_lines)
    if not has_ctrl:
        out.insert(0, "ctrl_interface=%s" % DEFAULT_CTRL_INTERFACE)
    return out


def _format_network_block(ssid, password=None, flags=""):
    lines = ["network={", '\tssid="%s"' % _escape_wpa_quoted(ssid)]
    upper_flags = str(flags or "").upper()
    if password is None:
        lines.append("\tkey_mgmt=NONE")
    else:
        if "WEP" in upper_flags and "WPA" not in upper_flags and "RSN" not in upper_flags:
            lines.append("\tkey_mgmt=NONE")
            lines.append('\twep_key0="%s"' % _escape_wpa_quoted(password))
            lines.append("\twep_tx_keyidx=0")
        elif _HEX_RE.match(password):
            lines.append("\tpsk=%s" % password)
        else:
            lines.append('\tpsk="%s"' % _escape_wpa_quoted(password))
        if "SAE" in upper_flags and "PSK" not in upper_flags:
            lines.append("\tkey_mgmt=SAE")
    lines.append("}")
    return lines


def _serialize_wpa_conf(header_lines, networks):
    parts = [line for line in header_lines]
    while parts and parts[-1] == "":
        parts.pop()
    for _, block in networks:
        if parts:
            parts.append("")
        parts.extend(block)
    return "\n".join(parts) + "\n"


def upsert_wpa_network(conf_path, ssid, password=None, flags=""):
    """Backup `conf_path` to `.bak` and add/replace the `network={}` block for `ssid`."""
    validate_ssid(ssid)
    validate_password(password, flags=flags)

    existing_text = ""
    if os.path.isfile(conf_path):
        backup_wpa_conf(conf_path)
        try:
            with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
                existing_text = f.read()
        except OSError as exc:
            raise NetworkError("Wi-Fi yapılandırması okunamadı: %s" % exc) from exc

    header, networks = parse_wpa_conf(existing_text)
    header = _ensure_ctrl_interface(header)
    filtered = [(s, blk) for s, blk in networks if s != ssid]
    filtered.append((ssid, _format_network_block(ssid, password=password, flags=flags)))
    new_text = _serialize_wpa_conf(header, filtered)
    _atomic_write_private(conf_path, new_text)


def remove_wpa_network(conf_path, ssid):
    """Backup `conf_path` to `.bak` and remove all `network={}` blocks matching `ssid`."""
    validate_ssid(ssid)
    if not conf_path or not os.path.isfile(conf_path):
        return False
    backup_wpa_conf(conf_path)
    try:
        with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
            existing_text = f.read()
    except OSError as exc:
        raise NetworkError("Wi-Fi yapılandırması okunamadı: %s" % exc) from exc
    header, networks = parse_wpa_conf(existing_text)
    filtered = [(s, blk) for s, blk in networks if s != ssid]
    if len(filtered) == len(networks):
        return False
    header = _ensure_ctrl_interface(header)
    _atomic_write_private(conf_path, _serialize_wpa_conf(header, filtered))
    return True


def parse_wpa_status(output):
    data = {}
    for line in (output or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def parse_signal_poll(output):
    info = parse_wpa_status(output)
    raw = info.get("RSSI")
    if raw is None:
        return None
    try:
        val = int(float(raw))
        return val if -120 <= val <= 0 else None
    except ValueError:
        return None


def parse_scan_results(output, saved_ssids=(), current_ssid="", current_state=""):
    """Parse `wpa_cli scan_results` table into sorted, deduplicated WifiNetwork objects."""
    saved_set = set(saved_ssids or ())
    by_ssid = {}
    for line in (output or "").splitlines():
        if not line.strip():
            continue
        lower = line.lower()
        if lower.startswith("bssid") and "signal" in lower:
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        bssid = parts[0].strip()
        try:
            freq = int(parts[1].strip())
        except ValueError:
            freq = 0
        try:
            sig = int(float(parts[2].strip()))
        except ValueError:
            sig = None
        flags = parts[3].strip()
        raw_ssid = "\t".join(parts[4:])
        ssid = decode_wpa_ssid(raw_ssid).strip("\x00")
        if not ssid:
            continue
        upper_flags = flags.upper()
        secured = any(tok in upper_flags for tok in ("WPA", "RSN", "WEP", "PSK", "SAE", "EAP"))
        connected = bool(current_ssid and ssid == current_ssid and current_state == "COMPLETED")
        net = WifiNetwork(
            ssid=ssid,
            bssid=bssid,
            frequency=freq,
            signal_dbm=sig,
            flags=flags,
            secured=secured,
            saved=(ssid in saved_set),
            connected=connected,
        )
        prev = by_ssid.get(ssid)
        if prev is None or (sig is not None and (prev.signal_dbm is None or sig > prev.signal_dbm)):
            by_ssid[ssid] = net

    nets = list(by_ssid.values())
    nets.sort(key=lambda n: (
        not n.connected,
        not n.saved,
        -(n.signal_dbm if n.signal_dbm is not None else -999),
        n.ssid.lower(),
    ))
    return nets


def parse_list_networks(output):
    """Return list of `(network_id, ssid, flags)` from `wpa_cli list_networks`."""
    rows = []
    for line in (output or "").splitlines():
        if not line.strip():
            continue
        if line.lower().startswith("network id"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        net_id = parts[0].strip()
        ssid = decode_wpa_ssid(parts[1])
        flags = parts[3].strip() if len(parts) >= 4 else ""
        if net_id.isdigit():
            rows.append((net_id, ssid, flags))
    return rows


def _default_runner(cmd, input_text=None, timeout=6.0):
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)


class WifiManager:
    """High-level Wi-Fi manager and reconnect supervisor for wlan0."""

    def __init__(
        self,
        interface=None,
        conf_path=None,
        proc_root=None,
        sys_net_root=None,
        runner=None,
        signal_fn=None,
        spawn_fn=None,
        sleep_fn=time.sleep,
        clock_fn=time.monotonic,
        scan_wait=1.0,
        reconnect_cooldown=15.0,
        dhcp_cooldown=15.0,
    ):
        self.interface = interface or os.environ.get("KOBRA_WIFI_IFACE", DEFAULT_INTERFACE)
        self.proc_root = proc_root or os.environ.get("KOBRA_PROC_ROOT", "/proc")
        self.sys_net_root = sys_net_root or os.environ.get("KOBRA_SYS_NET", "/sys/class/net")
        self._explicit_conf_path = conf_path
        self._runner = runner or _default_runner
        self._signal_fn = signal_fn or os.kill
        self._spawn_fn = spawn_fn or self._default_spawn
        self._sleep = sleep_fn
        self._clock = clock_fn
        self._scan_wait = float(scan_wait)
        self._reconnect_cooldown = float(reconnect_cooldown)
        self._dhcp_cooldown = float(dhcp_cooldown)
        self._lock = threading.RLock()
        self._last_reconnect_at = 0.0
        self._last_dhcp_renew_at = 0.0
        self._busy_until = 0.0
        self._pending_dhcp_renew = None  # (ssid, time) of a network switch whose DHCP lease must be renewed

    @staticmethod
    def _default_spawn(cmd):
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )

    def conf_path(self):
        if self._explicit_conf_path:
            return self._explicit_conf_path
        return resolve_wpa_conf_path(proc_root=self.proc_root, interface=self.interface)

    def _bin(self, name, env_var=None):
        return resolve_binary(name, env_var=env_var) or name

    def _wpa_cli(self, *args, timeout=5.0):
        wpa_cli_bin = self._bin("wpa_cli", "KOBRA_WPA_CLI")
        cmd = [wpa_cli_bin, "-i", self.interface] + list(args)
        rc, out, err = self._runner(cmd, None, timeout)
        return rc, out, err

    def _is_iface_up(self):
        iface_dir = os.path.join(self.sys_net_root, self.interface)
        flags_path = os.path.join(iface_dir, "flags")
        if os.path.isfile(flags_path):
            try:
                with open(flags_path, "r", encoding="utf-8", errors="ignore") as f:
                    raw = f.read().strip()
                return bool(int(raw, 0) & 0x1)
            except (OSError, ValueError):
                pass
        rc, out, _ = self._runner([self._bin("ifconfig", "KOBRA_IFCONFIG"), self.interface], None, 3.0)
        if rc == 0 and out:
            return "UP" in out.split()
        return False

    def _bring_iface_up(self):
        iface_dir = os.path.join(self.sys_net_root, self.interface)
        if os.path.isdir(self.sys_net_root) and not os.path.isdir(iface_dir):
            return False
        if self._is_iface_up():
            return True
        rc, _, _ = self._runner([self._bin("ifconfig", "KOBRA_IFCONFIG"), self.interface, "up"], None, 4.0)
        if rc != 0:
            ip_bin = resolve_binary("ip", "KOBRA_IP")
            if ip_bin:
                rc, _, _ = self._runner([ip_bin, "link", "set", self.interface, "up"], None, 4.0)
        return rc == 0

    def _read_iface_ip(self):
        rc, out, _ = self._runner([self._bin("ifconfig", "KOBRA_IFCONFIG"), self.interface], None, 3.0)
        if rc == 0 and out:
            m = _INET_ADDR_RE.search(out)
            if m and m.group(1) != "127.0.0.1":
                return m.group(1)
        ip_bin = resolve_binary("ip", "KOBRA_IP")
        if ip_bin:
            rc, out, _ = self._runner([ip_bin, "-4", "addr", "show", self.interface], None, 3.0)
            if rc == 0 and out:
                m = _INET_ADDR_RE.search(out)
                if m:
                    return m.group(1)
        return ""

    def _start_wpa_supplicant(self):
        conf = self.conf_path()
        if not conf or not os.path.isfile(conf):
            return False
        wpa_bin = self._bin("wpa_supplicant", "KOBRA_WPA_SUPPLICANT")
        try:
            self._spawn_fn([wpa_bin, "-B", "-Dnl80211", "-i", self.interface, "-c", conf])
            return True
        except OSError:
            return False

    @staticmethod
    def _hostname():
        try:
            host = socket.gethostname().strip()
        except OSError:
            return ""
        return "" if host in ("", "localhost", "(none)") else host

    def _ensure_udhcpc_for_wlan0(self, renew=False):
        """Respect 10-hostname-dns/dhcp_watchdog: never kill existing udhcpc or touch eth0.

        Only start `udhcpc -b -i wlan0` when no wlan0 udhcpc exists; send SIGUSR1 to renew
        if `renew=True`.
        """
        pids = find_udhcpc_pids(proc_root=self.proc_root, interface=self.interface)
        if pids:
            if renew:
                for pid in pids:
                    try:
                        self._signal_fn(pid, signal.SIGUSR1)
                    except OSError:
                        pass
            return True
        udhcpc_bin = self._bin("udhcpc", "KOBRA_UDHCPC")
        cmd = [udhcpc_bin, "-b", "-i", self.interface]
        # Rinkhals' 10-hostname-dns dhcp_watchdog kills every wlan0 udhcpc that lacks "-x hostname:<name>"
        host = self._hostname()
        if host:
            cmd.extend(["-x", "hostname:" + host])
        try:
            self._spawn_fn(cmd)
            return True
        except OSError:
            return False

    def get_status(self):
        with self._lock:
            conf = self.conf_path()
            saved = tuple(list_saved_ssids(conf))
            wpa_pids = find_wpa_supplicant_pids(proc_root=self.proc_root, interface=self.interface)
            udhcpc_pids = find_udhcpc_pids(proc_root=self.proc_root, interface=self.interface)
            iface_up = self._is_iface_up()

            rc, out, _ = self._wpa_cli("status")
            if rc != 0 or "wpa_state=" not in out:
                ip_addr = self._read_iface_ip() if iface_up else ""
                err = "" if wpa_pids else "wpa_supplicant çalışmıyor"
                return WifiStatus(
                    interface=self.interface,
                    iface_up=iface_up,
                    wpa_running=bool(wpa_pids),
                    udhcpc_running=bool(udhcpc_pids),
                    state="DISCONNECTED" if wpa_pids else "UNAVAILABLE",
                    ip_address=ip_addr,
                    saved_ssids=saved,
                    wpa_conf_path=conf,
                    error=err,
                )

            info = parse_wpa_status(out)
            state = info.get("wpa_state", "DISCONNECTED")
            ssid = decode_wpa_ssid(info.get("ssid", ""))
            bssid = info.get("bssid", "")
            ip_addr = info.get("ip_address", "") or self._read_iface_ip()
            sig_rc, sig_out, _ = self._wpa_cli("signal_poll")
            signal_dbm = parse_signal_poll(sig_out) if sig_rc == 0 else None

            return WifiStatus(
                interface=self.interface,
                iface_up=iface_up or state == "COMPLETED",
                wpa_running=True,
                udhcpc_running=bool(udhcpc_pids),
                state=state,
                ssid=ssid,
                bssid=bssid,
                ip_address=ip_addr,
                signal_dbm=signal_dbm,
                saved_ssids=saved,
                wpa_conf_path=conf,
            )

    def scan_results(self):
        with self._lock:
            status = self.get_status()
            rc, out, _ = self._wpa_cli("scan_results")
            if rc != 0:
                return []
            return parse_scan_results(
                out,
                saved_ssids=status.saved_ssids,
                current_ssid=status.ssid,
                current_state=status.state,
            )

    def scan(self):
        with self._lock:
            self._busy_until = self._clock() + 5.0
            self._bring_iface_up()
            if not find_wpa_supplicant_pids(proc_root=self.proc_root, interface=self.interface):
                self._start_wpa_supplicant()
            rc, out, _ = self._wpa_cli("scan")
            if rc != 0 and "FAIL-BUSY" not in out:
                raise NetworkError("Ağ taraması başlatılamadı")
        if self._scan_wait > 0:
            self._sleep(self._scan_wait)
        return self.scan_results()

    def connect(self, ssid, password=None, flags=""):
        validate_ssid(ssid)
        validate_password(password, flags=flags)

        with self._lock:
            self._busy_until = self._clock() + 15.0
            conf = self.conf_path()
            if password is not None or ssid not in list_saved_ssids(conf):
                upsert_wpa_network(conf, ssid, password=password, flags=flags)

            self._bring_iface_up()
            if not find_wpa_supplicant_pids(proc_root=self.proc_root, interface=self.interface):
                if not self._start_wpa_supplicant():
                    raise NetworkError("wpa_supplicant başlatılamadı")
                if self._scan_wait > 0:
                    self._sleep(min(0.5, self._scan_wait))

            rc, out, _ = self._wpa_cli("reconfigure")
            if rc != 0 or "FAIL" in out:
                # never run two wpa_supplicant on one interface: stop the old one before starting another
                for pid in find_wpa_supplicant_pids(proc_root=self.proc_root, interface=self.interface):
                    try:
                        self._signal_fn(pid, signal.SIGTERM)
                    except OSError:
                        pass
                if not self._start_wpa_supplicant():
                    raise NetworkError("Wi-Fi yapılandırması yüklenemedi")

            rc_list, out_list, _ = self._wpa_cli("list_networks")
            if rc_list == 0:
                target_id = None
                for net_id, net_ssid, _ in parse_list_networks(out_list):
                    if net_ssid == ssid:
                        target_id = net_id
                if target_id is not None:
                    rc_sel, out_sel, _ = self._wpa_cli("select_network", target_id)
                    if rc_sel != 0 or "FAIL" in out_sel:
                        # select_network disabled every other saved network: let wpa_supplicant use them again
                        self._wpa_cli("enable_network", "all")
                        self._wpa_cli("reconnect")
                        raise NetworkError("Ağ seçilemedi")
                    self._wpa_cli("enable_network", target_id)
            self._wpa_cli("reconnect")
            self._ensure_udhcpc_for_wlan0(renew=True)
            # The signal above comes before the association completes; the lease of the old network is renewed
            # again by ensure_connected() once wpa_supplicant reports COMPLETED for the new SSID.
            self._pending_dhcp_renew = (ssid, self._clock())
            self._last_dhcp_renew_at = self._clock()
            self._last_reconnect_at = self._clock()

    def forget(self, ssid):
        validate_ssid(ssid)
        with self._lock:
            self._busy_until = self._clock() + 5.0
            status = self.get_status()
            if status.ssid == ssid:
                self._wpa_cli("disconnect")
            conf = self.conf_path()
            remove_wpa_network(conf, ssid)
            if find_wpa_supplicant_pids(proc_root=self.proc_root, interface=self.interface):
                self._wpa_cli("reconfigure")

    def ensure_connected(self):
        """Watchdog pass: bring wlan0 up, restart dead wpa_supplicant, reconnect, ensure udhcpc."""
        with self._lock:
            now = self._clock()
            if now < self._busy_until:
                return False
            acted = False
            conf = self.conf_path()
            saved = list_saved_ssids(conf)
            iface_dir = os.path.join(self.sys_net_root, self.interface)
            if os.path.isdir(self.sys_net_root) and not os.path.isdir(iface_dir):
                return False

            if not self._is_iface_up():
                if self._bring_iface_up():
                    acted = True

            wpa_pids = find_wpa_supplicant_pids(proc_root=self.proc_root, interface=self.interface)
            if not wpa_pids and conf and os.path.isfile(conf):
                if self._start_wpa_supplicant():
                    acted = True

            udhcpc_pids = find_udhcpc_pids(proc_root=self.proc_root, interface=self.interface)
            if not udhcpc_pids:
                if self._ensure_udhcpc_for_wlan0(renew=False):
                    acted = True

            rc, out, _ = self._wpa_cli("status")
            if rc == 0 and "wpa_state=" in out:
                info = parse_wpa_status(out)
                state = info.get("wpa_state", "")
                ip_addr = info.get("ip_address", "") or self._read_iface_ip()
                pending = self._pending_dhcp_renew
                if pending is not None and (now - pending[1] > 300.0):
                    pending = self._pending_dhcp_renew = None
                if saved and (
                    state in ("DISCONNECTED", "INACTIVE")
                    or (pending is not None and state != "COMPLETED")
                ):
                    if now - self._last_reconnect_at >= self._reconnect_cooldown:
                        # Re-enable all saved networks so a failed select_network (e.g. wrong password)
                        # falls back to previously working saved networks.
                        self._wpa_cli("enable_network", "all")
                        self._wpa_cli("reconnect")
                        self._last_reconnect_at = now
                        if pending is not None:
                            self._pending_dhcp_renew = None
                        acted = True
                elif state == "COMPLETED":
                    switched = pending is not None and decode_wpa_ssid(info.get("ssid", "")) == pending[0]
                    if (not ip_addr or switched) and now - self._last_dhcp_renew_at >= self._dhcp_cooldown:
                        self._ensure_udhcpc_for_wlan0(renew=True)
                        self._last_dhcp_renew_at = now
                        if switched:
                            self._pending_dhcp_renew = None
                        acted = True
            return acted

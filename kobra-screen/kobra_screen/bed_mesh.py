"""Bed mesh loading (Klipper active/profiles and stock printer_mutable.cfg) and 3D isometric rendering."""

from dataclasses import dataclass
import hashlib
import json
import math
import os
import struct
import tempfile
import zlib

from .theme import COLORS

FLATNESS_GOOD_MAX = 0.20  # heuristic threshold (mm)
FLATNESS_WARN_MAX = 0.50  # heuristic threshold (mm)

BED_MESH_ROTATIONS = (0, 90, 180, 270)
BED_MESH_Z_SCALES = (50, 100, 200)

_COLOR_STOPS = (
    (0.00, (45, 130, 235)),   # low (blue)
    (0.25, (40, 195, 200)),   # cyan
    (0.50, (0, 174, 66)),     # mid (Bambu green)
    (0.75, (240, 195, 45)),   # yellow
    (1.00, (235, 72, 65)),    # high (red)
)

_MAX_MESH_CACHE_FILES = 12
DRAG_STEP = 5
DRAG_CACHE_FILES = 150


@dataclass(frozen=True)
class BedMeshData:
    matrix: tuple
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    source_kind: str   # "klipper" | "stock"
    profile_name: str
    z_min: float
    z_max: float
    z_range: float
    z_avg: float
    rating_key: str    # "İyi" | "Orta" | "Kötü"
    rating_color: str  # "ok" | "warn" | "danger"

    @property
    def rows(self):
        return len(self.matrix)

    @property
    def cols(self):
        return len(self.matrix[0]) if self.matrix else 0


def _normalize_matrix(raw_matrix):
    """Validate and convert a 2D list/tuple into a rectangular tuple of float tuples."""
    if not isinstance(raw_matrix, (list, tuple)) or len(raw_matrix) < 2:
        return None
    rows = []
    width = None
    for row in raw_matrix:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            return None
        if width is None:
            width = len(row)
        elif len(row) != width:
            return None
        parsed_row = []
        for val in row:
            try:
                num = float(val)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(num):
                return None
            parsed_row.append(num)
        rows.append(tuple(parsed_row))
    return tuple(rows)


def _parse_points_text(points_raw):
    """Parse newline-separated rows of comma/whitespace-separated floats."""
    if not isinstance(points_raw, str) or not points_raw.strip():
        return None
    matrix = []
    for line in points_raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace(",", " ").split() if p.strip()]
        if not parts:
            continue
        matrix.append(parts)
    return _normalize_matrix(matrix)


def _build_mesh_data(matrix, bounds, source_kind, profile_name):
    flat = [v for row in matrix for v in row]
    z_min = min(flat)
    z_max = max(flat)
    z_range = z_max - z_min
    z_avg = sum(flat) / float(len(flat))
    if z_range < FLATNESS_GOOD_MAX:
        rating_key, rating_color = "İyi", "ok"
    elif z_range <= FLATNESS_WARN_MAX:
        rating_key, rating_color = "Orta", "warn"
    else:
        rating_key, rating_color = "Kötü", "danger"
    min_x, max_x, min_y, max_y = bounds
    return BedMeshData(
        matrix=matrix,
        min_x=float(min_x),
        max_x=float(max_x),
        min_y=float(min_y),
        max_y=float(max_y),
        source_kind=source_kind,
        profile_name=str(profile_name or ""),
        z_min=z_min,
        z_max=z_max,
        z_range=z_range,
        z_avg=z_avg,
        rating_key=rating_key,
        rating_color=rating_color,
    )


def _safe_float(val, default):
    try:
        num = float(val)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError):
        return default


def parse_klipper_mesh(bed_mesh_status):
    """Return active Klipper probed_matrix/mesh_matrix or saved profile, else None."""
    if not isinstance(bed_mesh_status, dict) or not bed_mesh_status:
        return None

    # 1. Active mesh: probed_matrix first (per Klipper bed_mesh.py), fallback to mesh_matrix.
    active = _normalize_matrix(bed_mesh_status.get("probed_matrix"))
    if active is None:
        active = _normalize_matrix(bed_mesh_status.get("mesh_matrix"))
    if active is not None:
        bmin = bed_mesh_status.get("mesh_min") or (0.0, 0.0)
        bmax = bed_mesh_status.get("mesh_max") or (250.0, 250.0)
        bounds = (
            _safe_float(bmin[0] if len(bmin) > 0 else 0.0, 0.0),
            _safe_float(bmax[0] if len(bmax) > 0 else 250.0, 250.0),
            _safe_float(bmin[1] if len(bmin) > 1 else 0.0, 0.0),
            _safe_float(bmax[1] if len(bmax) > 1 else 250.0, 250.0),
        )
        name = str(bed_mesh_status.get("profile_name") or "").strip() or "default"
        return _build_mesh_data(active, bounds, "klipper", name)

    # 2. Saved Klipper profiles: prefer "default", then sorted names.
    profiles = bed_mesh_status.get("profiles")
    if isinstance(profiles, dict) and profiles:
        names = ["default"] if "default" in profiles else []
        names.extend(k for k in sorted(profiles) if k != "default")
        for name in names:
            prof = profiles.get(name)
            if not isinstance(prof, dict):
                continue
            mat = _normalize_matrix(prof.get("points"))
            if mat is None:
                continue
            params = prof.get("mesh_params") or {}
            bounds = (
                _safe_float(params.get("min_x"), 0.0),
                _safe_float(params.get("max_x"), 250.0),
                _safe_float(params.get("min_y"), 0.0),
                _safe_float(params.get("max_y"), 250.0),
            )
            return _build_mesh_data(mat, bounds, "klipper", name)

    return None


def parse_mutable_mesh(raw_text):
    """Parse stock printer_mutable.cfg JSON ('bed_mesh ...' section)."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    try:
        data = json.loads(raw_text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    sections = []
    if "bed_mesh default" in data:
        sections.append(("bed_mesh default", data["bed_mesh default"]))
    for key in sorted(data):
        if key.startswith("bed_mesh ") and key != "bed_mesh default":
            sections.append((key, data[key]))

    for sec_name, sec in sections:
        if not isinstance(sec, dict):
            continue
        mat = _parse_points_text(sec.get("points"))
        if mat is None:
            continue
        bounds = (
            _safe_float(sec.get("min_x"), 0.0),
            _safe_float(sec.get("max_x"), 250.0),
            _safe_float(sec.get("min_y"), 0.0),
            _safe_float(sec.get("max_y"), 250.0),
        )
        prof = sec_name[len("bed_mesh "):].strip() or "default"
        return _build_mesh_data(mat, bounds, "stock", prof)
    return None


def resolve_bed_mesh(bed_mesh_status=None, mutable_cfg_text=None):
    """Resolve bed mesh following priority: active Klipper -> Klipper profiles -> stock printer_mutable.cfg."""
    mesh = parse_klipper_mesh(bed_mesh_status)
    if mesh is not None:
        return mesh
    return parse_mutable_mesh(mutable_cfg_text)


def height_color(value, z_min, z_max):
    """Map Z height to an (R, G, B) gradient color."""
    span = z_max - z_min
    if span <= 1e-6:
        return _COLOR_STOPS[2][1]
    t = max(0.0, min(1.0, (float(value) - z_min) / span))
    for i in range(len(_COLOR_STOPS) - 1):
        t0, c0 = _COLOR_STOPS[i]
        t1, c1 = _COLOR_STOPS[i + 1]
        if t <= t1:
            local = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(int(round(c0[k] + (c1[k] - c0[k]) * local)) for k in range(3))
    return _COLOR_STOPS[-1][1]


def contrast_text_color(rgb):
    """Pick dark or light text color with good contrast over an RGB cell fill."""
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return "on_accent" if lum > 135 else "text"


REF_TILT = 30.0  # elevation the original fixed projection used
DEFAULT_TILT = 38.0
TILT_RANGE = (12.0, 62.0)


def _rotate_xy(nx, ny, rot_deg):
    """Rotate a bed point about the vertical axis; exact for multiples of 90 degrees."""
    rot = float(rot_deg) % 360.0
    if rot == 0.0:
        return nx, ny
    if rot == 90.0:
        return -ny, nx
    if rot == 180.0:
        return -nx, -ny
    if rot == 270.0:
        return ny, -nx
    a = math.radians(rot)
    ca, sa = math.cos(a), math.sin(a)
    return nx * ca - ny * sa, nx * sa + ny * ca


def clamp_tilt(tilt):
    return max(TILT_RANGE[0], min(TILT_RANGE[1], float(tilt)))


def project_mesh_quads(matrix, w, h, rot_deg=0, z_scale=100, pad=10, tilt=DEFAULT_TILT):
    """Project a 2D height matrix into depth-sorted isometric 3D quads inside (w, h).

    `rot_deg` turns the bed about the vertical axis (any angle) and `tilt` is the viewing elevation in degrees.
    Returns (quads, base_outline, front_edge_pts) where:
      quads: list of ([(x0,y0),(x1,y1),(x2,y2),(x3,y3)], fill_rgb, edge_rgb, avg_z)
      base_outline: [(x,y), (x,y), (x,y), (x,y)] reference base plane corners at z_avg
      front_edge_pts: ((x0,y0), (x1,y1)) highlighted front-Y reference edge on the base
    """
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    if rows < 2 or cols < 2:
        return [], [], None

    flat = [v for row in matrix for v in row]
    z_min = min(flat)
    z_max = max(flat)
    z_avg = sum(flat) / float(len(flat))
    max_half = max(0.05, max(abs(z_max - z_avg), abs(z_avg - z_min)))

    # Keep z excursion cleanly within the viewport across x50 / x100 / x200 while scaling linearly.
    z_scale = int(z_scale) if int(z_scale) in BED_MESH_Z_SCALES else 100
    tilt = clamp_tilt(tilt)
    tilt_rad = math.radians(tilt)
    px_per_mm = min(z_scale * 0.80, (h * 0.24 / max_half) * (z_scale / 200.0))
    px_per_mm *= math.cos(tilt_rad) / math.cos(math.radians(REF_TILT))

    usable_w = max(20.0, float(w) - 2.0 * pad)
    usable_h = max(20.0, float(h) - 2.0 * pad)
    iso_cos = 0.866
    iso_sin = 0.88 * math.sin(tilt_rad)  # 0.44 at the default 30 degrees
    xy_scale = min(usable_w / (2.0 * 2.0 * iso_cos), usable_h * 0.56 / (2.0 * iso_sin))
    cx = w * 0.5
    cy = h * 0.53

    def proj(nx, ny, z_val):
        rx, ry = _rotate_xy(nx, ny, rot_deg)
        sx = cx + (rx - ry) * iso_cos * xy_scale
        sy = cy - (rx + ry) * iso_sin * xy_scale - (z_val - z_avg) * px_per_mm
        depth = rx + ry  # larger rx+ry = further back/up on screen; draw back-to-front
        return (sx, sy), depth

    verts = []
    for r in range(rows):
        ny = -1.0 + 2.0 * (r / float(rows - 1))
        vrow = []
        for c in range(cols):
            nx = -1.0 + 2.0 * (c / float(cols - 1))
            pt, depth = proj(nx, ny, matrix[r][c])
            vrow.append((pt, depth, matrix[r][c]))
        verts.append(vrow)

    quads = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            p00, d00, z00 = verts[r][c]
            p10, d10, z10 = verts[r][c + 1]
            p11, d11, z11 = verts[r + 1][c + 1]
            p01, d01, z01 = verts[r + 1][c]
            avg_z = 0.25 * (z00 + z10 + z11 + z01)
            avg_depth = 0.25 * (d00 + d10 + d11 + d01)
            base_rgb = height_color(avg_z, z_min, z_max)
            # Subtle directional slope shading (x-gradient brightens/dims slightly)
            slope = ((z10 - z00) + (z11 - z01)) * 0.5
            shade = max(0.76, min(1.14, 0.96 - slope * 0.20))
            fill_rgb = tuple(max(0, min(255, int(round(ch * shade)))) for ch in base_rgb)
            edge_rgb = tuple(max(12, int(round(ch * 0.48))) for ch in fill_rgb)
            quads.append((avg_depth, (p00, p10, p11, p01), fill_rgb, edge_rgb, avg_z))

    # Painter's algorithm: largest depth (backmost) first -> smallest depth (frontmost) last.
    quads.sort(key=lambda q: -q[0])
    out_quads = [(pts, fill, edge, az) for _, pts, fill, edge, az in quads]

    b00, _ = proj(-1.0, -1.0, z_avg)
    b10, _ = proj(1.0, -1.0, z_avg)
    b11, _ = proj(1.0, 1.0, z_avg)
    b01, _ = proj(-1.0, 1.0, z_avg)
    return out_quads, [b00, b10, b11, b01], (b00, b10)


def _draw_line(buf, w, h, p0, p1, rgb):
    x0, y0 = int(round(p0[0])), int(round(p0[1]))
    x1, y1 = int(round(p1[0])), int(round(p1[1]))
    r, g, b = rgb
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < w and 0 <= y0 < h:
            idx = (y0 * w + x0) * 3
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _fill_triangle(buf, w, h, p0, p1, p2, rgb):
    """Scanline fill using the long edge and the two short edges (slopes computed once)."""
    pixel = bytes(rgb)
    (x0, y0), (x1, y1), (x2, y2) = sorted(((p0[0], p0[1]), (p1[0], p1[1]), (p2[0], p2[1])), key=lambda p: p[1])
    iy_min = max(0, int(math.ceil(y0 - 0.5)))
    iy_max = min(h - 1, int(math.floor(y2 + 0.5)))
    if iy_max < iy_min or y2 - y0 < 1e-6:
        return
    s02 = (x2 - x0) / (y2 - y0)
    s01 = (x1 - x0) / (y1 - y0) if y1 - y0 > 1e-6 else 0.0
    s12 = (x2 - x1) / (y2 - y1) if y2 - y1 > 1e-6 else 0.0
    last_x = w - 1
    for y in range(iy_min, iy_max + 1):
        yc = y0 if y < y0 else (y2 if y > y2 else y)
        xa = x0 + (yc - y0) * s02
        xb = x0 + (yc - y0) * s01 if yc < y1 else x1 + (yc - y1) * s12
        if xa > xb:
            xa, xb = xb, xa
        lx = int(xa)  # floor for the non-negative case; negatives are clamped below
        if xa < 0:
            lx = 0
        rx = int(xb) + 1 if xb >= 0 else 0
        if rx > last_x:
            rx = last_x
        if rx < lx:
            continue
        start = (y * w + lx) * 3
        buf[start:start + (rx - lx + 1) * 3] = pixel * (rx - lx + 1)


def _encode_rgb_png(buf, w, h):
    """Encode an RGB bytearray into a valid 24-bit PNG without Pillow."""
    stride = w * 3
    raw_rows = bytearray((stride + 1) * h)
    for y in range(h):
        dst = y * (stride + 1)
        src = y * stride
        raw_rows[dst] = 0
        raw_rows[dst + 1:dst + 1 + stride] = buf[src:src + stride]

    def chunk(tag, data):
        crc = zlib.crc32(tag)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), 1)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def render_mesh_surface_png(matrix, w, h, rot_deg=0, z_scale=100, cache_dir=None, tilt=DEFAULT_TILT, prefix="mesh",
                            max_files=_MAX_MESH_CACHE_FILES, edges=True):
    """Render an isometric 3D surface to a cached PNG file and return its path.

    Angles are rounded to whole degrees; `prefix` / `max_files` keep the cheap drag previews (lots of small
    files) separate from the full-size pictures in the cache directory.
    """
    w = max(24, int(w))
    h = max(24, int(h))
    rot = float(int(round(float(rot_deg))) % 360)
    tilt = float(int(round(clamp_tilt(tilt))))
    z_scale = int(z_scale)
    if cache_dir is None:
        cache_dir = os.path.join(tempfile.gettempdir(), "kobra-screen-mesh")
    os.makedirs(cache_dir, exist_ok=True)

    sig_src = json.dumps({"m": matrix, "w": w, "h": h, "r": rot, "t": tilt, "z": z_scale},
                         separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha1(sig_src).hexdigest()[:16]
    out_path = os.path.join(cache_dir, "%s_%s.png" % (prefix, digest))
    if os.path.exists(out_path) and os.path.getsize(out_path) > 32:
        try:
            os.utime(out_path, None)
        except OSError:
            pass
        return out_path

    bg = COLORS["sunken"]
    buf = bytearray(bytes(bg) * (w * h))

    # Subtle 1px sunken frame
    border = COLORS["border_subtle"]
    _draw_line(buf, w, h, (0, 0), (w - 1, 0), border)
    _draw_line(buf, w, h, (0, h - 1), (w - 1, h - 1), border)
    _draw_line(buf, w, h, (0, 0), (0, h - 1), border)
    _draw_line(buf, w, h, (w - 1, 0), (w - 1, h - 1), border)

    pad = max(4, int(round(10 * min(w, 248) / 248.0)))
    quads, base_outline, front_edge = project_mesh_quads(matrix, w, h, rot_deg=rot, z_scale=z_scale, pad=pad,
                                                         tilt=tilt)
    if base_outline:
        ref_col = COLORS["divider"]
        for i in range(4):
            _draw_line(buf, w, h, base_outline[i], base_outline[(i + 1) % 4], ref_col)
    if front_edge is not None:
        _draw_line(buf, w, h, front_edge[0], front_edge[1], COLORS["accent"])

    for pts, fill_rgb, edge_rgb, _ in quads:
        p0, p1, p2, p3 = pts
        _fill_triangle(buf, w, h, p0, p1, p2, fill_rgb)
        _fill_triangle(buf, w, h, p0, p2, p3, fill_rgb)
        if edges:
            for i in range(4):
                _draw_line(buf, w, h, pts[i], pts[(i + 1) % 4], edge_rgb)

    png_bytes = _encode_rgb_png(buf, w, h)
    tmp_path = "%s.tmp.%d" % (out_path, os.getpid())
    with open(tmp_path, "wb") as f:
        f.write(png_bytes)
    os.replace(tmp_path, out_path)

    # Prune old cache entries of this kind to keep the temp dir bounded.
    try:
        entries = []
        for fname in os.listdir(cache_dir):
            if fname.startswith(prefix + "_") and fname.endswith(".png"):
                fp = os.path.join(cache_dir, fname)
                entries.append((os.path.getmtime(fp), fp))
        if len(entries) > max_files:
            entries.sort()
            for _, old_fp in entries[:-max_files]:
                if old_fp != out_path:
                    try:
                        os.remove(old_fp)
                    except OSError:
                        pass
    except OSError:
        pass
    return out_path


def render_drag_preview_png(matrix, w, h, rot_deg, tilt, z_scale=100, cache_dir=None):
    """Half-size, coarse-angle picture for use while a finger drags the surface (LVGL stretches it).

    Angles snap to DRAG_STEP degrees so a back-and-forth drag keeps hitting the cache.
    """
    step = DRAG_STEP
    rot = (int(round(float(rot_deg) / step)) * step) % 360
    tl = int(round(clamp_tilt(tilt) / step)) * step
    return render_mesh_surface_png(matrix, max(24, int(w) // 2), max(24, int(h) // 2), rot_deg=rot, tilt=tl,
                                   z_scale=z_scale, cache_dir=cache_dir, prefix="meshlo", max_files=DRAG_CACHE_FILES,
                                   edges=False)

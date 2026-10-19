"""Render a Botslab/360 vacuum map package (infoType 20004) to a PNG.

The map `.bin` fetched from OSS is one or more concatenated JSON records; the
map record carries an occupancy grid as base64(LZ4-block) plus world-space
overlays (dock, robot, no-go zones, rooms). Pure helper: no Home Assistant deps
(uses Pillow, which ships with HA core, and a pure-Python LZ4 block decoder).

Rendering is split in two:
  * ``build_map`` -> ``RenderedMap`` : the expensive, static-per-map base image
    (occupancy grid, rooms, no-go, dock) + geometry; cache this by upload tag.
  * ``compose_png`` : cheap live overlay (robot marker + heading + path) drawn
    on a copy of the base; call this every poll/camera fetch.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import math
from dataclasses import dataclass, field

# occupancy cell values
_UNKNOWN, _FLOOR, _WALL = 0, 127, 255

# palette (RGB)
C_FLOOR = (236, 240, 245)
C_WALL = (44, 56, 82)
C_NOGO_FILL = (224, 74, 74, 70)
C_NOGO_LINE = (224, 74, 74, 235)
C_DOCK = (46, 196, 110)
C_ROBOT = (255, 140, 0)
C_PATH = (90, 200, 250, 210)
ROOM_PALETTE = [
    (122, 193, 255), (255, 193, 122), (150, 214, 160), (214, 150, 200),
    (224, 208, 130), (150, 200, 214), (220, 150, 150), (178, 170, 230),
    (160, 210, 200), (230, 180, 140), (190, 200, 150), (200, 160, 210),
]


def _b64name(s: str) -> str:
    try:
        return base64.b64decode(s).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return s or ""


def parse_map(bin_text: str) -> dict:
    """Extract the occupancy-grid record from the concatenated-JSON package."""
    dec = json.JSONDecoder()
    i, n = 0, len(bin_text)
    best = None
    while i < n:
        while i < n and bin_text[i] in " \r\n\t":
            i += 1
        if i >= n:
            break
        try:
            obj, i = dec.raw_decode(bin_text, i)
        except json.JSONDecodeError:
            break
        data = obj.get("data", obj) if isinstance(obj, dict) else {}
        if isinstance(data, dict) and "map" in data and "width" in data:
            best = data
    if best is None:
        raise ValueError("no occupancy-grid record found in map package")
    return best


def lz4_block_decompress(src: bytes, out_size: int) -> bytes:
    """Pure-Python LZ4 *block* decompression (no native dep / build toolchain)."""
    out = bytearray(out_size)
    si = di = 0
    n = len(src)
    while si < n:
        token = src[si]; si += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[si]; si += 1; lit += b
                if b != 255:
                    break
        out[di:di + lit] = src[si:si + lit]
        di += lit; si += lit
        if si >= n:
            break
        offset = src[si] | (src[si + 1] << 8); si += 2
        mlen = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                b = src[si]; si += 1; mlen += b
                if b != 255:
                    break
        start = di - offset
        for k in range(mlen):
            out[di] = out[start + k]; di += 1
    return bytes(out[:di])


def decode_grid(m: dict) -> bytes:
    w, h = int(m["width"]), int(m["height"])
    return lz4_block_decompress(base64.b64decode(m["map"]), w * h)


def rooms(m: dict) -> list[dict]:
    """Return [{id, name, cleanTimes, vertexs(mm)}] from smartArea."""
    out = []
    for r in (m.get("smartArea") or {}).get("value") or []:
        out.append({
            "id": r.get("id"),
            "name": _b64name(r.get("name", "")) or f"Room {r.get('id')}",
            "cleanTimes": r.get("cleanTimes"),
            "vertexs": r.get("vertexs") or [],
        })
    return out


def _rasterize(poly: list, cell: int):
    """Rasterize a polygon (mm) to a boolean occupancy grid of `cell`-mm cells."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    w = max(1, int((x1 - x0) // cell) + 1)
    h = max(1, int((y1 - y0) // cell) + 1)
    n = len(poly)
    grid = [[False] * w for _ in range(h)]
    for r in range(h):
        cy = y0 + (r + 0.5) * cell
        xc = []
        for i in range(n):
            ax, ay = poly[i]
            bx, by = poly[(i + 1) % n]
            if (ay <= cy < by) or (by <= cy < ay):
                xc.append(ax + (bx - ax) * (cy - ay) / (by - ay))
        xc.sort()
        for k in range(0, len(xc) - 1, 2):
            ca = max(0, int((xc[k] - x0) // cell))
            cb = min(w - 1, int((xc[k + 1] - x0) // cell))
            for c in range(ca, cb + 1):
                grid[r][c] = True
    return grid, x0, y0, w, h


def _largest_rect(grid, used, w: int, h: int):
    """Largest all-free (grid True & not used) axis-aligned rectangle (histogram)."""
    best = (0, 0, 0, 0, 0)  # area, r0, c0, r1, c1
    heights = [0] * w
    for r in range(h):
        for c in range(w):
            heights[c] = heights[c] + 1 if (grid[r][c] and not used[r][c]) else 0
        stack = []
        for c in range(w + 1):
            cur = heights[c] if c < w else 0
            start = c
            while stack and stack[-1][1] > cur:
                sc, sh = stack.pop()
                area = sh * (c - sc)
                if area > best[0]:
                    best = (area, r - sh + 1, sc, r, c - 1)
                start = sc
            stack.append((start, cur))
    return best


def room_cover_rects(
    poly: list, cell: int = 100, max_rects: int = 10, min_cov: float = 0.93
) -> list[list[int]]:
    """Cover a (rectilinear) room polygon with a few axis-aligned rectangles.

    The robot's inline zone cleaning only accepts *rectangles*, not room
    outlines, and caps the zone count (~20). A greedy maximal-rectangle cover
    tiles the room interior with ``<= max_rects`` rectangles reaching ~``min_cov``
    coverage, staying *inside* the room (no spill into neighbours). Returns
    ``[[x1, y1, x2, y2], ...]`` in vacuum mm.
    """
    if len(poly) < 3:
        return []
    grid, x0, y0, w, h = _rasterize(poly, cell)
    total = sum(row.count(True) for row in grid)
    if not total:
        return []
    used = [[False] * w for _ in range(h)]
    rects: list[list[int]] = []
    covered = 0
    for _ in range(max_rects):
        area, r0, c0, r1, c1 = _largest_rect(grid, used, w, h)
        if area <= 0:
            break
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                used[r][c] = True
        covered += area
        rects.append([
            int(x0 + c0 * cell), int(y0 + r0 * cell),
            int(x0 + (c1 + 1) * cell), int(y0 + (r1 + 1) * cell),
        ])
        if covered / total >= min_cov:
            break
    return rects


@dataclass
class MapGeometry:
    """World(mm)<->image(pixel) transform for a cropped, scaled map image."""
    w: int
    h: int
    res: float               # meters / cell
    x_min: float             # meters, world origin of grid (col 0)
    y_min: float             # meters, world origin of grid (row 0, bottom)
    cmin: int                # crop bbox in cell coords
    rmin: int
    cmax: int
    rmax: int
    scale: int

    def w2p(self, wx_mm: float, wy_mm: float) -> tuple[float, float]:
        cx = (wx_mm / 1000.0 - self.x_min) / self.res
        cy = (wy_mm / 1000.0 - self.y_min) / self.res
        return ((cx - self.cmin) * self.scale, (self.rmax - cy) * self.scale)

    @property
    def size(self) -> tuple[int, int]:
        return ((self.cmax - self.cmin + 1) * self.scale,
                (self.rmax - self.rmin + 1) * self.scale)

    def calibration_points(self) -> list[dict]:
        """3 non-collinear {vacuum:{x,y}, map:{x,y}} pairs for map cards."""
        pts = [
            (self.x_min * 1000.0, self.y_min * 1000.0),
            (self.x_min * 1000.0 + 1000.0, self.y_min * 1000.0),
            (self.x_min * 1000.0, self.y_min * 1000.0 + 1000.0),
        ]
        out = []
        for vx, vy in pts:
            px, py = self.w2p(vx, vy)
            out.append({"vacuum": {"x": round(vx), "y": round(vy)},
                        "map": {"x": round(px), "y": round(py)}})
        return out


@dataclass
class RenderedMap:
    base: "object"           # PIL.Image.Image (RGBA, cropped + scaled)
    geom: MapGeometry
    rooms: list = field(default_factory=list)
    areas: list = field(default_factory=list)
    dock: list | None = None
    default_pose: list | None = None


def _rgb_lut():
    r = bytearray(256); g = bytearray(256); b = bytearray(256)
    r[_FLOOR], g[_FLOOR], b[_FLOOR] = C_FLOOR
    r[_WALL], g[_WALL], b[_WALL] = C_WALL
    return bytes(r), bytes(g), bytes(b)


def build_map(bin_text: str, scale: int = 6, margin_cells: int = 4) -> RenderedMap:
    """Parse + decode + paint the static base image (cache by upload tag)."""
    from PIL import Image, ImageDraw

    m = parse_map(bin_text)
    w, h = int(m["width"]), int(m["height"])
    res = float(m["resolution"])
    x_min, y_min = float(m["x_min"]), float(m["y_min"])
    grid = decode_grid(m)

    lflip = Image.frombytes("L", (w, h), grid).transpose(Image.FLIP_TOP_BOTTOM)
    alpha = lflip.point(lambda v: 0 if v == _UNKNOWN else 255)

    bbox = alpha.getbbox()  # (left, upper, right, lower) in flipped (top-origin) px
    if bbox is None:
        bbox = (0, 0, w, h)
    left, upper, right, lower = bbox
    left = max(0, left - margin_cells); upper = max(0, upper - margin_cells)
    right = min(w, right + margin_cells); lower = min(h, lower + margin_cells)

    rlut, glut, blut = _rgb_lut()
    rgb = Image.merge("RGB", (lflip.point(rlut), lflip.point(glut), lflip.point(blut)))
    rgb.putalpha(alpha)
    crop = rgb.crop((left, upper, right, lower))
    crop = crop.resize(((right - left) * scale, (lower - upper) * scale), Image.NEAREST)

    # cell-coord crop bbox (bottom-origin rows): row_top -> grid_y = h-1-row_top
    geom = MapGeometry(
        w=w, h=h, res=res, x_min=x_min, y_min=y_min,
        cmin=left, cmax=right - 1, rmin=h - lower, rmax=h - 1 - upper, scale=scale,
    )

    rm = RenderedMap(base=crop.convert("RGBA"), geom=geom,
                     rooms=rooms(m), areas=m.get("area") or [],
                     dock=m.get("chargeHandlePos"),
                     default_pose=(m.get("posArray") or [None])[0])

    draw = ImageDraw.Draw(rm.base, "RGBA")
    # rooms: translucent fill + outline + label
    for idx, r in enumerate(rm.rooms):
        v = r.get("vertexs") or []
        if len(v) < 3:
            continue
        poly = [geom.w2p(x, y) for x, y in v]
        col = ROOM_PALETTE[idx % len(ROOM_PALETTE)]
        draw.polygon(poly, fill=(col[0], col[1], col[2], 110))
        draw.line(poly + [poly[0]], fill=(col[0], col[1], col[2], 255),
                  width=max(1, scale // 3))
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        draw.text((cx, cy), r["name"], fill=(30, 36, 50, 255), anchor="mm")
    # no-go zones
    for a in rm.areas:
        v = a.get("vertexs") or []
        if len(v) >= 3:
            poly = [geom.w2p(x, y) for x, y in v]
            draw.polygon(poly, fill=C_NOGO_FILL, outline=C_NOGO_LINE)
    # dock glyph
    if rm.dock:
        _draw_dock(draw, geom.w2p(*rm.dock[:2]), scale)
    return rm


def _draw_dock(draw, xy, scale):
    x, y = xy
    s = max(6, scale * 2)
    draw.rectangle([x - s, y - s * 0.7, x + s, y + s * 0.7],
                   fill=C_DOCK, outline=(255, 255, 255, 255), width=2)
    draw.polygon([(x - s, y - s * 0.7), (x, y - s * 1.4), (x + s, y - s * 0.7)],
                 fill=C_DOCK, outline=(255, 255, 255, 255))


def _parse_pose(pose):
    if isinstance(pose, str):
        try:
            pose = json.loads(pose)
        except json.JSONDecodeError:
            return None
    if isinstance(pose, (list, tuple)) and len(pose) >= 2:
        ang = float(pose[2]) if len(pose) >= 3 else 0.0
        return float(pose[0]), float(pose[1]), ang
    return None


def _heading_rad(angle_raw: float, conv: str) -> float:
    """Map the device angle to a screen-space heading (radians, screen y-down)."""
    if conv == "deg_cw":
        return math.radians(angle_raw)
    if conv == "rad_ccw":
        return -angle_raw
    if conv == "rad_cw":
        return angle_raw
    # default deg_ccw: world CCW degrees -> screen (y flipped) => negate
    return -math.radians(angle_raw)


def compose_png(rm: RenderedMap, pose=None, path=None, angle_conv: str = "deg_ccw") -> bytes:
    """Draw the live overlay (path + robot + heading) on a copy of the base."""
    from PIL import Image, ImageDraw

    img = rm.base.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    g = rm.geom

    if path:
        pts = [g.w2p(p[0], p[1]) for p in path if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(pts) >= 2:
            draw.line(pts, fill=C_PATH, width=max(2, g.scale // 2), joint="curve")

    p = _parse_pose(pose) or (_parse_pose(rm.default_pose) if rm.default_pose else None)
    if p:
        x, y, ang = p
        cx, cy = g.w2p(x, y)
        r = max(7, int(g.scale * 2.2))
        hr = _heading_rad(ang, angle_conv)
        tip = (cx + math.cos(hr) * r * 1.8, cy + math.sin(hr) * r * 1.8)
        base_l = (cx + math.cos(hr + 2.5) * r, cy + math.sin(hr + 2.5) * r)
        base_r = (cx + math.cos(hr - 2.5) * r, cy + math.sin(hr - 2.5) * r)
        draw.polygon([tip, base_l, base_r], fill=(255, 255, 255, 230))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=C_ROBOT + (255,), outline=(255, 255, 255, 255), width=max(2, g.scale // 3))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_png(bin_text: str, scale: int = 6) -> bytes:
    """Back-compat one-shot render (uses the map's own saved robot pose)."""
    rm = build_map(bin_text, scale=scale)
    return compose_png(rm)

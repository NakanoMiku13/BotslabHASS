"""Render a Botslab/360 vacuum map package (infoType 20004) to a PNG.

The map `.bin` fetched from OSS is one or more concatenated JSON records; the
map record carries an occupancy grid as base64(LZ4-block) plus world-space
overlays (dock, robot, no-go zones, rooms). Pure helper: no Home Assistant deps.
"""
from __future__ import annotations

import base64
import binascii
import json

# occupancy cell values
_UNKNOWN, _FLOOR, _WALL = 0, 127, 255

# palette (RGBA)
C_BG = (18, 22, 28, 0)         # unknown -> transparent
C_FLOOR = (56, 108, 176, 255)  # free space
C_WALL = (18, 32, 58, 255)     # walls/obstacles
ROOM_PALETTE = [
    (122, 193, 255), (255, 193, 122), (160, 214, 160), (214, 160, 200),
    (224, 208, 130), (150, 200, 214), (210, 150, 150), (180, 180, 230),
]


def _b64name(s: str) -> str:
    try:
        return base64.b64decode(s).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return s or ""


def parse_map(bin_text: str) -> dict:
    """Extract the map record (the one with a `map`/occupancy grid) from the
    concatenated-JSON map package."""
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
    """Pure-Python LZ4 *block* decompression (no native dep / build toolchain).

    The map grid is stored as base64(LZ4-block). We avoid the `lz4` pip package
    because it has no prebuilt wheel for this runtime and fails to build in the
    HA container.
    """
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
    sa = (m.get("smartArea") or {}).get("value") or []
    for r in sa:
        out.append({
            "id": r.get("id"),
            "name": _b64name(r.get("name", "")) or f"Room {r.get('id')}",
            "cleanTimes": r.get("cleanTimes"),
            "vertexs": r.get("vertexs") or [],
        })
    return out


def render_png(bin_text: str, scale: int = 4) -> bytes:
    """Full map package text -> PNG bytes."""
    from PIL import Image, ImageDraw
    m = parse_map(bin_text)
    w, h = int(m["width"]), int(m["height"])
    res = float(m["resolution"])
    x_min, y_min = float(m["x_min"]), float(m["y_min"])
    grid = decode_grid(m)

    # base occupancy image (grid y=0 is bottom -> flip vertically)
    base = Image.frombytes("L", (w, h), grid).transpose(Image.FLIP_TOP_BOTTOM)
    pal = [0, 0, 0] * 256
    for v, c in ((_UNKNOWN, C_BG), (_FLOOR, C_FLOOR), (_WALL, C_WALL)):
        pal[v * 3:v * 3 + 3] = list(c[:3])
    p = Image.new("P", base.size)
    p.putpalette(pal)
    p.putdata(base.getdata())
    img = p.convert("RGBA")
    # unknown -> transparent
    px = img.load()
    for yy in range(h):
        for xx in range(w):
            if grid_at(grid, w, h, xx, yy) == _UNKNOWN:
                px[xx, h - 1 - yy] = C_BG
    img = img.resize((w * scale, h * scale), Image.NEAREST)

    draw = ImageDraw.Draw(img, "RGBA")

    def w2p(wx_mm, wy_mm):
        cx = (wx_mm / 1000.0 - x_min) / res
        cy = (wy_mm / 1000.0 - y_min) / res
        return (cx * scale, (h - 1 - cy) * scale)

    # rooms: translucent fills + labels
    for idx, r in enumerate(rooms(m)):
        if len(r["vertexs"]) < 3:
            continue
        poly = [w2p(x, y) for x, y in r["vertexs"]]
        col = ROOM_PALETTE[idx % len(ROOM_PALETTE)]
        draw.polygon(poly, fill=(col[0], col[1], col[2], 90))
        cxs = sum(p[0] for p in poly) / len(poly)
        cys = sum(p[1] for p in poly) / len(poly)
        draw.text((cxs, cys), r["name"], fill=(255, 255, 255, 230), anchor="mm")

    # no-go zones: red
    for a in m.get("area", []):
        v = a.get("vertexs") or []
        if len(v) >= 3:
            draw.polygon([w2p(x, y) for x, y in v],
                         fill=(220, 60, 60, 70), outline=(230, 70, 70, 230))

    # dock
    if m.get("chargeHandlePos"):
        dx, dy = w2p(*m["chargeHandlePos"])
        r0 = max(5, scale * 2)
        draw.ellipse([dx - r0, dy - r0, dx + r0, dy + r0],
                     fill=(60, 200, 90, 255), outline=(255, 255, 255, 255), width=2)

    # robot position(s) + heading
    for pos in (m.get("posArray") or []):
        rx, ry = w2p(pos[0], pos[1])
        r0 = max(6, int(scale * 2.5))
        draw.ellipse([rx - r0, ry - r0, rx + r0, ry + r0],
                     fill=(255, 140, 0, 255), outline=(255, 255, 255, 255), width=2)

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def grid_at(grid: bytes, w: int, h: int, x: int, y: int) -> int:
    return grid[y * w + x]

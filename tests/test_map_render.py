"""Offline unit tests for map_render (room decomposition, parsing). No HA deps."""
import base64
import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "custom_components",
        "botslab_vacuum",
    ),
)

import map_render  # noqa: E402


def _point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def _rect_area(r):
    return abs((r[2] - r[0]) * (r[3] - r[1]))


def _poly_area(p):
    n = len(p)
    return abs(
        sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1]
            for i in range(n))
    ) / 2


class TestRoomCoverRects(unittest.TestCase):
    SQUARE = [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]
    # L-shape (rectilinear): big square minus top-right quadrant
    LSHAPE = [[0, 0], [2000, 0], [2000, 1000], [1000, 1000],
              [1000, 2000], [0, 2000]]

    def test_square_single_rect(self):
        rects = map_render.room_cover_rects(self.SQUARE, cell=100)
        self.assertEqual(len(rects), 1)
        r = rects[0]
        self.assertGreaterEqual(_rect_area(r), 0.9 * _poly_area(self.SQUARE))

    def test_rects_stay_inside_polygon(self):
        # Every rectangle's centre must lie inside the room (no spill to neighbours)
        rects = map_render.room_cover_rects(self.LSHAPE, cell=100)
        self.assertGreater(len(rects), 1)
        for r in rects:
            cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            self.assertTrue(_point_in_poly(cx, cy, self.LSHAPE),
                            f"rect centre {cx},{cy} outside room")

    def test_coverage_reaches_threshold(self):
        rects = map_render.room_cover_rects(self.LSHAPE, cell=50,
                                            max_rects=12, min_cov=0.9)
        covered = sum(_rect_area(r) for r in rects)
        # L-shape area is 3/4 of the 2000x2000 square = 3_000_000
        self.assertGreaterEqual(covered, 0.85 * _poly_area(self.LSHAPE))
        # and must not massively overshoot into the empty quadrant
        self.assertLessEqual(covered, 1.05 * _poly_area(self.LSHAPE))

    def test_max_rects_cap(self):
        rects = map_render.room_cover_rects(self.LSHAPE, cell=25, max_rects=3)
        self.assertLessEqual(len(rects), 3)

    def test_degenerate_polygon(self):
        self.assertEqual(map_render.room_cover_rects([[0, 0], [1, 1]]), [])
        self.assertEqual(map_render.room_cover_rects([]), [])


class TestParseMap(unittest.TestCase):
    def test_finds_grid_record_among_many(self):
        noise = json.dumps({"data": {"foo": 1}})
        grid = json.dumps({"data": {"map": "AAA", "width": 10, "height": 5,
                                    "mapId": 42}})
        text = noise + "\n" + grid
        rec = map_render.parse_map(text)
        self.assertEqual(rec["mapId"], 42)
        self.assertEqual(rec["width"], 10)

    def test_no_grid_raises(self):
        with self.assertRaises(ValueError):
            map_render.parse_map(json.dumps({"data": {"nope": 1}}))


class TestRooms(unittest.TestCase):
    def test_decodes_names_and_vertexs(self):
        m = {"smartArea": {"value": [
            {"id": 0, "name": base64.b64encode(b"Sala").decode(),
             "cleanTimes": 2, "vertexs": [[0, 0], [1, 0], [1, 1]]},
            {"id": 1, "name": "", "vertexs": []},
        ]}}
        rms = map_render.rooms(m)
        self.assertEqual(rms[0]["name"], "Sala")
        self.assertEqual(rms[0]["id"], 0)
        self.assertEqual(rms[0]["vertexs"], [[0, 0], [1, 0], [1, 1]])
        self.assertEqual(rms[1]["name"], "Room 1")  # fallback for empty name


class TestLz4Block(unittest.TestCase):
    def test_literal_only_block(self):
        src = bytes([0x40]) + b"ABCD"  # token: 4 literals, no match
        self.assertEqual(map_render.lz4_block_decompress(src, 4), b"ABCD")

    def test_match_copy(self):
        # 4 literals "aaaa" then a match (offset 1, len 4) -> "aaaaaaaa"
        src = bytes([0x40]) + b"aaaa" + bytes([0x01, 0x00])
        self.assertEqual(map_render.lz4_block_decompress(src, 8), b"a" * 8)


if __name__ == "__main__":
    unittest.main()

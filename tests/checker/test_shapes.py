"""圓角圖的 PNG（`fcn_checker.shapes`，#253）：抗鋸齒、一層一層疊、編成 Tk 讀得到的 RGBA PNG。"""

from __future__ import annotations

import struct
import zlib

from fcn_checker import shapes


def pixels(png: bytes) -> tuple[int, int, list[bytes]]:
    """解開沒有 filter 的 RGBA PNG：(寬, 高, 每列的像素)。"""
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    start = png.index(b"IDAT") + 4
    length = struct.unpack(">I", png[start - 8 : start - 4])[0]
    raw = zlib.decompress(png[start : start + length])
    stride = 1 + 4 * width
    return width, height, [raw[y * stride + 1 : (y + 1) * stride] for y in range(height)]


def test_a_rounded_box_is_transparent_outside_its_corners() -> None:
    png = shapes.render(20, 20, [shapes.Layer(shapes.rounded_box(0, 0, 20, 20, 6), "#0A64D8")])
    width, height, rows = pixels(png)

    assert (width, height) == (20, 20)
    assert rows[0][0:4] == b"\0\0\0\0", "左上角在圓角外：全透明"
    assert rows[10][40:44] == bytes((0x0A, 0x64, 0xD8, 255)), "中間：實心的顏色"
    assert 0 < rows[2][1 * 4 + 3] < 255, "圓角邊上：半透明（抗鋸齒）"


def test_later_layers_cover_earlier_ones_with_their_opacity() -> None:
    whole = shapes.rounded_box(0, 0, 4, 4, 0)
    png = shapes.render(4, 4, [shapes.Layer(whole, "#FFFFFF"), shapes.Layer(whole, "#000000", opacity=0.5)])
    _, _, rows = pixels(png)

    assert rows[2][8:12] == bytes((128, 128, 128, 255)), "白底上蓋一半的黑"


def test_a_ring_keeps_only_the_band_between_two_shapes() -> None:
    outer, inner = shapes.rounded_box(0, 0, 10, 10, 0), shapes.rounded_box(2, 2, 8, 8, 0)
    _, _, rows = pixels(shapes.render(10, 10, [shapes.Layer(shapes.ring(outer, inner), "#000000")]))

    assert rows[0][3] == 255 and rows[5][5 * 4 + 3] == 0, "外圈實心、中間挖空"

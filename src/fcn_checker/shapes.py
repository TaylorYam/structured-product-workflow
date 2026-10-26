"""畫圓角外框、圓點與勾勾的 PNG（ttk 的圖片元件用），不靠 Pillow：Tk 8.6 內建讀 PNG，透明度照 alpha 疊上去。

每個圖形用「到邊的距離」算每一格蓋住多少（抗鋸齒），一層一層疊（`Layer`），最後編成 RGBA PNG。
只用在 `fcn_checker.ui.apply_style`：圖小（幾十像素見方），開視窗時一次畫完。
"""

from __future__ import annotations

import base64
import math
import struct
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

Distance = Callable[[float, float], float]  # 像素中心 → 到圖形邊的距離（裡面是負的）


def rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rounded_box(left: float, top: float, right: float, bottom: float, radius: float) -> Distance:
    """圓角矩形（座標是像素邊線）；radius 是圓角半徑。"""
    cx, cy = (left + right) / 2, (top + bottom) / 2
    hx, hy = (right - left) / 2 - radius, (bottom - top) / 2 - radius

    def distance(x: float, y: float) -> float:
        dx, dy = abs(x - cx) - hx, abs(y - cy) - hy
        outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
        return outside + min(max(dx, dy), 0.0) - radius

    return distance


def ring(outer: Distance, inner: Distance) -> Distance:
    """outer 裡面、inner 外面的那一圈（外框）。"""
    return lambda x, y: max(outer(x, y), -inner(x, y))


def polyline(points: Sequence[tuple[float, float]], width: float) -> Distance:
    """有粗細、圓頭的折線（勾勾、箭頭）。"""
    segments = list(zip(points, points[1:], strict=False))

    def distance(x: float, y: float) -> float:
        best = math.inf
        for (ax, ay), (bx, by) in segments:
            vx, vy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy)))
            best = min(best, math.hypot(x - ax - t * vx, y - ay - t * vy))
        return best - width / 2

    return distance


@dataclass(frozen=True)
class Layer:
    shape: Distance
    color: str
    opacity: float = 1.0


def render(width: int, height: int, layers: Sequence[Layer]) -> bytes:
    """依序疊上每一層（後面的蓋在前面上），回傳 RGBA PNG。"""
    colors = [(rgb(layer.color), layer.opacity) for layer in layers]
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # 每列開頭的 filter type：不用 filter
        for x in range(width):
            red = green = blue = alpha = 0.0  # premultiplied
            for layer, ((r, g, b), opacity) in zip(layers, colors, strict=True):
                cover = max(0.0, min(1.0, 0.5 - layer.shape(x + 0.5, y + 0.5))) * opacity
                if cover:
                    red, green, blue = (
                        r * cover + red * (1 - cover),
                        g * cover + green * (1 - cover),
                        b * cover + blue * (1 - cover),
                    )
                    alpha = cover + alpha * (1 - cover)
            if alpha:
                rows += bytes((round(red / alpha), round(green / alpha), round(blue / alpha), round(alpha * 255)))
            else:
                rows += b"\0\0\0\0"
    return _png(width, height, bytes(rows))


def _png(width: int, height: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8 bit、RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def png_data(png: bytes) -> str:
    """給 `tk.PhotoImage(data=...)` 的 base64 文字。"""
    return base64.b64encode(png).decode("ascii")

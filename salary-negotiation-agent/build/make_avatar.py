#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 salary-negotiation-agent 头像：512×512 PNG。

设计语义：三根递增柱 = 三档锚（底线 / 目标 / 开口），一条上升折线 = 谈判向上。
深底亮主体，色数远超 8，满足平台辨识度判据。

纯标准库：手写 PNG（zlib + IHDR/IDAT/IEND），4× 超采样后降采样做抗锯齿。
同输入同输出，无随机。
"""

import os
import struct
import zlib

OUT_SIZE = 512      # 最终边长
SS = 2              # 超采样倍数（1024 画，512 出）


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.buf = bytearray(w * h * 3)

    def px(self, x, y, rgb):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i:i + 3] = bytes(rgb)

    def get(self, x, y):
        i = (y * self.w + x) * 3
        return self.buf[i], self.buf[i + 1], self.buf[i + 2]

    def rows(self):
        stride = self.w * 3
        return [self.buf[y * stride:(y + 1) * stride] for y in range(self.h)]


def lerp(a, b, t):
    return int(round(a + (b - a) * t))


def radial_bg(cv, cx, cy, maxr, inner, outer):
    for y in range(cv.h):
        for x in range(cv.w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            t = min(1.0, d / maxr) ** 1.15
            cv.px(x, y, (lerp(inner[0], outer[0], t),
                         lerp(inner[1], outer[1], t),
                         lerp(inner[2], outer[2], t)))


def fill_rect(cv, x0, y0, x1, y1, top, bottom):
    """矩形，垂直渐变（上亮下暗），带 1px 内侧高光。"""
    h = max(1, y1 - y0)
    for y in range(y0, y1):
        t = (y - y0) / h
        base = (lerp(top[0], bottom[0], t),
                lerp(top[1], bottom[1], t),
                lerp(top[2], bottom[2], t))
        for x in range(x0, x1):
            cv.px(x, y, base)


def fill_circle(cv, cx, cy, r, rgb):
    r2 = r * r
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                cv.px(x, y, rgb)


def draw_line(cv, x0, y0, x1, y1, width, rgb):
    """粗线：按点到线段距离判定。"""
    half = width / 2.0
    dx, dy = x1 - x0, y1 - y0
    seg2 = dx * dx + dy * dy or 1
    xmin, xmax = min(x0, x1) - width, max(x0, x1) + width
    ymin, ymax = min(y0, y1) - width, max(y0, y1) + width
    for y in range(max(0, ymin), min(cv.h, ymax + 1)):
        for x in range(max(0, xmin), min(cv.w, xmax + 1)):
            t = ((x - x0) * dx + (y - y0) * dy) / seg2
            t = max(0.0, min(1.0, t))
            px_, py_ = x0 + t * dx, y0 + t * dy
            if (x - px_) ** 2 + (y - py_) ** 2 <= half * half:
                cv.px(x, y, rgb)


def fill_tri(cv, p1, p2, p3, rgb):
    xs = [p1[0], p2[0], p3[0]]
    ys = [p1[1], p2[1], p3[1]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    def sign(a, b, c):
        return (a[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (a[1] - c[1])

    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            p = (x, y)
            d1, d2, d3 = sign(p, p1, p2), sign(p, p2, p3), sign(p, p3, p1)
            neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
            pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
            if not (neg and pos):
                cv.px(x, y, rgb)


def downsample(cv, factor):
    out = Canvas(cv.w // factor, cv.h // factor)
    for y in range(out.h):
        for x in range(out.w):
            rs = gs = bs = 0
            for dy in range(factor):
                for dx in range(factor):
                    r, g, b = cv.get(x * factor + dx, y * factor + dy)
                    rs += r
                    gs += g
                    bs += b
            n = factor * factor
            out.px(x, y, (rs // n, gs // n, bs // n))
    return out


def build():
    S = SS
    cv = Canvas(OUT_SIZE * S, OUT_SIZE * S)

    # 背景：深蓝径向渐变
    radial_bg(cv, OUT_SIZE * S // 2, int(OUT_SIZE * S * 0.40),
              OUT_SIZE * S * 0.78, (30, 40, 64), (7, 9, 16))

    # 三根柱（512 尺度坐标，乘 S）：底线 / 目标 / 开口
    bars = [
        (118, 120, (108, 136, 190), (62, 84, 128)),   # 底线锚：蓝灰
        (222, 200, (250, 190, 78), (188, 132, 40)),   # 目标锚：金
        (326, 286, (240, 92, 84), (176, 52, 48)),     # 开口锚：红（涨为红）
    ]
    base_y = 432
    for bx, bh, top, bottom in bars:
        fill_rect(cv, bx * S, (base_y - bh) * S, (bx + 68) * S, base_y * S, top, bottom)
        # 柱顶高光条
        fill_rect(cv, bx * S, (base_y - bh) * S, (bx + 68) * S, (base_y - bh) * S + 3 * S,
                  (255, 255, 255), (230, 236, 248))

    # 基线
    fill_rect(cv, 96 * S, base_y * S, 420 * S, base_y * S + 3 * S, (86, 100, 128), (52, 62, 82))

    # 三点连线 + 上升箭头
    p1 = (152 * S, (base_y - 120) * S - 10 * S)
    p2 = (256 * S, (base_y - 200) * S - 10 * S)
    p3 = (360 * S, (base_y - 286) * S - 10 * S)
    white = (238, 243, 252)
    draw_line(cv, p1[0], p1[1], p2[0], p2[1], 7 * S, white)
    draw_line(cv, p2[0], p2[1], p3[0], p3[1], 7 * S, white)

    # 从第三点延伸出箭头
    tip = (404 * S, 92 * S)
    draw_line(cv, p3[0], p3[1], tip[0], tip[1], 7 * S, white)
    fill_tri(cv, (tip[0] + 6 * S, tip[1] - 6 * S),
             (tip[0] - 16 * S, tip[1] - 2 * S),
             (tip[0] - 4 * S, tip[1] + 18 * S), white)

    # 三个节点
    for p in (p1, p2, p3):
        fill_circle(cv, p[0], p[1], 9 * S, white)
        fill_circle(cv, p[0], p[1], 4 * S, (26, 34, 52))

    return downsample(cv, S)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "avatars", "salary-negotiation.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv = build()
    size = write_png(out, cv.w, cv.h, cv.rows())
    colors = len({cv.get(x, y) for y in range(0, cv.h, 3) for x in range(0, cv.w, 3)})
    print("输出 %s" % out)
    print("尺寸 %dx%d  大小 %.1fKB  采样色数 %d" % (cv.w, cv.h, size / 1024.0, colors))


if __name__ == "__main__":
    main()

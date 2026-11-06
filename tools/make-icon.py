#!/usr/bin/env python3
"""Draws simplehome-browser.png (256 px): SimpleHome's command-prompt mark (the chevron and
cursor from its favicon, same colours) inside a ring, the browser's addition. Needs pycairo.
The PNG is committed; run this only if the mark changes."""
import math
import os
import cairo

SIZE, GRID = 256, 64
BG, MARK, ACCENT = (0x0a, 0x06, 0x06), (0xe0, 0x36, 0x2b), (0xff, 0x85, 0x52)
rgb = lambda c: tuple(v / 255 for v in c)  # noqa: E731

surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
cr = cairo.Context(surf)
cr.scale(SIZE / GRID, SIZE / GRID)

def rounded(x0, y0, x1, y1, r):
    cr.new_sub_path()
    cr.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
    cr.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
    cr.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
    cr.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()

rounded(0, 0, 64, 64, 14)
cr.set_source_rgb(*rgb(BG))
cr.fill()
# the ring: a horizon the prompt sits inside, brighter toward the top
grad = cairo.LinearGradient(0, 8, 0, 56)
grad.add_color_stop_rgba(0, *rgb(ACCENT), 0.95)
grad.add_color_stop_rgba(1, *rgb(MARK), 0.35)
cr.set_source(grad)
cr.set_line_width(3)
cr.arc(32, 32, 24, 0, 2 * math.pi)
cr.stroke()
cr.set_line_cap(cairo.LINE_CAP_ROUND)
cr.set_line_join(cairo.LINE_JOIN_ROUND)
cr.set_line_width(7)
cr.set_source_rgb(*rgb(MARK))
cr.move_to(19, 21); cr.line_to(32, 32); cr.line_to(19, 43)
cr.stroke()
rounded(35, 37, 49, 43.5, 3.25)
cr.set_source_rgb(*rgb(ACCENT))
cr.fill()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "simplehome-browser.png")
surf.write_to_png(out)
print("wrote", os.path.normpath(out))

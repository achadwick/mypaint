# This file is part of MyPaint.
# Copyright (C) 2014 by Andrew Chadwick <a.t.chadwick@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

"""Graphical rendering helpers (splines, alpha checks, brush preview)

See also: gui.style

"""

## Imports

import logging
logger = logging.getLogger(__name__)

import math
from numpy import array
import cairo

from lib.helpers import clamp
import gui.style
from lib.color import HCYColor, RGBColor

import gi
from gi.repository import GdkPixbuf
from gi.repository import Gdk
from gi.repository import Gtk

from lib.brush import Brush, BrushInfo
import lib.tiledsurface
from lib.pixbufsurface import render_as_pixbuf


## Module constants

_BRUSH_PREVIEW_POINTS = [
    # px,  py,   press, xtilt, ytilt  # px,  py,   press, xtilt, ytilt
    (0.00, 0.00,  0.00,  0.00, 0.00), (1.00, 0.05,  0.00, -0.06, 0.05),
    (0.10, 0.10,  0.20,  0.10, 0.05), (0.90, 0.15,  0.90, -0.05, 0.05),
    (0.11, 0.30,  0.90,  0.08, 0.05), (0.86, 0.35,  0.90, -0.04, 0.05),
    (0.13, 0.50,  0.90,  0.06, 0.05), (0.84, 0.55,  0.90, -0.03, 0.05),
    (0.17, 0.70,  0.90,  0.04, 0.05), (0.83, 0.75,  0.90, -0.02, 0.05),
    (0.25, 0.90,  0.20,  0.02, 0.00), (0.81, 0.95,  0.00,  0.00, 0.00),
    (0.41, 0.95,  0.00,  0.00, 0.00), (0.80, 1.00,  0.00,  0.00, 0.00),
]


## Drawing functions

def spline_4p(t, p_1, p0, p1, p2):
    """Interpolated point using a Catmull-Rom spline

    :param float t: Time parameter, between 0.0 and 1.0
    :param array p_1: Point p[-1]
    :param array p0: Point p[0]
    :param array p1: Point p[1]
    :param array p2: Point p[2]
    :returns: Interpolated point, between p0 and p1
    :rtype: array

    Used for a succession of points, this function makes smooth curves
    passing through all specified points, other than the first and last.
    For each pair of points, and their immediate predecessor and
    successor points, the `t` parameter should be stepped incrementally
    from 0 (for point p0) to 1 (for point p1).  See also:

    * `spline_iter()`
    * http://en.wikipedia.org/wiki/Cubic_Hermite_spline
    * http://stackoverflow.com/questions/1251438
    """
    return (
        t*((2-t)*t - 1) * p_1 +
        (t*t*(3*t - 5) + 2) * p0 +
        t*((4 - 3*t)*t + 1) * p1 +
        (t-1)*t*t * p2
    ) / 2


def spline_iter(tuples, double_first=True, double_last=True):
    """Converts an list of control point tuples to interpolatable arrays

    :param list tuples: Sequence of tuples of floats
    :param bool double_first: Repeat 1st point, putting it in the result
    :param bool double_last: Repeat last point, putting it in the result
    :returns: Iterator producing (p-1, p0, p1, p2)

    The resulting sequence of 4-tuples is intended to be fed into
    spline_4p().  The start and end points are therefore normally
    doubled, producing a curve that passes through them, along a vector
    aimed at the second or penultimate point respectively.

    """
    cint = [None, None, None, None]
    if double_first:
        cint[0:3] = cint[1:4]
        cint[3] = array(tuples[0])
    for ctrlpt in tuples:
        cint[0:3] = cint[1:4]
        cint[3] = array(ctrlpt)
        if None not in cint:
            yield cint
    if double_last:
        cint[0:3] = cint[1:4]
        cint[3] = array(tuples[-1])
        yield cint


def _variable_pressure_scribble(w, h, tmult):
    points = _BRUSH_PREVIEW_POINTS
    px, py, press, xtilt, ytilt = points[0]
    yield (10, px*w, py*h, 0.0, xtilt, ytilt)
    event_dtime = 0.005
    point_time = 0.1
    for p_1, p0, p1, p2 in spline_iter(points, True, True):
        dt = 0.0
        while dt < point_time:
            t = dt/point_time
            px, py, press, xtilt, ytilt = spline_4p(t, p_1, p0, p1, p2)
            yield (event_dtime, px*w, py*h, press, xtilt, ytilt)
            dt += event_dtime
    px, py, press, xtilt, ytilt = points[-1]
    yield (10, px*w, py*h, 0.0, xtilt, ytilt)


def render_brush_preview_pixbuf(brushinfo, max_edge_tiles=4):
    """Renders brush preview images

    :param lib.brush.BrushInfo brushinfo: settings to render
    :param int max_edge_tiles: Use at most this many tiles along an edge
    :returns: Preview image, at 128x128 pixels
    :rtype: GdkPixbuf

    This generates the preview image (128px icon) used for brushes which
    don't have saved ones. These include brushes picked from .ORA files
    where the parent_brush_name doesn't correspond to a brush in the
    user's MyPaint brushes - they're used as the default, and for the
    Auto button in the Brush Icon editor.

    Brushstrokes are inherently unpredictable in size, so the allowable
    area is grown until the brush fits or until the rendering becomes
    too big. `max_edge_tiles` limits this growth.
    """
    assert max_edge_tiles >= 1
    brushinfo = brushinfo.clone()  # avoid capturing a ref
    brush = Brush(brushinfo)
    surface = lib.tiledsurface.Surface()
    N = lib.tiledsurface.N
    for size_in_tiles in range(1, max_edge_tiles):
        width = N * size_in_tiles
        height = N * size_in_tiles
        surface.clear()
        fg, spiral = _brush_preview_bg_fg(surface, size_in_tiles, brushinfo)
        brushinfo.set_color_rgb(fg)
        brush.reset()
        # Curve
        shape = _variable_pressure_scribble(width, height, size_in_tiles)
        surface.begin_atomic()
        for dt, x, y, p, xt, yt in shape:
            brush.stroke_to(surface.backend, x, y, p, xt, yt, dt)
        surface.end_atomic()
        # Check rendered size
        tposs = surface.get_tile_coords()
        outside = min({tx for tx, ty in tposs}) < 0
        outside = outside or (min({ty for tx, ty in tposs}) < 0)
        outside = outside or (max({tx for tx, ty in tposs}) >= size_in_tiles)
        outside = outside or (max({ty for tx, ty in tposs}) >= size_in_tiles)

        if not outside:
            break
    # Convert to pixbuf at the right scale
    rect = (0, 0, width, height)
    pixbuf = render_as_pixbuf(surface, *rect, alpha=True)
    if max(width, height) != 128:
        interp = (GdkPixbuf.InterpType.NEAREST if max(width, height) < 128
                  else GdkPixbuf.InterpType.BILINEAR)
        pixbuf = pixbuf.scale_simple(128, 128, interp)
    # Composite over a checquered bg via Cairo: shows erases
    size = gui.style.ALPHA_CHECK_SIZE
    nchecks = int(128 / size)
    cairo_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 128, 128)
    cr = cairo.Context(cairo_surf)
    render_checks(cr, size, nchecks)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    cairo_surf.flush()
    return Gdk.pixbuf_get_from_surface(cairo_surf, 0, 0, 128, 128)


def _brush_preview_bg_fg(surface, size_in_tiles, brushinfo):
    """Render the background for brush previews, return paint color"""
    # The background color represents the overall nature of the brush
    col1 = (0.85, 0.85, 0.80)  # Boring grey, with a hint of paper-yellow
    col2 = (0.80, 0.80, 0.80)  # Grey, but will appear blueish in contrast
    fgcol = (0.05, 0.15, 0.20)  # Hint of color shows off HSV varier brushes
    spiral = False
    N = lib.tiledsurface.N
    fx = [
        (
            "eraser",  # pink=rubber=eraser; red=danger
            (0.8, 0.7, 0.7),  # pink/red tones: pencil eraser/danger
            (0.75, 0.60, 0.60),
            False, fgcol
        ),
        (
            "colorize",
            (0.8, 0.8, 0.8),  # orange on gray
            (0.6, 0.6, 0.6),
            False, (0.6, 0.2, 0.0)
        ),
        (
            "smudge",  # blue=water=wet, with some contrast
            (0.85, 0.85, 0.80),  # same as the regular paper color
            (0.60, 0.60, 0.70),  # bluer (water, wet); more contrast
            True, fgcol
        ),
    ]
    for cname, c1, c2, c_spiral, c_fg, in fx:
        if brushinfo.has_large_base_value(cname):
            col1 = c1
            col2 = c2
            fgcol = c_fg
            spiral = c_spiral
            break

    never_smudger = (brushinfo.has_small_base_value("smudge") and
                     brushinfo.has_only_base_value("smudge"))
    colorizer = brushinfo.has_large_base_value("colorize")

    if never_smudger and not colorizer:
        col2 = col1

    a = 1 << 15
    col1_fix15 = [c*a for c in col1] + [a]
    col2_fix15 = [c*a for c in col2] + [a]
    for ty in range(0, size_in_tiles):
        tx_thres = max(0, size_in_tiles - ty - 1)
        for tx in range(0, size_in_tiles):
            topcol = col1_fix15
            botcol = col1_fix15
            if tx > tx_thres:
                topcol = col2_fix15
            if tx >= tx_thres:
                botcol = col2_fix15
            with surface.tile_request(tx, ty, readonly=False) as dst:
                if topcol == botcol:
                    dst[:] = topcol
                else:
                    for i in range(N):
                        dst[0:N-i, i, ...] = topcol
                        dst[N-i:N, i, ...] = botcol
    return fgcol, spiral


def render_checks(cr, size, nchecks):
    """Render a checquerboard pattern to a cairo surface"""
    cr.set_source_rgb(*gui.style.ALPHA_CHECK_COLOR_1)
    cr.paint()
    cr.set_source_rgb(*gui.style.ALPHA_CHECK_COLOR_2)
    for i in xrange(0, nchecks):
        for j in xrange(0, nchecks):
            if (i+j) % 2 == 0:
                continue
            cr.rectangle(i*size, j*size, size, size)
            cr.fill()


def load_symbolic_icon(icon_name, size, fg=None, success=None,
                       warning=None, error=None, outline=None):
    """More Pythonic wrapper for gtk_icon_info_load_symbolic() etc.

    :param str icon_name: Name of the symbolic icon to render
    :param int size: Pixel size to render at
    :param tuple fg: foreground color (rgba tuple, values in [0..1])
    :param tuple success: success color (rgba tuple, values in [0..1])
    :param tuple warning: warning color (rgba tuple, values in [0..1])
    :param tuple error: error color (rgba tuple, values in [0..1])
    :param tuple outline: outline color (rgba tuple, values in [0..1])
    :returns: The rendered symbolic icon
    :rtype: GdkPixbuf.Pixbuf

    If the outline color is specified, a single-pixel outline is faked
    for the icon. Outlined renderings require a size 2 pixels larger
    than non-outlined if the central icon is to be of the same size.

    The returned value should be cached somewhere.

    """
    theme = Gtk.IconTheme.get_default()
    if outline is not None:
        size -= 2
    info = theme.lookup_icon(icon_name, size, Gtk.IconLookupFlags(0))
    rgba_or_none = lambda tup: (tup is not None) and Gdk.RGBA(*tup) or None
    icon_pixbuf, was_symbolic = info.load_symbolic(
        fg=rgba_or_none(fg),
        success_color=rgba_or_none(success),
        warning_color=rgba_or_none(warning),
        error_color=rgba_or_none(error),
    )
    assert was_symbolic
    if outline is None:
        return icon_pixbuf

    result = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, True, 8,
        size+2, size+2,
    )
    result.fill(0x00000000)
    outline_rgba = list(outline)
    outline_rgba[3] /= 3.0
    outline_rgba = Gdk.RGBA(*outline_rgba)
    outline_stamp, was_symbolic = info.load_symbolic(
        fg=outline_rgba,
        success_color=outline_rgba,
        warning_color=outline_rgba,
        error_color=outline_rgba,
    )
    w = outline_stamp.get_width()
    h = outline_stamp.get_height()
    assert was_symbolic
    offsets = [
        (-1, -1), (0, -1), (1, -1),
        (-1, 0),           (1, 0),
        (-1, 1),  (0, 1),  (1, 1),
    ]
    for dx, dy in offsets:
        outline_stamp.composite(
            result,
            dx+1, dy+1, w, h,
            dx+1, dy+1, 1, 1,
            GdkPixbuf.InterpType.NEAREST, 255,
        )
    icon_pixbuf.composite(
        result,
        1, 1, w, h,
        1, 1, 1, 1,
        GdkPixbuf.InterpType.NEAREST, 255,
    )
    return result


def render_round_floating_button(cr, x, y, color, pixbuf,
                                 radius=gui.style.FLOATING_BUTTON_RADIUS):
    """Draw a round floating button with a standard size.

    These are used within certain overlays tightly associated with
    particular interaction modes for manipulating things on the canvas.

    """
    x = round(float(x))
    y = round(float(y))
    render_round_floating_color_chip(cr, x, y, color, radius=radius)
    cr.save()
    w = pixbuf.get_width()
    h = pixbuf.get_height()
    x -= w/2
    y -= h/2
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, x, y)
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.paint()
    cr.restore()


def _get_paint_chip_highlight(color):
    """Paint chip highlight edge color"""
    highlight = HCYColor(color=color)
    ky = gui.style.PAINT_CHIP_HIGHLIGHT_HCY_Y_MULT
    kc = gui.style.PAINT_CHIP_HIGHLIGHT_HCY_C_MULT
    highlight.y = clamp(highlight.y * ky, 0, 1)
    highlight.c = clamp(highlight.c * kc, 0, 1)
    return highlight


def _get_paint_chip_shadow(color):
    """Paint chip shadow edge color"""
    shadow = HCYColor(color=color)
    ky = gui.style.PAINT_CHIP_SHADOW_HCY_Y_MULT
    kc = gui.style.PAINT_CHIP_SHADOW_HCY_C_MULT
    shadow.y = clamp(shadow.y * ky, 0, 1)
    shadow.c = clamp(shadow.c * kc, 0, 1)
    return shadow


def render_round_floating_color_chip(cr, x, y, color, radius):
    """Draw a round color chip with a slight drop shadow

    Currently used for dismiss/delete buttons and control points.
    The button's style is similar to that used for the paint chips
    in the dockable palette panel.

    """
    x = round(float(x))
    y = round(float(y))

    cr.save()
    cr.set_dash([], 0)
    cr.set_line_width(0)

    base_col = RGBColor(color=color)
    hi_col = _get_paint_chip_highlight(base_col)

    xoffs = gui.style.DROP_SHADOW_X_OFFSET
    yoffs = gui.style.DROP_SHADOW_Y_OFFSET
    blur = gui.style.DROP_SHADOW_BLUR
    alpha = gui.style.DROP_SHADOW_ALPHA
    drop_shadow = cairo.RadialGradient(
        x+xoffs, y+yoffs, radius,
        x+xoffs, y+yoffs, radius + blur,
    )
    drop_shadow.add_color_stop_rgba(0, 0, 0, 0, alpha)
    drop_shadow.add_color_stop_rgba(1, 0, 0, 0, 0.0)
    cr.arc(x+xoffs, y+yoffs, radius + blur + 1, 0, 2*math.pi)
    cr.set_source(drop_shadow)
    cr.fill()

    cr.arc(x, y, radius, 0, 2*math.pi)
    cr.set_source_rgb(*base_col.get_rgb())
    cr.fill_preserve()
    cr.clip_preserve()

    cr.set_source_rgb(*hi_col.get_rgb())
    cr.set_line_width(2)
    cr.stroke()

    cr.restore()


def draw_draggable_edge_drop_shadow(cr, p0, p1, width):
    """Draw the drop shadow for an edge which can be dragged, then clear the path"""
    x0, y0 = p0
    x1, y1 = p1
    cr.move_to(x0, y0)
    cr.line_to(x1, y1)
    draw_draggable_path_drop_shadow(cr, width)
    cr.new_path()

def draw_draggable_path_drop_shadow(cr, width):
    """Draw the drop shadow for all draggable edges in the current path, without clearing the current path"""

    cr.save()

    # Drop shadow
    alpha = gui.style.DROP_SHADOW_ALPHA
    xoffs = gui.style.DROP_SHADOW_X_OFFSET * 0.5
    yoffs = gui.style.DROP_SHADOW_Y_OFFSET * 0.5
    blur = gui.style.DROP_SHADOW_BLUR * 0.5

    steps = int(math.ceil(blur * 2))
    a = float(gui.style.DROP_SHADOW_ALPHA) / steps
    cr.set_source_rgba(0, 0, 0, a)

    old_path = cr.copy_path()
    cr.translate(xoffs, yoffs)
    cr.new_path()
    cr.append_path(old_path)
    steps = int(math.ceil(blur * 2))
    for i in range(steps):  # [0...4]
        b = blur * float(i+1) / steps
        cr.set_line_width(width + b)
        cr.stroke_preserve()
    cr.translate(-xoffs, -yoffs)
    cr.new_path()
    cr.append_path(old_path)
    cr.restore()


## Test code

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    import sys
    import lib.pixbuf
    for myb_file in sys.argv[1:]:
        if not myb_file.lower().endswith(".myb"):
            logger.warning("Ignored %r: not a .myb file", myb_file)
            continue
        myb_fp = open(myb_file, 'r')
        myb_json = myb_fp.read()
        myb_fp.close()
        myb_brushinfo = BrushInfo(myb_json)
        myb_pixbuf = render_brush_preview_pixbuf(myb_brushinfo)
        if myb_pixbuf is not None:
            myb_basename = myb_file[:-4]
            png_file = "%s_autopreview.png" % (myb_file,)
            logger.info("Saving to %r...", png_file)
            lib.pixbuf.save(myb_pixbuf, png_file, "png")

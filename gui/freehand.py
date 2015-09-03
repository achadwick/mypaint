# This file is part of MyPaint.
# Copyright (C) 2008-2013 by Martin Renold <martinxyz@gmx.ch>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or

"""Freehand drawing modes"""

## Imports

import math
from numpy import array
from numpy import isfinite
from lib.helpers import clamp
import logging
from collections import deque
logger = logging.getLogger(__name__)

import gtk2compat
from gettext import gettext as _
import gobject
import gtk
from gtk import gdk
from libmypaint import brushsettings

import gui.mode
from drawutils import spline_4p

from lib import mypaintlib


## Class defs

class FreehandMode (gui.mode.BrushworkModeMixin,
                    gui.mode.ScrollableModeMixin,
                    gui.mode.InteractionMode):
    """Freehand drawing mode

    To improve application responsiveness, this mode uses an internal
    queue for capturing input data. The raw motion data from the stylus
    is queued; an idle routine then tidies up this data and feeds it
    onward. The presence of an input capture queue means that long
    queued strokes can be terminated by entering a new mode, or by
    pressing Escape.

    This is the default mode in MyPaint.

    """

    ## Class constants & instance defaults

    ACTION_NAME = 'FreehandMode'
    permitted_switch_actions = set()   # Any action is permitted

    _OPTIONS_WIDGET = None

    IS_LIVE_UPDATEABLE = True

    # Motion queue processing (raw data capture)

    # This controls processing of an internal queue of event data such
    # as the x and y coords, pressure and tilt prior to the strokes
    # rendering.

    MOTION_QUEUE_PRIORITY = gobject.PRIORITY_DEFAULT_IDLE

    # The Right Thing To Do generally is to spend as little time as
    # possible directly handling each event received. Disconnecting
    # stroke rendering from event processing buys the user the ability
    # to quit out of a slowly/laggily rendering stroke if desired.

    # Due to later versions of GTK3 (3.8+) discarding successive motion
    # events received in the same frame (so-called "motion
    # compression"), we employ a GDK event filter (on platforms using
    # Xi2) to capture coordinates faster than GDK deigns to pass them on
    # to us. Reason: we want the fidelity that GDK refuses to give us
    # currently for a fancy Wacom device delivering motion events at
    # ~200Hz: frame clock speeds are only 50 or 60 Hz.

    # https://gna.org/bugs/?21003
    # https://gna.org/bugs/?20822
    # https://bugzilla.gnome.org/show_bug.cgi?id=702392

    # It's hard to capture and translate tilt and pressure info in a
    # manner that's compatible with GDK without using GDK's own private
    # internals.  Implementing our own valuator translations would be
    # likely to break, so for these pices of info we interpolate the
    # values received on the clock tick-synchronized motion events GDK
    # gives us, but position them at the x and y that Xi2 gave us.
    # Pressure and tilt fidelities matter less than positional accuracy.

    ## Initialization

    def __init__(self, ignore_modifiers=True, **args):
        # Ignore the additional arg that flip actions feed us
        super(FreehandMode, self).__init__(**args)
        self._cursor_hidden_tdws = set()
        self._cursor_hidden = None

    ## Metadata

    pointer_behavior = gui.mode.Behavior.PAINT_FREEHAND
    scroll_behavior = gui.mode.Behavior.CHANGE_VIEW

    @classmethod
    def get_name(cls):
        return _(u"Freehand Drawing")

    def get_usage(self):
        return _(u"Paint free-form brush strokes")

    ## Per-TDW drawing state

    def _reset_drawing_state(self):
        """Resets all per-TDW drawing state"""
        self._drawing_state = {}

    def _get_drawing_state(self, tdw):
        drawstate = self._drawing_state.get(tdw, None)
        if drawstate is None:
            drawstate = _DrawingState()
            self._drawing_state[tdw] = drawstate
        return drawstate

    ## Mode stack & current mode

    def enter(self, doc, **kwds):
        """Enter freehand mode"""
        super(FreehandMode, self).enter(doc, **kwds)
        self._reset_drawing_state()
        self._debug = (logger.getEffectiveLevel() == logging.DEBUG)

    def leave(self, **kwds):
        """Leave freehand mode"""
        self._reset_drawing_state()
        self._reinstate_drawing_cursor(tdw=None)
        super(FreehandMode, self).leave(**kwds)

    ## Special cursor state while there's pressure

    def _hide_drawing_cursor(self, tdw):
        """Hide the cursor while painting, if configured to.

        :param tdw: Canvas widget to hide the cursor on.
        :type tdw: gui.tileddrawwindow.TiledDrawWindow

        """
        if tdw in self._cursor_hidden_tdws:
            return
        if not tdw.app:
            return
        if not tdw.app.preferences.get("ui.hide_cursor_while_painting"):
            return
        cursor = self._cursor_hidden
        if not cursor:
            cursor = gdk.Cursor(gdk.CursorType.BLANK_CURSOR)
            self._cursor_hidden = cursor
        tdw.set_override_cursor(cursor)
        self._cursor_hidden_tdws.add(tdw)

    def _reinstate_drawing_cursor(self, tdw=None):
        """Un-hide any hidden cursors.

        :param tdw: Canvas widget to reset. None means all affected.
        :type tdw: gui.tileddrawwindow.TiledDrawWindow

        """
        if tdw is None:
            for tdw in self._cursor_hidden_tdws:
                tdw.set_override_cursor(None)
            self._cursor_hidden_tdws.clear()
        elif tdw in self._cursor_hidden_tdws:
            tdw.set_override_cursor(None)
            self._cursor_hidden_tdws.remove(tdw)

    ## Input handlers

    def button_press_cb(self, tdw, event):
        result = False
        current_layer = tdw.doc.layer_stack.current
        if (current_layer.get_paintable() and event.button == 1
                and event.type == gdk.BUTTON_PRESS):
            # Single button press
            # Stroke started, notify observers
            self.doc.input_stroke_started(event)
            # Mouse button pressed (while painting without pressure
            # information)
            drawstate = self._get_drawing_state(tdw)
            if not drawstate.last_event_had_pressure:
                # For the mouse we don't get a motion event for
                # "pressure" changes, so we simulate it. (Note: we can't
                # use the event's button state because it carries the
                # old state.)
                self.motion_notify_cb(tdw, event, fakepressure=0.5)

            drawstate.button_down = event.button
            self.last_good_raw_pressure = 0.0
            self.last_good_raw_xtilt = 0.0
            self.last_good_raw_ytilt = 0.0

            # Hide the cursor if configured to
            self._hide_drawing_cursor(tdw)

            result = True
        return (super(FreehandMode, self).button_press_cb(tdw, event)
                or result)

    def button_release_cb(self, tdw, event):
        result = False
        current_layer = tdw.doc.layer_stack.current
        if current_layer.get_paintable() and event.button == 1:
            # See comment above in button_press_cb.
            drawstate = self._get_drawing_state(tdw)
            if not drawstate.last_event_had_pressure:
                self.motion_notify_cb(tdw, event, fakepressure=0.0)
            # Notify observers after processing the event
            self.doc.input_stroke_ended(event)

            drawstate.button_down = None
            self.last_good_raw_pressure = 0.0
            self.last_good_raw_xtilt = 0.0
            self.last_good_raw_ytilt = 0.0

            # Reinstate the normal cursor if it was hidden
            self._reinstate_drawing_cursor(tdw)

            result = True
        return (super(FreehandMode, self).button_release_cb(tdw, event)
                or result)

    def motion_notify_cb(self, tdw, event, fakepressure=None):
        """Motion event handler: queues raw input and returns

        :param tdw: The TiledDrawWidget receiving the event
        :param event: the MotionNotify event being handled
        :param fakepressure: fake pressure to use if no real pressure

        Fake pressure is passed with faked motion events, e.g.
        button-press and button-release handlers for mouse events.

        GTK 3.8 and above does motion compression, forcing our use of
        event filter hackery to obtain the high-resolution event
        positions required for making brushstrokes. This handler is
        still called for the events the GDK compression code lets
        through, and it is the only source of pressure and tilt info
        available when motion compression is active.
        """

        # Do nothing if painting is inactivated
        current_layer = tdw.doc._layers.current
        if not (tdw.is_sensitive and current_layer.get_paintable()):
            return False

        drawstate = self._get_drawing_state(tdw)

        # If the device has changed and the last pressure value from the
        # previous device is not equal to 0.0, this can leave a visible
        # stroke on the layer even if the 'new' device is not pressed on
        # the tablet and has a pressure axis == 0.0.  Reseting the brush
        # when the device changes fixes this issue, but there may be a
        # much more elegant solution that only resets the brush on this
        # edge-case.
        same_device = True
        if tdw.app is not None:
            device = event.get_source_device()
            same_device = tdw.app.device_monitor.device_used(device)
            if not same_device:
                tdw.doc.brush.reset()

        # Extract the raw readings for this event
        x = event.x
        y = event.y
        time = event.time
        pressure = event.get_axis(gdk.AXIS_PRESSURE)
        xtilt = event.get_axis(gdk.AXIS_XTILT)
        ytilt = event.get_axis(gdk.AXIS_YTILT)
        state = event.state

        # Workaround for buggy evdev behaviour.
        # Events sometimes get a zero raw pressure reading when the
        # pressure reading has not changed. This results in broken
        # lines. As a workaround, forbid zero pressures if there is a
        # button pressed down, and substitute the last-known good value.
        # Detail: https://github.com/mypaint/mypaint/issues/29
        if drawstate.button_down is not None:
            if pressure == 0.0:
                pressure = drawstate.last_good_raw_pressure
            elif pressure is not None and isfinite(pressure):
                drawstate.last_good_raw_pressure = pressure

        # Ensure each event has a defined pressure
        if pressure is not None:
            # Using the reported pressure. Apply some sanity checks
            if not isfinite(pressure):
                # infinity/nan: use button state (instead of clamping in
                # brush.hpp) https://gna.org/bugs/?14709
                pressure = None
            else:
                pressure = clamp(pressure, 0.0, 1.0)
            drawstate.last_event_had_pressure = True

        # Fake the pressure if we have none, or if infinity was reported
        if pressure is None:
            if fakepressure is not None:
                pressure = clamp(fakepressure, 0.0, 1.0)
            else:
                pressure = (state & gdk.BUTTON1_MASK) and 0.5 or 0.0
            drawstate.last_event_had_pressure = False

        # Check whether tilt is present.  For some tablets without
        # tilt support GTK reports a tilt axis with value nan, instead
        # of None.  https://gna.org/bugs/?17084
        if xtilt is None or ytilt is None or not isfinite(xtilt+ytilt):
            xtilt = 0.0
            ytilt = 0.0
        else:
            # Evdev workaround. X and Y tilts suffer from the same
            # problem as pressure for fancier devices.
            if drawstate.button_down is not None:
                if xtilt == 0.0:
                    xtilt = drawstate.last_good_raw_xtilt
                else:
                    drawstate.last_good_raw_xtilt = xtilt
                if ytilt == 0.0:
                    ytilt = drawstate.last_good_raw_ytilt
                else:
                    drawstate.last_good_raw_ytilt = ytilt

            # Tilt inputs are assumed to be relative to the viewport,
            # but the canvas may be rotated or mirrored, or both.
            # Compensate before passing them to the brush engine.
            # https://gna.org/bugs/?19988
            if tdw.mirrored:
                xtilt *= -1.0
            if tdw.rotation != 0:
                tilt_angle = math.atan2(ytilt, xtilt) - tdw.rotation
                tilt_magnitude = math.sqrt((xtilt**2) + (ytilt**2))
                xtilt = tilt_magnitude * math.cos(tilt_angle)
                ytilt = tilt_magnitude * math.sin(tilt_angle)

        # HACK: color picking, do not paint
        # TEST: Does this ever happen now?
        if state & gdk.CONTROL_MASK or state & gdk.MOD1_MASK:
            # Don't simply return; this is a workaround for unwanted
            # lines in https://gna.org/bugs/?16169
            pressure = 0.0

        # Apply pressure mapping if we're running as part of a full
        # MyPaint application (and if there's one defined).
        if tdw.app is not None and tdw.app.pressure_mapping:
            pressure = tdw.app.pressure_mapping(pressure)

        # Apply any configured while-drawing cursor
        if pressure > 0:
            self._hide_drawing_cursor(tdw)
        else:
            self._reinstate_drawing_cursor(tdw)

        # HACK: straight line mode?
        # TEST: Does this ever happen?
        if state & gdk.SHIFT_MASK:
            pressure = 0.0

        # Queue this event
        x, y = tdw.display_to_model(x, y)
        event_data = (time, x, y, pressure, xtilt, ytilt)
        drawstate.queue_motion(event_data)
        # Start the motion event processor, if it isn't already running
        if not drawstate.motion_processing_cbid:
            cbid = gobject.idle_add(self._motion_queue_idle_cb, tdw,
                                    priority=self.MOTION_QUEUE_PRIORITY)
            drawstate.motion_processing_cbid = cbid

    ## Motion queue processing

    def _motion_queue_idle_cb(self, tdw):
        """Idle callback; processes each queued event"""
        drawstate = self._get_drawing_state(tdw)
        # Stop if asked to stop
        if drawstate.motion_processing_cbid is None:
            drawstate.motion_queue = deque()
            return False
        # Forward one or more motion events to the canvas
        for event in drawstate.next_processing_events():
            self._process_queued_event(tdw, event)
        # Stop if the queue is now empty
        if len(drawstate.motion_queue) == 0:
            drawstate.motion_processing_cbid = None
            return False
        # Otherwise, continue being invoked
        return True

    def _process_queued_event(self, tdw, event_data):
        """Process one motion event from the motion queue"""
        drawstate = self._get_drawing_state(tdw)
        time, x, y, pressure, xtilt, ytilt = event_data
        model = tdw.doc

        # Calculate time delta for the brush engine
        last_event_time = drawstate.last_handled_event_time
        drawstate.last_handled_event_time = time
        if not last_event_time:
            return
        dtime = (time - last_event_time)/1000.0
        if self._debug:
            cavg = drawstate.avgtime
            if cavg is not None:
                tavg, nevents = cavg
                nevents += 1
                tavg += (dtime - tavg)/nevents
            else:
                tavg = dtime
                nevents = 1
            if nevents*tavg > 1.0 and nevents > 20:
                logger.debug("Processing at %d events/s (t_avg=%0.3fs)",
                             nevents, tavg)
                drawstate.avgtime = None
            else:
                drawstate.avgtime = (tavg, nevents)

        # Refuse drawing if the layer is locked or hidden
        current_layer = model._layers.current
        if current_layer.locked or not current_layer.visible:
            return

        # Feed data to the brush engine.  Pressure and tilt cleanup
        # needs to be done here to catch all forwarded data after the
        # earlier interpolations. The interpolation method used for
        # filling in missing axis data is known to generate
        # OverflowErrors for legitimate but pathological input streams.
        # https://github.com/mypaint/mypaint/issues/344

        pressure = clamp(pressure, 0.0, 1.0)
        tilt = clamp(xtilt, -1.0, 1.0)
        ytilt = clamp(ytilt, -1.0, 1.0)
        self.stroke_to(model, dtime, x, y, pressure, xtilt, ytilt)

        # Update the TDW's idea of where we last painted
        # FIXME: this should live in the model, not the view
        if pressure:
            tdw.set_last_painting_pos((x, y))

    ## Mode options

    def get_options_widget(self):
        """Get the (class singleton) options widget"""
        cls = self.__class__
        if cls._OPTIONS_WIDGET is None:
            widget = FreehandOptionsWidget()
            cls._OPTIONS_WIDGET = widget
        return cls._OPTIONS_WIDGET


class _DrawingState (object):
    """Per-canvas drawing state.

    Various kinds of queue for data fixup, before it's passed to the
    libmypaint brush engine.

    """

    def __init__(self):
        object.__init__(self)

        self.last_event_had_pressure = False

        # Raw data which was delivered with an identical timestamp
        # to the previous one.  Happens on Windows due to differing
        # clock granularities (at least, under GTK2).
        self._zero_dtime_motions = []

        # Motion Queue

        # Combined, cleaned-up motion data queued ready for rendering
        # Using a queue makes rendering independent of data gathering.
        self.motion_queue = deque()
        self.motion_processing_cbid = None
        self._last_queued_event_time = 0

        # Queued Event Handling

        # Time of the last-processed event
        self.last_handled_event_time = 0

        # Debugging: number of events procesed each second,
        # average times.
        self.avgtime = None

        # Button pressed while drawing
        # Not every device sends button presses, but evdev ones
        # do, and this is used as a workaround for an evdev bug:
        # https://github.com/mypaint/mypaint/issues/29
        self.button_down = None

        self.last_good_raw_pressure = 0.0
        self.last_good_raw_xtilt = 0.0
        self.last_good_raw_ytilt = 0.0

    def queue_motion(self, event_data):
        """Append one raw motion event to the motion queue

        :param event_data: Extracted data from an event.
        :type event_data: tuple

        Events are tuples of the form ``(time, x, y, pressure,
        xtilt, ytilt)``. Times are in milliseconds, and are
        expressed as ints. ``x`` and ``y`` are ordinary Python
        floats, and refer to model coordinates. The pressure and
        tilt values have the meaning assigned to them by GDK; if
        ```pressure`` is None, pressure and tilt values will be
        interpolated from surrounding defined values.

        Zero-dtime events are detected and cleaned up here.
        """
        time, x, y, pressure, xtilt, ytilt = event_data
        if time < self._last_queued_event_time:
            logger.warning('Time is running backwards! Corrected.')
            time = self._last_queued_event_time

        if time == self._last_queued_event_time:
            # On Windows, GTK timestamps have a resolution around
            # 15ms, but tablet events arrive every 8ms.
            # https://gna.org/bugs/index.php?16569
            zdata = (x, y, pressure, xtilt, ytilt)
            self._zero_dtime_motions.append(zdata)
        else:
            # Queue any previous events that had identical
            # timestamps, linearly interpolating their times.
            if self._zero_dtime_motions:
                dtime = time - self._last_queued_event_time
                if dtime > 100:
                    # Really old events; don't associate them with
                    # the new one.
                    zt = time - 100.0
                    interval = 100.0
                else:
                    zt = self._last_queued_event_time
                    interval = float(dtime)
                step = interval / (len(self._zero_dtime_motions) + 1)
                for zx, zy, zp, zxt, zyt in self._zero_dtime_motions:
                    zt += step
                    zevent_data = (zt, zx, zy, zp, zxt, zyt)
                    self.motion_queue.append(zevent_data)
                # Reset the backlog buffer
                self._zero_dtime_motions = []
            # Queue this event too
            self.motion_queue.append(event_data)
            # Update the timestamp used above
            self._last_queued_event_time = time

    def next_processing_events(self):
        """Fetches zero or more events to process from the queue"""
        if len(self.motion_queue) > 0:
            event = self.motion_queue.popleft()
            yield event


class FreehandOptionsWidget (gui.mode.PaintingModeOptionsWidgetBase):
    """Configuration widget for freehand mode"""

    def init_specialized_widgets(self, row):
        cname = "slow_tracking"
        label = gtk.Label()
        #TRANSLATORS: Short alias for "Slow position tracking". This is
        #TRANSLATORS: used on the options panel.
        label.set_text(_("Smooth:"))
        label.set_alignment(1.0, 0.5)
        label.set_hexpand(False)
        self.adjustable_settings.add(cname)
        adj = self.app.brush_adjustment[cname]
        scale = gtk.HScale(adj)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        self.attach(label, 0, row, 1, 1)
        self.attach(scale, 1, row, 1, 1)
        row += 1
        return row

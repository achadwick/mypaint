
import os.path
from gettext import gettext as _
import logging
logger = logging.getLogger(__name__)
import fractions
import math

import gi
from gi.repository import Gtk
from gi.repository import GObject


class FrameSizeDialogPresenter (object):
    """Display logic and launcher for the frame size dialog"""

    _GLADE_FILE = os.path.splitext(__file__)[0] + '.glade'
    _DIMS_FMT1 =  _(u"%(width).0f \u00D7 %(height).0f %(unit)s")
    _DIMS_FMT2 =  _(u"%(width).1f \u00D7 %(height).1f %(unit)s")
    _PIXEL_UNIT = -1
    _UNIT_INFO = [
        # GTK-constant    Code  Display  Table-fmt   Num-per-inch Digits
        (Gtk.Unit.MM,     "mm", _("mm"), _DIMS_FMT1, 25.4,        1),
        (Gtk.Unit.INCH,   "in", _("in"), _DIMS_FMT2, 1.0,         2),
        (Gtk.Unit.POINTS, "pt", _("pt"), _DIMS_FMT1, 72,          1),
        (_PIXEL_UNIT,     "px", _("px"), _DIMS_FMT1, None,        0),
        ]
    _UNITS = [i[0] for i in _UNIT_INFO]
    _UNIT2NAME = dict([(i[0], i[1]) for i in _UNIT_INFO])
    _NAME2UNIT = dict([(i[1], i[0]) for i in _UNIT_INFO])
    _UNIT2DNAME = dict([(i[0], i[2]) for i in _UNIT_INFO])
    _UNIT2FMT = dict([(i[0], i[3]) for i in _UNIT_INFO])
    _UNIT2CONV = dict([(i[0], i[4]) for i in _UNIT_INFO])
    _UNIT2DIGITS = dict([(i[0], i[5]) for i in _UNIT_INFO])
    _MAX_SIZE_PX = 32000
    _STEP_PX = 10

    def __init__(self, model):
        """Initialize, creating the View"""
        object.__init__(self)
        self._model = model
        self._pending_model_frame_updates = {}
        self._paper_sizes = {}
        self._portrait_selected = False
        b = Gtk.Builder()
        self._builder = b
        b.add_from_file(self._GLADE_FILE)
        self.dialog = b.get_object("frame_size_dialog")
        self._init_units()
        self._init_paper_sizes()
        b.connect_signals(self)
        self._aspect_ratio = None  # None, or shortest/longest
        self._width_px = 0
        self._height_px = 0
        self._update_paper_sizes()
        self._model.frame_updated += self._model_frame_updated_cb
        fx, fw, fw, fh = self._model.get_frame()
        if fw > 0 and fh > 0:
            self._set_dimension_values(w=fw, h=fh)
            self._width_px = fh
            self._height_px = fw
        self._update_dimension_spinners()
        self._update_orientation_radioactions()

    def run(self):
        return self.dialog.run()

    def _init_units(self):
        """Initializes the units combo with the standard units"""
        combo = self._builder.get_object("units_combobox")
        store = self._builder.get_object("units_liststore")
        for unit in self._UNITS:
            name = self._UNIT2NAME[unit]
            dname = self._UNIT2DNAME[unit]
            store.append((name, dname))
        combo.set_active(len(self._UNITS) - 1)

    def _init_paper_sizes(self):
        """Initializes the paper sizes list with the standard sizes"""
        store = self._builder.get_object("paper_size_liststore")
        store.clear()
        # The standard list is huge, so show those with defined consts first
        sizes = [self._get_paper_size(getattr(Gtk, n)) for n in dir(Gtk)
                 if "PAPER_NAME_" in n]
        seen = set(sizes)
        sizes += [s for s in Gtk.paper_size_get_paper_sizes(False)
                  if s not in seen]
        unit = Gtk.Unit.MM
        for size in sizes:
            name = size.get_name()
            dname = size.get_display_name()
            store.append((name, dname, ""))

    @property
    def dpi(self):
        adj = self._builder.get_object("dpi_adj")
        return float(adj.get_value())

    def _to_pixels(self, value):
        if self.unit != self._PIXEL_UNIT:
            inches = float(value) / self._UNIT2CONV[self.unit]
            return inches * self.dpi
        else:
            return float(value)

    def _from_pixels(self, value):
        if self.unit != self._PIXEL_UNIT:
            inches = float(value) / self.dpi
            return inches * self._UNIT2CONV[self.unit]
        else:
            return float(value)
        
    def _update_dimension_spinners(self):
        maxval = self._from_pixels(self._MAX_SIZE_PX)
        step = self._from_pixels(self._STEP_PX)
        digits = self._UNIT2DIGITS[self.unit]
        for dimname, pxval in [("width", self._width_px),
                               ("height", self._height_px)]:
            adj = self._builder.get_object("%s_adj" % dimname)
            adj.configure(self._from_pixels(pxval), 0.0, maxval, step, step, 0)
            spinbut = self._builder.get_object("%s_spinbutton" % dimname)
            spinbut.set_digits(digits)

    def _set_dimension_values(self, w=None, h=None):
        logger.debug("_set_dimension_values(w=%r, h=%r)", w, h)
        ndigits = self._UNIT2DIGITS[self.unit]
        for adj_name, value in [("width_adj", w), ("height_adj", h)]:
            if value is None:
                continue
            adj = self._builder.get_object(adj_name)
            if round(value, ndigits) != round(adj.get_value(), ndigits):
                adj.set_value(value)
        self._update_ratio()

    def _model_frame_updated_cb(self, model, old_frame, new_frame):
        """Internal callback: frame size was updated, update UI"""
        x, y, new_w, new_h = new_frame
        logger.debug("_model_frame_updated(w=%r, h=%r)", new_w, new_h)
        update_info = [("w", int(new_w)),
                       ("h", int(new_h))]
        update = {}
        for dim_name, new_px_val in update_info:
            new_val = self._from_pixels(float(new_px_val))
            update[dim_name] = new_val
        self._set_dimension_values(**update)

    def _update_model_frame(self):
        """Internal: update parts of the model frame from UI values"""
        x, y, old_w, old_h = self._model.get_frame()
        update_info = [("width_adj", "width", int(old_w), self._width_px),
                       ("height_adj", "height", int(old_h), self._height_px)]
        updates = self._pending_model_frame_updates.copy()
        for adj_name, dim_name, old_val, new_val in update_info:
            if new_val != old_val:
                updates[dim_name] = new_val
        if not updates:
            return
        if not self._pending_model_frame_updates:
            GObject.idle_add(self._do_model_frame_update)
        if self._pending_model_frame_updates != updates:
            self._pending_model_frame_updates = updates
            logger.debug("Pending updates: now %r",
                         self._pending_model_frame_updates)

    def _do_model_frame_update(self):
        updates = self._pending_model_frame_updates
        if updates:
            logger.debug("Updating model with pending updates %r",
                         updates)
            self._model.update_frame(user_initiated=True, **updates)
            self._pending_model_frame_updates = {}
            desel_opts = {"h": True, "w": True}
            if "width" not in updates: desel_opts["w"] = False
            if "height" not in updates: desel_opts["h"] = False
            self._deselect_paper_size_if_necessary(**desel_opts)
        return False

    def _deselect_paper_size_if_necessary(self, h=True, w=True):
        # Possibly remove the selection in the paper size list
        # Only if the value(s) look to have changed
        paper_size = self.paper_size
        if paper_size is None:
            return
        new_w_px = w and self._width_px or None
        new_h_px = h and self._height_px or None
        paper_w_in = paper_size.get_width(Gtk.Unit.INCH)
        paper_h_in = paper_size.get_height(Gtk.Unit.INCH)
        # Flip dimensions according to the user's chosen layout
        if ((self._portrait_selected and paper_w_in > paper_h_in) or
            (not self._portrait_selected) and paper_w_in < paper_h_in):
            paper_w_in, paper_h_in = paper_h_in, paper_w_in
        # Deselect if any of the paper dimensions no longer match the rest of
        # the UI.
        desel_info = [ ("width", new_w_px, paper_w_in),
                       ("height", new_h_px, paper_h_in), ]
        ndigits = self._UNIT2DIGITS[self.unit]
        for d, new_d_px, paper_d_in in desel_info:
            if new_d_px is None:
                continue
            paper_d_px = paper_d_in * self.dpi
            paper_d_px = int(round(paper_d_px))
            if paper_d_px != new_d_px:
                logger.debug("Deselecting because paper's %s %r != new %s %r",
                             d, paper_d_px, d, new_d_px)
                sel = self._builder.get_object("paper_size_selection")
                sel.unselect_all()
                break

    def width_adj_value_changed_cb(self, adj):
        """Callback: frame width adjustment was updated (bound in UI XML)"""
        width_value = float(adj.get_value())
        self._width_px = int(round(self._to_pixels(width_value)))
        if self.lock_aspect:
            update = True
            try:
                if adj.__ratio_interlock:
                    adj.__ratio_interlock = False
                    update = False
            except AttributeError:
                pass
            if update:
                assert self._aspect_ratio is not None
                height_value = width_value / self._aspect_ratio
                height_adj = self._builder.get_object("height_adj")
                height_adj.__ratio_interlock = True
                height_adj.set_value(height_value)
        else:
            self._update_ratio()
        self._update_model_frame()

    def height_adj_value_changed_cb(self, adj):
        """Callback: frame height adjustment was updated (bound in UI XML)"""
        height_value = float(adj.get_value())
        self._height_px = int(round(self._to_pixels(height_value)))
        if self.lock_aspect:
            update = True
            try:
                if adj.__ratio_interlock:
                    adj.__ratio_interlock = False
                    update = False
            except AttributeError:
                pass
            if update:
                assert self._aspect_ratio is not None
                width_value = height_value * self._aspect_ratio
                width_adj = self._builder.get_object("width_adj")
                width_adj.__ratio_interlock = True
                width_adj.set_value(width_value)
        else:
            self._update_ratio()
        self._update_model_frame()

    def _update_ratio(self):
        """Update the dialog aspect ratio, and its associated label"""
        if self._width_px <= 0 or self._height_px <= 0:
            #TRANSLATORS: aspect ratio label, when either dimension is zero
            txt = _("NaN")
            self._aspect_ratio = None
        else:
            w, h = self._width_px, self._height_px
            ratio = float(w) / float(h)
            self._aspect_ratio = ratio
            # Should really be checking whether the difference is less than 1mm
            # at the current dpi.
            if abs(ratio - (1/math.sqrt(2))) <= 0.0075:
                #TRANSLATORS: aspect ratio label for A4 etc: 1:sqrt(2)
                txt = _(u"~1:\u221A2")
            elif abs(ratio - math.sqrt(2)) <= 0.0075:
                #TRANSLATORS: aspect ratio label for A4 etc: sqrt(2):1
                txt = _(u"~\u221A2:1")
            else:
                gcd = fractions.gcd(w, h)
                #TRANSLATORS: aspect ratio label for other ratios
                txt = _("%d:%d") % (w/gcd, h/gcd)
        label = self._builder.get_object("aspect_ratio_label")
        label.set_text(txt)

    @property
    def lock_aspect(self):
        checkbut = self._builder.get_object("lock_aspect_checkbutton")
        return checkbut.get_active()

    @lock_aspect.setter
    def lock_aspect(self, value):
        checkbut = self._builder.get_object("lock_aspect_checkbutton")
        checkbut.set_active(bool(value))

    def _update_paper_sizes(self):
        """Update paper size list when resolution or units change"""
        paper_size_store = self._builder.get_object("paper_size_liststore")
        i = paper_size_store.get_iter_first()
        unit = self.unit
        dpi = self.dpi
        while i:
            size_name, = paper_size_store.get(i, 0)
            size = self._get_paper_size(size_name)
            if unit == self._PIXEL_UNIT:
                w = size.get_width(Gtk.Unit.INCH)
                h = size.get_height(Gtk.Unit.INCH)
                w *= dpi
                h *= dpi
            else:
                w = size.get_width(unit)
                h = size.get_height(unit)
            dims = self._UNIT2FMT[unit] % {
                    "width": w,
                    "height": h,
                    "unit": self._UNIT2DNAME[unit],
                }
            paper_size_store.set(i, 2, dims)
            i = paper_size_store.iter_next(i)

    def frame_size_dialog_after_show_cb(self, dialog):
        """Post-show setup tweaks for the dialog"""
        # Set the paper size selection mode here to ensure that no paper size
        # is selected at first.
        sel = self._builder.get_object("paper_size_selection")
        sel.set_mode(Gtk.SelectionMode.SINGLE)
        sel.unselect_all()

    def _get_paper_size(self, name):
        size = self._paper_sizes.get(name, None)
        if size is None:
            size = Gtk.PaperSize.new(name)
            self._paper_sizes[name] = size
        return size

    @property
    def paper_size(self):
        sel = self._builder.get_object("paper_size_selection")
        if sel.count_selected_rows() == 0:
            return
        size_store, i = sel.get_selected()
        size_name, = size_store.get(i, 0)
        return self._get_paper_size(size_name)

    @property
    def unit(self):
        units_combo = self._builder.get_object("units_combobox")
        unit_name = units_combo.get_active_id()
        return self._NAME2UNIT[unit_name]

    def paper_size_selection_changed_cb(self, sel):
        """Update the rest of the UI when a paper size is chosen"""
        # Don't do anything if the selection was cleared
        size = self.paper_size
        if size is None:
            return
        # Disable the aspect lock button
        self.lock_aspect = False
        # Update the dimensions spinboxes
        unit = self.unit
        dpi = self.dpi
        if unit == self._PIXEL_UNIT:
            w = size.get_width(Gtk.Unit.INCH)
            h = size.get_height(Gtk.Unit.INCH)
            w *= dpi
            h *= dpi
        else:
            w = size.get_width(unit)
            h = size.get_height(unit)
        # Flip dimensions according to the user's chosen layout
        if ((self._portrait_selected and w > h) or
            (not self._portrait_selected) and w < h):
            w, h = h, w
        # Set dimension spinboxes
        self._set_dimension_values(w, h)

    def units_combobox_changed_cb(self, combo):
        self._update_paper_sizes()
        self._update_dimension_spinners()

    def dpi_spinbutton_value_changed_cb(self, spinbut):
        if self.unit == self._PIXEL_UNIT:
            self._update_paper_sizes()
        else:
            self._update_dimension_spinners()

    def portrait_radioaction_changed_cb(self, portrait_action, current):
        """Callback: update sizes when the orientation flips"""
        # Update our internal record
        self._portrait_selected = portrait_action is current
        logger.debug("self._portrait_selected := %r", self._portrait_selected)
        # Disable the aspect lock button
        self.lock_aspect = False
        # Decide whether we need to update the dimensions spinboxes
        if self._orientation_swap_needed():
            logger.debug("Swapping height and width dimensions")
            w_adj = self._builder.get_object("width_adj")
            h_adj = self._builder.get_object("height_adj")
            w_value = w_adj.get_value()
            h_value = h_adj.get_value()
            w_adj.set_value(h_value)
            h_adj.set_value(w_value)

    def _update_orientation_radioactions(self):
        """Internal: Updates the orientation UI to match the dimensions"""
        if self._orientation_swap_needed():
            if self._portrait_selected:
                action_name = "landscape_radioaction"
            else:
                action_name = "portrait_radioaction"
            logger.debug("Making %r active", action_name)
            action = self._builder.get_object(action_name)
            action.set_active(True)

    def _orientation_swap_needed(self):
        """Internal: True if the orientation does not match the dimensions"""
        if self._portrait_selected:
            if self._width_px > self._height_px:
                logger.debug("Swap needed: portrait -> landscape")
                return True
        else:
            if self._height_px > self._width_px:
                logger.debug("Swap needed: landscape -> portrait")
                return True
        return False



if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    import lib.document
    model = lib.document.Document()
    model.update_frame(width=400, height=300)
    fspres = FrameSizeDialogPresenter(model)
    result = fspres.run()
    logger.info("Dialog returned result %r", result)


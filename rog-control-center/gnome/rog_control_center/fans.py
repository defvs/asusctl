# SPDX-License-Identifier: MPL-2.0
"""Accessible, staged eight-point editor with a live Cairo preview."""

from gi.repository import Adw, Gtk

from .backend import FANS, PLATFORM, PROFILES, validate_curve
from .widgets import button, group, label


class FanEditor:
    def __init__(self, window, parent):
        self.window = window
        self.dirty = False
        self.loading = False
        self.profile = None
        self.fan = None
        self.points = []
        self.original_pwm = []
        self.original_percent = []
        self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        parent.append(self.container)
        settings = group(
            self.container,
            "Fan curves",
            "Edit a profile without switching to it. Save to apply your changes.",
        )
        profiles = window.snapshot.first(PLATFORM).props.get("PlatformProfileChoices", [])
        self.profiles = [p for p in profiles if p in window.snapshot.curves]
        self.profile_row = Adw.ComboRow(
            use_markup=False,
            title="Profile to edit",
            model=Gtk.StringList.new([PROFILES.get(p, str(p)) for p in self.profiles]),
        )
        active = window.snapshot.first(PLATFORM).props.get("PlatformProfile")
        if active in self.profiles:
            self.profile_row.set_selected(self.profiles.index(active))
        settings.add(self.profile_row)
        self.fan_row = Adw.ComboRow(use_markup=False, title="Fan")
        settings.add(self.fan_row)
        self.enabled = Adw.SwitchRow(
            use_markup=False,
            title="Use custom curve",
            subtitle="Off uses the firmware’s automatic fan control.",
        )
        settings.add(self.enabled)
        self.enabled.connect("notify::active", self.edited)
        self.profile_row.connect("notify::selected", self.selection_changed)
        self.fan_row.connect("notify::selected", self.selection_changed)
        self.graph = Gtk.DrawingArea(content_height=230, hexpand=True)
        self.graph.add_css_class("card")
        self.graph.set_draw_func(self.draw)
        self.graph.update_property([Gtk.AccessibleProperty.LABEL], ["Fan speed by temperature"])
        self.graph.set_tooltip_text(
            "Drag a point to adjust the curve, or use the exact values below."
        )
        self.graph.set_cursor_from_name("crosshair")
        self.dragging = None
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self.drag_begin)
        drag.connect("drag-update", self.drag_update)
        drag.connect("drag-end", self.drag_end)
        self.graph.add_controller(drag)
        self.container.append(self.graph)
        self.point_group = group(
            self.container,
            "Curve points",
            "Both temperature and speed must increase from top to bottom.",
        )
        self.point_expander = Adw.ExpanderRow(
            use_markup=False,
            title="Edit exact values",
            subtitle="Temperature (°C) and fan speed (%)",
        )
        self.point_group.add(self.point_expander)
        for i in range(8):
            row = Adw.ActionRow(use_markup=False, title=f"Point {i + 1}")
            temp = Gtk.SpinButton.new_with_range(0, 100, 1)
            speed = Gtk.SpinButton.new_with_range(0, 100, 1)
            temp.set_width_chars(3)
            speed.set_width_chars(3)
            temp.set_valign(Gtk.Align.CENTER)
            speed.set_valign(Gtk.Align.CENTER)
            temp.update_property(
                [Gtk.AccessibleProperty.LABEL], [f"Point {i + 1} temperature in °C"]
            )
            speed.update_property(
                [Gtk.AccessibleProperty.LABEL], [f"Point {i + 1} fan speed in percent"]
            )
            row.add_suffix(temp)
            row.add_suffix(label("°C", "dim-label"))
            row.add_suffix(speed)
            row.add_suffix(label("%", "dim-label"))
            temp.connect("value-changed", self.edited)
            speed.connect("value-changed", self.edited)
            self.points.append((temp, speed))
            self.point_expander.add_row(row)
        self.validation = label("", "error", xalign=0, wrap=True)
        self.container.append(self.validation)
        actions = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            min_children_per_line=1,
            max_children_per_line=3,
            column_spacing=8,
            row_spacing=8,
        )
        self.reset = button("Restore defaults", callback=self.restore)
        self.discard = button("Discard", callback=self.load)
        self.save = button("Save curve", css="suggested-action", callback=self.apply)
        actions.insert(self.reset, -1)
        actions.insert(self.discard, -1)
        actions.insert(self.save, -1)
        self.container.append(actions)
        self.load()

    def selection_changed(self, *_):
        if self.loading:
            return
        if self.dirty:
            selected_profile, selected_fan = (
                self.profile_row.get_selected(),
                self.fan_row.get_selected(),
            )
            self.loading = True
            self.profile_row.set_selected(self.profiles.index(self.profile))
            self.fan_row.set_selected(self.fans.index(self.fan))
            self.loading = False

            def switch():
                self.loading = True
                self.profile_row.set_selected(selected_profile)
                # Fan names can change with the profile; load regenerates them.
                self.fan_row.set_selected(selected_fan)
                self.loading = False
                self.load()

            self.window.confirm(
                "Discard unsaved curve?",
                "Your saved fan curve will remain in use.",
                "Discard changes",
                switch,
            )
        else:
            self.load()

    def load(self, *_):
        if not self.profiles:
            self.container.set_sensitive(False)
            return
        self.loading = True
        self.profile = self.profiles[self.profile_row.get_selected()]
        curves = self.window.snapshot.curves[self.profile]
        previous_fans = getattr(self, "fans", [])
        selected = self.fan_row.get_selected()
        if selected < len(previous_fans):
            self.fan = previous_fans[selected]
        self.fans = [c[0] for c in curves]
        self.fan_row.set_model(Gtk.StringList.new(self.fans))
        if not self.fans:
            self.loading = False
            return
        self.fan = self.fan if self.fan in self.fans else self.fans[0]
        self.fan_row.set_selected(self.fans.index(self.fan))
        _, pwm, temps, enabled = next(c for c in curves if c[0] == self.fan)
        self.original_pwm = list(pwm)
        self.original_percent = [round(p * 100 / 255) for p in pwm]
        for (temp, speed), t, p in zip(self.points, temps, self.original_percent, strict=True):
            temp.set_value(t)
            speed.set_value(p)
        self.enabled.set_active(enabled)
        self.loading = False
        self.dirty = False
        self.validate()
        self.graph.queue_draw()

    def values(self):
        temps = [t.get_value_as_int() for t, _ in self.points]
        percentages = [s.get_value_as_int() for _, s in self.points]
        # Preserve byte-exact values for unedited points (percent display is rounded).
        pwm = [
            raw if p == original else round(p * 255 / 100)
            for p, original, raw in zip(
                percentages, self.original_percent, self.original_pwm, strict=True
            )
        ]
        # An edited percentage can round above an untouched adjacent byte even
        # when both display the same percentage (12% -> 31, while 30 -> 12%).
        # Keep those flat sections monotonic without altering untouched values.
        for index, percentage in enumerate(percentages):
            if percentage == self.original_percent[index]:
                continue
            untouched = [
                j
                for j, p in enumerate(percentages)
                if p == percentage and p == self.original_percent[j]
            ]
            low = max((pwm[j] for j in untouched if j < index), default=0)
            high = min((pwm[j] for j in untouched if j > index), default=255)
            pwm[index] = max(low, min(high, pwm[index]))
        return temps, pwm

    def validate(self):
        error = ""
        try:
            validate_curve(*self.values())
        except ValueError as exc:
            error = str(exc)
        self.validation.set_label(error)
        self.save.set_sensitive(self.dirty and not error)
        self.discard.set_sensitive(self.dirty)
        self.reset.set_sensitive(not self.dirty)
        return not error

    def edited(self, *_):
        if self.loading or self.window.syncing:
            return
        self.dirty = True
        self.validate()
        self.graph.queue_draw()

    def apply(self):
        if not self.validate():
            return
        temps, pwm = self.values()
        fan = self.window.snapshot.first(FANS)
        profile, fan_name, enabled = self.profile, self.fan, self.enabled.get_active()
        self.window.perform(
            lambda: self.window.backend.save_curve(
                fan, profile, fan_name, temps, pwm, enabled
            ),
            self.container,
            "Fan curve saved",
            self.load,
        )

    def restore(self):
        fan = self.window.snapshot.first(FANS)
        self.window.confirm(
            "Restore fan curves?",
            f"All fans in {PROFILES.get(self.profile, self.profile)} will return to their defaults.",
            "Restore defaults",
            lambda: self.window.perform(
                lambda: self.window.backend.method(
                    fan, "SetCurvesToDefaults", "(u)", (self.profile,)
                ),
                self.container,
                "Default fan curves restored",
                self.load,
            ),
        )

    def drag_begin(self, _gesture, x, y):
        left, top, right, bottom = 46, 24, self.graph.get_width() - 22, self.graph.get_height() - 36
        distances = [
            (
                (left + (right - left) * t.get_value() / 100 - x) ** 2
                + (bottom - (bottom - top) * s.get_value() / 100 - y) ** 2,
                index,
            )
            for index, (t, s) in enumerate(self.points)
        ]
        distance, index = min(distances)
        self.dragging = (index, x, y) if distance <= 20**2 else None

    def drag_update(self, _gesture, dx, dy):
        if self.dragging is None:
            return
        index, x, y = self.dragging
        left, top, right, bottom = 46, 24, self.graph.get_width() - 22, self.graph.get_height() - 36
        if right <= left or bottom <= top:
            return
        temperature = round((x + dx - left) / (right - left) * 100)
        speed = round((bottom - y - dy) / (bottom - top) * 100)
        previous = self.points[index - 1] if index else None
        following = self.points[index + 1] if index < 7 else None
        for control, value, column in [
            (self.points[index][0], temperature, 0),
            (self.points[index][1], speed, 1),
        ]:
            low = previous[column].get_value_as_int() if previous else 0
            high = following[column].get_value_as_int() if following else 100
            control.set_value(max(low, min(high, value)))

    def drag_end(self, *_):
        self.dragging = None

    def draw(self, _area, cr, width, height):
        colour = self.graph.get_color()
        manager = Adw.StyleManager.get_default()
        if hasattr(manager, "get_accent_color_rgba"):
            accent = manager.get_accent_color_rgba()
        else:
            found, accent = self.graph.get_style_context().lookup_color("accent_bg_color")
            if not found:
                accent = colour
        left, top, right, bottom = 46, 24, width - 22, height - 36
        if right <= left:
            return
        cr.select_font_face("Sans")
        cr.set_font_size(11)
        for value in (0, 25, 50, 75, 100):
            y = bottom - (bottom - top) * value / 100
            cr.set_source_rgba(colour.red, colour.green, colour.blue, 0.12)
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()
            cr.set_source_rgba(colour.red, colour.green, colour.blue, 0.65)
            cr.move_to(5, y + 4)
            cr.show_text(f"{value}%")
        for value in (0, 25, 50, 75, 100):
            x = left + (right - left) * value / 100
            cr.move_to(x - 12, height - 12)
            cr.show_text(f"{value}°")
        coords = [
            (
                left + (right - left) * t.get_value() / 100,
                bottom - (bottom - top) * s.get_value() / 100,
            )
            for t, s in self.points
        ]
        if not coords:
            return
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.12)
        cr.move_to(coords[0][0], bottom)
        for x, y in coords:
            cr.line_to(x, y)
        cr.line_to(coords[-1][0], bottom)
        cr.close_path()
        cr.fill()
        cr.set_source_rgb(accent.red, accent.green, accent.blue)
        cr.set_line_width(2.5)
        for i, (x, y) in enumerate(coords):
            cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
        cr.stroke()
        for x, y in coords:
            cr.arc(x, y, 4, 0, 6.284)
            cr.fill()

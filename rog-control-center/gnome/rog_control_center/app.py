# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import argparse
import gettext
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .backend import (
    ANIME,
    ARMOURY,
    AURA,
    BACKLIGHT,
    FANS,
    MODES,
    PLATFORM,
    PROFILES,
    SLASH,
    SLASH_MODES,
    Backend,
    gpu_mode,
)
from .fans import FanEditor
from .widgets import button, empty, group, info_row, label, metric, page

APP_ID = "org.opengamingcollective.ROGControlCenter"
gettext.bindtextdomain("rog-control-center", "/usr/share/locale")
gettext.textdomain("rog-control-center")

ATTRIBUTE_LABELS = {
    "PptPl1Spl": ("Sustained CPU power", "Long-term power limit (W)."),
    "PptPl2Sppt": ("Boost CPU power", "Short-term power limit (W)."),
    "PptPl3Fppt": ("Fast CPU boost", "Peak CPU power limit (W)."),
    "PptFppt": ("Fast package power", "Peak package power limit (W)."),
    "PptApuSppt": ("APU power limit", "Combined CPU and integrated GPU power (W)."),
    "PptPlatformSppt": ("Platform power limit", "Total platform power budget (W)."),
    "NvDynamicBoost": ("NVIDIA Dynamic Boost", "Additional GPU power budget (W)."),
    "NvTempTarget": ("GPU temperature target", "GPU thermal target (°C)."),
    "DgpuBaseTgp": ("GPU base power", "Base GPU power limit (W)."),
    "DgpuTgp": ("GPU power limit", "Total GPU power limit (W)."),
    "PanelOverdrive": ("Panel overdrive", "Reduce response time on the built-in display."),
    "BootSound": ("Startup sound", "Play the ROG sound when the laptop starts."),
    "McuPowersave": ("Controller power saving", "Reduce the embedded controller’s idle power use."),
    "MiniLedMode": ("Mini LED backlight", "Choose the display’s local dimming mode."),
    "PanelHdMode": ("Display resolution mode", "A restart may be required."),
    "ScreenAutoBrightness": ("Automatic screen brightness", "Adjust brightness automatically."),
    "ChargeMode": ("Charging mode", "Firmware charging behaviour."),
    "ApuMem": ("Reserved graphics memory", "Takes effect after a restart."),
    "CoresPerformance": ("Performance CPU cores", "Takes effect after a restart."),
    "CoresEfficiency": ("Efficiency CPU cores", "Takes effect after a restart."),
    "EgpuEnable": ("External GPU", "Takes effect after a restart."),
    "EgpuConnected": ("External GPU connected", ""),
    "PendingReboot": ("Restart required", ""),
}
PPT_NAMES = {
    name
    for name in ATTRIBUTE_LABELS
    if name.startswith("Ppt") or name.startswith("Nv") or name in ("DgpuBaseTgp", "DgpuTgp")
}
BRIGHTNESS = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}
EPP = {
    0: "Automatic",
    1: "Performance",
    2: "Prefer performance",
    3: "Prefer power saving",
    4: "Power saving",
}


class Window(Adw.ApplicationWindow):
    def __init__(self, application, backend):
        super().__init__(
            application=application,
            title="ROG Control Center",
            default_width=1060,
            default_height=820,
        )
        Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(
            str(Path(__file__).resolve().parent.parent / "icons")
        )
        self.backend = backend
        self.snapshot = None
        self.syncing = False
        self.refreshing = False
        self.pending = 0
        self.bindings = []
        self.topology = None
        self.editor = None
        self.closing = False
        self.set_size_request(360, 440)
        self.toast = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=240)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        self.toast.set_child(self.split)
        self.set_content(self.toast)
        sidebar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="ROG Control Center", subtitle="Laptop settings")
        )
        sidebar.add_top_bar(header)
        self.sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.connect("row-selected", self.navigate)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar_box.append(self.sidebar_list)
        sidebar_box.append(Gtk.Box(vexpand=True))
        footer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_start=18,
            margin_end=18,
            margin_bottom=18,
        )
        footer.append(label("ASUS LINUX", "caption", xalign=0))
        self.connection_label = label("Connecting…", "dim-label", xalign=0, wrap=True)
        footer.append(self.connection_label)
        sidebar_box.append(footer)
        sidebar.set_content(sidebar_box)
        self.split.set_sidebar(Adw.NavigationPage.new(sidebar, "ROG Control Center"))
        content = Adw.ToolbarView()
        content_header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="Overview")
        content_header.set_title_widget(self.title_widget)
        self.refresh_button = button(
            icon="view-refresh-symbolic",
            callback=self.refresh,
            tooltip="Refresh hardware status (Ctrl+R)",
        )
        content_header.pack_start(self.refresh_button)
        menu = Gio.Menu()
        menu.append("Appearance", "app.appearance")
        menu.append("Keyboard Shortcuts", "app.shortcuts")
        menu.append("About ROG Control Center", "app.about")
        menu.append("Quit", "app.quit")
        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"
        )
        content_header.pack_end(menu_button)
        content.add_top_bar(content_header)
        self.banner = Adw.Banner(
            title="Preview mode · hardware disconnected", revealed=backend.demo
        )
        content.add_top_bar(self.banner)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vhomogeneous(False)
        self.stack.set_hhomogeneous(False)
        loading = Adw.StatusPage(
            title="Connecting to your laptop",
            description="Reading available controls from asusd.",
            icon_name="computer-symbolic",
        )
        self.stack.add_named(loading, "loading")
        content.set_content(self.stack)
        self.content_page = Adw.NavigationPage.new(content, "Overview")
        self.split.set_content(self.content_page)
        self.connect("close-request", self.close_requested)
        self.timer = GLib.timeout_add_seconds(3, self.periodic_refresh)
        self.refresh()

    def periodic_refresh(self):
        if self.get_visible() and not self.pending:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self, *_):
        if self.refreshing or self.pending or self.closing:
            return
        self.refreshing = True
        self.refresh_button.set_sensitive(False)
        self.backend.submit(self.backend.discover, self.refreshed)

    def refreshed(self, snapshot, error):
        self.refreshing = False
        self.refresh_button.set_sensitive(True)
        if error:
            self.connection_label.set_label("Service unavailable")
            self.banner.set_title(
                "Cannot reach asusd. Check that the service is running, then refresh."
            )
            self.banner.set_revealed(True)
            if self.snapshot:
                # Stale values remain readable; no control can write until reconnected.
                for name in self.page_names:
                    self.stack.get_child_by_name(name).set_sensitive(False)
            elif not self.stack.get_child_by_name("error"):
                status = Adw.StatusPage(
                    title="Laptop service unavailable",
                    description="ROG Control Center needs asusd to manage your laptop.\n"
                    "Start it with: systemctl start asusd",
                    icon_name="network-offline-symbolic",
                )
                retry = button("Try again", css="suggested-action", callback=self.refresh)
                retry.set_halign(Gtk.Align.CENTER)
                status.set_child(retry)
                self.stack.add_named(status, "error")
                self.stack.set_visible_child_name("error")
            return
        self.snapshot = snapshot
        version = snapshot.first(PLATFORM)
        self.connection_label.set_label(
            "Preview · no hardware writes"
            if self.backend.demo
            else f"Connected · asusd {version.props.get('Version', '')}"
            if version
            else "No ASUS controls found"
        )
        self.banner.set_revealed(self.backend.demo)
        if self.backend.demo:
            self.banner.set_title("Preview mode · hardware disconnected")
        self.syncing = True
        try:
            if self.topology != snapshot.topology:
                self.build_pages()
                self.topology = snapshot.topology
            for binding in self.bindings:
                binding(snapshot)
            for name in self.page_names:
                self.stack.get_child_by_name(name).set_sensitive(True)
            if self.editor and not self.editor.dirty:
                self.editor.load()
        finally:
            self.syncing = False

    def perform(self, operation, widget=None, message="Setting updated", after=None):
        if self.syncing or self.closing:
            return
        self.pending += 1
        if widget:
            widget.set_sensitive(False)

        def work():
            operation()
            return self.backend.discover()

        def done(snapshot, error):
            self.pending -= 1
            if widget:
                widget.set_sensitive(True)
            if error:
                self.error("Could not apply setting", error)
                self.refresh()
            else:
                self.refreshed(snapshot, None)
                if after:
                    after()
                self.last_toast = Adw.Toast(title=message)
                self.toast.add_toast(self.last_toast)

        self.backend.submit(work, done)

    def error(self, title, details):
        dialog = Adw.AlertDialog(heading=title, body=details)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present(self)

    def confirm(self, title, body, action, callback):
        dialog = Adw.AlertDialog(heading=title, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", action)
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, response: callback() if response == "apply" else None)
        dialog.present(self)

    def close_requested(self, *_):
        if self.editor and self.editor.dirty:
            self.confirm(
                "Discard unsaved curve?",
                "Your fan-curve edits have not been saved.",
                "Discard and close",
                self.force_close,
            )
            return True
        self.shutdown()
        return False

    def force_close(self):
        if self.editor:
            self.editor.dirty = False
        self.close()

    def shutdown(self):
        self.closing = True
        GLib.source_remove(self.timer)
        self.backend.close()

    def navigate(self, _list, row):
        if not row:
            return
        name = row.page_name
        self.stack.set_visible_child_name(name)
        title = self.page_names[name]
        self.title_widget.set_title(title)
        self.content_page.set_title(title)
        self.split.set_show_content(True)

    def go(self, name):
        child = self.sidebar_list.get_first_child()
        while child:
            if child.page_name == name:
                self.sidebar_list.select_row(child)
                self.split.set_show_content(True)
                return
            child = child.get_next_sibling()

    def build_pages(self):
        previous = self.stack.get_visible_child_name()
        self.bindings = []
        self.editor = None
        while self.stack.get_first_child():
            self.stack.remove(self.stack.get_first_child())
        self.sidebar_list.remove_all()
        self.page_names = {
            "overview": "Overview",
            "performance": "Performance",
            "power": "Power & Battery",
            "lighting": "Lighting",
            "hardware": "Hardware",
        }
        icons = [
            "computer-symbolic",
            "speedometer-symbolic",
            "battery-symbolic",
            "keyboard-brightness-symbolic",
            "preferences-system-symbolic",
        ]
        builders = [self.overview, self.performance, self.power, self.lighting, self.hardware]
        for (name, title), icon, builder in zip(
            self.page_names.items(), icons, builders, strict=True
        ):
            row = Gtk.ListBoxRow()
            row.page_name = name
            box = Gtk.Box(
                spacing=12, margin_start=12, margin_end=12, margin_top=12, margin_bottom=12
            )
            box.append(Gtk.Image(icon_name=icon))
            box.append(label(title, xalign=0))
            row.set_child(box)
            self.sidebar_list.append(row)
            scroll, body = page(
                title,
                {
                    "overview": "Your laptop at a glance.",
                    "performance": "Balance speed, temperature and fan noise.",
                    "power": "Make each charge last longer.",
                    "lighting": "Make your laptop feel like yours.",
                    "hardware": "Display, graphics and firmware settings.",
                }[name],
            )
            builder(body)
            self.stack.add_named(scroll, name)
        self.go(previous if previous in self.page_names else "overview")

    def bind_property(self, device, name, setter):
        key = device.key

        def update(snapshot):
            current = snapshot.find(key)
            if current and name in current.props:
                setter(current.props[name])

        self.bindings.append(update)
        setter(device.props[name])

    def switch(self, parent, device, name, title, subtitle=""):
        if name not in device.props or name not in device.writable:
            return None
        row = Adw.SwitchRow(use_markup=False, title=title, subtitle=subtitle)
        row.set_title_lines(2)
        row.set_subtitle_lines(3)
        parent.add(row)
        self.bind_property(device, name, row.set_active)

        def changed(*_):
            if not self.syncing:
                value = row.get_active()
                self.perform(lambda: self.backend.set_property(device, name, value), row)

        row.connect("notify::active", changed)
        return row

    def combo(self, parent, device, name, title, choices, subtitle="", convert=None, confirm=None):
        if name not in device.props or name not in device.writable:
            return None
        choices = dict(choices)
        current = device.props[name]
        if current not in choices:
            choices[current] = str(current)
        values = list(choices)
        row = Adw.ComboRow(
            use_markup=False,
            title=title,
            subtitle=subtitle,
            model=Gtk.StringList.new(list(choices.values())),
        )
        row.set_title_lines(2)
        row.set_subtitle_lines(3)
        parent.add(row)
        self.bind_property(
            device, name, lambda v: row.set_selected(values.index(v)) if v in values else None
        )

        def selected(*_):
            if self.syncing or row.get_selected() >= len(values):
                return
            value = values[row.get_selected()]
            if convert:
                value = convert(value)

            def apply():
                self.perform(
                    lambda: self.backend.set_property(device, name, value),
                    row,
                    "Change scheduled · restart required" if confirm else "Setting updated",
                )

            if confirm:
                # Restore the hardware state while the confirmation is open.
                self.syncing = True
                live = self.snapshot.find(device.key).props[name]
                if live in values:
                    row.set_selected(values.index(live))
                self.syncing = False
                self.confirm("Schedule hardware change?", confirm, "Schedule change", apply)
            else:
                apply()

        row.connect("notify::selected", selected)
        return row

    def numeric(self, parent, device, name, title, low, high, step=1, subtitle="", after=None):
        row = Adw.ActionRow(use_markup=False, title=title, subtitle=subtitle)
        row.set_title_lines(2)
        row.set_subtitle_lines(3)
        parent.add(row)
        controls = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        spin = Gtk.SpinButton.new_with_range(low, high, max(step, 1))
        spin.set_width_chars(3)
        spin.update_property([Gtk.AccessibleProperty.LABEL], [title])
        apply = button(icon="object-select-symbolic", tooltip="Apply " + title)
        state = {"dirty": False}
        controls.append(spin)
        controls.append(apply)
        row.add_suffix(controls)

        def update(value):
            if not state["dirty"]:
                spin.set_value(value)
                apply.set_sensitive(False)

        self.bind_property(device, name, update)

        def changed(*_):
            if not self.syncing:
                state["dirty"] = True
                apply.set_sensitive(True)

        spin.connect("value-changed", changed)

        def save(*_):
            value = spin.get_value_as_int()

            def saved():
                state["dirty"] = False
                self.syncing = True
                update(self.snapshot.find(device.key).props[name])
                self.syncing = False
                if after:
                    after()

            self.perform(
                lambda: self.backend.set_property(device, name, value),
                row,
                "Setting updated",
                saved,
            )

        apply.connect("clicked", save)
        return row

    def profile_control(self, parent):
        platform = self.snapshot.first(PLATFORM)
        if not platform or "PlatformProfile" not in platform.props:
            return
        box = group(parent, "Performance profile", "Changes apply immediately.")
        self.combo(
            box,
            platform,
            "PlatformProfile",
            "Active profile",
            {p: PROFILES.get(p, str(p)) for p in platform.props.get("PlatformProfileChoices", [])},
            "Quiet saves power. Balanced suits everyday use. Performance prioritises speed.",
        )

    def overview(self, parent):
        hero = Gtk.Box(spacing=20)
        icon = Gtk.Image(icon_name="computer-symbolic", pixel_size=64)
        icon.add_css_class("accent")
        hero.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        text.append(
            label(
                self.snapshot.telemetry.get("model", "ASUS laptop"), "title-2", xalign=0, wrap=True
            )
        )
        text.append(label(self.snapshot.telemetry.get("board", ""), "dim-label", xalign=0))
        hero.append(text)
        parent.append(hero)
        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=1,
            max_children_per_line=2,
            column_spacing=12,
            row_spacing=12,
        )
        flow.set_activate_on_single_click(False)
        stats = {}
        for key, title, icon in [
            ("cpu_temp", "CPU temperature", "temperature-symbolic"),
            ("battery", "Battery", "battery-symbolic"),
            ("fans", "Cooling", "fan-symbolic"),
            ("memory", "Memory", "media-flash-symbolic"),
        ]:
            card, value, detail = metric(title, icon)
            flow.insert(card, -1)
            stats[key] = value, detail
        parent.append(flow)

        def update(snapshot):
            t = snapshot.telemetry
            stats["cpu_temp"][0].set_label(f"{t['cpu_temp']:.0f} °C" if "cpu_temp" in t else "—")
            stats["cpu_temp"][1].set_label(
                "Processor temperature" if "cpu_temp" in t else "Sensor unavailable"
            )
            battery = t.get("battery", {})
            capacity = battery.get("capacity")
            stats["battery"][0].set_label(f"{capacity:.0f}%" if capacity is not None else "—")
            stats["battery"][1].set_label(battery.get("status", "No battery detected"))
            fans = t.get("fans", [])
            stats["fans"][0].set_label(f"{fans[0][1]:,} rpm" if fans else "—")
            stats["fans"][1].set_label(
                " · ".join(f"{name}: {rpm:,} rpm" for name, rpm in fans[1:])
                or ("CPU fan speed" if fans else "Fan sensors unavailable")
            )
            memory = t.get("memory")
            stats["memory"][0].set_label(f"{memory:.0f}%" if memory is not None else "—")
            stats["memory"][1].set_label(
                "RAM in use" if memory is not None else "Sensor unavailable"
            )

        self.bindings.append(update)
        self.profile_control(parent)
        shortcuts = group(parent, "Quick access")
        for title, subtitle, icon, name in [
            (
                "Protect your battery",
                "Set a charge limit or charge fully for a trip.",
                "battery-symbolic",
                "power",
            ),
            (
                "Adjust cooling",
                "Edit custom fan curves for each profile.",
                "fan-symbolic",
                "performance",
            ),
            (
                "Personalise lighting",
                "Keyboard effects and lid lighting.",
                "keyboard-symbolic",
                "lighting",
            ),
        ]:
            row = Adw.ActionRow(use_markup=False, title=title, subtitle=subtitle, activatable=True)
            row.set_subtitle_lines(2)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _, n=name: self.go(n))
            shortcuts.add(row)
        if not self.snapshot.devices:
            empty(
                parent,
                "No ASUS controls found",
                "Hardware monitoring is available. This device does not expose supported ASUS controls.",
            )

    def performance(self, parent):
        self.profile_control(parent)
        if self.snapshot.curves and any(self.snapshot.curves.values()):
            self.editor = FanEditor(self, parent)
        else:
            empty(
                parent,
                "Automatic cooling",
                "Custom fan curves are unavailable on this device."
                if not self.snapshot.curve_error
                else "Could not read fan curves. Refresh to try again.",
                "fan-symbolic",
            )
        platform = self.snapshot.first(PLATFORM)
        if platform:
            energy = group(
                parent,
                "CPU energy preference",
                "Fine-tune how each performance profile balances speed and power use.",
            )
            self.switch(
                energy, platform, "PlatformProfileLinkedEpp", "Link CPU preference to profile"
            )
            for name, title in [
                ("ProfileQuietEpp", "Quiet"),
                ("ProfileBalancedEpp", "Balanced"),
                ("ProfilePerformanceEpp", "Performance"),
            ]:
                self.combo(energy, platform, name, title, EPP)
        ppt = [
            d
            for d in self.snapshot.devices
            if d.interface == ARMOURY and d.props.get("Name") in PPT_NAMES
        ]
        if ppt and platform:
            tuning = group(
                parent,
                "Power tuning",
                "Limits apply to the active profile and current power source. Enable tuning before editing.",
            )
            enable_row = self.switch(tuning, platform, "EnablePptGroup", "Enable power tuning")
            if enable_row:

                def tuning_available(s):
                    p = s.first(PLATFORM).props
                    curves = s.curves.get(p.get("PlatformProfile"), [])
                    available = not s.first(FANS) or any(c[3] for c in curves)
                    enable_row.set_sensitive(available or p.get("EnablePptGroup", False))
                    enable_row.set_subtitle(
                        "Applies to the active profile and power source."
                        if available
                        else "Save an enabled custom fan curve for the active profile first."
                    )

                self.bindings.append(tuning_available)
            for d in ppt:
                row = self.attribute_control(tuning, d)
                if row:
                    self.bindings.append(
                        lambda s, r=row: r.set_sensitive(
                            s.first(PLATFORM).props.get("EnablePptGroup", False)
                        )
                    )

    def power(self, parent):
        battery = self.snapshot.telemetry.get("battery")
        if battery:
            status = group(parent, "Battery status")
            fields = {
                name: info_row(status, title)[1]
                for name, title in [
                    ("capacity", "Charge"),
                    ("status", "State"),
                    ("health", "Battery health"),
                    ("watts", "Battery power"),
                ]
            }

            def update(snapshot):
                values = snapshot.telemetry.get("battery", {})
                for key, value_label in fields.items():
                    val = values.get(key)
                    unit = "%" if key in ("capacity", "health") else " W" if key == "watts" else ""
                    value_label.set_label(
                        (
                            f"{val:.1f}"
                            if key == "watts"
                            else str(int(val))
                            if isinstance(val, (int, float))
                            else str(val)
                        )
                        + unit
                        if val is not None
                        else "Unavailable"
                    )

            self.bindings.append(update)
        platform = self.snapshot.first(PLATFORM)
        if platform and "ChargeControlEndThreshold" in platform.writable:
            charging = group(
                parent,
                "Charging",
                "A limit of 80% reduces battery wear when your laptop is usually plugged in.",
            )
            self.numeric(
                charging,
                platform,
                "ChargeControlEndThreshold",
                "Charge limit",
                20,
                100,
                subtitle="20–100%. Apply with the check button.",
            )
            row = Adw.ActionRow(
                use_markup=False,
                title="Full charge for a trip",
                subtitle="Charge to 100% once, then restore your usual limit.",
            )
            row.set_subtitle_lines(2)
            full = button(
                "Charge once",
                callback=lambda: self.perform(
                    lambda: self.backend.method(platform, "OneShotFullCharge"),
                    row,
                    "One-time full charge enabled",
                ),
            )
            full.set_valign(Gtk.Align.CENTER)
            row.add_suffix(full)
            charging.add(row)
        if platform:
            auto = group(
                parent,
                "Automatic profiles",
                "Switch performance profile when the power source changes.",
            )
            choices = {
                p: PROFILES.get(p, str(p)) for p in platform.props.get("PlatformProfileChoices", [])
            }
            self.switch(auto, platform, "ChangePlatformProfileOnAc", "Switch when plugged in")
            self.combo(auto, platform, "PlatformProfileOnAc", "Plugged-in profile", choices)
            self.switch(auto, platform, "ChangePlatformProfileOnBattery", "Switch on battery")
            self.combo(auto, platform, "PlatformProfileOnBattery", "Battery profile", choices)
            self.switch(
                auto,
                platform,
                "DisableNvidiaPowerdOnBattery",
                "Reduce NVIDIA power use on battery",
                "Stop NVIDIA Dynamic Boost while unplugged.",
            )
        charge = self.snapshot.attribute("ChargeMode")
        if charge:
            self.attribute_control(group(parent, "Firmware charging"), charge)
        if not battery and not platform:
            empty(
                parent,
                "No battery controls",
                "Battery settings are unavailable on this device.",
                "battery-symbolic",
            )

    def attribute_control(self, parent, device):
        name = device.props.get("Name", device.path.rsplit("/", 1)[-1])
        title, subtitle = ATTRIBUTE_LABELS.get(name, (name, "Firmware setting."))
        props = device.props
        if (
            name in ("EgpuConnected", "PendingReboot", "ChargeMode", "DgpuBaseTgp")
            or "CurrentValue" not in device.writable
        ):
            _, text = info_row(parent, title)
            self.bind_property(device, "CurrentValue", lambda v: text.set_label(str(v)))
            return None
        choices = props.get("PossibleValues", [])
        restart = name in (
            "ApuMem",
            "CoresPerformance",
            "CoresEfficiency",
            "EgpuEnable",
            "PanelHdMode",
        )
        if choices:
            labels = {
                v: ("Off" if v == 0 else "On") if choices == [0, 1] else str(v) for v in choices
            }
            if name == "ApuMem":
                labels = {v: "Automatic" if v == 0 else f"{v} GB" for v in choices}
            return self.combo(
                parent,
                device,
                "CurrentValue",
                title,
                labels,
                subtitle,
                confirm="The new setting takes effect after a restart." if restart else None,
            )
        low, high = props.get("MinValue", -1), props.get("MaxValue", -1)
        if low >= 0 and high >= low:
            return self.numeric(
                parent,
                device,
                "CurrentValue",
                title,
                low,
                high,
                props.get("ScalarIncrement", 1),
                subtitle,
            )
        _, text = info_row(parent, title)
        self.bind_property(device, "CurrentValue", lambda v: text.set_label(str(v)))
        return None

    def hardware(self, parent):
        info = group(parent, "This laptop")
        t = self.snapshot.telemetry
        info_row(info, "Model", t.get("model", "Unknown"), "computer-symbolic")
        info_row(info, "Board", t.get("board", "Unknown"))
        info_row(info, "Processor", t.get("cpu", "Unavailable"))
        dgpu, mux = self.snapshot.attribute("DgpuDisable"), self.snapshot.attribute("GpuMuxMode")
        if dgpu or mux:
            graphics = group(parent, "Graphics", "Changes are scheduled for the next restart.")
            _, current = info_row(graphics, "Current mode")
            _, pending = info_row(graphics, "Scheduled mode")
            self.bindings.append(lambda s: current.set_label(gpu_mode(s)))
            self.bindings.append(
                lambda s: pending.set_label(
                    gpu_mode(s, True)
                    if any(
                        d.props.get("QueuedGpuValue", -1) >= 0
                        for d in s.devices
                        if d.props.get("Name") in ("DgpuDisable", "GpuMuxMode")
                    )
                    else "No change scheduled"
                )
            )
            modes = (["Integrated"] if dgpu else []) + ["Hybrid"] + (["Ultimate"] if mux else [])
            row = Adw.ActionRow(
                use_markup=False,
                title="Schedule graphics mode",
                subtitle="Integrated saves power. Hybrid uses both GPUs. Ultimate uses the discrete GPU.",
            )
            row.set_subtitle_lines(3)
            graphics.add(row)
            controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            dropdown = Gtk.DropDown.new_from_strings(modes)
            dropdown.set_selected(modes.index(gpu_mode(self.snapshot, True)))
            apply = button("Apply", css="suggested-action")
            controls.append(dropdown)
            controls.append(apply)
            row.add_suffix(controls)

            def schedule(*_):
                mode = modes[dropdown.get_selected()]
                self.confirm(
                    f"Schedule {mode.lower()} graphics?",
                    "Save your work and restart when ready. The app will not restart your laptop.",
                    "Schedule change",
                    lambda: self.perform(
                        lambda: self.backend.set_gpu(self.snapshot, mode),
                        row,
                        "Graphics change scheduled · restart required",
                    ),
                )

            apply.connect("clicked", schedule)
        rest = [
            d
            for d in self.snapshot.devices
            if d.interface == ARMOURY
            and d.props.get("Name") not in PPT_NAMES | {"DgpuDisable", "GpuMuxMode", "ChargeMode"}
        ]
        if rest:
            firmware = group(
                parent, "Display & firmware", "Available settings are reported by your laptop."
            )
            for d in rest:
                self.attribute_control(firmware, d)
        backlight = self.snapshot.first(BACKLIGHT)
        if backlight:
            display = group(parent, "ScreenPad")
            self.switch(display, backlight, "ScreenpadPower", "Enable ScreenPad")
            self.switch(
                display, backlight, "ScreenpadSyncWithPrimary", "Match main display brightness"
            )
            if "ScreenpadBrightness" in backlight.writable:
                self.numeric(display, backlight, "ScreenpadBrightness", "Brightness", 0, 100)

    def lighting(self, parent):
        lights = [
            d
            for d in self.snapshot.devices
            if d.interface in (AURA, ANIME, SLASH, "xyz.ljones.XgmLed")
        ]
        if not lights:
            empty(
                parent,
                "No supported lighting",
                "Your laptop does not expose lighting controls.",
                "keyboard-symbolic",
            )
            return
        for i, d in enumerate(lights):
            if d.interface == AURA:
                self.aura(parent, d, i)
            elif d.interface == ANIME:
                self.anime(parent, d)
            elif d.interface == SLASH:
                self.slash(parent, d)
            else:
                self.switch(group(parent, "XG Mobile"), d, "XgmLedEnabled", "Enable lighting")

    def aura(self, parent, d, index):
        aura = group(
            parent, "Keyboard lighting" if index == 0 else "Aura · " + d.path.rsplit("/", 1)[-1]
        )
        self.combo(
            aura,
            d,
            "Brightness",
            "Brightness",
            {v: BRIGHTNESS.get(v, str(v)) for v in d.props.get("SupportedBrightness", [])},
        )
        self.combo(
            aura,
            d,
            "LedMode",
            "Effect",
            {v: MODES.get(v, str(v)) for v in d.props.get("SupportedBasicModes", [])},
        )
        effect = d.props.get("LedModeData")
        if effect and "LedModeData" in d.writable:
            self.effect_editor(aura, d)
        power = d.props.get("LedPower")
        if power and "LedPower" in d.writable:
            zones = {
                0: "Lid logo",
                1: "Keyboard",
                2: "Light bar",
                3: "Lid",
                4: "Rear glow",
                5: "Keyboard & light bar",
                6: "ROG Ally",
            }
            triggers = group(parent, "When lighting is active")
            for state in power[0]:
                zone = state[0]
                expander = Adw.ExpanderRow(use_markup=False, title=zones.get(zone, f"Zone {zone}"))
                triggers.add(expander)
                for j, name in enumerate(
                    ["During startup", "While awake", "During sleep", "During shutdown"], 1
                ):
                    row = Adw.SwitchRow(use_markup=False, title=name)
                    expander.add_row(row)

                    def update(snapshot, r=row, z=zone, pos=j):
                        device = snapshot.find(d.key)
                        entry = next((v for v in device.props["LedPower"][0] if v[0] == z), None)
                        if entry:
                            r.set_active(entry[pos])

                    self.bindings.append(update)
                    row.set_active(state[j])

                    def change(*_, r=row, z=zone, pos=j):
                        if self.syncing:
                            return
                        entries = [list(v) for v in self.snapshot.find(d.key).props["LedPower"][0]]
                        for v in entries:
                            if v[0] == z:
                                v[pos] = r.get_active()
                        self.perform(
                            lambda: self.backend.set_property(d, "LedPower", (entries,)), r
                        )

                    row.connect("notify::active", change)

    def effect_editor(self, parent, d):
        row = Adw.ExpanderRow(
            use_markup=False, title="Customise effect", subtitle="Colours, speed and lighting zone"
        )
        parent.add(row)
        colours = []
        for title in ("Primary colour", "Secondary colour"):
            colour_row = Adw.ActionRow(use_markup=False, title=title)
            chooser = Gtk.ColorDialogButton(
                dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER
            )
            chooser.update_property([Gtk.AccessibleProperty.LABEL], [title])
            colour_row.add_suffix(chooser)
            row.add_row(colour_row)
            colours.append(chooser)
        speed = Adw.ComboRow(
            use_markup=False, title="Speed", model=Gtk.StringList.new(["Slow", "Medium", "Fast"])
        )
        direction = Adw.ComboRow(
            use_markup=False,
            title="Direction",
            model=Gtk.StringList.new(["Right", "Left", "Up", "Down"]),
        )
        row.add_row(speed)
        row.add_row(direction)
        zones = list(d.props.get("SupportedBasicZones", [])) or [0]
        zone_labels = {
            0: "All keys",
            1: "Left",
            2: "Centre left",
            3: "Centre right",
            4: "Right",
            5: "Logo",
            6: "Left light bar",
            7: "Right light bar",
        }
        zone = Adw.ComboRow(
            use_markup=False,
            title="Zone",
            model=Gtk.StringList.new([zone_labels.get(z, str(z)) for z in zones]),
        )
        row.add_row(zone)
        action_row = Adw.ActionRow(use_markup=False, title="Apply effect details")
        apply = button("Apply", css="suggested-action")
        apply.set_valign(Gtk.Align.CENTER)
        action_row.add_suffix(apply)
        row.add_row(action_row)
        state = {"dirty": False}

        def update(value):
            if state["dirty"]:
                return
            _, z, primary, secondary, s, direction_value = value
            for chooser, rgb in zip(colours, (primary, secondary), strict=True):
                rgba = Gdk.RGBA()
                rgba.red, rgba.green, rgba.blue, rgba.alpha = *(v / 255 for v in rgb), 1
                chooser.set_rgba(rgba)
            speed.set_selected(
                ["Slow", "Med", "Fast"].index(s) if s in ("Slow", "Med", "Fast") else 1
            )
            direction.set_selected(
                ["Right", "Left", "Up", "Down"].index(direction_value)
                if direction_value in ("Right", "Left", "Up", "Down")
                else 0
            )
            zone.set_selected(zones.index(z) if z in zones else 0)
            apply.set_sensitive(False)

        self.bind_property(d, "LedModeData", update)

        def edited(*_):
            if not self.syncing:
                state["dirty"] = True
                apply.set_sensitive(True)

        for chooser in colours:
            chooser.connect("notify::rgba", edited)
        for combo in (speed, direction, zone):
            combo.connect("notify::selected", edited)

        def save(*_):
            rgb = [
                tuple(
                    round(v * 255)
                    for v in (c.get_rgba().red, c.get_rgba().green, c.get_rgba().blue)
                )
                for c in colours
            ]
            latest = self.snapshot.find(d.key)
            value = (
                latest.props["LedMode"],
                zones[zone.get_selected()],
                *rgb,
                ["Slow", "Med", "Fast"][speed.get_selected()],
                ["Right", "Left", "Up", "Down"][direction.get_selected()],
            )

            def saved():
                state["dirty"] = False
                self.syncing = True
                update(self.snapshot.find(d.key).props["LedModeData"])
                self.syncing = False

            self.perform(
                lambda: self.backend.set_property(d, "LedModeData", value),
                row,
                "Lighting updated",
                saved,
            )

        apply.connect("clicked", save)

    def anime(self, parent, d):
        anime = group(parent, "AniMe Matrix", "LED display on the laptop lid.")
        self.switch(anime, d, "EnableDisplay", "Enable display")
        self.combo(anime, d, "Brightness", "Brightness", BRIGHTNESS)
        self.switch(anime, d, "BuiltinsEnabled", "Use built-in animations")
        for name, title in [
            ("OffWhenLidClosed", "Turn off when lid is closed"),
            ("OffWhenSuspended", "Turn off during sleep"),
            ("OffWhenUnplugged", "Turn off on battery"),
        ]:
            self.switch(anime, d, name, title)
        if "BuiltinAnimations" in d.writable:
            animations = group(parent, "Lid animations")
            values = [
                ("Startup", ["GlitchConstruction", "StaticEmergence"]),
                ("Awake", ["BinaryBannerScroll", "RogLogoGlitch"]),
                ("Sleep", ["BannerSwipe", "Starfield"]),
                ("Shutdown", ["GlitchOut", "SeeYa"]),
            ]
            for index, (title, choices) in enumerate(values):
                r = Adw.ComboRow(
                    use_markup=False,
                    title=title,
                    model=Gtk.StringList.new(
                        [
                            "".join(" " + c if c.isupper() and j else c for j, c in enumerate(v))
                            for v in choices
                        ]
                    ),
                )
                animations.add(r)
                self.bind_property(
                    d,
                    "BuiltinAnimations",
                    lambda v, row=r, i=index, c=choices: (
                        row.set_selected(c.index(v[i])) if v[i] in c else None
                    ),
                )

                def change(*_, row=r, i=index, c=choices):
                    if self.syncing:
                        return
                    latest = list(self.snapshot.find(d.key).props["BuiltinAnimations"])
                    latest[i] = c[row.get_selected()]
                    self.perform(
                        lambda: self.backend.set_property(d, "BuiltinAnimations", tuple(latest)),
                        row,
                    )

                r.connect("notify::selected", change)

    def slash(self, parent, d):
        slash = group(parent, "Slash lighting", "Light strip on the laptop lid.")
        self.switch(slash, d, "Enabled", "Enable lighting")
        self.combo(slash, d, "Mode", "Animation", SLASH_MODES)
        for name, title, low, high in [
            ("Brightness", "Brightness", 0, 255),
            ("Interval", "Animation speed", 0, 5),
        ]:
            if name in d.writable:
                self.numeric(slash, d, name, title, low, high)
        for name, title in [
            ("ShowOnBoot", "During startup"),
            ("ShowOnShutdown", "During shutdown"),
            ("ShowOnSleep", "During sleep"),
            ("ShowOnBattery", "On battery"),
            ("ShowBatteryWarning", "Low battery warning"),
            ("ShowOnLidClosed", "When lid is closed"),
        ]:
            self.switch(slash, d, name, title)


class Application(Adw.Application):
    def __init__(self, demo=False):
        super().__init__(
            application_id=APP_ID + (".Preview" if demo else ""),
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.demo = demo
        self.window = None
        for name, callback in [
            ("quit", self.quit_window),
            ("about", self.about),
            ("appearance", self.appearance),
            ("shortcuts", self.shortcuts),
            ("refresh", lambda *_: self.window.refresh()),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.refresh", ["<Control>r"])
        self.set_accels_for_action("app.shortcuts", ["<Control>question"])
        for index, name in enumerate(
            ["overview", "performance", "power", "lighting", "hardware"], 1
        ):
            action = Gio.SimpleAction.new("go-" + name, None)
            action.connect("activate", lambda *_, n=name: self.window.go(n))
            self.add_action(action)
            self.set_accels_for_action("app.go-" + name, [f"<Control>{index}"])

    def do_activate(self):
        if not self.window:
            self.window = Window(self, Backend(self.demo))
        self.window.present()

    def quit_window(self, *_):
        if self.window:
            self.window.close()

    def about(self, *_):
        dialog = Adw.AboutDialog(
            application_name="ROG Control Center",
            application_icon="rog-control-center",
            developer_name="ASUS Linux contributors",
            version="6.5.0 · GNOME edition",
            website="https://github.com/defvs/asusctl",
            issue_url="https://github.com/defvs/asusctl/issues",
            license_type=Gtk.License.MPL_2_0,
            comments="Native GNOME controls for your ASUS laptop.\nPowered by asusd.",
        )
        dialog.present(self.window)

    def appearance(self, *_):
        dialog = Adw.PreferencesDialog(title="Appearance")
        p = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="Colour scheme",
            description="Follow GNOME or choose a colour scheme for this session.",
        )
        p.add(g)
        dialog.add(p)
        row = Adw.ComboRow(
            use_markup=False,
            title="Appearance",
            model=Gtk.StringList.new(["System", "Light", "Dark"]),
        )
        styles = [Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK]
        manager = Adw.StyleManager.get_default()
        row.set_selected(styles.index(manager.get_color_scheme()))
        row.connect(
            "notify::selected", lambda r, _: manager.set_color_scheme(styles[r.get_selected()])
        )
        g.add(row)
        dialog.present(self.window)

    def shortcuts(self, *_):
        dialog = Adw.PreferencesDialog(title="Keyboard Shortcuts")
        p = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Navigation")
        p.add(g)
        dialog.add(p)
        for name, keys in [
            ("Overview", "Ctrl+1"),
            ("Performance", "Ctrl+2"),
            ("Power & Battery", "Ctrl+3"),
            ("Lighting", "Ctrl+4"),
            ("Hardware", "Ctrl+5"),
            ("Refresh", "Ctrl+R"),
            ("Quit", "Ctrl+Q"),
        ]:
            info_row(g, name, keys)
        dialog.present(self.window)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Native GNOME ROG Control Center")
    parser.add_argument(
        "--demo", action="store_true", help="Preview the UI without reading or writing hardware"
    )
    options = parser.parse_args(argv)
    app = Application(demo=options.demo)
    return app.run([sys.argv[0]])

#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise startup/key-capture dialogs with a private GSettings memory backend."""

import sys
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk
from rog_control_center.app import Application
from test_shortcuts import ShortcutFixture

fixture = ShortcutFixture()
fixture.entry("custom1", "/usr/bin/rog-control-center", "Launch1")
app = Application(demo=True, shortcut_manager=fixture.manager)
errors = []


def finish():
    if app.window:
        app.window.close()
    app.quit()


def fail(exc):
    errors.append(str(exc))
    traceback.print_exc()
    finish()


sys.excepthook = lambda _cls, exc, _tb: fail(exc)


def respond(dialog, response):
    dialog.emit("response", response)
    dialog.close()


def check():
    try:
        if app.window is None or app.window.snapshot is None or app.shortcut_dialog is None:
            return True
        # Offered automatically on the first activation, with the existing key.
        dialog = app.shortcut_dialog
        assert "Update" in dialog.get_heading()
        assert dialog.get_response_enabled("enable")
        respond(dialog, "cancel")
        assert fixture.manager.inspect().state == "update", "Not now must not change GNOME settings"
        assert fixture.preferences.ask
        app.offer_rog_shortcut()
        respond(app.shortcut_dialog, "enable")
        assert fixture.manager.inspect().state == "ready"
        assert fixture.manager.inspect().binding == "Launch1"
        app.offer_rog_shortcut()
        assert app.shortcut_dialog is None, "An installed shortcut must not prompt again"

        # Opt out of startup prompts, retaining manual setup in the menu.
        fixture.entry("custom1", "/usr/bin/rog-control-center", "Launch1")
        app.offer_rog_shortcut()
        respond(app.shortcut_dialog, "never")
        assert not fixture.preferences.ask
        app.offer_rog_shortcut()
        assert app.shortcut_dialog is None
        app.offer_rog_shortcut(manual=True)
        assert app.shortcut_dialog is not None
        respond(app.shortcut_dialog, "cancel")

        # No assignment: require capture of the hardware key before enabling.
        fixture.manager.settings("org.gnome.settings-daemon.plugins.media-keys").set_strv(
            "custom-keybindings", []
        )
        app.offer_rog_shortcut(manual=True)
        dialog = app.shortcut_dialog
        assert not dialog.get_response_enabled("enable")
        controllers = dialog.observe_controllers()
        keys = next(
            controllers.get_item(i)
            for i in range(controllers.get_n_items())
            if isinstance(controllers.get_item(i), Gtk.EventControllerKey)
        )
        keys.emit("key-pressed", Gdk.keyval_from_name("a"), 0, Gdk.ModifierType(0))
        assert not dialog.get_response_enabled("enable"), (
            "Ordinary typing is not a hardware shortcut"
        )
        keys.emit("key-pressed", Gdk.keyval_from_name("Launch4"), 0, Gdk.ModifierType(0))
        assert dialog.get_response_enabled("enable")
        respond(dialog, "enable")
        assert fixture.manager.inspect().state == "ready"
        assert fixture.manager.inspect().binding == "Launch4"
        print(
            "Startup proposal, cancellation, update, opt-out and hardware-key capture passed (isolated settings)",
            flush=True,
        )
        finish()
        return False
    except Exception as exc:
        fail(exc)
        return False


def watchdog():
    errors.append("Shortcut smoke checks exceeded 20 seconds")
    finish()
    return False


app.connect("activate", lambda *_: GLib.timeout_add(100, check))
GLib.timeout_add_seconds(20, watchdog)
app.run([])
fixture.close()
raise SystemExit(1 if errors else 0)

#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise real GTK widgets. Demo writes are isolated; --live only reads hardware."""

import argparse
import sys
import time
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, GLib, Gsk, Gtk
from rog_control_center.app import Application
from rog_control_center.backend import FANS

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true")
parser.add_argument("--screenshots", type=Path)
options = parser.parse_args()
app = Application(demo=not options.live)
errors = []
steps = []


def fail(exc):
    errors.append(str(exc))
    traceback.print_exc()
    if app.window:
        if app.window.editor:
            app.window.editor.dirty = False
        app.window.close()
    app.quit()


def callback(fn):
    def wrapped(*args):
        try:
            return fn(*args)
        except Exception as exc:
            fail(exc)
            return False

    return wrapped


sys.excepthook = lambda cls, exc, tb: fail(exc)


def wait_for(condition, next_fn, timeout=10):
    deadline = time.monotonic() + timeout

    @callback
    def poll():
        if condition():
            next_fn()
            return False
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for GTK/backend state")
        return True

    GLib.timeout_add(50, poll)


@callback
def next_step():
    if not steps:
        print(
            "GTK smoke checks passed" + (" (read-only live service)" if options.live else ""),
            flush=True,
        )
        app.window.close()
        return False
    name, colour, width, height = steps.pop(0)
    w = app.window
    w.go(name)
    w.set_default_size(width, height)
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if colour == "dark" else Adw.ColorScheme.FORCE_LIGHT
    )

    @callback
    def inspect():
        assert w.stack.get_visible_child_name() == name
        child = w.stack.get_visible_child()
        minimum = child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        if name == "performance" and not options.live:
            editor = w.editor
            editor.point_expander.set_expanded(True)
            assert child.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= w.get_width()
            editor.point_expander.set_expanded(False)
            if width == 1060:
                t, s = editor.points[0]
                x = 46 + (editor.graph.get_width() - 68) * t.get_value() / 100
                y = (
                    editor.graph.get_height()
                    - 36
                    - (editor.graph.get_height() - 60) * s.get_value() / 100
                )
                editor.drag_begin(None, x, y)
                editor.drag_update(None, 10000, -10000)
                editor.drag_end()
                assert editor.dirty and editor.validate(), "Dragging must constrain adjacent points"
                editor.load()
        assert minimum <= w.get_width(), f"{name}: minimum {minimum} exceeds width {w.get_width()}"
        deadline = time.monotonic() + 5

        @callback
        def finish():
            if options.screenshots:
                options.screenshots.mkdir(parents=True, exist_ok=True)
                paintable = Gtk.WidgetPaintable.new(w)
                snapshot = Gtk.Snapshot.new()
                paintable.snapshot(snapshot, w.get_width(), w.get_height())
                node = snapshot.to_node()
                if node is None:
                    assert time.monotonic() < deadline, "Window did not finish rendering"
                    return True
                renderer = Gsk.Renderer.new_for_surface(w.get_surface())
                try:
                    texture = renderer.render_texture(node, None)
                    texture.save_to_png(str(options.screenshots / f"{name}-{colour}-{width}.png"))
                finally:
                    renderer.unrealize()
            print(
                f"{name}: {colour}, {w.get_width()} × {w.get_height()}, minimum {minimum}",
                flush=True,
            )
            GLib.timeout_add(50, next_step)
            return False

        # Point edits and expanders invalidate the current frame. Let GTK redraw.
        GLib.timeout_add(100, finish)
        return False

    GLib.timeout_add(300, inspect)
    return False


@callback
def ready():
    w = app.window
    assert w.snapshot is not None
    assert len(w.page_names) == 5
    if not options.live:
        for show in (app.about, app.appearance, app.shortcuts):
            show()
            dialog = w.get_visible_dialog()
            assert dialog is not None
            dialog.close()
        w.refreshed(None, "Test service disconnect")
        assert w.banner.get_revealed()
        assert not w.stack.get_child_by_name("performance").get_sensitive()
        w.refreshed(w.backend.discover(), None)
        assert w.stack.get_child_by_name("performance").get_sensitive()
        editor = w.editor
        assert editor is not None
        assert editor.profile == 0, "Editor should open on the active profile"
        original = editor.values()
        editor.points[0][0].set_value(65)
        assert editor.dirty
        assert not editor.save.get_sensitive(), "Descending curve must not be saved"
        w.refreshed(w.backend.discover(), None)
        assert editor.points[0][0].get_value_as_int() == 65, "Refresh must preserve unsaved edits"
        assert w.backend.discover().curves[0][0][2][0] == 30, "Drafts must not write to backend"
        editor.load()
        assert not editor.dirty
        assert editor.values() == original, "Discard restores raw PWM bytes without rounding"
        editor.points[0][0].set_value(25)
        editor.enabled.set_active(True)
        assert editor.save.get_sensitive()
        editor.apply()

        def saved():
            assert not editor.dirty
            curve = w.snapshot.curves[0][0]
            assert curve[2][0] == 25 and curve[3] is True
            assert tuple(editor.values()[1]) == curve[1], "Save must preserve untouched PWM bytes"
            # Return preview fixtures to their default appearance for screenshots.
            fan = w.snapshot.first(FANS)
            w.backend.method(fan, "SetCurvesToDefaults", "(u)", (0,))
            w.refreshed(w.backend.discover(), None)
            w.last_toast.dismiss()
            next_step()

        wait_for(lambda: w.pending == 0, callback(saved))
    else:
        print("Live interfaces:", [d.interface for d in w.snapshot.devices], flush=True)
        next_step()


for name in ["overview", "performance", "power", "lighting", "hardware"]:
    steps.append((name, "dark", 1060, 900))
    steps.append((name, "light", 390, 844))
steps.append(("overview", "light", 1060, 900))
app.connect(
    "activate", lambda *_: wait_for(lambda: app.window and app.window.snapshot is not None, ready)
)
app.run([])
raise SystemExit(1 if errors else 0)

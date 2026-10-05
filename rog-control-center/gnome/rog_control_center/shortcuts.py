# SPDX-License-Identifier: MPL-2.0
"""Persistent GNOME launch shortcuts, with consent in the frontend and no key grabs."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM = MEDIA + ".custom-keybinding"
PREFIX = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
GLOBAL = "org.gnome.settings-daemon.global-shortcuts"
GLOBAL_APP = GLOBAL + ".application"
BUILTINS = (
    MEDIA,
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.mutter.keybindings",
    "org.gnome.mutter.wayland.keybindings",
)


def accelerator(binding):
    valid, key, modifiers = Gtk.accelerator_parse(binding)
    return (key, int(modifiers)) if valid else None


def command_path(command):
    try:
        args = shlex.split(command)
    except ValueError:
        return None
    if len(args) != 1:
        return None
    executable = args[0] if "/" in args[0] else shutil.which(args[0])
    return Path(executable).resolve() if executable else None


def rog_command(command):
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    return bool(args and Path(args[0]).name in ("rog-control-center", "rog-control-center-slint"))


class PromptPreferences:
    def __init__(self, path=None):
        self.path = (
            Path(path)
            if path
            else Path(GLib.get_user_config_dir()) / "rog-control-center" / "gnome.json"
        )

    def read(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def ask(self):
        return self.read().get("offer_rog_shortcut", True) is not False

    def set_ask(self, value):
        data = self.read()
        data["offer_rog_shortcut"] = bool(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, indent=2)
            file.write("\n")
        try:
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ShortcutPlan:
    state: str
    binding: str = "XF86Launch3"
    path: str = ""
    previous_command: str = ""
    conflict: str = ""
    assigned: bool = False
    previous_binding: str = ""


class GnomeShortcut:
    def __init__(self, launcher, *, source=None, backend=None, desktop=None, preferences=None):
        self.launcher = Path(launcher).resolve()
        self.source = source if source is not None else Gio.SettingsSchemaSource.get_default()
        self.backend = backend
        self.desktop = desktop if desktop is not None else os.environ.get("XDG_CURRENT_DESKTOP", "")
        self.preferences = preferences if preferences is not None else PromptPreferences()

    def settings(self, schema, path=None):
        definition = self.source.lookup(schema, True) if self.source else None
        return Gio.Settings.new_full(definition, self.backend, path) if definition else None

    def entries(self, root):
        return [(path, self.settings(CUSTOM, path)) for path in root.get_strv("custom-keybindings")]

    def inspect(self, binding=None):
        if "GNOME" not in self.desktop.upper().split(":"):
            return ShortcutPlan("unavailable")
        root = self.settings(MEDIA)
        if root is None or self.settings(CUSTOM, PREFIX + "rog-control-center-gnome/") is None:
            return ShortcutPlan("unavailable")
        candidates = []
        rog_keys = {accelerator(f"Launch{i}")[0] for i in range(1, 5)}
        for path, settings in self.entries(root):
            trigger, command = settings.get_string("binding"), settings.get_string("command")
            parsed = accelerator(trigger)
            if (rog_command(command) or command_path(command) == self.launcher) and (
                not trigger or (parsed and parsed[0] in rog_keys)
            ):
                candidates.append((path, settings))
        # Prefer an already working assignment over an obsolete duplicate.
        candidates.sort(
            key=lambda item: (
                not (
                    item[1].get_string("binding")
                    and command_path(item[1].get_string("command")) == self.launcher
                ),
                not item[1].get_string("binding"),
            )
        )
        path, existing = candidates[0] if candidates else ("", None)
        trigger = binding or (existing.get_string("binding") if existing else "") or "XF86Launch3"
        previous = existing.get_string("command") if existing else ""
        assigned = bool(existing and existing.get_string("binding"))
        previous_binding = existing.get_string("binding") if existing else ""
        conflict = self.conflict(trigger, path)
        if conflict:
            return ShortcutPlan(
                "conflict", trigger, path, previous, conflict, assigned, previous_binding
            )
        if (
            existing
            and existing.get_string("binding")
            and command_path(previous) == self.launcher
            and accelerator(existing.get_string("binding")) == accelerator(trigger)
        ):
            return ShortcutPlan(
                "ready",
                trigger,
                path,
                previous,
                assigned=assigned,
                previous_binding=previous_binding,
            )
        if existing:
            return ShortcutPlan(
                "update",
                trigger,
                path,
                previous,
                assigned=assigned,
                previous_binding=previous_binding,
            )
        return ShortcutPlan("missing", trigger)

    def conflict(self, binding, own_path=""):
        expected = accelerator(binding)
        if expected is None:
            return "This key cannot be used as a shortcut."
        root = self.settings(MEDIA)
        for path, settings in self.entries(root):
            if path != own_path and accelerator(settings.get_string("binding")) == expected:
                return settings.get_string("name") or "Another custom shortcut"
        for schema in BUILTINS:
            settings = self.settings(schema)
            if settings is None:
                continue
            definition = self.source.lookup(schema, True)
            for key in definition.list_keys():
                value = settings.get_value(key)
                bindings = (
                    [value.unpack()]
                    if value.get_type_string() == "s"
                    else value.unpack()
                    if value.get_type_string() == "as"
                    else []
                )
                if any(accelerator(v) == expected for v in bindings):
                    return "GNOME: " + key.replace("-", " ")
        global_settings = self.settings(GLOBAL)
        if global_settings:
            for app in global_settings.get_strv("applications"):
                settings = self.settings(
                    GLOBAL_APP, "/org/gnome/settings-daemon/global-shortcuts/" + app + "/"
                )
                if not settings:
                    continue
                for _id, properties in settings.get_value("shortcuts").unpack():
                    if any(accelerator(v) == expected for v in properties.get("shortcuts", [])):
                        return properties.get("description", app)
        return ""

    def install(self, expected):
        # Re-read at acceptance so a concurrent edit cannot be overwritten by a stale dialog.
        current = self.inspect(expected.binding)
        if current.state == "ready":
            return current
        if current != expected or current.state not in ("missing", "update"):
            raise ValueError(
                "The shortcut changed while this dialog was open. Review it and try again."
            )
        if not self.launcher.is_file() or not os.access(self.launcher, os.X_OK):
            raise ValueError("The app launcher is missing or is not executable.")
        root = self.settings(MEDIA)
        paths = root.get_strv("custom-keybindings")
        path = current.path
        if not path:
            suffix = 0
            while True:
                candidate = (
                    PREFIX + "rog-control-center-gnome" + (f"-{suffix}" if suffix else "") + "/"
                )
                settings = self.settings(CUSTOM, candidate)
                if candidate not in paths and not any(
                    settings.get_string(k) for k in ("name", "command", "binding")
                ):
                    path = candidate
                    break
                suffix += 1
        settings = self.settings(CUSTOM, path)
        values = {"command": shlex.quote(str(self.launcher))}
        if not current.path or accelerator(settings.get_string("binding")) != accelerator(
            current.binding
        ):
            values["binding"] = current.binding
        if not settings.get_string("name"):
            values["name"] = "ROG Control Center"
        if any(not settings.is_writable(k) for k in values) or (
            path not in paths and not root.is_writable("custom-keybindings")
        ):
            raise ValueError("Your desktop policy does not allow changing this shortcut.")
        previous = {key: settings.get_user_value(key) for key in values}
        settings.delay()
        for key, value in values.items():
            if not settings.set_string(key, value):
                settings.revert()
                raise ValueError("GNOME could not save the shortcut.")
        settings.apply()
        if path not in paths:
            # Preserve the latest list, including unrelated shortcuts added during setup.
            latest = root.get_strv("custom-keybindings")
            if not root.set_strv("custom-keybindings", latest + [path]):
                for key, value in previous.items():
                    settings.reset(key) if value is None else settings.set_value(key, value)
                settings.apply()
                raise ValueError("GNOME could not activate the shortcut.")
        Gio.Settings.sync()
        return self.inspect()

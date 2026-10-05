# SPDX-License-Identifier: MPL-2.0
import json
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gi.repository import Gio, GLib
from rog_control_center.shortcuts import (
    CUSTOM,
    GLOBAL,
    GLOBAL_APP,
    MEDIA,
    PREFIX,
    GnomeShortcut,
    PromptPreferences,
)


class ShortcutFixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rog-shortcut-test-")
        self.directory = Path(self.temporary.name)
        schemas = self.directory / "schemas"
        schemas.mkdir()
        shutil.copy(Path(__file__).parent / "schemas/shortcuts.gschema.xml", schemas)
        subprocess.run(["glib-compile-schemas", str(schemas)], check=True)
        self.source = Gio.SettingsSchemaSource.new_from_directory(str(schemas), None, True)
        self.backend = Gio.memory_settings_backend_new()
        folder = self.directory / "checkout with spaces"
        folder.mkdir()
        self.launcher = folder / "rog-control-center"
        self.launcher.write_text("#!/bin/sh\nexit 0\n")
        self.launcher.chmod(0o755)
        self.preferences = PromptPreferences(self.directory / "preferences.json")
        self.manager = GnomeShortcut(
            self.launcher,
            source=self.source,
            backend=self.backend,
            desktop="GNOME",
            preferences=self.preferences,
        )

    def entry(self, suffix, command, binding, name="ROG Control Center"):
        path = PREFIX + suffix + "/"
        settings = self.manager.settings(CUSTOM, path)
        for key, value in [("name", name), ("command", command), ("binding", binding)]:
            settings.set_string(key, value)
        root = self.manager.settings(MEDIA)
        paths = root.get_strv("custom-keybindings")
        if path not in paths:
            root.set_strv("custom-keybindings", paths + [path])
        return path, settings

    def close(self):
        self.temporary.cleanup()


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ShortcutFixture()
        self.manager = self.fixture.manager

    def tearDown(self):
        self.fixture.close()

    def test_inspection_does_not_create_a_shortcut(self):
        self.assertEqual(self.manager.inspect().state, "missing")
        self.assertEqual(self.manager.settings(MEDIA).get_strv("custom-keybindings"), [])
        self.assertFalse(self.fixture.preferences.path.exists())

    def test_setup_preserves_unrelated_shortcuts_and_quotes_the_launcher(self):
        unrelated, settings = self.fixture.entry("custom0", "firefox", "<Control><Alt>f", "Browser")
        plan = self.manager.install(self.manager.inspect("XF86Launch1"))
        self.assertEqual(plan.state, "ready")
        paths = self.manager.settings(MEDIA).get_strv("custom-keybindings")
        self.assertEqual(paths[0], unrelated)
        self.assertEqual(settings.get_string("command"), "firefox")
        assigned = self.manager.settings(CUSTOM, plan.path)
        self.assertEqual(shlex.split(assigned.get_string("command")), [str(self.fixture.launcher)])
        self.assertEqual(assigned.get_string("binding"), "XF86Launch1")
        self.manager.install(plan)
        self.assertEqual(self.manager.settings(MEDIA).get_strv("custom-keybindings"), paths)

    def test_upgrade_keeps_existing_rog_key_name_and_shortcut_list(self):
        path, settings = self.fixture.entry(
            "custom1", "/usr/bin/rog-control-center", "Launch1", "My ROG key"
        )
        before = self.manager.settings(MEDIA).get_strv("custom-keybindings")
        plan = self.manager.inspect()
        self.assertEqual((plan.state, plan.binding, plan.path), ("update", "Launch1", path))
        self.manager.install(plan)
        self.assertEqual(settings.get_string("binding"), "Launch1")
        self.assertEqual(settings.get_string("name"), "My ROG key")
        self.assertEqual(self.manager.settings(MEDIA).get_strv("custom-keybindings"), before)
        self.assertEqual(self.manager.inspect().state, "ready")

    def test_disabled_existing_shortcut_is_reused(self):
        path, settings = self.fixture.entry("custom1", shlex.quote(str(self.fixture.launcher)), "")
        self.assertEqual(self.manager.inspect().state, "update")
        ready = self.manager.install(self.manager.inspect())
        self.assertEqual(ready.path, path)
        self.assertEqual(settings.get_string("binding"), "XF86Launch3")

    def test_custom_conflict_handles_xf86_aliases_without_overwriting(self):
        path, settings = self.fixture.entry("custom0", "another-app", "Launch3", "Other app")
        plan = self.manager.inspect("XF86Launch3")
        self.assertEqual((plan.state, plan.conflict), ("conflict", "Other app"))
        with self.assertRaises(ValueError):
            self.manager.install(plan)
        self.assertEqual(settings.get_string("command"), "another-app")
        self.assertEqual(self.manager.settings(MEDIA).get_strv("custom-keybindings"), [path])
        self.assertEqual(self.manager.inspect("XF86Launch1").state, "missing")

    def test_modified_shortcut_does_not_conflict_with_unmodified_key(self):
        self.fixture.entry("custom0", "another-app", "<Control>Launch3")
        self.assertEqual(self.manager.inspect("XF86Launch3").state, "missing")

    def test_builtin_conflict_is_reported(self):
        self.manager.settings(MEDIA).set_strv("www", ["XF86Launch3"])
        self.assertEqual(self.manager.inspect().state, "conflict")

    def test_portal_conflict_is_reported_without_disabling_legacy_app(self):
        root = self.manager.settings(GLOBAL)
        app = "org.opengamingcollective.rog-control-center"
        root.set_strv("applications", [app])
        settings = self.manager.settings(
            GLOBAL_APP, "/org/gnome/settings-daemon/global-shortcuts/" + app + "/"
        )
        shortcuts = GLib.Variant(
            "a(sa{sv})",
            [
                (
                    "toggle_rog",
                    {
                        "shortcuts": GLib.Variant("as", ["Launch3"]),
                        "description": GLib.Variant("s", "Old app shortcut"),
                    },
                )
            ],
        )
        settings.set_value("shortcuts", shortcuts)
        self.assertEqual(self.manager.inspect().conflict, "Old app shortcut")
        self.assertEqual(settings.get_value("shortcuts"), shortcuts)
        self.fixture.entry("custom1", "/usr/bin/rog-control-center", "Launch1")
        self.assertEqual(self.manager.inspect().state, "update")

    def test_changed_assignment_cannot_be_overwritten_by_stale_dialog(self):
        _, settings = self.fixture.entry("custom1", "/usr/bin/rog-control-center", "Launch1")
        plan = self.manager.inspect()
        settings.set_string("command", "another-app")
        with self.assertRaises(ValueError):
            self.manager.install(plan)
        self.assertEqual(settings.get_string("command"), "another-app")

    def test_orphaned_user_settings_are_not_overwritten(self):
        orphan = self.manager.settings(CUSTOM, PREFIX + "rog-control-center-gnome/")
        orphan.set_string("command", "another-app")
        plan = self.manager.install(self.manager.inspect())
        self.assertNotEqual(plan.path, PREFIX + "rog-control-center-gnome/")
        self.assertEqual(orphan.get_string("command"), "another-app")

    def test_key_changed_after_the_prompt_cannot_be_restored_by_stale_confirmation(self):
        _, settings = self.fixture.entry("custom1", "/usr/bin/rog-control-center", "Launch1")
        plan = self.manager.inspect()
        settings.set_string("binding", "Launch3")
        with self.assertRaises(ValueError):
            self.manager.install(plan)
        self.assertEqual(settings.get_string("binding"), "Launch3")

    def test_index_write_failure_rolls_back_new_shortcut(self):
        original = self.manager.settings
        root = original(MEDIA)

        def settings(schema, path=None):
            return root if schema == MEDIA else original(schema, path)

        with (
            patch.object(self.manager, "settings", side_effect=settings),
            patch.object(root, "set_strv", return_value=False),
        ):
            with self.assertRaises(ValueError):
                self.manager.install(self.manager.inspect())
        self.assertEqual(root.get_strv("custom-keybindings"), [])
        entry = original(CUSTOM, PREFIX + "rog-control-center-gnome/")
        for key in ("name", "command", "binding"):
            self.assertIsNone(entry.get_user_value(key))

    def test_other_desktops_do_not_offer_gnome_shortcuts(self):
        self.manager.desktop = "KDE"
        self.assertEqual(self.manager.inspect().state, "unavailable")

    def test_dont_ask_again_preserves_other_preferences(self):
        self.fixture.preferences.path.write_text(json.dumps({"other": "keep"}))
        self.assertTrue(self.fixture.preferences.ask)
        self.fixture.preferences.set_ask(False)
        self.assertFalse(self.fixture.preferences.ask)
        self.assertEqual(self.fixture.preferences.read()["other"], "keep")


if __name__ == "__main__":
    unittest.main()

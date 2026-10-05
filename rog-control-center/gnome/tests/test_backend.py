# SPDX-License-Identifier: MPL-2.0
import unittest
from unittest.mock import patch

from gi.repository import GLib
from rog_control_center.backend import (
    ARMOURY,
    AURA,
    FANS,
    PLATFORM,
    Backend,
    Device,
    gpu_changes,
    gpu_mode,
    validate_curve,
)


class Recorder:
    def __init__(self):
        self.calls = []

    def call_sync(self, *args):
        self.calls.append(args)
        return GLib.Variant("()", ())


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.backend.connection = Recorder()

    def tearDown(self):
        self.backend.close()

    def test_property_keeps_wire_signature(self):
        d = Device("/keyboard", AURA, {}, {"LedModeData": "(uu(yyy)(yyy)ss)"}, {"LedModeData"})
        value = (0, 0, (53, 132, 228), (192, 97, 203), "Med", "Right")
        self.backend.set_property(d, "LedModeData", value)
        args = self.backend.connection.calls[0]
        self.assertEqual(args[3], "Set")
        self.assertEqual(args[4].get_type_string(), "(ssv)")
        nested = args[4].get_child_value(2).get_variant()
        self.assertEqual(nested.get_type_string(), "(uu(yyy)(yyy)ss)")
        self.assertEqual(nested.unpack(), value)

    def test_readonly_properties_cannot_be_written(self):
        d = Device("/platform", PLATFORM, {}, {"Version": "s"}, set())
        with self.assertRaises(ValueError):
            self.backend.set_property(d, "Version", "fake")
        self.assertEqual(self.backend.connection.calls, [])

    def test_firmware_semantics_override_generic_writable_introspection(self):
        d = Device(
            "/charge_mode", ARMOURY, {"Name": "ChargeMode"}, {"CurrentValue": "i"}, {"CurrentValue"}
        )
        with self.assertRaises(ValueError):
            self.backend.set_property(d, "CurrentValue", 0)
        self.assertEqual(self.backend.connection.calls, [])

    def test_fan_wire_format_has_structs_not_arrays(self):
        d = Device("/xyz/ljones", FANS, {})
        self.backend.save_curve(d, 2, "CPU", list(range(0, 80, 10)), list(range(0, 240, 30)), True)
        args = self.backend.connection.calls[0]
        self.assertEqual(args[3], "SetFanCurve")
        self.assertEqual(args[4].get_type_string(), "(u(s(yyyyyyyy)(yyyyyyyy)b))")
        profile, (fan, pwm, temp, enabled) = args[4].unpack()
        self.assertEqual((profile, fan, enabled), (2, "CPU", True))
        self.assertEqual(pwm, tuple(range(0, 240, 30)))
        self.assertEqual(temp, tuple(range(0, 80, 10)))

    def test_fan_invalid_data_never_reaches_bus(self):
        d = Device("/xyz/ljones", FANS, {})
        with self.assertRaises(ValueError):
            self.backend.save_curve(d, 0, "GPU", [30] * 7, [0] * 8, True)
        self.assertEqual(self.backend.connection.calls, [])


class CurveTests(unittest.TestCase):
    def test_flat_sections_are_valid(self):
        validate_curve([30, 40, 40, 60, 70, 80, 90, 100], [0, 30, 30, 90, 130, 170, 220, 255])

    def test_rejects_descending_and_out_of_range_points(self):
        for temp, pwm in [
            ([100, 90, 80, 70, 60, 50, 40, 30], [0] * 8),
            ([30] * 8, [0, 30, 20, 90, 130, 170, 220, 255]),
            ([101] * 8, [0] * 8),
            ([30] * 8, [256] * 8),
        ]:
            with self.subTest(temp=temp, pwm=pwm), self.assertRaises(ValueError):
                validate_curve(temp, pwm)


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend(demo=True)

    def tearDown(self):
        self.backend.close()

    def test_preview_never_connects_to_hardware(self):
        with patch(
            "rog_control_center.backend.Gio.bus_get_sync", side_effect=AssertionError("real bus")
        ):
            s = self.backend.discover()
            self.backend.set_property(s.first(PLATFORM), "ChargeControlEndThreshold", 60)
            self.backend.set_gpu(s, "Ultimate")
            self.backend.save_curve(
                s.first(FANS), 0, "GPU", list(range(0, 80, 10)), [100] * 8, True
            )
            self.assertEqual(
                self.backend.discover().first(PLATFORM).props["ChargeControlEndThreshold"], 60
            )

    def test_gpu_current_state_and_queued_state_are_separate(self):
        s = self.backend.discover()
        self.assertEqual(gpu_mode(s), "Hybrid")
        self.backend.set_gpu(s, "Ultimate")
        s = self.backend.discover()
        self.assertEqual(gpu_mode(s), "Hybrid")
        self.assertEqual(gpu_mode(s, queued=True), "Ultimate")
        self.backend.set_gpu(s, "Hybrid")
        self.assertEqual(
            self.backend.discover().attribute("GpuMuxMode").props["QueuedGpuValue"], -1
        )

    def test_gpu_switch_does_not_invent_unsupported_capabilities(self):
        s = self.backend.discover()
        s.devices = [d for d in s.devices if d.props.get("Name") != "GpuMuxMode"]
        self.assertEqual(len(gpu_changes(s, "Integrated")), 1)
        with self.assertRaises(ValueError):
            gpu_changes(s, "Ultimate")
        s.devices = [d for d in s.devices if d.props.get("Name") != "DgpuDisable"]
        with self.assertRaises(ValueError):
            gpu_changes(s, "Hybrid")

    def test_gpu_partial_failure_restores_previous_queue(self):
        s = self.backend.discover()
        original = self.backend.set_property
        writes = []

        def fail_second(d, name, value):
            writes.append((d.props["Name"], value))
            if len(writes) == 2:
                raise RuntimeError("second write failed")
            original(d, name, value)

        with patch.object(self.backend, "set_property", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.backend.set_gpu(s, "Integrated")
        self.assertEqual(writes, [("DgpuDisable", 1), ("GpuMuxMode", 1), ("DgpuDisable", 0)])
        self.assertEqual(gpu_mode(self.backend.discover(), queued=True), "Hybrid")

    def test_snapshot_is_isolated_from_preview_state(self):
        s = self.backend.discover()
        s.devices.clear()
        s.curves.clear()
        self.assertTrue(self.backend.discover().devices)
        self.assertTrue(self.backend.discover().curves)


if __name__ == "__main__":
    unittest.main()

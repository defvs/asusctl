# SPDX-License-Identifier: MPL-2.0
"""Transport and hardware discovery. All calls run on one worker, never GTK's thread."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from gi.repository import Gio, GLib

SERVICE = "xyz.ljones.Asusd"
PLATFORM = "xyz.ljones.Platform"
ARMOURY = "xyz.ljones.AsusArmoury"
FANS = "xyz.ljones.FanCurves"
AURA = "xyz.ljones.Aura"
ANIME = "xyz.ljones.Anime"
SLASH = "xyz.ljones.Slash"
BACKLIGHT = "xyz.ljones.Backlight"
READ_ONLY_ATTRIBUTES = {"ChargeMode", "DgpuBaseTgp", "EgpuConnected", "PendingReboot"}
PROFILES = {0: "Balanced", 1: "Performance", 2: "Quiet", 3: "Low power", 4: "Custom"}
MODES = {
    0: "Static",
    1: "Breathing",
    2: "Colour cycle",
    3: "Colour wave",
    4: "Stars",
    5: "Rain",
    6: "Highlight",
    7: "Laser",
    8: "Ripple",
    10: "Pulse",
    11: "Comet",
    12: "Flash",
}
SLASH_MODES = {
    6: "Static",
    16: "Bounce",
    18: "Slash",
    19: "Loading",
    29: "Bit stream",
    26: "Transmission",
    25: "Flow",
    37: "Flux",
    36: "Phantom",
    38: "Spectrum",
    50: "Hazard",
    51: "Interfacing",
    52: "Ramp",
    66: "Game over",
    67: "Start",
    68: "Buzzer",
}


@dataclass
class Device:
    path: str
    interface: str
    props: dict
    signatures: dict = field(default_factory=dict)
    writable: set = field(default_factory=set)

    @property
    def key(self):
        return self.path, self.interface


@dataclass
class Snapshot:
    devices: list[Device]
    telemetry: dict
    curves: dict = field(default_factory=dict)
    curve_error: str = ""

    def first(self, interface):
        return next((d for d in self.devices if d.interface == interface), None)

    def attribute(self, name):
        return next(
            (d for d in self.devices if d.interface == ARMOURY and d.props.get("Name") == name),
            None,
        )

    def find(self, key):
        return next((d for d in self.devices if d.key == key), None)

    @property
    def topology(self):
        return tuple((d.key, tuple(sorted(d.props))) for d in self.devices)


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return default


def number(path):
    try:
        return float(read(path))
    except ValueError:
        return None


def fan_name(name):
    """Turn hwmon labels into readable names while preserving unknown sensors."""
    name = name.strip().lower().replace("_", " ")
    return {
        "cpu fan": "CPU",
        "cpu": "CPU",
        "gpu fan": "GPU",
        "gpu": "GPU",
        "mid fan": "Middle",
        "mid": "Middle",
    }.get(name, name.title())


def battery_power(battery):
    watts = battery.get("watts")
    if watts is None:
        return "Power unavailable"
    direction = {"charging": " in", "discharging": " out"}.get(
        battery.get("status", "").lower(), ""
    )
    return f"{abs(watts):.1f} W{direction}"


def telemetry():
    """Read only local, inexpensive sysfs/proc metrics; never wake a discrete GPU."""
    data = {
        "model": read("/sys/class/dmi/id/product_family", "ASUS laptop"),
        "board": read("/sys/class/dmi/id/board_name"),
        "fans": [],
    }
    for base in sorted(Path("/sys/class/power_supply").glob("*")):
        if read(base / "type") != "Battery":
            continue
        battery = {"capacity": number(base / "capacity"), "status": read(base / "status")}
        full = number(base / "energy_full") or number(base / "charge_full")
        design = number(base / "energy_full_design") or number(base / "charge_full_design")
        battery["health"] = round(full / design * 100) if full and design else None
        watts = number(base / "power_now")
        if watts is None:
            current, voltage = number(base / "current_now"), number(base / "voltage_now")
            watts = current * voltage / 1e6 if current is not None and voltage else None
        battery["watts"] = watts / 1e6 if watts is not None else None
        data["battery"] = battery
        break
    for base in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        name = read(base / "name")
        if name in ("k10temp", "coretemp", "cpu_thermal"):
            value = number(base / "temp1_input")
            if value is not None:
                data["cpu_temp"] = value / 1000
        if name in ("asus", "asus_custom_fan_curve"):
            for file in sorted(base.glob("fan*_input")):
                rpm = number(file)
                if rpm is not None:
                    data["fans"].append(
                        (
                            read(
                                base / file.name.replace("_input", "_label"),
                                file.stem.replace("_input", "").upper(),
                            ),
                            int(rpm),
                        )
                    )
    mem = {}
    for line in read("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            mem[parts[0].rstrip(":")] = int(parts[1])
    if mem.get("MemTotal") and "MemAvailable" in mem:
        data["memory"] = (mem["MemTotal"] - mem["MemAvailable"]) / mem["MemTotal"] * 100
    for line in read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            data["cpu"] = line.split(":", 1)[1].strip()
            break
    return data


def validate_curve(temperatures, pwm):
    if len(temperatures) != 8 or len(pwm) != 8:
        raise ValueError("A fan curve must contain exactly eight points.")
    if any(not 0 <= t <= 100 for t in temperatures):
        raise ValueError("Temperatures must be between 0 and 100 °C.")
    if any(not 0 <= p <= 255 for p in pwm):
        raise ValueError("Fan speeds must be between 0 and 100%.")
    for values in (temperatures, pwm):
        if any(a > b for a, b in pairwise(values)):
            raise ValueError("Temperature and fan speed must increase from left to right.")


def gpu_mode(snapshot, queued=False):
    def value(name, default):
        d = snapshot.attribute(name)
        if not d:
            return default
        pending = d.props.get("QueuedGpuValue", -1)
        return pending if queued and pending >= 0 else d.props["CurrentValue"]

    if value("GpuMuxMode", 1) == 0:
        return "Ultimate"
    return "Integrated" if value("DgpuDisable", 0) == 1 else "Hybrid"


def gpu_changes(snapshot, mode):
    dgpu, mux = snapshot.attribute("DgpuDisable"), snapshot.attribute("GpuMuxMode")
    if mode not in ("Integrated", "Hybrid", "Ultimate"):
        raise ValueError("Unknown graphics mode.")
    if (mode == "Integrated" and not dgpu) or (mode == "Ultimate" and not mux):
        raise ValueError("This graphics mode is not supported by your laptop.")
    if not dgpu and not mux:
        raise ValueError("This laptop has no graphics mode switch.")
    # Enable the GPU before selecting its display path. These are independent attributes.
    return [(d, 1 if mode == "Integrated" else 0) for d in [dgpu] if d] + [
        (d, 0 if mode == "Ultimate" else 1) for d in [mux] if d
    ]


class Backend:
    def __init__(self, demo=False):
        self.demo = demo
        self.connection = None
        self.schema = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asusd")
        self.closed = False
        self.demo_snapshot = demo_snapshot() if demo else None

    def submit(self, operation, callback):
        future = self.executor.submit(operation)

        def complete(result):
            try:
                value, error = result.result(), None
            except Exception as exc:
                value, error = None, str(exc)
            if not self.closed:
                GLib.idle_add(deliver, value, error)

        def deliver(value, error):
            if not self.closed:
                callback(value, error)
            return GLib.SOURCE_REMOVE

        future.add_done_callback(complete)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)

    def call(self, path, interface, method, signature="()", args=()):
        if not self.connection:
            self.connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return self.connection.call_sync(
            SERVICE,
            path,
            interface,
            method,
            GLib.Variant(signature, args),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )

    def discover(self):
        if self.demo:
            return copy.deepcopy(self.demo_snapshot)
        objects = self.call(
            "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
        ).unpack()[0]
        devices = []
        for path, interfaces in sorted(objects.items()):
            for interface, props in sorted(interfaces.items()):
                if not interface.startswith("xyz.ljones."):
                    continue
                key = path, interface
                if key not in self.schema:
                    xml = self.call(
                        path, "org.freedesktop.DBus.Introspectable", "Introspect"
                    ).unpack()[0]
                    root = ET.fromstring(xml)
                    iface = next(i for i in root.findall("interface") if i.get("name") == interface)
                    signatures = {p.get("name"): p.get("type") for p in iface.findall("property")}
                    writable = {
                        p.get("name")
                        for p in iface.findall("property")
                        if "write" in p.get("access", "")
                    }
                    self.schema[key] = signatures, writable
                signatures, writable = self.schema[key]
                devices.append(Device(path, interface, props, signatures, writable))
        snapshot = Snapshot(devices, telemetry())
        platform = snapshot.first(PLATFORM)
        if platform:
            supported = self.call(platform.path, PLATFORM, "SupportedProperties").unpack()[0]
            platform.props["SupportedProperties"] = supported
            if "ChargeControlEndThreshold" not in supported:
                platform.props.pop("ChargeControlEndThreshold", None)
                platform.writable = platform.writable - {"ChargeControlEndThreshold"}
        fan = snapshot.first(FANS)
        platform = snapshot.first(PLATFORM)
        if fan and platform:
            try:
                for profile in platform.props.get("PlatformProfileChoices", []):
                    snapshot.curves[profile] = self.call(
                        fan.path, FANS, "FanCurveData", "(u)", (profile,)
                    ).unpack()[0]
            except Exception as exc:
                snapshot.curve_error = str(exc)
        return snapshot

    def set_property(self, device, name, value):
        if name not in device.writable or (
            device.interface == ARMOURY and device.props.get("Name") in READ_ONLY_ATTRIBUTES
        ):
            raise ValueError(f"{name} is not writable on this device.")
        variant = GLib.Variant(device.signatures[name], value)
        if self.demo:
            d = self.demo_snapshot.find(device.key)
            if d.interface == ARMOURY and d.props["Name"] in (
                "DgpuDisable",
                "GpuMuxMode",
                "ApuMem",
            ):
                d.props["QueuedGpuValue"] = value if value != d.props["CurrentValue"] else -1
            else:
                d.props[name] = variant.unpack()
            return
        self.call(
            device.path,
            "org.freedesktop.DBus.Properties",
            "Set",
            "(ssv)",
            (device.interface, name, variant),
        )

    def method(self, device, name, signature="()", args=()):
        if self.demo:
            if name == "SetFanCurve":
                profile, curve = args
                curves = self.demo_snapshot.curves[profile]
                curves[:] = [curve if c[0] == curve[0] else c for c in curves]
            elif name == "SetCurvesToDefaults":
                self.demo_snapshot.curves[args[0]] = demo_snapshot().curves[args[0]]
            elif name == "RestoreDefault":
                self.demo_snapshot.find(device.key).props["CurrentValue"] = device.props[
                    "DefaultValue"
                ]
            elif name == "OneShotFullCharge":
                self.demo_snapshot.first(PLATFORM).props["ChargeControlEndThreshold"] = 100
            return
        self.call(device.path, device.interface, name, signature, args)

    def save_curve(self, device, profile, fan, temperatures, pwm, enabled):
        validate_curve(temperatures, pwm)
        self.method(
            device,
            "SetFanCurve",
            "(u(s(yyyyyyyy)(yyyyyyyy)b))",
            (profile, (fan, tuple(pwm), tuple(temperatures), enabled)),
        )

    def set_gpu(self, snapshot, mode):
        changes = gpu_changes(snapshot, mode)
        applied = []
        try:
            for device, value in changes:
                self.set_property(device, "CurrentValue", value)
                applied.append(device)
        except Exception:
            # Restore the previous queued intent if the second independent write fails.
            for device in reversed(applied):
                old = device.props.get("QueuedGpuValue", -1)
                self.set_property(
                    device, "CurrentValue", old if old >= 0 else device.props["CurrentValue"]
                )
            raise


def demo_snapshot():
    """Explicit, isolated preview: never touches the system bus or hardware."""
    devices = []

    def add(path, interface, values):
        props = {k: v[1] for k, v in values.items()}
        devices.append(
            Device(
                path,
                interface,
                props,
                {k: v[0] for k, v in values.items()},
                {k for k, v in values.items() if len(v) < 3 or v[2]},
            )
        )

    add(
        "/xyz/ljones",
        PLATFORM,
        {
            "SupportedProperties": ("as", ["ChargeControlEndThreshold", "ThrottlePolicy"], False),
            "Version": ("s", "6.5.0", False),
            "PlatformProfile": ("u", 0),
            "PlatformProfileChoices": ("au", [2, 0, 1], False),
            "ChargeControlEndThreshold": ("y", 80),
            "EnablePptGroup": ("b", False),
            "PlatformProfileLinkedEpp": ("b", True),
            "ProfileQuietEpp": ("u", 4),
            "ProfileBalancedEpp": ("u", 3),
            "ProfilePerformanceEpp": ("u", 1),
            "ChangePlatformProfileOnAc": ("b", True),
            "ChangePlatformProfileOnBattery": ("b", True),
            "PlatformProfileOnAc": ("u", 0),
            "PlatformProfileOnBattery": ("u", 2),
            "DisableNvidiaPowerdOnBattery": ("b", True),
        },
    )
    add("/xyz/ljones", FANS, {})
    for name, path, value, low, high, choices in [
        ("DgpuDisable", "dgpu_disable", 0, -1, -1, [0, 1]),
        ("GpuMuxMode", "gpu_mux_mode", 1, -1, -1, [0, 1]),
        ("PanelOverdrive", "panel_overdrive", 1, -1, -1, [0, 1]),
        ("BootSound", "boot_sound", 0, -1, -1, [0, 1]),
        ("PptPl1Spl", "ppt_pl1_spl", 45, 15, 80, []),
        ("PptPl2Sppt", "ppt_pl2_sppt", 65, 15, 80, []),
        ("ApuMem", "apu_mem", 0, -1, -1, [0, 1, 2, 4, 8]),
    ]:
        add(
            "/xyz/ljones/asus_armoury/" + path,
            ARMOURY,
            {
                "Name": ("s", name, False),
                "CurrentValue": ("i", value),
                "DefaultValue": ("i", value, False),
                "MinValue": ("i", low, False),
                "MaxValue": ("i", high, False),
                "PossibleValues": ("ai", choices, False),
                "ScalarIncrement": ("i", 1, False),
                "QueuedGpuValue": ("i", -1, False),
            },
        )
    add(
        "/xyz/ljones/aura/keyboard",
        AURA,
        {
            "Brightness": ("u", 2),
            "LedMode": ("u", 0),
            "SupportedBrightness": ("au", [0, 1, 2, 3], False),
            "SupportedBasicModes": ("au", [0, 1, 2, 3, 10], False),
            "SupportedBasicZones": ("au", [], False),
            "SupportedPowerZones": ("au", [1], False),
            "LedModeData": (
                "(uu(yyy)(yyy)ss)",
                (0, 0, (53, 132, 228), (192, 97, 203), "Med", "Right"),
            ),
            "LedPower": ("(a(ubbbb))", ([(1, True, True, False, False)],)),
        },
    )
    add(
        "/xyz/ljones/anime",
        ANIME,
        {
            "EnableDisplay": ("b", True),
            "Brightness": ("u", 2),
            "BuiltinsEnabled": ("b", True),
            "BuiltinAnimations": (
                "(ssss)",
                ("GlitchConstruction", "BinaryBannerScroll", "BannerSwipe", "GlitchOut"),
            ),
            "OffWhenLidClosed": ("b", True),
            "OffWhenSuspended": ("b", True),
            "OffWhenUnplugged": ("b", True),
        },
    )
    curves = {
        p: [
            (fan, (0, 30, 60, 90, 130, 170, 220, 255), (30, 40, 50, 60, 70, 80, 90, 100), False)
            for fan in ("CPU", "GPU")
        ]
        for p in (0, 1, 2)
    }
    return Snapshot(
        devices,
        {
            "model": "ROG Zephyrus G14",
            "board": "GA401QC",
            "cpu": "AMD Ryzen 9 5900HS",
            "cpu_temp": 48,
            "memory": 42,
            "fans": [("CPU", 2200), ("GPU", 1900)],
            "battery": {"capacity": 78, "status": "Discharging", "health": 94, "watts": 9.2},
        },
        curves,
    )

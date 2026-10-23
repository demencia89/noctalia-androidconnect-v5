import base64
import array
import contextlib
from concurrent.futures import Future
import io
import fcntl
import json
import mmap
import os
from pathlib import Path
import queue
import select
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1] / "androidconnect"
sys.path.insert(0, str(PLUGIN))
import backend
from video import FrameServer, phone_filter, phone_geometry, preview_size


class ParsingTests(unittest.TestCase):
    def test_adb_states(self):
        devices = backend.parse_adb("""List of devices attached
USB1 device usb:1-2 product:p model:Test_Phone transport_id:1
192.0.2.10:40000 device product:p model:Wireless_Phone
USB2 unauthorized usb:2-1
USB3 offline
* daemon started successfully
""")
        self.assertEqual([item["state"] for item in devices], ["device", "device", "unauthorized", "offline"])
        self.assertTrue(devices[0]["usb"])
        self.assertFalse(devices[1]["usb"])
        self.assertEqual(devices[0]["name"], "Test Phone")

    def test_target_selection_never_guesses_between_phones(self):
        devices = [{"serial": "USB1", "state": "device"}, {"serial": "192.0.2.10:40000", "state": "device"}]
        self.assertEqual(backend.resolve_serial(devices, None, ""), "")
        self.assertEqual(backend.resolve_serial(devices[:1], None, ""), "USB1")
        self.assertEqual(backend.resolve_serial(devices[:1], None, "missing"), "")
        self.assertEqual(backend.resolve_serial(devices[:1], {"addresses": ["192.0.2.10"]}, ""), "")
        self.assertEqual(backend.resolve_serial(devices, {"addresses": ["192.0.2.10"]}, ""), "192.0.2.10:40000")
        self.assertEqual(backend.resolve_serial(devices, {"addresses": ["192.0.2.11"]}, ""), "")

    def test_screen_override_and_bounds(self):
        self.assertEqual(backend.display_size("Physical size: 1080x2400\nOverride size: 720x1600"), (720, 1600))
        for value in ("", "Physical size: 0x0", "Override size: 100000x100000"):
            with self.assertRaises(backend.OperationError):
                backend.display_size(value)
        width, height = preview_size(1080, 2400, 432)
        self.assertLessEqual(width, 312)
        self.assertLessEqual(height, 432)
        self.assertEqual(width % 2 + height % 2, 0)

    def test_phone_bezel_fits_without_changing_preview_height(self):
        self.assertEqual(phone_geometry(538, 1190), {
            "width": 597, "height": 1241,
            "screen": {"x": 25, "y": 26, "width": 538, "height": 1190, "radius": 52},
        })
        for maximum in (240, 432, 540, 720):
            for source in ((1080, 2400), (1440, 3200), (2400, 1080)):
                width, height = preview_size(*source, maximum * 1190 / 1241)
                phone = phone_geometry(width, height)
                screen = phone["screen"]
                self.assertLessEqual(phone["height"], maximum)
                self.assertLessEqual(phone["width"], 400)
                self.assertGreater(screen["x"], 0)
                self.assertGreater(screen["y"], 0)
                self.assertLess(screen["x"] + width, phone["width"])
                self.assertLess(screen["y"] + height, phone["height"])
                self.assertLessEqual(screen["radius"] * 2, min(width, height))

    def test_shared_phone_artwork_is_precropped(self):
        with (PLUGIN / "Assets" / "phone.png").open("rb") as image:
            header = image.read(24)
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack("!II", header[16:24]), (597, 1241))

    def test_idle_glass_preserves_artwork_and_screen_geometry(self):
        root = ET.parse(PLUGIN / "Assets" / "phone-idle.svg").getroot()
        self.assertEqual(root.get("viewBox"), "0 0 597 1241")
        nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
        image = nodes["artwork"].get("{http://www.w3.org/1999/xlink}href")
        self.assertTrue(image.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(image.split(",", 1)[1], validate=True),
                         (PLUGIN / "Assets" / "phone.png").read_bytes())
        screen = nodes["screen"]
        self.assertEqual([screen.get(key) for key in ("x", "y", "width", "height", "rx")],
                         ["25", "26", "538", "1190", "52"])
        self.assertEqual(screen.get("fill"), "url(#glass)")

    def test_endpoints(self):
        self.assertEqual(backend.endpoint("192.0.2.10", "40000"), "192.0.2.10:40000")
        self.assertEqual(backend.endpoint("[2001:db8::1]", "65535"), "[2001:db8::1]:65535")
        self.assertEqual(backend.endpoint("phone.local", "1"), "phone.local:1")
        for host, port in (("-flag", "20"), ("a;true", "20"), ("192.0.2.1", "0"), ("phone", "65536"), ("phone", "1;true")):
            with self.assertRaises(backend.OperationError):
                backend.endpoint(host, port)

    def test_adb_battery_status(self):
        self.assertEqual(backend.battery_info("  level: 37\n  scale: 100\n  status: 2\n"),
                         {"battery": 37, "charging": True})
        self.assertEqual(backend.battery_info("level: 3\nscale: 4\nstatus: 3"),
                         {"battery": 75, "charging": False})
        self.assertEqual(backend.battery_info("no battery data")["battery"], -1)

    def test_orientation_is_verified_not_guessed(self):
        self.assertTrue(backend.natural_orientation("SurfaceOrientation: 0"))
        self.assertFalse(backend.natural_orientation("SurfaceOrientation: 1"))
        self.assertTrue(backend.natural_orientation("Viewport INTERNAL: displayId=0, orientation=0"))
        self.assertFalse(backend.natural_orientation("Viewport INTERNAL: displayId=0, orientation=ROTATION_90"))
        with self.assertRaises(backend.OperationError):
            backend.natural_orientation("Viewport EXTERNAL: displayId=1, orientation=0")

    def test_mdns_exact_services(self):
        services = backend.mdns_services("""List of discovered mdns services
phone _adb-tls-connect._tcp. 192.0.2.10:40000
pair _adb-tls-pairing._tcp. [2001:db8::1]:40001
other _http._tcp 192.0.2.11:80
broken _adb-tls-connect._tcp not-an-endpoint
""")
        self.assertEqual(len(services), 2)
        self.assertEqual(services[0]["endpoint"], "192.0.2.10:40000")
        self.assertEqual(services[1]["host"], "2001:db8::1")

    def test_bus_variants(self):
        self.assertEqual(backend.unwrap({"name": {"type": "s", "data": "Phone"},
                                         "paired": {"type": "b", "data": True}}),
                         {"name": "Phone", "paired": True})


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="androidconnect-test-")
        self.path = Path(self.directory.name)
        self.bridge = backend.Bridge(self.path, self.path)
        self.bridge.panel_open = True
        self.bridge.model.update(serial="USB1", adb=[{"serial": "USB1", "state": "device"}])

    def tearDown(self):
        self.directory.cleanup()

    def request(self, value, name="1000000000000-000001.json"):
        (self.path / name).write_text(json.dumps(value), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.bridge.receive()

    def test_late_input_cannot_reach_a_different_target(self):
        with patch.object(self.bridge, "handle") as handle:
            self.request({"action": "key", "target": "USB2", "key": 3})
            handle.assert_not_called()
            self.request({"action": "key", "target": "USB1", "key": 3})
            handle.assert_called_once()

    def test_late_kde_action_cannot_reach_a_different_target(self):
        self.bridge.model["selectedKde"] = "phone1"
        with patch.object(self.bridge, "handle") as handle:
            self.request({"action": "unpair", "kdeTarget": "phone2"})
            handle.assert_not_called()

    def test_input_after_panel_close_is_discarded(self):
        self.bridge.panel_open = False
        with patch.object(self.bridge, "handle") as handle:
            self.request({"action": "key", "target": "USB1", "key": 3})
            handle.assert_not_called()

    def test_window_requests_require_an_open_panel_and_current_target(self):
        with patch.object(self.bridge, "start_window") as start:
            self.request({"action": "window", "target": "USB2", "token": "late"})
            self.bridge.panel_open = False
            self.request({"action": "window", "target": "USB1", "token": "closed"})
            start.assert_not_called()
            self.bridge.panel_open = True
            self.request({"action": "window", "target": "USB1", "token": "current"})
            start.assert_called_once_with("current")

    def test_window_handoff_stops_embedded_video_and_survives_panel_close(self):
        embedded, frames = Mock(), Mock()
        self.bridge.scrcpy, self.bridge.frames = embedded, frames
        self.bridge.model.update(keepAwake=True, dim=True)
        with patch("backend.shutil.which", side_effect=lambda name: "/tool" if name in {"adb", "scrcpy"} else None), \
             patch("backend.Child") as child, patch("backend.adb") as adb:
            for audio in (False, True):
                self.bridge.model["audio"] = audio
                self.bridge.handle({"action": "window", "token": "test-window"})
                argv = child.call_args.args[0]
                self.assertEqual(argv[:2], ["scrcpy", "--serial=USB1"])
                self.assertFalse(any("v4l2" in arg or arg == "--no-window" for arg in argv))
                self.assertIn("--no-clipboard-autosync", argv)
                self.assertEqual("--no-audio" in argv, not audio)
                if not audio:
                    self.assertIn("--stay-awake", argv)
                    self.assertIn("--turn-screen-off", argv)
                self.assertIsNone(self.bridge.frames)
                self.assertIsNone(self.bridge.scrcpy)
                self.assertTrue(self.bridge.auto_suppressed)
                self.assertEqual(self.bridge.model["windowRequest"], "test-window")
                child.return_value.stop.reset_mock()
                self.bridge.handle({"action": "close"})
                child.return_value.stop.assert_not_called()
            embedded.stop.assert_called_once()
            frames.stop.assert_called_once()
            adb.assert_not_called()
            self.bridge.close()
            child.return_value.stop.assert_called_once()
            self.assertIsNone(self.bridge.window)

    def test_reopening_panel_returns_from_window_to_embedded_mode(self):
        child = Mock()
        self.bridge.window = child
        self.bridge.auto_suppressed = True
        self.bridge.handle({"action": "open"})
        child.stop.assert_called_once()
        self.assertIsNone(self.bridge.window)
        self.assertTrue(self.bridge.panel_open)
        self.assertFalse(self.bridge.auto_suppressed)

    def test_window_launch_failure_is_not_acknowledged(self):
        with patch("backend.shutil.which", return_value="/tool"), \
             patch("backend.Child", side_effect=OSError("Cannot launch scrcpy")):
            self.request({"action": "window", "target": "USB1", "token": "failed"})
        self.assertNotIn("windowRequest", self.bridge.model)
        self.assertIsNone(self.bridge.window)
        self.assertTrue(self.bridge.panel_open)
        self.assertIn("Cannot launch scrcpy", self.bridge.model["error"])

    def test_closed_window_is_reaped_and_failures_are_reported(self):
        for code in (0, 1):
            child = Mock()
            child.process.poll.return_value = code
            child.process.returncode = code
            child.error.return_value = "Window failed"
            self.bridge.window = child
            self.bridge.auto_suppressed = True
            with contextlib.redirect_stdout(io.StringIO()), patch.object(self.bridge, "notice") as notice:
                self.bridge.tick()
                self.assertEqual(notice.call_count, int(code != 0))
            child.stop.assert_called_once()
            self.assertIsNone(self.bridge.window)

    def test_remote_text_is_one_quoted_word(self):
        text = "hello';$(echo unsafe)& world"
        with patch("backend.run", return_value="") as run:
            self.bridge.handle({"action": "text", "text": text})
            argv = run.call_args.args[0]
            self.assertEqual(argv[:7], ["adb", "-s", "USB1", "shell", "input", "text", shlex.quote(text.replace(" ", "%s"))])
            self.assertEqual(shlex.split(argv[-1]), [text.replace(" ", "%s")])

    def test_scroll_swipes_use_validated_gesture_path(self):
        self.bridge.scrcpy = object()
        self.bridge.model.update(phase="streaming", sourceWidth=1000, sourceHeight=2000)
        for points, expected in (
            ([0.5, 0.56, 0.5, 0.44], [500, 1120, 500, 880]),
            ([0.5, 0.44, 0.5, 0.56], [500, 880, 500, 1120]),
            ([0.56, 0.5, 0.44, 0.5], [560, 1000, 440, 1000]),
        ):
            with patch("backend.adb", side_effect=["SurfaceOrientation: 0", ""]) as adb:
                self.bridge.handle({"action": "gesture", "points": points, "duration": 120})
                self.assertEqual(adb.call_args.args, ("USB1", "shell", "input", "swipe", *expected, 120))
        for value in (-0.1, 1.1, float("nan"), float("inf")):
            with patch("backend.adb", return_value="SurfaceOrientation: 0") as adb:
                with self.assertRaises(backend.OperationError):
                    self.bridge.handle({"action": "gesture", "points": [value, 0.5, 0.5, 0.5]})
                self.assertEqual(adb.call_count, 1)

    def test_bad_commands_do_not_execute(self):
        with patch("backend.run") as run:
            for request in ({"action": "key", "key": 999}, {"action": "text", "text": "bad\ntext"},
                            {"action": "configure", "options": {"video_device": "/tmp/not-video"}}):
                with self.assertRaises(backend.OperationError):
                    self.bridge.handle(request)
            run.assert_not_called()

    def test_occupied_loopback_is_not_reused_or_killed(self):
        with patch("backend.shutil.which", return_value="/tool"), \
             patch("backend.Path.is_char_device", return_value=True), \
             patch("backend.os.access", return_value=True), \
             patch("backend.adb", side_effect=["Physical size: 1080x2400", "SurfaceOrientation: 0"]), \
             patch("backend.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"1234", b"")), \
             patch("backend.Child") as child:
            with self.assertRaisesRegex(backend.OperationError, "in use"):
                self.bridge.start_mirror()
            child.assert_not_called()

    def test_embedded_scrcpy_never_creates_a_window(self):
        self.assertTrue(self.bridge.model["audio"])
        with patch("backend.shutil.which", return_value="/tool"), \
             patch("backend.Path.is_char_device", return_value=True), \
             patch("backend.os.access", return_value=True), \
             patch("backend.adb", side_effect=["Physical size: 1080x2400", "SurfaceOrientation: 0"] * 2), \
             patch("backend.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"")), \
             patch("backend.Child") as child, patch("backend.FrameServer") as frames:
            child.return_value.error.return_value = "No video mirroring, SDK mouse disabled"
            with contextlib.redirect_stdout(io.StringIO()):
                for audio, fps in ((False, 30), (True, 60)):
                    self.bridge.model["audio"] = audio
                    self.bridge.config["fps"] = fps
                    self.bridge.start_mirror()
                    argv = child.call_args.args[0]
                    self.assertIn("--no-window", argv)
                    self.assertEqual("--no-audio" in argv, not audio)
                    self.assertIn("--max-fps=" + str(fps), argv)
                    video = frames.call_args.args[1]
                    self.assertLess(video.index("-framerate"), video.index("-i"))
                    self.assertEqual(video[video.index("-framerate") + 1], str(fps))
                    self.assertNotIn("fps=", video[video.index("-filter_complex") + 1])
                    self.assertNotIn("-loop", video)  # Artwork/masks are computed once, not per frame.
                    self.assertIn(str(PLUGIN / "Assets" / "phone.png"), video)
                    self.assertEqual(frames.call_args.args[2:4],
                                     (self.bridge.model["width"], self.bridge.model["height"]))
                    self.assertEqual(video[video.index("-fps_mode") + 1], "passthrough")
            child.return_value.error.assert_not_called()

    def test_mirror_becomes_ready_only_after_actual_frames(self):
        frames = SimpleNamespace(ever_connected=False)
        self.bridge.frames = frames
        self.bridge.config["auto_mirror"] = False
        self.bridge.model["phase"] = "starting"
        with contextlib.redirect_stdout(io.StringIO()):
            self.bridge.tick()
            self.assertEqual(self.bridge.model["phase"], "starting")
            self.bridge.events.put(("ready", "", frames))
            self.bridge.tick()
        self.assertEqual(self.bridge.model["phase"], "streaming")

    def test_pairing_secret_is_stdin_only(self):
        with patch("backend.run", return_value="Successfully paired") as run:
            self.bridge.wireless({"action": "pairAdb", "host": "192.0.2.10", "port": "40000", "code": "123456"})
            self.assertNotIn("123456", str(run.call_args.args))
            self.assertEqual(run.call_args.kwargs["input"], "123456\n")

    def test_qr_secret_is_not_status_or_preferences(self):
        with patch("backend.run", return_value=""), patch("backend.shutil.which", return_value="/tool"):
            self.bridge.wireless({"action": "qr"})
            secret = self.bridge.qr["secret"]
            self.assertNotIn(secret, json.dumps(self.bridge.model))
            self.assertNotIn(secret, json.dumps(self.bridge.preferences))
            self.bridge.cancel_qr()

    def test_explicit_phone_mapping_survives_kde_switches(self):
        self.bridge.model["kde"] = [{"id": "phone1"}, {"id": "phone2"}]
        self.bridge.set_selection({"kde": "phone1", "serial": "USB1"})
        self.bridge.set_selection({"kde": "phone2"})
        self.assertEqual(self.bridge.model["serial"], "")
        self.bridge.set_selection({"kde": "phone1"})
        self.assertEqual(self.bridge.model["serial"], "USB1")

    def test_initial_size_is_large_and_every_user_choice_is_remembered(self):
        self.assertEqual((self.bridge.model["phoneSize"], self.bridge.model["phoneSizeDirection"]), (3, -1))
        for size, direction in ((1, 1), (2, -1), (3, -1)):
            self.bridge.handle({"action": "size", "preset": size, "direction": direction})
            restored = backend.Bridge(self.path, self.path)
            self.assertEqual((restored.model["phoneSize"], restored.model["phoneSizeDirection"]), (size, direction))

    def test_invalid_saved_size_falls_back_to_large(self):
        for value in (None, True, 0, 4, "large"):
            (self.path / "devices.json").write_text(json.dumps({"phoneSize": value}))
            self.assertEqual(backend.Bridge(self.path, self.path).model["phoneSize"], 3)

    def test_phone_size_is_saved_without_restarting_video(self):
        with patch.object(self.bridge, "start_mirror") as start, patch.object(self.bridge, "stop_mirror") as stop:
            self.bridge.handle({"action": "size", "preset": 3, "direction": -1})
            start.assert_not_called()
            stop.assert_not_called()
        restored = backend.Bridge(self.path, self.path)
        self.assertEqual((restored.model["phoneSize"], restored.model["phoneSizeDirection"]), (3, -1))
        for size, direction in ((0, 1), (4, 1), (True, 1), (2, 0), (2, True)):
            with self.assertRaises(backend.OperationError):
                self.bridge.handle({"action": "size", "preset": size, "direction": direction})

    def test_local_nickname_persists_and_reset_restores_original(self):
        with patch("backend.run") as run, patch.object(self.bridge, "stop_mirror") as stop:
            self.bridge.handle({"action": "rename", "nameKey": "adb:USB1", "name": "  Café phone  "})
            run.assert_not_called()
            stop.assert_not_called()
        self.assertEqual(self.bridge.model["names"], {"adb:USB1": "Café phone"})
        restored = backend.Bridge(self.path, self.path)
        self.assertEqual(restored.model["names"], {"adb:USB1": "Café phone"})
        self.bridge.handle({"action": "rename", "nameKey": "adb:USB1", "name": "  "})
        self.assertEqual(backend.Bridge(self.path, self.path).model["names"], {})

    def test_kde_nicknames_are_separate_from_adb_and_stale_edits_are_rejected(self):
        self.bridge.handle({"action": "rename", "nameKey": "adb:USB1", "name": "USB phone"})
        self.bridge.model.update(selectedKde="paired", kde=[{"id": "paired", "name": "System name"}])
        with self.assertRaises(backend.OperationError):
            self.bridge.handle({"action": "rename", "nameKey": "adb:USB1", "name": "Stale name"})
        self.bridge.handle({"action": "rename", "nameKey": "kde:paired", "name": "Paired phone"})
        self.assertEqual(self.bridge.model["names"], {"adb:USB1": "USB phone", "kde:paired": "Paired phone"})
        self.assertEqual(self.bridge.model["kde"][0]["name"], "System name")
        self.bridge.panel_open = False
        self.request({"action": "rename", "nameKey": "kde:paired", "name": "Closed edit"})
        self.assertEqual(self.bridge.model["names"]["kde:paired"], "Paired phone")

    def test_invalid_nicknames_and_failed_saves_do_not_change_preferences(self):
        for name in (None, 42, "x" * 81, "bad\nname", "bad\x00name", "bad\u202ename"):
            with self.assertRaises(backend.OperationError):
                self.bridge.handle({"action": "rename", "nameKey": "adb:USB1", "name": name})
        with patch("backend.save_json", side_effect=OSError("Cannot save")):
            with self.assertRaises(OSError):
                self.bridge.handle({"action": "rename", "nameKey": "adb:USB1", "name": "Unsaved"})
        self.assertEqual(self.bridge.model["names"], {})
        self.assertEqual(self.bridge.preferences["names"], {})


class WirelessTests(unittest.TestCase):
    setUp = CommandTests.setUp
    tearDown = CommandTests.tearDown
    request = CommandTests.request

    def phone(self):
        self.bridge.model.update(selectedKde="phone1", serial="", configured=True, adb=[], kde=[
            {"id": "phone1", "addresses": ["192.0.2.10"], "paired": True, "reachable": True},
            {"id": "phone2", "addresses": ["192.0.2.20"], "paired": True, "reachable": True},
        ])
        self.bridge.model["tools"]["adb"] = True

    @staticmethod
    def service(name="adb-phone", host="192.0.2.10", port="40001", kind="_adb-tls-connect._tcp"):
        return {"name": name, "host": host, "port": port, "type": kind, "endpoint": backend.endpoint(host, port)}

    def update(self, **result):
        with contextlib.redirect_stdout(io.StringIO()):
            self.bridge.update_probe(result)

    def finish(self, address=None, error=None):
        future = Future()
        self.bridge.reconnect["future"] = future
        future.set_exception(error) if error else future.set_result(address)
        with contextlib.redirect_stdout(io.StringIO()):
            self.bridge.poll_reconnect()

    def test_changed_port_and_duplicate_mdns_transport_match_one_phone(self):
        phone = {"addresses": ["192.0.2.10"]}
        devices = [{"serial": value, "state": "device"} for value in
                   ("192.0.2.20:40000", "192.0.2.10:40001", "adb-phone._adb-tls-connect._tcp")]
        service = self.service()
        self.assertEqual(backend.resolve_serial(devices, phone, "192.0.2.10:39999", [service]), "192.0.2.10:40001")
        self.assertEqual(backend.resolve_serial(devices[::2], phone, "192.0.2.10:39999", [service]),
                         "adb-phone._adb-tls-connect._tcp")
        service["name"] += "._adb-tls-connect._tcp."
        self.assertEqual(backend.transport_host(devices[-1]["serial"], [service]), "192.0.2.10")

    def test_ipv6_alias_matching_and_no_device_name_guessing(self):
        service = self.service(host="2001:db8::10")
        self.assertEqual(backend.transport_host("adb-phone._adb-tls-connect._tcp", [service]), "2001:db8::10")
        devices = [{"serial": "[2001:db8::10]:40001", "state": "device"}]
        self.assertEqual(backend.resolve_serial(devices, {"addresses": ["2001:0db8::10"]}, ""), devices[0]["serial"])
        self.assertEqual(backend.resolve_serial(devices, {"addresses": [""], "name": "Same phone"}, ""), "")

    def test_selected_missing_kde_phone_never_falls_back_to_another_phone(self):
        self.phone()
        self.bridge.model.update(kde=[], adb=[{"serial": "192.0.2.20:40000", "state": "device"}])
        self.assertEqual(self.bridge.resolved_serial(), "")

    def test_discovery_retries_and_only_connects_the_selected_host(self):
        clock = SimpleNamespace(now=0)
        cancel = Mock()
        cancel.is_set.return_value = False
        cancel.wait.side_effect = lambda delay: setattr(clock, "now", clock.now + delay)
        wrong = self.service(host="192.0.2.20")
        pairing = self.service(kind="_adb-tls-pairing._tcp")
        with patch("backend.time.monotonic", side_effect=lambda: clock.now), \
             patch("backend.discover_services", side_effect=[[wrong, pairing], [wrong, self.service()]]), \
             patch("backend.run", return_value="connected to 192.0.2.10:40001") as run:
            self.assertEqual(backend.connect_wireless(["192.0.2.10"], cancel, 12), "192.0.2.10:40001")
            run.assert_called_once_with(["adb", "connect", "192.0.2.10:40001"], timeout=3)
        self.assertEqual(clock.now, 1)

    def test_failed_old_port_is_rediscovered_and_timeout_is_bounded(self):
        clock = SimpleNamespace(now=0)
        cancel = Mock()
        cancel.is_set.return_value = False
        cancel.wait.side_effect = lambda delay: setattr(clock, "now", clock.now + delay)
        with patch("backend.time.monotonic", side_effect=lambda: clock.now), \
             patch("backend.discover_services", side_effect=[[self.service(port="40000")], [self.service()]]), \
             patch("backend.run", side_effect=[backend.OperationError("stale port"), "connected to phone"]) as run:
            self.assertEqual(backend.connect_wireless(["192.0.2.10"], cancel, 12), "192.0.2.10:40001")
            self.assertEqual(run.call_args_list[0].args[0][-1], "192.0.2.10:40000")
        clock.now = 0
        with patch("backend.time.monotonic", side_effect=lambda: clock.now), \
             patch("backend.discover_services", return_value=[]), patch("backend.run") as run:
            with self.assertRaisesRegex(backend.OperationError, "timed out"):
                backend.connect_wireless(["192.0.2.10"], cancel, 3)
            run.assert_not_called()
        self.assertEqual(clock.now, 3)

    def test_cancellation_after_discovery_does_not_connect(self):
        cancel = threading.Event()
        def discover():
            cancel.set()
            return [self.service()]
        with patch("backend.discover_services", side_effect=discover), patch("backend.run") as run:
            self.assertIsNone(backend.connect_wireless(["192.0.2.10"], cancel, 12))
            run.assert_not_called()

    def test_avahi_fallback_is_parsed_without_a_shell(self):
        text = "=;wlan0;IPv4;adb-phone;_adb-tls-connect._tcp;local;phone.local;192.0.2.10;40001;\n"
        with patch("backend.shutil.which", return_value="/tool"), \
             patch("backend.run", side_effect=[backend.OperationError("mdns unavailable"), text]) as run:
            self.assertEqual(backend.discover_services(), [self.service()])
            self.assertEqual(run.call_args.args[0], ["avahi-browse", "-rtkp", "_adb-tls-connect._tcp"])

    def test_panel_open_schedules_one_attempt_and_wireless_page_retries(self):
        self.phone()
        self.bridge.panel_open = False
        self.update()
        self.assertIsNone(self.bridge.reconnect)
        self.bridge.handle({"action": "open"})
        self.update()
        first = self.bridge.reconnect
        self.assertEqual((first["hosts"], first["timeout"]), (["192.0.2.10"], 12))
        self.update()
        self.assertIs(self.bridge.reconnect, first)
        self.finish(error=backend.OperationError("timed out"))
        self.assertEqual(self.bridge.model["wirelessState"], "timeout")
        self.update()
        self.assertIsNone(self.bridge.reconnect)
        with patch("backend.run") as run:
            self.bridge.handle({"action": "wirelessOpen"})
            run.assert_not_called()  # Scanning is queued, never performed on the UI/input thread.
        self.assertEqual(self.bridge.reconnect["timeout"], 18)

    def test_success_is_saved_for_selected_phone_then_authorized_by_probe(self):
        self.phone()
        self.bridge.request_reconnect()
        self.finish("192.0.2.10:40001")
        self.assertEqual(self.bridge.model["serial"], "")
        self.assertEqual(self.bridge.preferences["profiles"], {"phone1": "192.0.2.10:40001"})
        self.assertEqual(self.bridge.preferences["wireless"], {"phone1": {"host": "192.0.2.10"}})
        self.update(adb=[{"serial": "192.0.2.10:40001", "state": "device"}])
        self.assertEqual(self.bridge.model["serial"], "192.0.2.10:40001")

    def test_switch_or_close_cancels_pending_results(self):
        self.phone()
        self.bridge.request_reconnect()
        job = self.bridge.reconnect
        self.bridge.set_selection({"kde": "phone2"})
        self.assertTrue(job["cancel"].is_set())
        self.assertIsNone(self.bridge.reconnect)
        self.update(probeSelection="phone1", adb=[{"serial": "192.0.2.10:40001", "state": "device"}])
        self.assertEqual(self.bridge.model["serial"], "")
        self.assertEqual(self.bridge.model["selectedKde"], "phone2")
        self.bridge.request_reconnect()
        job = self.bridge.reconnect
        self.bridge.handle({"action": "close"})
        self.assertTrue(job["cancel"].is_set())
        self.update()
        self.assertIsNone(self.bridge.reconnect)

    def test_moved_host_does_not_accept_an_old_connection_result(self):
        self.phone()
        self.bridge.request_reconnect()
        self.bridge.model["kde"][0]["addresses"] = ["192.0.2.30"]
        self.finish("192.0.2.10:40001")
        self.assertNotIn("phone1", self.bridge.preferences["profiles"])

    def test_connected_phone_needs_no_scan_and_keeps_explicit_usb_mapping(self):
        self.phone()
        self.bridge.preferences["profiles"]["phone1"] = "USB1"
        self.update(adb=[{"serial": "192.0.2.10:40001", "state": "device"},
                         {"serial": "USB1", "state": "device"}])
        self.bridge.handle({"action": "wirelessOpen"})
        self.assertIsNone(self.bridge.reconnect)
        self.assertEqual(self.bridge.model["wirelessState"], "connected")
        self.assertEqual(self.bridge.preferences["profiles"]["phone1"], "USB1")
        self.update(adb=[{"serial": "USB1", "state": "device"}])
        self.assertEqual(self.bridge.model["serial"], "USB1")

    def test_mdns_identity_does_not_flip_to_ip_and_restart_preview(self):
        self.phone()
        alias = "adb-phone._adb-tls-connect._tcp"
        self.bridge.model["serial"] = alias
        self.bridge.preferences["profiles"]["phone1"] = alias
        result = {"services": [self.service()], "adb": [
            {"serial": alias, "state": "device"}, {"serial": "192.0.2.10:40001", "state": "device"}]}
        with patch.object(self.bridge, "stop_mirror") as stop:
            self.update(**result)
            self.update(**result)
            stop.assert_not_called()
        self.assertEqual(self.bridge.preferences["profiles"]["phone1"], alias)

    def test_adb_only_remembers_host_but_never_chooses_another_phone(self):
        self.bridge.model.update(serial="", adb=[])
        self.bridge.model["tools"]["adb"] = True
        self.bridge.request_reconnect(18, "192.0.2.10")
        self.finish("192.0.2.10:40001")
        self.update(adb=[{"serial": "192.0.2.10:40002", "state": "device"},
                         {"serial": "192.0.2.20:40000", "state": "device"}])
        self.assertEqual(self.bridge.model["serial"], "192.0.2.10:40002")
        self.update(adb=[{"serial": "192.0.2.20:40000", "state": "device"}])
        self.assertEqual(self.bridge.model["serial"], "")

    def test_foreign_host_and_late_wireless_commands_are_rejected(self):
        self.phone()
        with self.assertRaises(backend.OperationError):
            self.bridge.request_reconnect(18, "192.0.2.20")
        self.request({"action": "wirelessOpen", "kdeTarget": "phone2"})
        self.assertIsNone(self.bridge.reconnect)

    def test_close_is_processed_while_background_discovery_is_waiting(self):
        self.phone()
        self.bridge.config["auto_mirror"] = False
        self.bridge.request_reconnect()
        started = threading.Event()
        def connect(hosts, cancelled, timeout):
            started.set()
            cancelled.wait(3)
        with contextlib.redirect_stdout(io.StringIO()), patch("backend.connect_wireless", side_effect=connect), \
             patch("backend.probe", return_value={"probeSelection": "phone1"}):
            thread = threading.Thread(target=self.bridge.loop)
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                (self.path / "1000000000000-000001.json").write_text('{"action":"close"}')
                deadline = time.monotonic() + 1
                while self.bridge.panel_open and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(self.bridge.panel_open)
            finally:
                self.bridge.stopped = True
                thread.join(5)
                self.assertFalse(thread.is_alive())


class PixelClient:
    """Test reader for the documented little-endian memfd protocol."""

    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.socket.settimeout(5)
        self.socket.connect(str(path))
        self.socket.send(struct.pack("<II", 1, 1))
        self.buffer = None
        self.width = self.height = self.seals = None

    def frame(self):
        self.socket.send(struct.pack("<II", 2, 0))
        message, ancillary, flags, _ = self.socket.recvmsg(16, socket.CMSG_SPACE(16), socket.MSG_CMSG_CLOEXEC)
        assert not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
        if len(message) == 16:
            kind, buffer_id, self.width, self.height = struct.unpack("<IIII", message)
            assert (kind, buffer_id) == (1, 0) and len(ancillary) == 1
            level, kind, data = ancillary[0]
            descriptors = array.array("i")
            descriptors.frombytes(data)
            assert (level, kind, len(descriptors)) == (socket.SOL_SOCKET, socket.SCM_RIGHTS, 1)
            descriptor = descriptors[0]
            try:
                assert not os.get_inheritable(descriptor)
                size = self.width * self.height * 4
                assert os.fstat(descriptor).st_size == size
                self.seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
                assert self.seals & fcntl.F_SEAL_SHRINK
                self.buffer = mmap.mmap(descriptor, size, access=mmap.ACCESS_READ)
            finally:
                os.close(descriptor)
            message, ancillary, flags, _ = self.socket.recvmsg(16, socket.CMSG_SPACE(16), socket.MSG_CMSG_CLOEXEC)
        assert message == struct.pack("<II", 2, 0) and not ancillary
        assert not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
        return bytes(self.buffer)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.socket.close()
        if self.buffer is not None:
            self.buffer.close()


class PixelProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac-pixels-")
        self.path = Path(self.directory.name) / "pixels.sock"
        with patch.object(FrameServer, "capture"):
            self.frames = FrameServer(self.path, [], 2, 2, queue.Queue())
        self.publish(10)

    def tearDown(self):
        self.frames.stop()
        self.directory.cleanup()

    def publish(self, value):
        with self.frames.condition:
            self.frames.latest = bytes([value, value, value, 255]) * 4
            self.frames.version += 1
            self.frames.condition.notify_all()

    def test_shared_buffer_stays_unchanged_until_next_request(self):
        with PixelClient(self.path) as client:
            first = client.frame()
            self.assertEqual((client.width, client.height), (2, 2))
            self.assertEqual(first, bytes([10, 10, 10, 255]) * 4)
            self.publish(20)
            self.publish(30)
            client.socket.settimeout(0.15)
            with self.assertRaises(socket.timeout):
                client.socket.recv(16)
            self.assertEqual(bytes(client.buffer), first, "The shell still owns this buffer")
            self.assertEqual(client.frame(), bytes([30, 30, 30, 255]) * 4, "Drop intermediate frames")

    def test_paused_drawing_does_not_disconnect(self):
        with PixelClient(self.path) as client:
            first = client.frame()
            self.publish(42)
            client.socket.settimeout(1.1)
            with self.assertRaises(socket.timeout):
                client.socket.recv(16)
            self.assertIsNotNone(self.frames.client)
            self.assertEqual(bytes(client.buffer), first)
            self.assertEqual(client.frame()[0], 42)

    def test_reconnect_gets_current_frame_without_waiting_for_capture(self):
        for _ in range(3):
            with PixelClient(self.path) as client:
                self.assertEqual(client.frame()[0], 10)

    def test_invalid_messages_are_rejected_and_next_client_can_connect(self):
        for packet, hello in ((struct.pack("<II", 2, 0), False),
                              (struct.pack("<II", 1, 2), False),
                              (b"short", True), (b"x" * 32, True),
                              (struct.pack("<II", 2, 1), True),
                              (struct.pack("<II", 1, 1), True)):
            with self.subTest(packet=packet, hello=hello), socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                client.settimeout(2)
                client.connect(str(self.path))
                if hello:
                    client.send(struct.pack("<II", 1, 1))
                client.send(packet)
                self.assertEqual(client.recv(16), b"")
        with PixelClient(self.path) as client:
            self.assertEqual(client.frame()[0], 10)

    def test_unexpected_received_descriptor_is_closed(self):
        with tempfile.TemporaryFile() as file, socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
            client.settimeout(2)
            client.connect(str(self.path))
            rights = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [file.fileno()]))]
            client.sendmsg([struct.pack("<II", 1, 1)], rights)
            self.assertEqual(client.recv(16), b"")
            os.fstat(file.fileno())  # Only the received copy should have been closed.
        with PixelClient(self.path) as client:
            self.assertEqual(client.frame()[0], 10)

    def test_stop_unblocks_pending_request_and_cleans_socket(self):
        with PixelClient(self.path) as client:
            client.frame()
            client.socket.send(struct.pack("<II", 2, 0))  # No newer frame is available.
            self.frames.stop()
            self.assertEqual(client.socket.recv(16), b"")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.frames.server_thread.is_alive())

    def test_existing_socket_path_is_not_replaced(self):
        identity = self.path.stat()
        with self.assertRaises(OSError):
            FrameServer(self.path, [], 2, 2, queue.Queue())
        self.assertEqual(self.path.stat().st_ino, identity.st_ino)

    def test_replacement_socket_path_survives_stop(self):
        self.path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as other:
            other.bind(str(self.path))
            identity = self.path.stat()
            self.frames.stop()
            self.assertEqual(self.path.stat().st_ino, identity.st_ino)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed for real frame tests")
class VideoTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ANDROIDCONNECT_PIXEL_CONSUMER"), "Set ANDROIDCONNECT_PIXEL_CONSUMER to the upstream test reader")
    def test_upstream_cpp_reader(self):
        command = ["ffmpeg", "-nostdin", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=64x96:rate=60",
                   "-vf", "format=rgba", "-threads", "1", "-f", "rawvideo", "pipe:1"]
        with tempfile.TemporaryDirectory(prefix="ac-upstream-") as directory:
            path = Path(directory) / "pixels.sock"
            frames = FrameServer(path, command, 64, 96, queue.Queue())
            try:
                result = subprocess.run([os.environ["ANDROIDCONNECT_PIXEL_CONSUMER"], str(path)],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("reconnect OK", result.stdout)
            finally:
                frames.stop()


    def test_real_frames_reconnect_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="ac-video-") as directory:
            path = Path(directory) / "frames.sock"
            command = ["ffmpeg", "-nostdin", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=64x96:rate=20",
                       "-vf", "format=rgba", "-threads", "1", "-f", "rawvideo", "pipe:1"]
            frames = FrameServer(path, command, 64, 96, queue.Queue())
            try:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                for _ in range(2):
                    with PixelClient(path) as client:
                        for _ in range(2):
                            pixels = client.frame()
                            self.assertEqual((client.width, client.height), (64, 96))
                            self.assertTrue(all(value == 255 for value in pixels[3::4]))
                        client.socket.settimeout(0.1)
                        with self.assertRaises(socket.timeout):
                            client.socket.recv(16)
            finally:
                frames.stop()
            self.assertFalse(path.exists())
            self.assertFalse(frames.capture_thread.is_alive())
            self.assertFalse(frames.server_thread.is_alive())

    def test_real_bezel_is_transparent_and_keeps_video_advancing(self):
        width, height = 108, 240
        phone = phone_geometry(width, height)
        command = ["ffmpeg", "-nostdin", "-v", "error", "-filter_complex_threads", "1",
                   "-re", "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=60",
                   "-threads", "1", "-i", str(PLUGIN / "Assets" / "phone.png"),
                   "-filter_complex", phone_filter(width, height), "-map", "[phone]",
                   "-threads", "1", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
        with tempfile.TemporaryDirectory(prefix="ac-bezel-") as directory:
            path = Path(directory) / "frames.sock"
            frames = FrameServer(path, command, phone["width"], phone["height"], queue.Queue())
            try:
                with PixelClient(path) as client:
                    images = []
                    for _ in range(3):
                        pixels = client.frame()
                        self.assertEqual((client.width, client.height), (phone["width"], phone["height"]))
                        self.assertEqual(pixels[3], 0)  # No rectangular background outside the phone.
                        center = (phone["height"] // 2 * phone["width"] + phone["width"] // 2) * 4
                        self.assertEqual(pixels[center + 3], 255)
                        # The center row is exactly the live screen, not the artwork's wallpaper.
                        screen = phone["screen"]
                        row = (screen["y"] + height // 2) * phone["width"] + screen["x"]
                        images.append(pixels[row * 4:(row + width) * 4])
                    self.assertNotEqual(images[0], images[-1])
            finally:
                frames.stop()
            self.assertFalse(path.exists())


class CompanionTests(unittest.TestCase):
    def read_event(self, process):
        ready, _, _ = select.select([process.stdout], [], [], 10)
        self.assertTrue(ready, "Companion did not publish status")
        line = process.stdout.readline()
        self.assertTrue(line, "Companion exited unexpectedly")
        return json.loads(line)

    def test_private_start_commands_single_instance_and_shutdown(self):
        with tempfile.TemporaryDirectory(prefix="ac-companion-") as directory:
            root = Path(directory)
            data = root / "data"
            env = dict(os.environ, XDG_RUNTIME_DIR=str(root), PATH="/nonexistent", PYTHONDONTWRITEBYTECODE="1")
            command = [sys.executable, "-B", "-u", str(PLUGIN / "backend.py"), "--data", str(data)]
            process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            runtime = None
            try:
                first = self.read_event(process)
                runtime = Path(first["value"]["runtime"])
                self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
                duplicate = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(duplicate.returncode, 2)
                self.assertIsNone(process.poll())
                temporary = runtime / "1000000000000-000001.pending"
                temporary.write_text(json.dumps({"action": "unknown-command"}), encoding="utf-8")
                temporary.rename(temporary.with_suffix(".json"))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if self.read_event(process).get("kind") == "notice":
                        break
                else:
                    self.fail("Invalid command was not reported")
            finally:
                process.terminate()
                try:
                    _, error = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    self.fail("Companion did not exit after SIGTERM")
                self.assertEqual(process.returncode, 0, error)
                if runtime is not None:
                    self.assertFalse(runtime.exists())


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
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
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1] / "androidconnect"
sys.path.insert(0, str(PLUGIN))
import backend
from video import FrameServer, preview_size


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
                    self.assertNotIn("fps=", video[video.index("-vf") + 1])
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


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed for real frame tests")
class VideoTests(unittest.TestCase):
    def receive(self, client, count):
        result = bytearray()
        while len(result) < count:
            part = client.recv(count - len(result))
            self.assertTrue(part)
            result.extend(part)
        return bytes(result)

    def test_real_frames_reconnect_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="ac-video-") as directory:
            path = Path(directory) / "frames.sock"
            command = ["ffmpeg", "-nostdin", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=64x96:rate=20",
                       "-vf", "format=rgba", "-threads", "1", "-f", "rawvideo", "pipe:1"]
            frames = FrameServer(path, command, 64, 96, queue.Queue())
            try:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                for _ in range(2):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(5)
                        client.connect(str(path))
                        for _ in range(2):
                            client.sendall(b"R")
                            self.assertEqual(struct.unpack("!4sII", self.receive(client, 12)), (b"NRF1", 64, 96))
                            pixels = self.receive(client, 64 * 96 * 4)
                            self.assertTrue(all(value == 255 for value in pixels[3::4]))
                        client.settimeout(0.1)
                        with self.assertRaises(socket.timeout):
                            client.recv(1)
            finally:
                frames.stop()
            self.assertFalse(path.exists())
            self.assertFalse(frames.capture_thread.is_alive())
            self.assertFalse(frames.server_thread.is_alive())


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

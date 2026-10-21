"""Small NRF1 producer. Capture/decoding stays outside the shell and Luau VM."""

import os
from pathlib import Path
import queue
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time


class Child:
    def __init__(self, argv, *, pixels=False):
        self.log = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if pixels else self.log, stderr=self.log,
            )
        except Exception:
            self.log.close()
            raise

    def error(self):
        size = os.fstat(self.log.fileno()).st_size
        return os.pread(self.log.fileno(), 4096, max(0, size - 4096)).decode("utf-8", "replace").strip()

    def stop(self, sig=signal.SIGTERM):
        if self.process.poll() is None:
            self.process.send_signal(sig)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process.stdout is not None:
            self.process.stdout.close()
        self.log.close()


class FrameServer:
    def __init__(self, path, command, width, height, events):
        if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4194304):
            raise ValueError("Invalid frame size")
        self.path = Path(path)
        self.command = command
        self.width, self.height = width, height
        self.events = events
        self.stopped = threading.Event()
        self.condition = threading.Condition()
        self.latest = None
        self.version = 0
        self.child = None
        self.client = None
        self.last_client = time.monotonic()
        self.ever_connected = False
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.server.bind(str(self.path))
            os.chmod(self.path, 0o600)
            self.identity = self.path.lstat()
            self.server.listen(1)
            self.server.settimeout(0.25)
        except Exception:
            self.server.close()
            raise
        self.capture_thread = threading.Thread(target=self.capture, daemon=True)
        self.server_thread = threading.Thread(target=self.serve, daemon=True)
        self.capture_thread.start()
        self.server_thread.start()

    def emit(self, kind, detail=""):
        # Only lifecycle events, never pixels or a per-frame event queue.
        try:
            self.events.put_nowait((kind, detail, self))
        except queue.Full:
            pass

    def capture(self):
        deadline = time.monotonic() + 8
        size = self.width * self.height * 4
        while not self.stopped.is_set():
            child = None
            try:
                child = Child(self.command, pixels=True)
                self.child = child
                while not self.stopped.is_set():
                    pixels = child.process.stdout.read(size)
                    if len(pixels) != size:
                        break
                    with self.condition:
                        first = self.latest is None
                        self.latest = pixels
                        self.version += 1
                        self.condition.notify_all()
                    if first:
                        self.emit("ready")
                detail = child.error()
                child.stop()
                self.child = None
                if self.stopped.is_set():
                    return
                if self.latest is not None or time.monotonic() >= deadline:
                    self.emit("error", detail or "Video capture stopped")
                    return
                # scrcpy may need a moment to publish the loopback format.
                self.stopped.wait(0.25)
            except (OSError, ValueError) as error:
                if child is not None:
                    child.stop()
                    self.child = None
                if not self.stopped.is_set():
                    self.emit("error", str(error))
                return

    def serve(self):
        header = struct.pack("!4sII", b"NRF1", self.width, self.height)
        while not self.stopped.is_set():
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with client:
                credentials = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                if struct.unpack("3i", credentials)[1] != os.geteuid():
                    continue
                self.client = client
                self.ever_connected = True
                sent = -1
                client.settimeout(1)
                try:
                    # One requested reply only: fit a preview frame in the send
                    # buffer so nonblocking readers need not wait between chunks.
                    client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, min(self.width * self.height * 4 + 12, 4 * 1024 * 1024))
                    while not self.stopped.is_set():
                        if client.recv(1) != b"R":
                            break
                        with self.condition:
                            ready = self.condition.wait_for(
                                lambda: self.stopped.is_set() or (self.latest is not None and sent != self.version),
                                timeout=5,
                            )
                            if not ready or self.stopped.is_set():
                                break
                            pixels, sent = self.latest, self.version
                        client.sendall(header)
                        client.sendall(pixels)
                except (ConnectionError, socket.timeout, OSError):
                    pass
                finally:
                    self.client = None
                    self.last_client = time.monotonic()

    def stop(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        client = self.client
        if client is not None:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.server.close()
        # Terminating FFmpeg unblocks a V4L2 read before joining its reader.
        child = self.child
        if child is not None and child.process.poll() is None:
            child.process.terminate()
        self.capture_thread.join(timeout=3)
        self.server_thread.join(timeout=2)
        try:
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) == (self.identity.st_dev, self.identity.st_ino):
                self.path.unlink()
        except FileNotFoundError:
            pass


def preview_size(width, height, maximum_height):
    scale = min(360 / width, maximum_height / height, 1)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)

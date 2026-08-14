#!/usr/bin/env python3
"""
Anti-Theft Monitor Module (REQ_18)

Reusable anti-theft monitor for the Car Control System. Polls the
accelerometer for sudden movement and, if the doors are locked at the time,
sounds the buzzer and shows a warning on the LCD.

The module is UI-agnostic: the host application (e.g. car_control.py) passes
three callbacks it uses to talk to the display and the door-lock state:
  - is_locked:      callable() -> bool     whether the doors are locked
  - show_alert:     callable()             show the theft warning screen
  - restore_screen: callable()             redraw the screen that showed before

The `is_locked`, `show_alert` and `restore_screen` callbacks that touch shared
state / the LCD are invoked while holding the `state_lock` passed to the
constructor, matching how the rest of the car control app serializes LCD
access. Everything sensor/actuator/logic related (accelerometer, buzzer,
camera, threading, alarm state machine) lives here. No HAL files are modified.

When an alarm triggers, the Pi camera snaps a timestamped photo into the
capture directory as evidence. Backends, tried in order: picamera2,
libcamera-still, then legacy picamera. If no camera is available the alarm
still works - the photo is simply skipped.
"""

import datetime
import os
import shutil
import subprocess
import threading
import time

from hal import hal_accelerometer
from hal import hal_buzzer

# Defaults (configurable via the AntiTheftMonitor constructor)
DEFAULT_ACCEL_THRESHOLD = 0.5   # g's of change between reads that counts as "sudden"
DEFAULT_POLL_INTERVAL   = 0.2   # seconds between accelerometer samples
DEFAULT_BUZZ_DURATION   = 3     # seconds the buzzer/warning stays on once triggered
DEFAULT_CAPTURE_DIR     = "captures"  # folder for theft photos


class _Camera:
    """Thin wrapper around the Raspberry Pi camera so capture failures can
    never crash the alarm. Backends, tried in order:
      1. picamera2 (Python libcamera bindings) - preferred.
      2. `libcamera-still` CLI (ships with libcamera-apps; no extra Python
         packages needed - works without internet).
      3. Legacy `picamera` driver (old OS images only).
    Not thread-safe on its own - the caller serializes access (and the alarm
    state machine guarantees at most one capture at a time anyway)."""

    def __init__(self):
        self._impl = None  # ("picamera2" | "cli" | "picamera", camera_object_or_None)
        self._lock = threading.Lock()

    def open(self):
        """Try to initialize the camera. Failures are logged, not raised."""
        try:
            from picamera2 import Picamera2
            cam = Picamera2()
            cam.configure(cam.create_still_configuration())
            cam.start()
            self._impl = ("picamera2", cam)
            print("Camera ready (picamera2)")
            return
        except Exception as picamera2_exc:
            print("Warning: picamera2 init failed (" + str(picamera2_exc) + ")")

        if shutil.which("libcamera-still"):
            self._impl = ("cli", None)
            print("Camera ready (libcamera-still)")
            return

        try:
            import picamera
            cam = picamera.PiCamera()
            cam.resolution = (1024, 768)
            self._impl = ("picamera", cam)
            print("Camera ready (picamera)")
        except Exception as exc:
            print("Warning: camera unavailable - theft photos disabled (" + str(exc) + ")")

    def capture(self, path):
        """Take a photo and save it to `path`. Returns True on success."""
        with self._lock:
            if self._impl is None:
                return False
            kind, cam = self._impl
            try:
                if kind == "picamera2":
                    cam.capture_file(path)
                elif kind == "cli":
                    # --nopreview so it doesn't try to open a window headless;
                    # --timeout lets auto-exposure settle before the still.
                    result = subprocess.run(
                        ["libcamera-still", "--output", path, "--nopreview", "--timeout", "2000"],
                        capture_output=True, timeout=20,
                    )
                    if result.returncode != 0:
                        detail = result.stderr.decode(errors="replace").strip()
                        print("Warning: libcamera-still failed (" + detail + ")")
                        return False
                else:  # picamera
                    cam.capture(path)
                return True
            except Exception as exc:
                print("Warning: camera capture failed (" + str(exc) + ")")
                return False

    def close(self):
        with self._lock:
            if self._impl is None:
                return
            kind, cam = self._impl
            if kind == "picamera2":
                try:
                    cam.stop()
                except Exception:
                    pass
            elif kind == "picamera":
                try:
                    cam.close()
                except Exception:
                    pass
            # "cli" backend needs no cleanup
            self._impl = None


class AntiTheftMonitor:
    def __init__(self, state_lock, is_locked, show_alert, restore_screen,
                 threshold=DEFAULT_ACCEL_THRESHOLD,
                 poll_interval=DEFAULT_POLL_INTERVAL,
                 buzz_duration=DEFAULT_BUZZ_DURATION,
                 capture_dir=DEFAULT_CAPTURE_DIR):
        self.state_lock = state_lock
        self.is_locked = is_locked
        self.show_alert = show_alert
        self.restore_screen = restore_screen
        self.threshold = threshold
        self.poll_interval = poll_interval
        self.buzz_duration = buzz_duration
        self.capture_dir = capture_dir

        self._accel = None
        self._camera = _Camera()
        self._alarm_active = False
        self._thread = None
        self._stop_event = threading.Event()

    def init(self):
        """Set up the accelerometer, buzzer and camera. Call once before
        start(). Camera init failures are non-fatal."""
        self._accel = hal_accelerometer.init()
        hal_buzzer.init()
        self._camera.open()

    def start(self):
        """Start the background monitoring thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the monitoring thread to stop, silence the buzzer and close
        the camera."""
        self._stop_event.set()
        hal_buzzer.turn_off()
        self._camera.close()

    def _capture_photo(self):
        """Take a timestamped evidence photo into the capture directory.
        Runs in its own thread so a slow camera never delays the buzzer or
        the screen restore. Fails gracefully (just a log line)."""
        try:
            os.makedirs(self.capture_dir, exist_ok=True)
        except OSError as exc:
            print("Warning: cannot create capture dir (" + str(exc) + ")")
            return

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(self.capture_dir, "theft_" + stamp + ".jpg")
        if self._camera.capture(path):
            print("Photo saved: " + path)
        else:
            print("Warning: could not take theft photo (camera not available or capture failed)")

    def _trigger_alarm(self):
        """Show the warning, snap an evidence photo and sound the buzzer for
        buzz_duration seconds. The buzzer and photo run outside state_lock so
        other threads aren't blocked while they work; the LCD calls happen
        under the lock like every other display update in the app."""
        with self.state_lock:
            if self._alarm_active:
                return  # already sounding - don't stack overlapping alarms
            self._alarm_active = True
            self.show_alert()

        print("!!! WARNING: sudden movement detected while doors are locked - possible theft !!!")
        threading.Thread(target=self._capture_photo, daemon=True).start()
        hal_buzzer.turn_on()
        self._stop_event.wait(self.buzz_duration)  # returns early on stop()
        hal_buzzer.turn_off()

        with self.state_lock:
            self._alarm_active = False
            self.restore_screen()

    def _run(self):
        """Continuously poll the accelerometer for sudden speed changes.
        - Doors locked + sudden change  -> sound buzzer, show warning.
        - Doors unlocked + sudden change -> ignored, nothing happens.
        Runs regardless of RFID session state, since theft attempts are most
        likely while nobody has tapped in."""
        prev_magnitude = None

        while True:
            if self._stop_event.wait(self.poll_interval):
                break  # stop() was called

            x, y, z = self._accel.get_3_axis()
            magnitude = (x ** 2 + y ** 2 + z ** 2) ** 0.5

            if prev_magnitude is not None:
                delta = abs(magnitude - prev_magnitude)
                if delta >= self.threshold:
                    with self.state_lock:
                        locked = self.is_locked()
                    if locked:
                        self._trigger_alarm()
                    # else: doors unlocked - sudden movement is expected/ignored

            prev_magnitude = magnitude


if __name__ == "__main__":
    # Standalone mode (for testing without the car control UI): treat the
    # doors as always locked, and report alerts to the console.
    def console_alert():
        print("!!! WARNING !!! Theft Detected!")

    def do_nothing():
        pass

    monitor = AntiTheftMonitor(
        threading.Lock(),
        is_locked=lambda: True,
        show_alert=console_alert,
        restore_screen=do_nothing,
    )
    print("Anti-Theft System Starting...")
    monitor.init()
    print("Doors are locked. Monitoring accelerometer for sudden movement...")
    monitor.start()

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        monitor.stop()
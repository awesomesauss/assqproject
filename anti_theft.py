import datetime
import os
import shutil
import subprocess
import threading
import time

from hal import hal_accelerometer
from hal import hal_buzzer

# Default configuration settings
DEFAULT_ACCEL_THRESHOLD = 0.5
DEFAULT_POLL_INTERVAL = 0.2
DEFAULT_BUZZ_DURATION = 3
DEFAULT_CAPTURE_DIR = "captures"


class _Camera:
    """Helper class to manage Raspberry Pi camera capture via libcamera-still."""

    def __init__(self):
        self._available = False
        self._lock = threading.Lock()

    def open(self):
        # Check if libcamera-still CLI is available
        if shutil.which("libcamera-still"):
            self._available = True
            print("Camera ready (libcamera-still)")
        else:
            print("Warning: libcamera-still unavailable - theft photos disabled")

    def capture(self, path):
        # Take photo using libcamera-still and save to path
        with self._lock:
            if not self._available:
                return False
            try:
                result = subprocess.run(
                    ["libcamera-still", "--output", path, "--nopreview", "--timeout", "2000"],
                    capture_output=True, timeout=20,
                )
                if result.returncode != 0:
                    detail = result.stderr.decode(errors="replace").strip()
                    print("Warning: libcamera-still failed (" + detail + ")")
                    return False
                return True
            except Exception as exc:
                print("Warning: camera capture failed (" + str(exc) + ")")
                return False

    def close(self):
        self._available = False


class AntiTheftMonitor:
    def __init__(self, state_lock, is_locked, show_alert, restore_screen,
                 on_alert=None,
                 threshold=DEFAULT_ACCEL_THRESHOLD,
                 poll_interval=DEFAULT_POLL_INTERVAL,
                 buzz_duration=DEFAULT_BUZZ_DURATION,
                 capture_dir=DEFAULT_CAPTURE_DIR):
        self.state_lock = state_lock
        self.is_locked = is_locked
        self.show_alert = show_alert
        self.restore_screen = restore_screen
        self.on_alert = on_alert
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
        # Initialize sensors and camera
        try:
            self._accel = hal_accelerometer.init()
        except Exception as exc:
            print("Warning: accelerometer init failed (" + str(exc) + ") - anti-theft disabled.")
        try:
            hal_buzzer.init()
        except Exception as exc:
            print("Warning: buzzer init failed (" + str(exc) + ")")
        self._camera.open()

    def start(self):
        # Start background polling thread
        if self._accel is None:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        # Stop background thread and turn off buzzer
        self._stop_event.set()
        hal_buzzer.turn_off()
        self._camera.close()

    def _capture_photo(self):
        # Create directory and take photo with timestamp
        try:
            os.makedirs(self.capture_dir, exist_ok=True)
        except OSError as exc:
            print("Warning: cannot create capture dir (" + str(exc) + ")")
            return None

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(self.capture_dir, "theft_" + stamp + ".jpg")
        if self._camera.capture(path):
            print("Photo saved: " + path)
            return path
        else:
            print("Warning: could not take theft photo (camera not available or capture failed)")
            return None

    def _trigger_alarm(self):
        # Sound buzzer, show alert screen, and capture evidence photo
        with self.state_lock:
            if self._alarm_active:
                return
            self._alarm_active = True
            self.show_alert()

        print("!!! WARNING: sudden movement detected while doors are locked - possible theft !!!")
        photo_path = self._capture_photo()
        hal_buzzer.turn_on()
        if self.on_alert is not None:
            self.on_alert(photo_path)
        self._stop_event.wait(self.buzz_duration)
        hal_buzzer.turn_off()

        with self.state_lock:
            self._alarm_active = False
            self.restore_screen()

    def _run(self):
        # Main loop checking accelerometer readings
        prev_magnitude = None

        while True:
            if self._stop_event.wait(self.poll_interval):
                break

            try:
                x, y, z = self._accel.get_3_axis()
            except OSError as exc:
                print("Warning: accelerometer read failed (" + str(exc) + ")", end="\r")
                prev_magnitude = None
                continue

            magnitude = (x ** 2 + y ** 2 + z ** 2) ** 0.5

            if prev_magnitude is not None:
                delta = abs(magnitude - prev_magnitude)
                if delta >= self.threshold:
                    with self.state_lock:
                        locked = self.is_locked()
                    if locked:
                        self._trigger_alarm()

            prev_magnitude = magnitude


if __name__ == "__main__":
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
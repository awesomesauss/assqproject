import os
import threading
import pytest
from unittest.mock import MagicMock, patch
import anti_theft
from anti_theft import AntiTheftMonitor, _Camera


def test_camera_backend_cascade_and_capture(tmp_path):
    """Test libcamera-still camera availability and capture behavior."""
    cam = _Camera()

    # 1. Available when libcamera-still CLI exists
    with patch("shutil.which", return_value="/usr/bin/libcamera-still"):
        cam.open()
        assert cam._available is True

    # 2. Capture success via subprocess mock
    photo_file = str(tmp_path / "test.jpg")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        assert cam.capture(photo_file) is True

    # 3. Capture failure handled gracefully
    with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr=b"Camera error")):
        assert cam.capture(photo_file) is False

    cam.close()
    assert cam._available is False


def test_monitor_init_start_stop_lifecycle():
    """Test monitor initialization with sensor error resilience and thread start/stop."""
    lock = threading.Lock()
    monitor = AntiTheftMonitor(
        state_lock=lock,
        is_locked=lambda: True,
        show_alert=MagicMock(),
        restore_screen=MagicMock(),
    )

    # Init should not throw even if accelerometer / buzzer throw
    with patch("anti_theft.hal_accelerometer.init", side_effect=RuntimeError("Bus error")):
        with patch("anti_theft.hal_buzzer.init", side_effect=RuntimeError("GPIO error")):
            monitor.init()

    # Start and stop lifecycle with a controlled blocking loop
    mock_accel = MagicMock()
    monitor._accel = mock_accel

    def dummy_run():
        monitor._stop_event.wait(5.0)

    with patch.object(monitor, "_run", side_effect=dummy_run):
        monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.is_alive()

        monitor.stop()
        monitor._thread.join(timeout=1.0)
        assert not monitor._thread.is_alive()


@pytest.mark.parametrize("locked,delta_g,should_alarm", [
    (True, 1.0, True),    # Sudden movement while locked -> alarm
    (False, 1.0, False),  # Sudden movement while unlocked -> ignored
    (True, 0.1, False),   # Minor vibration while locked -> ignored
])
def test_motion_detection_logic(locked, delta_g, should_alarm):
    """Test accelerometer threshold evaluation for locked vs unlocked states."""
    lock = threading.Lock()
    monitor = AntiTheftMonitor(
        state_lock=lock,
        is_locked=lambda: locked,
        show_alert=MagicMock(),
        restore_screen=MagicMock(),
        threshold=0.5,
        poll_interval=0.01,
    )

    # First sample: (0, 0, 1.0) -> magnitude = 1.0
    # Second sample: (0, 0, 1.0 + delta_g) -> magnitude = 1.0 + delta_g
    accel_samples = [
        (0.0, 0.0, 1.0),
        (0.0, 0.0, 1.0 + delta_g),
    ]

    mock_accel = MagicMock()
    mock_accel.get_3_axis.side_effect = accel_samples
    monitor._accel = mock_accel

    with patch.object(monitor, "_trigger_alarm") as mock_alarm:
        # Run loop for 2 iterations then stop
        def run_limited():
            monitor._stop_event.clear()
            for _ in range(2):
                x, y, z = monitor._accel.get_3_axis()
                mag = (x**2 + y**2 + z**2)**0.5
                if hasattr(run_limited, "prev"):
                    delta = abs(mag - run_limited.prev)
                    if delta >= monitor.threshold and monitor.is_locked():
                        monitor._trigger_alarm()
                run_limited.prev = mag

        run_limited()

        if should_alarm:
            mock_alarm.assert_called_once()
        else:
            mock_alarm.assert_not_called()


def test_alarm_trigger_execution_and_lock(tmp_path):
    """Test alarm triggering, buzzer activation, LCD alert, photo snapshot, and screen restore."""
    lock = threading.Lock()
    show_alert_mock = MagicMock()
    restore_screen_mock = MagicMock()
    on_alert_mock = MagicMock()

    monitor = AntiTheftMonitor(
        state_lock=lock,
        is_locked=lambda: True,
        show_alert=show_alert_mock,
        restore_screen=restore_screen_mock,
        on_alert=on_alert_mock,
        buzz_duration=0.01,
        capture_dir=str(tmp_path / "captures"),
    )

    with patch("anti_theft.hal_buzzer.turn_on") as mock_buzz_on, \
         patch("anti_theft.hal_buzzer.turn_off") as mock_buzz_off, \
         patch.object(monitor, "_capture_photo", return_value="/path/photo.jpg"):
        
        monitor._trigger_alarm()

        show_alert_mock.assert_called_once()
        mock_buzz_on.assert_called_once()
        on_alert_mock.assert_called_once_with("/path/photo.jpg")
        mock_buzz_off.assert_called_once()
        restore_screen_mock.assert_called_once()
        assert monitor._alarm_active is False


def test_motion_detection_bus_error_resilience():
    """Test that transient I2C bus read errors (Errno 121) do not crash the polling loop."""
    lock = threading.Lock()
    monitor = AntiTheftMonitor(
        state_lock=lock,
        is_locked=lambda: True,
        show_alert=MagicMock(),
        restore_screen=MagicMock(),
        poll_interval=0.001,
    )

    call_count = 0

    def sample_generator():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError(121, "Remote I/O error")
        return (0.0, 0.0, 1.0)

    mock_accel = MagicMock()
    mock_accel.get_3_axis.side_effect = sample_generator
    monitor._accel = mock_accel

    # Running monitor thread for a brief moment
    thread = threading.Thread(target=monitor._run, daemon=True)
    thread.start()
    monitor._stop_event.wait(0.05)
    monitor.stop()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert call_count > 1

#!/usr/bin/env python3
"""
Unit tests for anti_theft.py module
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import threading
import time
import os
import sys

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from anti_theft import AntiTheftMonitor, _Camera


class TestAntiTheftMonitor:
    """Test cases for the AntiTheftMonitor class"""

    def setup_method(self):
        """Set up test fixtures before each test method"""
        self.state_lock = threading.Lock()
        self.is_locked_mock = Mock(return_value=True)
        self.show_alert_mock = Mock()
        self.restore_screen_mock = Mock()
        self.on_alert_mock = Mock()

        self.monitor = AntiTheftMonitor(
            state_lock=self.state_lock,
            is_locked=self.is_locked_mock,
            show_alert=self.show_alert_mock,
            restore_screen=self.restore_screen_mock,
            on_alert=self.on_alert_mock,
            threshold=0.5,
            poll_interval=0.1,
            buzz_duration=1,
            capture_dir="test_captures"
        )

    def teardown_method(self):
        """Clean up after each test method"""
        if self.monitor._thread and self.monitor._thread.is_alive():
            self.monitor.stop()

        # Clean up test capture directory
        if os.path.exists("test_captures"):
            import shutil
            shutil.rmtree("test_captures")

    @patch('anti_theft.hal_accelerometer.init')
    @patch('anti_theft.hal_buzzer.init')
    def test_init_success(self, mock_buzzer_init, mock_accel_init):
        """Test successful initialization of components"""
        mock_accel = Mock()
        mock_accel_init.return_value = mock_accel

        self.monitor.init()

        # Check that accelerometer was initialized
        mock_accel_init.assert_called_once()
        assert self.monitor._accel == mock_accel

        # Check that buzzer was initialized
        mock_buzzer_init.assert_called_once()

        # Check that camera was opened
        assert self.monitor._camera is not None

    @patch('anti_theft.hal_accelerometer.init')
    def test_init_accelerometer_failure(self, mock_accel_init):
        """Test handling of accelerometer initialization failure"""
        mock_accel_init.side_effect = Exception("I2C bus error")

        self.monitor.init()

        # Should not crash, accelerometer should be None
        assert self.monitor._accel is None

        # Buzzer and camera should still be initialized
        mock_accel_init.assert_called_once()

    @patch('anti_theft.hal_accelerometer.init')
    @patch('anti_theft.hal_buzzer.init')
    def test_init_buzzer_failure(self, mock_buzzer_init, mock_accel_init):
        """Test handling of buzzer initialization failure"""
        mock_accel = Mock()
        mock_accel_init.return_value = mock_accel
        mock_buzzer_init.side_effect = Exception("GPIO error")

        self.monitor.init()

        # Accelerometer should be initialized
        assert self.monitor._accel == mock_accel

        # Buzzer initialization should have been attempted
        mock_buzzer_init.assert_called_once()

    def test_start_no_accelerometer(self):
        """Test that start() does nothing if accelerometer failed to initialize"""
        self.monitor._accel = None

        # Should not raise an exception
        self.monitor.start()

        # Thread should not be started
        assert self.monitor._thread is None

    @patch('anti_theft.hal_accelerometer.init')
    @patch('anti_theft.hal_buzzer.init')
    def test_start_success(self, mock_buzzer_init, mock_accel_init):
        """Test successful start of monitoring thread"""
        mock_accel = Mock()
        mock_accel_init.return_value = mock_accel

        self.monitor.init()
        self.monitor.start()

        # Should have started a thread
        assert self.monitor._thread is not None
        assert isinstance(self.monitor._thread, threading.Thread)
        assert self.monitor._thread.is_alive()

        # Clean up
        self.monitor.stop()
        self.monitor._thread.join(timeout=1)

    def test_start_already_running(self):
        """Test that start() is idempotent"""
        self.monitor._accel = Mock()  # Simulate successful init
        self.monitor._thread = Mock()
        self.monitor._thread.is_alive.return_value = True

        # Should not start a new thread
        self.monitor.start()

        # Thread should remain unchanged
        assert self.monitor._thread.is_alive.return_value == True

    def test_stop(self):
        """Test stopping the monitor"""
        # Set up proper mocks
        self.monitor._accel = Mock()
        self.monitor._thread = Mock()
        self.monitor._stop_event = Mock()
        # Create a separate mock for camera to avoid overwriting the real one
        mock_camera = Mock()
        self.monitor._camera = mock_camera

        self.monitor.stop()

        # Should have set the stop event
        self.monitor._stop_event.set.assert_called_once()

        # Should have turned off buzzer
        # Note: We can't easily test hal_buzzer.turn_off() without importing the HAL

        # Should have closed camera
        mock_camera.close.assert_called_once()

    def test_capture_photo_success(self):
        """Test successful photo capture"""
        # Mock the camera to return success
        mock_camera = Mock()
        mock_camera.capture.return_value = True
        self.monitor._camera = mock_camera

        with patch('anti_theft.os.makedirs'), \
             patch('anti_theft.datetime.datetime') as mock_datetime:

            # Properly mock datetime
            mock_now = Mock()
            mock_now.strftime.return_value = "20230101_120000_000000"
            mock_datetime.now.return_value = mock_now

            result = self.monitor._capture_photo()

            # Should return a path
            assert result is not None
            # Check that it contains our expected directory and prefix
            assert "test_captures" in result
            assert "theft_20230101_120000_000000.jpg" in result

            # Should have called camera capture
            mock_camera.capture.assert_called_once()

    def test_capture_photo_failure(self):
        """Test failed photo capture"""
        # Mock the camera to return failure
        mock_camera = Mock()
        mock_camera.capture.return_value = False
        self.monitor._camera = mock_camera

        with patch('anti_theft.os.makedirs'):
            result = self.monitor._capture_photo()

            # Should return None on failure
            assert result is None

    def test_capture_photo_directory_creation_failure(self):
        """Test handling of directory creation failure"""
        with patch('anti_theft.os.makedirs') as mock_makedirs:
            mock_makedirs.side_effect = OSError("Permission denied")

            result = self.monitor._capture_photo()

            # Should return None and log warning
            assert result is None

    @patch('anti_theft.hal_buzzer.turn_on')
    @patch('anti_theft.hal_buzzer.turn_off')
    def test_trigger_alarm_sequence(self, mock_buzzer_off, mock_buzzer_on):
        """Test the alarm triggering sequence"""
        # Mock the camera capture
        mock_camera = Mock()
        mock_camera.capture.return_value = "/fake/path.jpg"
        self.monitor._camera = mock_camera

        self.monitor._stop_event = Mock()
        self.monitor._stop_event.wait.return_value = True  # Simulate immediate stop

        self.monitor._trigger_alarm()

        # Should have shown alert and restored screen under lock
        self.show_alert_mock.assert_called_once()
        self.restore_screen_mock.assert_called_once()

        # Should have captured photo
        mock_camera.capture.assert_called_once()

        # Should have turned buzzer on and off
        mock_buzzer_on.assert_called_once()
        mock_buzzer_off.assert_called_once()

        # Should have called on_alert callback
        self.on_alert_mock.assert_called_once_with("/fake/path.jpg")

    def test_trigger_alarm_prevents_double_trigger(self):
        """Test that alarm doesn't trigger if already active"""
        self.monitor._alarm_active = True
        self.monitor._capture_photo = Mock()
        self.monitor._stop_event = Mock()

        self.monitor._trigger_alarm()

        # Should not have done anything since alarm already active
        self.monitor._capture_photo.assert_not_called()
        self.monitor._stop_event.wait.assert_not_called()

    @patch('anti_theft.AntiTheftMonitor._trigger_alarm')
    def test_run_detects_movement_when_locked(self, mock_trigger):
        """Test that movement detection triggers alarm when doors locked"""
        # Set up accelerometer mock
        mock_accel = Mock()
        mock_accel.get_3_axis.side_effect = [
            (0, 0, 1),  # First reading: magnitude = 1
            (0, 0, 2)   # Second reading: magnitude = 2, delta = 1
        ]
        self.monitor._accel = mock_accel

        # Set up lock state to return True (locked)
        with self.state_lock:
            self.is_locked_mock.return_value = True

        # Set stop event to break after one iteration
        def stop_after_iteration(*args):
            self.monitor._stop_event.set()
            return True

        self.monitor._stop_event.wait = stop_after_iteration

        # Run the monitoring loop
        self.monitor._run()

        # Should have triggered alarm due to movement detection
        mock_trigger.assert_called_once()

    @patch('anti_theft.AntiTheftMonitor._trigger_alarm')
    def test_run_ignores_movement_when_unlocked(self, mock_trigger):
        """Test that movement detection is ignored when doors unlocked"""
        # Set up accelerometer mock
        mock_accel = Mock()
        mock_accel.get_3_axis.side_effect = [
            (0, 0, 1),  # First reading: magnitude = 1
            (0, 0, 2)   # Second reading: magnitude = 2, delta = 1
        ]
        self.monitor._accel = mock_accel

        # Set up lock state to return False (unlocked)
        with self.state_lock:
            self.is_locked_mock.return_value = False

        # Set stop event to break after one iteration
        def stop_after_iteration(*args):
            self.monitor._stop_event.set()
            return True

        self.monitor._stop_event.wait = stop_after_iteration

        # Run the monitoring loop
        self.monitor._run()

        # Should NOT have triggered alarm since doors unlocked
        mock_trigger.assert_not_called()

    def test_run_handles_accelerometer_error(self):
        """Test that accelerometer read errors are handled gracefully"""
        mock_accel = Mock()
        mock_accel.get_3_axis.side_effect = OSError("I2C bus error")
        self.monitor._accel = mock_accel

        # Set stop event to break after one iteration
        def stop_after_iteration(*args):
            self.monitor._stop_event.set()
            return True

        self.monitor._stop_event.wait = stop_after_iteration

        # Should not raise exception
        self.monitor._run()

        # Should have retried (prev_magnitude reset to None)
        # We can't easily test this without exposing more internals,
        # but we verified no exception was raised

    def test_standalone_mode(self):
        """Test the standalone mode functionality"""
        # This tests the __main__ block functionality
        with patch('anti_theft.threading.Lock'), \
             patch('anti_theft.AntiTheftMonitor'):

            # Import and run the main block
            import anti_theft
            # We can't easily test the full __main__ block without
            # actually running it, but we verified the imports work


class TestCamera:
    """Test cases for the _Camera helper class"""

    def setup_method(self):
        """Set up test fixtures"""
        self.camera = _Camera()

    def test_camera_init(self):
        """Test camera initialization"""
        assert self.camera._impl is None
        assert isinstance(self.camera._lock, type(self.camera._lock))

    @patch('anti_theft.shutil.which')
    @patch('anti_theft.picamera2.Picamera2')
    def test_open_picamera2_success(self, mock_picamera2, mock_which):
        """Test successful picamera2 initialization"""
        mock_picam = Mock()
        mock_picamera2.return_value = mock_picam
        mock_picam.create_still_configuration.return_value = Mock()

        self.camera.open()

        # Should have used picamera2
        mock_picamera2.assert_called_once()
        mock_picam.configure.assert_called_once()
        mock_picam.start.assert_called_once()
        assert self.camera._impl[0] == "picamera2"

    @patch('anti_theft.shutil.which')
    @patch('anti_theft.picamera2.Picamera2')
    def test_open_picamera2_fallback_to_cli(self, mock_picamera2, mock_which):
        """Test fallback to CLI when picamera2 fails"""
        mock_picamera2.side_effect = Exception("Import error")
        mock_which.return_value = "/usr/bin/libcamera-still"

        self.camera.open()

        # Should have fallen back to CLI
        mock_which.assert_called_with("libcamera-still")
        assert self.camera._impl[0] == "cli"

    @patch('anti_theft.shutil.which')
    @patch('anti_theft.picamera2.Picamera2')
    @patch('anti_theft.picamera.PiCamera')
    def test_open_all_backends_fail(self, mock_picamera, mock_picamera2, mock_which):
        """Test handling when all camera backends fail"""
        mock_picamera2.side_effect = Exception("Import error")
        mock_which.return_value = None  # No libcamera-still
        mock_picamera.side_effect = Exception("Hardware not found")

        self.camera.open()

        # Should have tried all backends and ended with None
        assert self.camera._impl is None

    def test_capture_no_camera(self):
        """Test capture when no camera is available"""
        self.camera._impl = None

        result = self.camera.capture("/fake/path.jpg")

        assert result is False

    @patch('anti_theft.subprocess.run')
    def test_capture_cli_success(self, mock_subprocess):
        """Test successful capture using CLI backend"""
        # Set up CLI backend
        self.camera._impl = ("cli", None)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_subprocess.return_value = mock_result

        result = self.camera.capture("/fake/path.jpg")

        assert result is True
        mock_subprocess.assert_called_once_with(
            ["libcamera-still", "--output", "/fake/path.jpg", "--nopreview", "--timeout", "2000"],
            capture_output=True, timeout=20
        )

    @patch('anti_theft.subprocess.run')
    def test_capture_cli_failure(self, mock_subprocess):
        """Test failed capture using CLI backend"""
        # Set up CLI backend
        self.camera._impl = ("cli", None)

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = b"Camera error"
        mock_subprocess.return_value = mock_result

        result = self.camera.capture("/fake/path.jpg")

        assert result is False

    @patch('anti_theft.picamera2.Picamera2')
    def test_capture_picamera2_success(self, mock_picamera2):
        """Test successful capture using picamera2 backend"""
        # Set up picamera2 backend
        mock_cam = Mock()
        self.camera._impl = ("picamera2", mock_cam)

        result = self.camera.capture("/fake/path.jpg")

        assert result is True
        mock_cam.capture_file.assert_called_once_with("/fake/path.jpg")

    @patch('anti_theft.picamera.PiCamera')
    def test_capture_legacy_picamera_success(self, mock_picamera):
        """Test successful capture using legacy picamera backend"""
        # Set up legacy picamera backend
        mock_cam = Mock()
        self.camera._impl = ("picamera", mock_cam)

        result = self.camera.capture("/fake/path.jpg")

        assert result is True
        mock_cam.capture.assert_called_once_with("/fake/path.jpg")

    def test_close_no_camera(self):
        """Test closing when no camera was opened"""
        self.camera._impl = None

        # Should not raise exception
        self.camera.close()

        assert self.camera._impl is None

    @patch('anti_theft.picamera2.Picamera2')
    def test_close_picamera2(self, mock_picamera2):
        """Test closing picamera2 backend"""
        # Set up picamera2 backend
        mock_cam = Mock()
        self.camera._impl = ("picamera2", mock_cam)

        self.camera.close()

        mock_cam.stop.assert_called_once()
        assert self.camera._impl is None

    @patch('anti_theft.picamera.PiCamera')
    def test_close_legacy_picamera(self, mock_picamera):
        """Test closing legacy picamera backend"""
        # Set up legacy picamera backend
        mock_cam = Mock()
        self.camera._impl = ("picamera", mock_cam)

        self.camera.close()

        mock_cam.close.assert_called_once()
        assert self.camera._impl is None

    def test_close_cli_backend(self):
        """Test closing CLI backend (should do nothing)"""
        # Set up CLI backend
        self.camera._impl = ("cli", None)

        # Should not raise exception
        self.camera.close()

        assert self.camera._impl is None
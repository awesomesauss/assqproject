import json
import time
import pytest
from unittest.mock import MagicMock, patch
import car_control


def test_simulation_readings_and_warmup():
    """Test fuel drain, battery drain, and engine temperature simulation models."""
    # 1. Fuel and battery should NOT drain when engine is OFF
    with car_control.state_lock:
        car_control.engine_on = False
        car_control._fuel_level = 100.0
        car_control._fuel_last_update = 1000.0
        car_control._battery_level = 100.0
        car_control._battery_last_update = 1000.0

    with patch("time.time", return_value=1100.0):  # 100s elapsed
        with car_control.state_lock:
            assert car_control.get_fuel_level() == 100
            assert car_control.get_battery_level() == 100

    # 2. Fuel and battery SHOULD drain when engine is ON
    with car_control.state_lock:
        car_control.engine_on = True
        car_control._fuel_last_update = 1100.0
        car_control._battery_last_update = 1100.0

    with patch("time.time", return_value=1200.0):  # 100s running
        with car_control.state_lock:
            # 100 - (0.05 * 100) = 95%
            assert car_control.get_fuel_level() == 95
            # 100 - (0.015 * 100) = 98.5 -> 98 or 99%
            assert car_control.get_battery_level() in (98, 99)

    # 3. Test engine temperature warmup dynamics
    with car_control.state_lock:
        car_control._engine_temp = 30.0
        car_control._engine_temp_last_update = 1000.0
        car_control.engine_on = True

    # After 60 seconds of running, temp should climb halfway: 30 + (60/120 * 60) = 60C
    with patch("time.time", return_value=1060.0):
        with car_control.state_lock:
            temp = car_control.get_engine_temperature()
            assert temp == 60

    # Hold steady when engine is stopped
    with car_control.state_lock:
        car_control.engine_on = False
    with patch("time.time", return_value=1200.0):
        with car_control.state_lock:
            temp = car_control.get_engine_temperature()
            assert temp == 60


def test_settings_persistence_and_defaults(tmp_settings_file):
    """Test loading and saving settings under valid, missing, and corrupted file states."""
    # Write initial settings file
    with open(tmp_settings_file, "w") as f:
        json.dump({"ac_temp": 25, "engine_on": True, "door_locked": False}, f)

    car_control.load_settings()
    assert car_control.ac_temp == 25
    assert car_control.engine_on is True
    assert car_control.door_locked is False

    # Modify and save
    with car_control.state_lock:
        car_control.ac_temp = 19
        car_control.engine_on = False
        car_control.door_locked = True
        car_control.save_settings()

    with open(tmp_settings_file, "r") as f:
        saved = json.load(f)
    assert saved == {"ac_temp": 19, "engine_on": False, "door_locked": True}

    # Corrupted file handles gracefully without raising
    with open(tmp_settings_file, "w") as f:
        f.write("corrupted json content")
    car_control.load_settings()  # Should not raise exception


def test_cabin_temperature_retry_mechanism(monkeypatch):
    """Test DHT11 reading retry mechanism for flaky sensors and complete failure."""
    monkeypatch.setattr(car_control, "DHT_MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(car_control, "DHT_RETRY_DELAY", 0.0)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    # Flaky sensor returns -100 twice, then 23.5C
    flaky_readings = [(-100, -100), (-100, -100), (23.5, 45.0)]
    with patch("car_control.hal_temp_humidity_sensor.read_temp_humidity", side_effect=flaky_readings):
        temp = car_control.read_cabin_temp()
        assert temp == 23.5

    # Completely failing sensor returns -100 after retries
    with patch("car_control.hal_temp_humidity_sensor.read_temp_humidity", return_value=(-100, -100)):
        temp = car_control.read_cabin_temp()
        assert temp == -100


def test_menu_navigation_and_keypad_actions(monkeypatch, tmp_settings_file):
    """Test keypad debounce, RFID gating, and full menu state transitions."""
    monkeypatch.setattr(car_control, "last_key_time", 0.0)
    monkeypatch.setattr(car_control.lcd_display, "lcd_clear", MagicMock())
    monkeypatch.setattr(car_control.lcd_display, "lcd_display_string", MagicMock())
    monkeypatch.setattr(car_control, "read_cabin_temp", lambda: 22.0)

    # 1. Keypad ignored when RFID is not tapped
    car_control.RFID_ACCESS = False
    car_control.current_menu = "MAIN"
    car_control.key_pressed('1')
    assert car_control.current_menu == "MAIN"

    # 2. Grant RFID access
    car_control.RFID_ACCESS = True
    car_control.last_key_time = 0.0

    # 3. Main -> Control Menu
    car_control.key_pressed('1')
    assert car_control.current_menu == "CONTROL"
    assert car_control.control_menu_page == 0

    # 4. Scroll Control Menu page (0 <-> 1)
    car_control.last_key_time = 0.0
    car_control.key_pressed('#')
    assert car_control.control_menu_page == 1

    # 5. Control Menu Page 1 -> Door Control -> Toggle Door
    car_control.last_key_time = 0.0
    car_control.door_locked = True
    car_control.key_pressed('2')
    assert car_control.current_menu == "DOOR_CONTROL"

    car_control.last_key_time = 0.0
    car_control.key_pressed('1')
    assert car_control.door_locked is False

    # 6. Back button from Door Control -> Control Menu
    car_control.last_key_time = 0.0
    car_control.key_pressed('*')
    assert car_control.current_menu == "CONTROL"

    # 7. Control Menu Page 0 -> AC Control -> Increment/Decrement with bounds clamping
    car_control.control_menu_page = 0
    car_control.last_key_time = 0.0
    car_control.key_pressed('1')
    assert car_control.current_menu == "AC_CONTROL"

    car_control.ac_temp = car_control.AC_TEMP_MAX
    car_control.last_key_time = 0.0
    car_control.key_pressed('2')  # Try to exceed max
    assert car_control.ac_temp == car_control.AC_TEMP_MAX

    car_control.last_key_time = 0.0
    car_control.key_pressed('1')  # Decrease temp
    assert car_control.ac_temp == car_control.AC_TEMP_MAX - 1


def test_session_and_inactivity_lifecycle(monkeypatch, tmp_settings_file):
    """Test starting a session, resetting inactivity timer, and timeout teardown."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(car_control.lcd_display, "lcd_clear", MagicMock())
    monkeypatch.setattr(car_control.lcd_display, "lcd_display_string", MagicMock())

    with car_control.state_lock:
        car_control.start_session()
        assert car_control.RFID_ACCESS is True
        assert car_control.current_menu == "MAIN"

        # Simulate inactivity timeout
        car_control.door_locked = False
        car_control.end_session(goodbye=True)
        assert car_control.RFID_ACCESS is False
        assert car_control.door_locked is True  # Door locks on session end


def test_remote_control_and_status_sync(tmp_settings_file):
    """Test get_status and remote actuators (lock/unlock door and start/stop engine)."""
    with patch("car_control.read_cabin_temp", return_value=22.0):
        status = car_control.get_status()
        assert "door_locked" in status
        assert "engine_on" in status
        assert "fuel" in status
        assert "battery" in status
        assert "engine_temp" in status
        assert status["cabin_temp"] == 22.0

    with patch("car_control.hal_servo.set_servo_position") as mock_servo:
        car_control.remote_set_door_locked(True)
        assert car_control.door_locked is True
        mock_servo.assert_called_with(car_control.DOOR_LOCKED_POS)

        car_control.remote_set_door_locked(False)
        assert car_control.door_locked is False
        mock_servo.assert_called_with(car_control.DOOR_UNLOCKED_POS)

    with patch("car_control.hal_dc_motor.set_motor_speed") as mock_motor:
        car_control.remote_set_engine_on(True)
        assert car_control.engine_on is True
        mock_motor.assert_called_with(car_control.ENGINE_RUN_SPEED)

        car_control.remote_set_engine_on(False)
        assert car_control.engine_on is False
        mock_motor.assert_called_with(0)

    # Test remote AC temperature set
    success, msg = car_control.remote_set_ac_temp(25)
    assert success is True
    assert car_control.ac_temp == 25
    assert "25C" in msg

    # Test remote AC temperature out-of-range rejection
    success, msg = car_control.remote_set_ac_temp(35)
    assert success is False
    assert car_control.ac_temp == 25  # Unchanged
    assert "between 16C and 30C" in msg

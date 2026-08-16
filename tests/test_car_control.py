#!/usr/bin/env python3
"""
Unit tests for car_control.py module
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import os
import sys
import threading
import time

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the module - we'll need to mock the HAL imports
with patch.dict('sys.modules', {
    'hal.hal_keypad': Mock(),
    'hal.hal_lcd': Mock(),
    'hal.hal_rfid_reader': Mock(),
    'hal.hal_temp_humidity_sensor': Mock(),
    'hal.hal_dc_motor': Mock(),
    'hal.hal_servo': Mock(),
    'anti_theft': Mock(),
    'tele': Mock()
}):
    import car_control


class TestCarControlFuelBattery:
    """Test cases for fuel and battery level functions"""

    def test_get_fuel_level(self):
        """Test fuel level calculation"""
        # Mock time.time to return a fixed value for consistent testing
        with patch('car_control.time.time') as mock_time:
            # Set SIM_START_TIME and current time to known values
            mock_time.return_value = 1000.0
            car_control.SIM_START_TIME = 900.0  # 100 seconds elapsed

            level = car_control.get_fuel_level()

            # FUEL_LEVEL_START = 100.0, FUEL_DRAIN_RATE = 0.05
            # After 100 seconds: 100 - (0.05 * 100) = 100 - 5 = 95
            assert level == 95

    def test_get_fuel_level_minimum_zero(self):
        """Test that fuel level doesn't go below zero"""
        with patch('car_control.time.time') as mock_time:
            # Set time far in the future to drain all fuel
            mock_time.return_value = 3000.0
            car_control.SIM_START_TIME = 0.0  # 3000 seconds elapsed

            level = car_control.get_fuel_level()

            # Should be 0, not negative
            assert level == 0

    def test_get_battery_level(self):
        """Test battery level calculation"""
        with patch('car_control.time.time') as mock_time:
            mock_time.return_value = 1000.0
            car_control.SIM_START_TIME = 900.0  # 100 seconds elapsed

            level = car_control.get_battery_level()

            # BATTERY_LEVEL_START = 100.0, BATTERY_DRAIN_RATE = 0.015
            # After 100 seconds: 100 - (0.015 * 100) = 100 - 1.5 = 98.5 -> round to 99
            assert level == 99

    def test_get_battery_level_minimum_zero(self):
        """Test that battery level doesn't go below zero"""
        with patch('car_control.time.time') as mock_time:
            mock_time.return_value = 10000.0
            car_control.SIM_START_TIME = 0.0  # 10000 seconds elapsed

            level = car_control.get_battery_level()

            # Should be 0, not negative
            assert level == 0


class TestCarControlEngineTemperature:
    """Test cases for engine temperature function"""

    def setup_method(self):
        """Reset engine temperature globals before each test"""
        car_control._engine_temp = float(car_control.ENGINE_TEMP_START)
        car_control._engine_temp_last_update = time.time()

    def test_get_engine_temperature_engine_off(self):
        """Test engine temperature when engine is off"""
        car_control.engine_on = False

        # Should return the last known temperature (no change when off)
        temp = car_control.get_engine_temperature()

        # With engine off, temperature should hold steady
        assert temp == int(round(car_control.ENGINE_TEMP_START))

    def test_get_engine_temperature_engine_on_warming_up(self):
        """Test engine temperature when engine is on and warming up"""
        car_control.engine_on = True
        car_control._engine_temp = float(car_control.ENGINE_TEMP_START)

        # Mock time to simulate elapsed time
        with patch('car_control.time.time') as mock_time:
            # Set last update 60 seconds ago (halfway to max temp)
            car_control._engine_temp_last_update = time.time() - 60
            mock_time.return_value = time.time()

            temp = car_control.get_engine_temperature()

            # Should have increased from start temp
            expected_min = car_control.ENGINE_TEMP_START
            expected_max = car_control.ENGINE_TEMP_MAX
            assert expected_min <= temp <= expected_max
            assert temp > car_control.ENGINE_TEMP_START  # Should have increased

    def test_get_engine_temperature_engine_on_at_max(self):
        """Test engine temperature when engine is at maximum"""
        car_control.engine_on = True
        car_control._engine_temp = float(car_control.ENGINE_TEMP_MAX)
        car_control._engine_temp_last_update = time.time()

        temp = car_control.get_engine_temperature()

        # Should stay at max temperature
        assert temp == car_control.ENGINE_TEMP_MAX

    def test_get_engine_temperature_reset_when_engine_stops(self):
        """Test that temperature resets when engine stops"""
        # First, simulate engine running and warming up
        car_control.engine_on = True
        car_control._engine_temp = 50.0  # Some intermediate value
        car_control._engine_temp_last_update = time.time()

        # Now turn engine off
        car_control.engine_on = False

        # The temperature variable should be reset to start temp
        # This happens in remote_set_engine_off, but let's test the get function behavior
        # when engine is off (it should hold the last value)

        with patch('car_control.time.time') as mock_time:
            mock_time.return_value = car_control._engine_temp_last_update + 10

            temp = car_control.get_engine_temperature()

            # When engine is off, temperature should hold steady (not reset in get function)
            # The reset happens in remote_set_engine_off
            assert temp == 50  # Should hold the last value when engine off


class TestCarControlSettingsPersistence:
    """Test cases for settings persistence"""

    def setup_method(self):
        """Backup original settings and clear settings file"""
        self.original_settings = getattr(car_control, 'SETTINGS_FILE', 'car_settings.json')
        self.test_settings_file = 'test_car_settings.json'
        car_control.SETTINGS_FILE = self.test_settings_file

        # Remove test settings file if it exists
        if os.path.exists(self.test_settings_file):
            os.remove(self.test_settings_file)

    def teardown_method(self):
        """Restore original settings and clean up"""
        car_control.SETTINGS_FILE = self.original_settings
        if os.path.exists(self.test_settings_file):
            os.remove(self.test_settings_file)

    def test_load_settings_file_not_exists(self):
        """Loading settings when file doesn't exist should use defaults"""
        # Ensure settings file doesn't exist
        if os.path.exists(self.test_settings_file):
            os.remove(self.test_settings_file)

        # Reset to defaults
        car_control.ac_temp = car_control.AC_TEMP_DEFAULT
        car_control.engine_on = False
        car_control.door_locked = True

        # Load settings (should not change anything)
        car_control.load_settings()

        # Values should remain at defaults
        assert car_control.ac_temp == car_control.AC_TEMP_DEFAULT
        assert car_control.engine_on == False
        assert car_control.door_locked == True

    def test_load_settings_with_valid_file(self):
        """Loading settings from a valid file"""
        # Create test settings file
        test_data = {
            "ac_temp": 25,
            "engine_on": True,
            "door_locked": False
        }

        with open(self.test_settings_file, 'w') as f:
            json.dump(test_data, f)

        # Load settings
        car_control.load_settings()

        # Values should match what we saved
        assert car_control.ac_temp == 25
        assert car_control.engine_on == True
        assert car_control.door_locked == False

    def test_load_settings_partial_file(self):
        """Loading settings from a partial file (some missing values)"""
        # Create test settings file with only some values
        test_data = {
            "ac_temp": 28
            # Missing engine_on and door_locked
        }

        with open(self.test_settings_file, 'w') as f:
            json.dump(test_data, f)

        # Set initial values to something different
        car_control.ac_temp = 20
        car_control.engine_on = True
        car_control.door_locked = False

        # Load settings
        car_control.load_settings()

        # ac_temp should be updated, others should remain unchanged
        assert car_control.ac_temp == 28
        assert car_control.engine_on == True  # Unchanged
        assert car_control.door_locked == False  # Unchanged

    def test_load_settings_invalid_json(self):
        """Loading settings from invalid JSON file should not crash"""
        # Create invalid JSON file
        with open(self.test_settings_file, 'w') as f:
            f.write("{ invalid json }")

        # Set initial values
        car_control.ac_temp = 20
        car_control.engine_on = True
        car_control.door_locked = False

        # Load settings (should not crash)
        car_control.load_settings()

        # Values should remain unchanged
        assert car_control.ac_temp == 20
        assert car_control.engine_on == True
        assert car_control.door_locked == False

    def test_save_settings(self):
        """Saving settings should create valid JSON file"""
        # Set known values
        car_control.ac_temp = 22
        car_control.engine_on = True
        car_control.door_locked = False

        # Save settings
        car_control.save_settings()

        # File should exist and contain correct data
        assert os.path.exists(self.test_settings_file)

        with open(self.test_settings_file, 'r') as f:
            data = json.load(f)

        assert data["ac_temp"] == 22
        assert data["engine_on"] == True
        assert data["door_locked"] == False

    def test_save_settings_io_error(self):
        """Handling of IO error during settings save"""
        # Make directory read-only to cause IO error (on Windows this might not work as expected)
        # Instead, we'll mock the open function to raise an exception
        with patch('builtins.open', side_effect=OSError("Disk full")):
            # Should not raise exception
            car_control.save_settings()
            # If we get here without exception, the test passes


class TestCarControlMenuDisplay:
    """Test cases for menu display functions"""

    def setup_method(self):
        """Mock LCD display before each test"""
        self.lcd_mock = Mock()
        car_control.lcd_display = self.lcd_mock

    def test_display_idle(self):
        """Test idle screen display"""
        car_control.display_idle()

        # Should clear display and show two lines
        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("RFID ACCESS", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("REQUIRED", 2)

    def test_display_goodbye(self):
        """Test goodbye screen display"""
        car_control.display_goodbye()

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("Goodbye!", 1)

    def test_display_main_menu(self):
        """Test main menu display"""
        car_control.display_main_menu()

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("1:Control Sys", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("2:Monitor Sys", 2)

    def test_display_control_menu_page_0(self):
        """Test control menu display - page 0"""
        car_control.display_control_menu(page=0)

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("AC/Heat Control", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("Start Engine", 2)

    def test_display_control_menu_page_1(self):
        """Test control menu display - page 1"""
        car_control.display_control_menu(page=1)

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("Start Engine", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("Lock/Unlock Door", 2)

    def test_display_monitor_menu(self):
        """Test monitor menu display"""
        car_control.display_monitor_menu(page=0)

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("1:Battery/Fuel", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("2:Engine Temp", 2)

    def test_display_fuel_battery_levels_no_change(self):
        """Test fuel/battery display skips redraw when values unchanged"""
        # Set last shown values to current values
        car_control._last_fuel_battery_shown = (50, 80)

        # Mock the get functions to return same values
        with patch('car_control.get_fuel_level', return_value=50), \
             patch('car_control.get_battery_level', return_value=80):

            car_control.display_fuel_battery_levels()

            # Should not have cleared or updated display (values unchanged)
            car_control.lcd_display.lcd_clear.assert_not_called()
            car_control.lcd_display.lcd_display_string.assert_not_called()

    def test_display_fuel_battery_levels_changed(self):
        """Test fuel/battery display updates when values change"""
        # Set last shown values to different values
        car_control._last_fuel_battery_shown = (40, 70)

        # Mock the get functions to return new values
        with patch('car_control.get_fuel_level', return_value=50), \
             patch('car_control.get_battery_level', return_value=80):

            car_control.display_fuel_battery_levels()

            # Should have updated display
            car_control.lcd_display.lcd_clear.assert_called_once()
            car_control.lcd_display.lcd_display_string.assert_any_call("Fuel: 50%", 1)
            car_control.lcd_display.lcd_display_string.assert_any_call("Battery: 80%", 2)

            # Should have updated last shown values
            assert car_control._last_fuel_battery_shown == (50, 80)

    def test_display_fuel_battery_levels_force(self):
        """Test fuel/battery display with force=True always updates"""
        # Set last shown values to same as current (would normally skip)
        car_control._last_fuel_battery_shown = (50, 80)

        # Mock the get functions to return same values
        with patch('car_control.get_fuel_level', return_value=50), \
             patch('car_control.get_battery_level', return_value=80):

            car_control.display_fuel_battery_levels(force=True)

            # Should have updated display despite no change (due to force=True)
            car_control.lcd_display.lcd_clear.assert_called_once()
            car_control.lcd_display.lcd_display_string.assert_any_call("Fuel: 50%", 1)
            car_control.lcd_display.lcd_display_string.assert_any_call("Battery: 80%", 2)

    def test_display_engine_temp_reading_no_change(self):
        """Test engine temp display skips redraw when value unchanged"""
        car_control._last_engine_temp_shown = 75

        with patch('car_control.get_engine_temperature', return_value=75):
            car_control.display_engine_temp_reading()

            car_control.lcd_display.lcd_clear.assert_not_called()
            car_control.lcd_display.lcd_display_string.assert_not_called()

    def test_display_engine_temp_reading_changed(self):
        """Test engine temp display updates when value changes"""
        car_control._last_engine_temp_shown = 70

        with patch('car_control.get_engine_temperature', return_value=75):
            car_control.display_engine_temp_reading()

            car_control.lcd_display.lcd_clear.assert_called_once()
            car_control.lcd_display.lcd_display_string.assert_any_call("Engine Temp:", 1)
            car_control.lcd_display.lcd_display_string.assert_any_call("75C  *:Back", 2)

            assert car_control._last_engine_temp_shown == 75

    def test_display_engine_temp_reading_force(self):
        """Test engine temp display with force=True"""
        car_control._last_engine_temp_shown = 75  # Same as current

        with patch('car_control.get_engine_temperature', return_value=75):
            car_control.display_engine_temp_reading(force=True)

            # Should update despite no change
            car_control.lcd_display.lcd_clear.assert_called_once()
            car_control.lcd_display.lcd_display_string.assert_any_call("Engine Temp:", 1)
            car_control.lcd_display.lcd_display_string.assert_any_call("75C  *:Back", 2)

    def test_display_ac_control_valid_temp(self):
        """Test AC control display with valid cabin temp"""
        car_control.ac_temp = 22

        with patch('car_control.read_cabin_temp', return_value=24.5):
            car_control.display_ac_control(22)

            car_control.lcd_display.lcd_clear.assert_called_once()
            car_control.lcd_display.lcd_display_string.assert_any_call("Set:22C", 1)
            car_control.lcd_display.lcd_display_string.assert_any_call("Cabin:24.5C", 2)

    def test_display_ac_control_invalid_temp(self):
        """Test AC control display with invalid cabin temp"""
        car_control.ac_temp = 22

        with patch('car_control.read_cabin_temp', return_value=-100):  # Invalid reading
            car_control.display_ac_control(22)

            car_control.lcd_display.lcd_clear.assert_called_once()
            car_control.lcd_display.lcd_display_string.assert_any_call("Set:22C", 1)
            car_control.lcd_display.lcd_display_string.assert_any_call("Cabin: N/A", 2)

    def test_display_engine_control(self):
        """Test engine control display"""
        car_control.engine_on = True
        car_control.display_engine_control()

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("Engine: ON", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("1:Toggle *:Back", 2)

    def test_display_door_control(self):
        """Test door control display"""
        car_control.door_locked = True
        car_control.display_door_control()

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("Door: LOCKED", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("1:Toggle *:Back", 2)

    def test_display_theft_alert(self):
        """Test theft alert display"""
        car_control.display_theft_alert()

        car_control.lcd_display.lcd_clear.assert_called_once()
        car_control.lcd_display.lcd_display_string.assert_any_call("!!! WARNING !!!", 1)
        car_control.lcd_display.lcd_display_string.assert_any_call("Theft Detected!", 2)


class TestCarControlHelpers:
    """Test cases for helper functions"""

    def test_reset_inactivity_timer(self):
        """Test resetting inactivity timer"""
        original_time = car_control.last_activity_time
        time.sleep(0.01)  # Ensure time moves forward

        car_control.reset_inactivity_timer()

        # Timer should have been updated to a newer time
        assert car_control.last_activity_time >= original_time

    def test_start_session(self):
        """Test starting a session"""
        with patch('car_control.state_lock'):
            car_control.start_session()

            assert car_control.RFID_ACCESS == True
            assert car_control.current_menu == "MAIN"
            assert car_control.control_menu_page == 0
            assert car_control.monitor_menu_page == 0
            # display_main_menu should have been called
            car_control.lcd_display.lcd_clear.assert_called()
            car_control.lcd_display.lcd_display_string.assert_any_call("1:Control Sys", 1)
            car_control.lcd_display.lcd_display_string.assert_any_call("2:Monitor Sys", 2)

    def test_end_session_with_goodbye(self):
        """Test ending session with goodbye message"""
        car_control.RFID_ACCESS = True
        car_control.door_locked = False  # Start with unlocked

        with patch('car_control.state_lock'), \
             patch('car_control.display_goodbye'), \
             patch('car_control.display_idle'), \
             patch('car_control.hal_servo'), \
             patch('car_control.save_settings'):

            car_control.end_session(goodbye=True)

            # Should have locked door and saved settings
            assert car_control.RFID_ACCESS == False
            # door_locked should be set to True
            car_control.hal_servo.set_servo_position.assert_called_with(car_control.DOOR_LOCKED_POS)
            car_control.save_settings.assert_called()

    def test_end_session_without_goodbye(self):
        """Test ending session without goodbye message"""
        car_control.RFID_ACCESS = True

        with patch('car_control.state_lock'), \
             patch('car_control.display_goodbye') as mock_goodbye, \
             patch('car_control.display_idle'), \
             patch('car_control.hal_servo'), \
             patch('car_control.save_settings'):

            car_control.end_session(goodbye=False)

            # Should not have shown goodbye
            mock_goodbye.assert_not_called()
            # But should still have cleaned up
            assert car_control.RFID_ACCESS == False

    def test_get_status(self):
        """Test getting status snapshot"""
        # Set up known state
        car_control.door_locked = True
        car_control.engine_on = False

        with patch('car_control.state_lock'), \
             patch('car_control.get_fuel_level', return_value=75), \
             patch('car_control.get_battery_level', return_value=80), \
             patch('car_control.get_engine_temperature', return_value=60), \
             patch('car_control.read_cabin_temp', return_value=22.5):

            status = car_control.get_status()

            assert status["door_locked"] == True
            assert status["engine_on"] == False
            assert status["fuel"] == 75
            assert status["battery"] == 80
            assert status["engine_temp"] == 60
            assert status["cabin_temp"] == 22.5

    def test_remote_set_door_locked(self):
        """Test remote door locking"""
        with patch('car_control.state_lock'), \
             patch('car_control.hal_servo'), \
             patch('car_control.save_settings'), \
             patch('car_control.display_door_control'):

            # Test locking
            car_control.remote_set_door_locked(True)

            assert car_control.door_locked == True
            car_control.hal_servo.set_servo_position.assert_called_with(car_control.DOOR_LOCKED_POS)
            car_control.save_settings.assert_called()

            # Test unlocking
            car_control.remote_set_door_locked(False)

            assert car_control.door_locked == False
            car_control.hal_servo.set_servo_position.assert_called_with(car_control.DOOR_UNLOCKED_POS)

    def test_remote_set_engine_on(self):
        """Test remote engine control"""
        with patch('car_control.state_lock'), \
             patch('car_control.hal_dc_motor'), \
             patch('car_control.save_settings'), \
             patch('car_control.display_engine_control'):

            # Test starting engine
            car_control.remote_set_engine_on(True)

            assert car_control.engine_on == True
            car_control.hal_dc_motor.set_motor_speed.assert_called_with(car_control.ENGINE_RUN_SPEED)
            car_control.save_settings.assert_called()

            # Test stopping engine (should reset temperature)
            car_control.remote_set_engine_on(False)

            assert car_control.engine_on == False
            car_control.hal_dc_motor.set_motor_speed.assert_called_with(0)
            # Temperature should be reset to start temp
            assert car_control._engine_temp == float(car_control.ENGINE_TEMP_START)


if __name__ == "__main__":
    pytest.main([__file__])
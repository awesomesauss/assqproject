#!/usr/bin/env python3
"""
Pytest configuration to mock HAL modules for testing
"""
import sys
from unittest.mock import Mock

# Mock the hal module and its submodules
sys.modules['hal'] = Mock()
sys.modules['hal.hal_accelerometer'] = Mock()
sys.modules['hal.hal_buzzer'] = Mock()
sys.modules['hal.hal_lcd'] = Mock()
sys.modules['hal.hal_keypad'] = Mock()
sys.modules['hal.hal_rfid_reader'] = Mock()
sys.modules['hal.hal_dc_motor'] = Mock()
sys.modules['hal.hal_servo'] = Mock()
sys.modules['hal.hal_temp_humidity_sensor'] = Mock()
sys.modules['hal.hal_adc'] = Mock()
sys.modules['hal.hal_led'] = Mock()
sys.modules['hal.hal_input_switch'] = Mock()
sys.modules['hal.hal_ir_sensor'] = Mock()
sys.modules['hal.hal_keypad'] = Mock()
sys.modules['hal.hal_lcd'] = Mock()
sys.modules['hal.hal_moisture_sensor'] = Mock()
sys.modules['hal.hal_usonic'] = Mock()

# Mock the smbus module that hal_accelerometer tries to import
sys.modules['smbus'] = Mock()

# Mock the telegram module that tele.py tries to import
sys.modules['telegram'] = Mock()
sys.modules['telegram.ext'] = Mock()
#!/usr/bin/env python3
"""
Car Monitoring System - Standalone Module
Provides functionality to monitor fuel/battery levels and engine temperature
"""

import time
import threading
from hal.hal_keypad import init as keypad_init
from hal.hal_lcd import lcd

# Global variables for LCD and keypad
lcd_display = lcd()
state_lock = threading.Lock()

# Timing constants
DISPLAY_TIMEOUT = 10  # seconds to wait for user input before returning to prompt
LAST_INPUT_TIME = 0

def reset_input_timer():
    """Reset the timer for user input"""
    global LAST_INPUT_TIME
    LAST_INPUT_TIME = time.time()

def check_for_timeout():
    """Check if we've waited too long for user input"""
    return time.time() - LAST_INPUT_TIME > DISPLAY_TIMEOUT

def get_fake_fuel_level():
    """Return a realistic fake fuel level percentage (healthy car)"""
    # Healthy car typically has 70-90% fuel when monitored
    return 75  # Fixed value for consistency, could add small random variation

def get_fake_battery_level():
    """Return a realistic fake battery level percentage (healthy car)"""
    # Healthy car battery typically charges to 80-95% when running
    return 88  # Fixed value for consistency

def get_fake_engine_temperature():
    """Return a realistic fake engine temperature in Celsius (healthy car)"""
    # Normal operating engine temperature is typically 85-95°C
    return 90  # Fixed value for consistency

def display_fuel_battery_levels():
    """Display fuel and battery levels on LCD"""
    with state_lock:
        lcd_display.lcd_clear()
        fuel_level = get_fake_fuel_level()
        battery_level = get_fake_battery_level()
        lcd_display.lcd_display_string(f"Fuel: {fuel_level}%", 1)
        lcd_display.lcd_display_string(f"Battery: {battery_level}%", 2)

def display_engine_temperature():
    """Display engine temperature on LCD"""
    with state_lock:
        lcd_display.lcd_clear()
        engine_temp = get_fake_engine_temperature()
        lcd_display.lcd_display_string(f"Engine Temp:", 1)
        lcd_display.lcd_display_string(f"{engine_temp}C", 2)

def prompt_for_selection():
    """Display prompt for user to select 1 or 2"""
    with state_lock:
        lcd_display.lcd_clear()
        lcd_display.lcd_display_string("Enter 1 or 2:", 1)
        lcd_display.lcd_display_string("1:Fuel/Batt 2:EngTemp", 2)

def monitor_car_systems():
    """Main monitoring function - handles user input and display"""
    global LAST_INPUT_TIME

    print("Car Monitoring System Started")
    print("Waiting for user input (1 for Fuel/Battery, 2 for Engine Temp)")
    print("Timeout: 10 seconds of inactivity")

    # Turn on backlight
    with state_lock:
        lcd_display.backlight(1)

    # Flag to signal when to exit
    exit_flag = threading.Event()

    # Keypad thread function
    def keypad_thread():
        """Thread function to handle keypad input"""
        def key_pressed(key):
            """Callback function for when a key is pressed on the keypad"""
            global LAST_INPUT_TIME

            with state_lock:
                key_str = str(key)
                reset_input_timer()

                if key_str == '1':
                    print("User selected: Fuel/Battery Levels")
                    display_fuel_battery_levels()
                elif key_str == '2':
                    print("User selected: Engine Temperature")
                    display_engine_temperature()
                elif key_str == '*':
                    print("User selected: Go back")
                    # This would typically return to the main menu in the full system
                    prompt_for_selection()

        # Initialize the keypad with our callback
        keypad_init(key_pressed)

        # Wait for exit signal
        exit_flag.wait()

    # Show initial prompt
    prompt_for_selection()
    reset_input_timer()

    # Start keypad thread
    keypad_thread_obj = threading.Thread(target=keypad_thread, daemon=True)
    keypad_thread_obj.start()

    try:
        # Main loop - wait for timeout or let keypad interrupts handle input
        while not exit_flag.is_set():
            time.sleep(0.1)

            # Check if we've timed out waiting for input
            if check_for_timeout():
                print("Timeout reached - returning to prompt")
                prompt_for_selection()
                reset_input_timer()

    except KeyboardInterrupt:
        print("\nMonitoring system stopped")
    finally:
        # Signal keypad thread to exit
        exit_flag.set()
        keypad_thread_obj.join(timeout=1.0)

        # Clean up LCD
        with state_lock:
            lcd_display.lcd_clear()
            lcd_display.backlight(0)  # Turn off backlight

if __name__ == "__main__":
    # Create and start the monitoring system
    monitor_car_systems()
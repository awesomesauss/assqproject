#!/usr/bin/env python3
"""
Car Control System - Main Application
Merged: menu navigation (Control/Monitor Car Systems) gated behind an
RFID tap, with an inactivity timeout that returns to the idle screen.
Adds an anti-theft monitor: sudden accelerometer movement while the
doors are locked sounds the buzzer and prints a warning; the same
movement while unlocked is ignored.
"""

import json
import time
import threading
from hal.hal_keypad import init as keypad_init
from hal.hal_lcd import lcd
from hal import hal_accelerometer
from hal import hal_buzzer

# Door lock control state
DOOR_LOCKED_POS = 0     # servo angle (degrees) when locked
DOOR_UNLOCKED_POS = 180  # servo angle (degrees) when unlocked
door_locked = True

# Anti-theft state (REQ_18): sudden accelerometer movement while the doors
# are locked triggers the buzzer + a warning message. The same movement
# while the doors are unlocked is ignored entirely.
ANTITHEFT_ACCEL_THRESHOLD = 0.5   # g's of change between consecutive reads that counts as "sudden"
ANTITHEFT_POLL_INTERVAL = 0.2     # seconds between accelerometer samples
ANTITHEFT_BUZZ_DURATION = 3       # seconds the buzzer/warning stays on once triggered
alarm_active = False              # True while the buzzer/warning is currently showing

#---------------------------------------------------------------------------------------------------------------------------#
# Settings persistence (AC temp, engine, door lock survive sessions/restarts)
#---------------------------------------------------------------------------------------------------------------------------#
SETTINGS_FILE = "car_settings.json"

def load_settings():
    """Load saved settings from disk into the global state. Missing/invalid
    file is not an error - the defaults already set above are kept."""
    global ac_temp, engine_on, door_locked
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        if "door_locked" in data:
            door_locked = bool(data["door_locked"])
    except (FileNotFoundError, ValueError, OSError):
        pass  # keep defaults

def save_settings():
    """Write the current settings to disk. Caller should already hold state_lock."""
    data = {
        "ac_temp": ac_temp,
        "engine_on": engine_on,
        "door_locked": door_locked,
    }
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        print("Warning: could not save settings to " + SETTINGS_FILE)

# Session / timeout state
last_activity_time = time.time()
INACTIVITY_TIMEOUT = 10    # seconds

# Keypad debounce state: the keypad HAL scans the matrix in a loop and can
# report the same key several times for one physical press/hold (no hardware
# debounce). Without collapsing those duplicates, one press could toggle a
# menu multiple times in a row, making it look like the menu changes on its
# own. Any repeat of the same key within KEY_DEBOUNCE_SECONDS is treated as
# part of the same physical press and ignored.
last_key = None
last_key_time = 0.0
KEY_DEBOUNCE_SECONDS = 0.3

state_lock = threading.Lock()  # protects the state above since it's touched by multiple threads

# Initialize LCD
lcd_display = lcd()

#---------------------------------------------------------------------------------------------------------------------------#
# Functions to display menus on the LCD
#---------------------------------------------------------------------------------------------------------------------------#
def display_door_control():
    """Display the Lock/Unlock Door screen: current lock state, toggled by key 1"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Door: " + ("LOCKED" if door_locked else "UNLOCKED"), 1)
    lcd_display.lcd_display_string("1:Toggle *:Back", 2)

def display_theft_alert():
    """Display the anti-theft warning screen"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("!!! WARNING !!!", 1)
    lcd_display.lcd_display_string("Theft Detected!", 2)

#-------------------------------------------------------------------------------------------------------------------------------#
# Anti-theft logic (REQ_18)
#-------------------------------------------------------------------------------------------------------------------------------#
def trigger_theft_alarm():
    """Sounds the buzzer and shows a warning for ANTITHEFT_BUZZ_DURATION
    seconds, then restores whatever screen should be showing. Runs outside
    state_lock for the duration of the buzz so other threads (keypad, RFID,
    timeout) aren't blocked while the alarm sounds."""
    global alarm_active

    with state_lock:
        if alarm_active:
            return  # already sounding - don't stack overlapping alarms
        alarm_active = True
        display_theft_alert()

    print("!!! WARNING: sudden movement detected while doors are locked - possible theft !!!")
    hal_buzzer.buzz_on()
    time.sleep(ANTITHEFT_BUZZ_DURATION)
    hal_buzzer.buzz_off()

    with state_lock:
        alarm_active = False
        #_redraw_current_screen()

def antitheft_thread():
    """Continuously polls the accelerometer for sudden speed changes.
    - Doors locked + sudden change  -> sound buzzer, print/show warning.
    - Doors unlocked + sudden change -> ignored, nothing happens.
    Runs regardless of RFID session state, since theft attempts are most
    likely while nobody has tapped in."""
    prev_magnitude = None

    while True:
        time.sleep(ANTITHEFT_POLL_INTERVAL)

        x, y, z = hal_accelerometer.read_accel()
        magnitude = (x ** 2 + y ** 2 + z ** 2) ** 0.5

        if prev_magnitude is not None:
            delta = abs(magnitude - prev_magnitude)
            if delta >= ANTITHEFT_ACCEL_THRESHOLD:
                with state_lock:
                    locked = door_locked
                if locked:
                    trigger_theft_alarm()
                # else: doors unlocked - sudden movement is expected/ignored

        prev_magnitude = magnitude

def main():
    print("Anti-Theft System Starting...")
    hal_accelerometer.init()
    hal_buzzer.init()
    print("Doors are locked. Monitoring accelerometer for sudden movement...")
 
    try:
        antitheft_thread()
    except KeyboardInterrupt:
        print("\nShutting down...")
 
 
if __name__ == "__main__":
    main()
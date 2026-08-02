#!/usr/bin/env python3
"""
Car Control System - Main Application
Merged: menu navigation (Control/Monitor Car Systems) gated behind an
RFID tap, with an inactivity timeout that returns to the idle screen.
"""

import time
import threading
from hal.hal_keypad import init as keypad_init
from hal.hal_lcd import lcd
from hal.hal_rfid_reader import init as rfid_init
from hal import hal_temp_humidity_sensor
from hal import hal_dc_motor
from hal import hal_servo

# Global variables for menu state
current_menu = "MAIN"  # MAIN, CONTROL, MONITOR, AC_CONTROL, ENGINE_CONTROL, DOOR_CONTROL
control_menu_page = 0  # 0 or 1 (for scrolling between two views)
monitor_menu_page = 0  # 0 only (no scrolling needed for monitor menu)

# AC / Heat control state (REQ_14, REQ_17)
AC_TEMP_MIN = 16
AC_TEMP_MAX = 30
AC_TEMP_STEP = 1
AC_TEMP_DEFAULT = 22
ac_temp = AC_TEMP_DEFAULT

# Engine control state
ENGINE_RUN_SPEED = 60  # motor speed (0-100) while "running"
engine_on = False

# Door lock control state
DOOR_LOCKED_POS = 0     # servo angle (degrees) when locked
DOOR_UNLOCKED_POS = 90  # servo angle (degrees) when unlocked
door_locked = True

# Session / timeout state
RFID_ACCESS = False        # True once a card has been tapped and menu is showing
last_activity_time = time.time()
INACTIVITY_TIMEOUT = 10    # seconds

state_lock = threading.Lock()  # protects the state above since it's touched by multiple threads

# Initialize LCD
lcd_display = lcd()

# Initialize RFID reader
reader = rfid_init()

#---------------------------------------------------------------------------------------------------------------------------#
# Functions to display menus on the LCD
#---------------------------------------------------------------------------------------------------------------------------#
def display_idle():
    """Display the idle/waiting-for-card screen"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("RFID ACCESS", 1)
    lcd_display.lcd_display_string("REQUIRED", 2)

def display_goodbye():
    """Display the goodbye message after a timeout"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Goodbye!", 1)

def display_main_menu():
    """Display the main menu on LCD"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("1:Control Sys", 1)
    lcd_display.lcd_display_string("2:Monitor Sys", 2)

def display_control_menu(page=0):
    """Display the Control Car Systems menu"""
    lcd_display.lcd_clear()
    if page == 0:
        lcd_display.lcd_display_string("AC/Heat Control", 1)
        lcd_display.lcd_display_string("Start Engine", 2)
    else:  # page == 1
        lcd_display.lcd_display_string("Start Engine", 1)
        lcd_display.lcd_display_string("Lock/Unlock Door", 2)

def display_monitor_menu(page=0):
    """Display the Monitor Car Systems menu"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Battery/Fuel Lvl", 1)
    lcd_display.lcd_display_string("Engine Temp", 2)

def display_ac_control(temp):
    """Display the AC/Heat control screen (REQ_14): shows the user's requested
    setpoint alongside the actual cabin temperature read from the DHT11
    sensor. Screen-only - no actuator is driven."""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string(f"Set:{temp}C", 1)

    cabin_temp, _humidity = hal_temp_humidity_sensor.read_temp_humidity()
    if cabin_temp == -100:  # sensor read failed/invalid
        lcd_display.lcd_display_string("Cabin: N/A", 2)
    else:
        lcd_display.lcd_display_string(f"Cabin:{cabin_temp:.1f}C", 2)

def display_engine_control():
    """Display the Start Engine screen: current on/off state, toggled by key 1"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Engine: " + ("ON" if engine_on else "OFF"), 1)
    lcd_display.lcd_display_string("1:Toggle *:Back", 2)

def display_door_control():
    """Display the Lock/Unlock Door screen: current lock state, toggled by key 1"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Door: " + ("LOCKED" if door_locked else "UNLOCKED"), 1)
    lcd_display.lcd_display_string("1:Toggle *:Back", 2)

#---------------------------------------------------------------------------------------------------------------------------#
# Activity / session helpers
#---------------------------------------------------------------------------------------------------------------------------#
def reset_inactivity_timer():
    """Reset the inactivity timer - caller must already hold state_lock"""
    global last_activity_time
    last_activity_time = time.time()

def start_session():
    """Called when a card is tapped: activate the menu system - caller must already hold state_lock"""
    global RFID_ACCESS, current_menu, control_menu_page, monitor_menu_page
    RFID_ACCESS = True
    current_menu = "MAIN"
    control_menu_page = 0
    monitor_menu_page = 0
    display_main_menu()
    reset_inactivity_timer()

def end_session(goodbye=True):
    """Called when the session times out: deactivate the menu system - caller must already hold state_lock"""
    global RFID_ACCESS
    RFID_ACCESS = False
    if goodbye:
        display_goodbye()
        time.sleep(2)  # let the user actually read "Goodbye!" (lock held so nothing else can write the LCD meanwhile)
    display_idle()

#-------------------------------------------------------------------------------------------------------------------------------#
# Keypad navigation logic
#-------------------------------------------------------------------------------------------------------------------------------#
def key_pressed(key):
    """Callback function for when a key is pressed on the keypad"""
    global current_menu, control_menu_page, monitor_menu_page, ac_temp, engine_on, door_locked
    key = str(key)  # hal_keypad's MATRIX mixes ints (1-9,0) and strs ('*','#'); normalize here

    with state_lock:
        # Ignore keypad input entirely until a card has been tapped
        if not RFID_ACCESS:
            return

        reset_inactivity_timer()

        if current_menu == "MAIN":
            if key == '1':
                current_menu = "CONTROL"
                control_menu_page = 0
                display_control_menu(0)
            elif key == '2':
                current_menu = "MONITOR"
                monitor_menu_page = 0
                display_monitor_menu(0)

        elif current_menu == "CONTROL":
            if key == '1' and control_menu_page == 0:  # Select "AC/Heat Control" (REQ_14)
                current_menu = "AC_CONTROL"
                ac_temp = AC_TEMP_DEFAULT
                display_ac_control(ac_temp)
            elif key == '2' and control_menu_page == 0:  # Select "Start Engine" (page 0 position)
                current_menu = "ENGINE_CONTROL"
                display_engine_control()
            elif key == '1' and control_menu_page == 1:  # Select "Start Engine" (page 1 position)
                current_menu = "ENGINE_CONTROL"
                display_engine_control()
            elif key == '2' and control_menu_page == 1:  # Select "Lock/Unlock Door"
                current_menu = "DOOR_CONTROL"
                display_door_control()
            elif key == '#':  # Scroll down (this keypad has no 'A' key)
                control_menu_page = 1 - control_menu_page  # Toggle between 0 and 1
                display_control_menu(control_menu_page)
            elif key == '*':  # Go back to main menu
                current_menu = "MAIN"
                display_main_menu()

        elif current_menu == "AC_CONTROL":
            if key == '2':  # Increase temperature
                ac_temp = min(AC_TEMP_MAX, ac_temp + AC_TEMP_STEP)
                display_ac_control(ac_temp)
            elif key == '8':  # Decrease temperature
                ac_temp = max(AC_TEMP_MIN, ac_temp - AC_TEMP_STEP)
                display_ac_control(ac_temp)
            elif key == '*':  # Go back to Control Car Systems menu
                current_menu = "CONTROL"
                display_control_menu(control_menu_page)

        elif current_menu == "ENGINE_CONTROL":
            if key == '1':  # Toggle engine on/off
                engine_on = not engine_on
                hal_dc_motor.set_motor_speed(ENGINE_RUN_SPEED if engine_on else 0)
                display_engine_control()
            elif key == '*':  # Go back to Control Car Systems menu
                current_menu = "CONTROL"
                display_control_menu(control_menu_page)

        elif current_menu == "DOOR_CONTROL":
            if key == '1':  # Toggle door locked/unlocked
                door_locked = not door_locked
                hal_servo.set_servo_position(DOOR_LOCKED_POS if door_locked else DOOR_UNLOCKED_POS)
                display_door_control()
            elif key == '*':  # Go back to Control Car Systems menu
                current_menu = "CONTROL"
                display_control_menu(control_menu_page)

        elif current_menu == "MONITOR":
            if key == '*':  # Go back to main menu
                current_menu = "MAIN"
                display_main_menu()

#-------------------------------------------------------------------------------------------------------------------------------#
# RFID scanning logic
#-------------------------------------------------------------------------------------------------------------------------------#
def rfid_thread():
    """Continuously polls for RFID card taps and starts a menu session on each tap"""
    while True:
        # read_id_no_block() only does request + anticollision (no auth/block reads),
        # since we only need to know a card is present, not read data off it. This
        # matters when someone taps and holds: reader.read() would otherwise keep
        # re-running the full authenticate + read-3-blocks sequence back to back.
        card_id = reader.read_id_no_block()
        if card_id:
            with state_lock:
                if not RFID_ACCESS:
                    start_session()
                else:
                    # A tap while a session is already active just refreshes the timer
                    reset_inactivity_timer()
            time.sleep(1)  # debounce so one tap/hold isn't processed repeatedly
        else:
            time.sleep(0.2)  # poll interval while no card is present

#-------------------------------------------------------------------------------------------------------------------------------#
# Inactivity timeout logic
#-------------------------------------------------------------------------------------------------------------------------------#
def timeout_watcher():
    """Watches the clock and ends the session (showing 'Goodbye!') after INACTIVITY_TIMEOUT seconds"""
    while True:
        time.sleep(0.5)
        with state_lock:
            elapsed = time.time() - last_activity_time
            if RFID_ACCESS and elapsed >= INACTIVITY_TIMEOUT:
                end_session(goodbye=True)

def keypad_thread():
    """Thread function to run the keypad get_key() blocking function"""
    from hal.hal_keypad import get_key
    get_key()  # This blocks and handles key presses via callback

def main():
    """Main application function"""
    print("Car Control System Starting...")

    lcd_display.backlight(1)  # Turn on backlight
    keypad_init(key_pressed)
    hal_temp_humidity_sensor.init()  # DHT11 sensor used for AC/heating screen (REQ_14)
    hal_dc_motor.init()   # drives the engine simulation motor
    hal_servo.init()      # drives the door lock servo

    # Sync actuators to the software's initial state (engine off, door locked)
    hal_dc_motor.set_motor_speed(0)
    hal_servo.set_servo_position(DOOR_LOCKED_POS)

    # Show idle screen until a card is tapped
    display_idle()

    print("System ready. Tap an RFID card to bring up the menu.")
    print("Keys: 1=Control Car Systems, 2=Monitor Car Systems, #=Scroll (in Control menu), *=Back")
    print("In AC/Heat Control: 2=Increase Temp, 8=Decrease Temp, *=Back")
    print("In Start Engine / Lock-Unlock Door: 1=Toggle, *=Back")
    print(f"Menu will time out to 'Goodbye!' after {INACTIVITY_TIMEOUT}s of no input.")

    try:
        keypad_thread_obj = threading.Thread(target=keypad_thread, daemon=True)
        keypad_thread_obj.start()

        rfid_thread_obj = threading.Thread(target=rfid_thread, daemon=True)
        rfid_thread_obj.start()

        timeout_thread_obj = threading.Thread(target=timeout_watcher, daemon=True)
        timeout_thread_obj.start()

        while True:
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        lcd_display.lcd_clear()
        lcd_display.backlight(0)  # Turn off backlight

if __name__ == "__main__":
    main()
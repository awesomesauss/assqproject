#!/usr/bin/env python3
"""
Car Control System - Main Application
This file implements a car control menu system using LCD and keypad
as per the requirements:
- Main menu with "Control Car Systems" (option 1) and "Monitor Car Systems" (option 2)
- Control menu:
    Line 1: "Control the air conditioning & heating of the car"
    Line 2: "Start the car engine"
    Press 'A' to scroll down to see: "Lock/Unlock car doors"
- Monitor menu:
    Line 1: "Monitor Car Battery/ Fuel Levels"
    Line 2: "Monitor Engine Temperature"
- Navigation: '*' to go back, 'A' to scroll in control menu
"""

import time
from hal.hal_keypad import init as keypad_init
from hal.hal_lcd import lcd

# Global variables for menu state
current_menu = "MAIN"  # MAIN, CONTROL, MONITOR
control_menu_page = 0  # 0 or 1 (for scrolling between two views)
monitor_menu_page = 0  # 0 only (no scrolling needed for monitor menu)

# Initialize LCD
lcd_display = lcd()

def display_main_menu():
    """Display the main menu on LCD"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("1: Control Car", 1)
    lcd_display.lcd_display_string("2: Monitor Car", 2)

def display_control_menu(page=0):
    """Display the Control Car Systems menu"""
    lcd_display.lcd_clear()
    if page == 0:
        # First screen: show "Control the air conditioning & heating of the car" and "Start the car engine"
        lcd_display.lcd_display_string("Control the air conditioning & heating of the car", 1)
        lcd_display.lcd_display_string("Start the car engine", 2)
    else:  # page == 1
        # Second screen: show "Start the car engine" and "Lock/Unlock car doors"
        lcd_display.lcd_display_string("Start the car engine", 1)
        lcd_display.lcd_display_string("Lock/Unlock car doors", 2)

def display_monitor_menu(page=0):
    """Display the Monitor Car Systems menu"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Monitor Car Battery/ Fuel Levels", 1)
    lcd_display.lcd_display_string("Monitor Engine Temperature", 2)

def key_pressed(key):
    """Callback function for when a key is pressed on the keypad"""
    global current_menu, control_menu_page, monitor_menu_page

    #print(f"Key pressed: {key}")  # For debugging - can be enabled if needed

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
        if key == 'A':  # Scroll down
            control_menu_page = 1 - control_menu_page  # Toggle between 0 and 1
            display_control_menu(control_menu_page)
        elif key == '*':  # Go back to main menu
            current_menu = "MAIN"
            display_main_menu()

    elif current_menu == "MONITOR":
        if key == '*':  # Go back to main menu
            current_menu = "MAIN"
            display_main_menu()

def keypad_thread():
    """Thread function to run the keypad get_key() blocking function"""
    # Import here to avoid circular imports
    from hal.hal_keypad import get_key
    get_key()  # This blocks and handles key presses via callback

def main():
    """Main application function"""
    print("Car Control System Starting...")

    # Initialize LCD
    lcd_display.lcd_backlight(1)  # Turn on backlight

    # Initialize keypad with callback
    keypad_init(key_pressed)

    # Display initial main menu
    display_main_menu()

    print("System ready. Use keypad to navigate.")
    print("Keys: 1=Control Car Systems, 2=Monitor Car Systems, A=Scroll (in Control menu), *=Back")

    try:
        # Start keypad scanning in a separate thread since get_key() is blocking
        import threading
        keypad_thread_obj = threading.Thread(target=keypad_thread, daemon=True)
        keypad_thread_obj.start()

        # Main thread just keeps the script alive
        while True:
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        lcd_display.lcd_clear()
        lcd_display.lcd_backlight(0)  # Turn off backlight

if __name__ == "__main__":
    main()
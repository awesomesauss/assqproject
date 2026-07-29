#RFID Main Menu
#---------------------------------------------------------------------------------------------------------------------------#
#Initialize the keypad, LCD, RFID card reader
#---------------------------------------------------------------------------------------------------------------------------#

import RPi.GPIO as GPIO
import time
import threading
from hal.hal_keypad import init as keypad_init
from hal.hal_lcd import lcd
from hal.hal_rfid_reader import init as rfid_init

# Global variables for menu state
current_menu = "MAIN"  # MAIN, CONTROL, MONITOR
control_menu_page = 0  # 0 or 1 (for scrolling between two views)
monitor_menu_page = 0  # 0 only (no scrolling needed for monitor menu)

# Session / timeout state
RFID_ACCESS = False        # True once a card has been tapped and menu is showing
last_activity_time = time.time()
INACTIVITY_TIMEOUT = 10       # seconds

state_lock = threading.Lock() # protects the state above since it's touched by multiple threads

#Initialize LCD
lcd_display = lcd()

#Initialize RFID reader
reader = rfid_init()

#---------------------------------------------------------------------------------------------------------------------------#
# Functions to display menus on the LCD
#---------------------------------------------------------------------------------------------------------------------------#
def display_idle():
    """Display the idle/waiting-for-card screen"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Tap RFID card", 1)
    lcd_display.lcd_display_string("to begin...", 2)

def display_goodbye():
    """Display the goodbye message after a timeout"""
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Goodbye!", 1)

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

#---------------------------------------------------------------------------------------------------------------------------#
# Activity / session helpers
#---------------------------------------------------------------------------------------------------------------------------#
def reset_inactivity_timer():
    """Reset the inactivity timer - call this on any keypad or card input"""
    global last_activity_time
    with state_lock:
        last_activity_time = time.time()

def start_session():
    """Called when a card is tapped: activate the menu system"""
    global RFID_ACCESS, current_menu, control_menu_page, monitor_menu_page
    with state_lock:
        RFID_ACCESS = True
        current_menu = "MAIN"
        control_menu_page = 0
        monitor_menu_page = 0
    display_main_menu()
    reset_inactivity_timer()

def end_session(show_goodbye=True):
    """Called when the session times out: deactivate the menu system"""
    global RFID_ACCESS
    with state_lock:
        RFID_ACCESS = False
    if show_goodbye:
        display_goodbye()
        time.sleep(2)  # let the user actually read "Goodbye!"
    display_idle()

#-------------------------------------------------------------------------------------------------------------------------------#
#Keypad navigation logic
#-------------------------------------------------------------------------------------------------------------------------------#
def key_pressed(key):
    """Callback function for when a key is pressed on the keypad"""
    global current_menu, control_menu_page, monitor_menu_page

    # Ignore keypad input entirely until a card has been tapped
    with state_lock:
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

#-------------------------------------------------------------------------------------------------------------------------------#
#RFID scanning logic
#-------------------------------------------------------------------------------------------------------------------------------#
def rfid_thread():
    """Continuously waits for RFID card taps and starts a menu session on each tap"""
    while True:
        # reader.read() blocks until a card is presented
        card_id, text = reader.read()
        with state_lock:
            already_active = RFID_ACCESS
        if not already_active:
            start_session()
        else:
            # A tap while a session is already active just refreshes the timer
            reset_inactivity_timer()
        time.sleep(1)  # simple debounce so one tap isn't read multiple times

#-------------------------------------------------------------------------------------------------------------------------------#
#Inactivity timeout logic
#-------------------------------------------------------------------------------------------------------------------------------#
def timeout_watcher():
    """Watches the clock and ends the session (showing 'Goodbye!') after INACTIVITY_TIMEOUT seconds"""
    while True:
        time.sleep(0.5)
        with state_lock:
            active = RFID_ACCESS
            elapsed = time.time() - last_activity_time
        if active and elapsed >= INACTIVITY_TIMEOUT:
            end_session(show_goodbye=True)

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

    # Show idle screen until a card is tapped
    display_idle()

    print("System ready. Tap an RFID card to bring up the menu.")
    print("Keys: 1=Control Car Systems, 2=Monitor Car Systems, A=Scroll (in Control menu), *=Back")
    print(f"Menu will time out to 'Goodbye!' after {INACTIVITY_TIMEOUT}s of no input.")

    try:
        # Start keypad scanning in a separate thread since get_key() is blocking
        keypad_thread_obj = threading.Thread(target=keypad_thread, daemon=True)
        keypad_thread_obj.start()

        # Start RFID scanning in a separate thread since reader.read() is blocking
        rfid_thread_obj = threading.Thread(target=rfid_thread, daemon=True)
        rfid_thread_obj.start()

        # Start the inactivity timeout watcher
        timeout_thread_obj = threading.Thread(target=timeout_watcher, daemon=True)
        timeout_thread_obj.start()

        # Main thread just keeps the script alive
        while True:
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        lcd_display.lcd_clear()
        lcd_display.lcd_backlight(0)  # Turn off backlight

if __name__ == "__main__":
    main()
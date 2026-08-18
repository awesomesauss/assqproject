import json
import time
import threading
from hal.hal_keypad import init as keypad_init
from hal.hal_lcd import lcd
from hal.hal_rfid_reader import init as rfid_init
from hal import hal_temp_humidity_sensor
from hal import hal_dc_motor
from hal import hal_servo
from anti_theft import AntiTheftMonitor
from tele import TelegramBot

# Global menu state
current_menu = "MAIN"
control_menu_page = 0
monitor_menu_page = 0

# AC temperature settings
AC_TEMP_MIN = 16
AC_TEMP_MAX = 30
AC_TEMP_STEP = 1
AC_TEMP_DEFAULT = 22
ac_temp = AC_TEMP_DEFAULT

# Engine state
ENGINE_RUN_SPEED = 60
engine_on = False

# Door lock state
DOOR_LOCKED_POS = 0
DOOR_UNLOCKED_POS = 180
door_locked = True

# Simulated sensor values
ENGINE_TEMP_START = 30
ENGINE_TEMP_MAX = 90
ENGINE_WARMUP_SECONDS = 120

_engine_temp = float(ENGINE_TEMP_START)
_engine_temp_last_update = time.time()

_last_fuel_battery_shown = (None, None)
_last_engine_temp_shown = None

FUEL_LEVEL_START = 100.0
FUEL_DRAIN_RATE = 0.05

BATTERY_LEVEL_START = 100.0
BATTERY_DRAIN_RATE = 0.015

_fuel_level = float(FUEL_LEVEL_START)
_fuel_last_update = time.time()

_battery_level = float(BATTERY_LEVEL_START)
_battery_last_update = time.time()


def get_fuel_level():
    # Drain fuel only when engine is on
    global _fuel_level, _fuel_last_update
    now = time.time()
    elapsed = now - _fuel_last_update
    _fuel_last_update = now

    if engine_on and _fuel_level > 0:
        _fuel_level = max(0.0, _fuel_level - (FUEL_DRAIN_RATE * elapsed))

    return int(round(_fuel_level))


def get_battery_level():
    # Drain battery only when engine is on
    global _battery_level, _battery_last_update
    now = time.time()
    elapsed = now - _battery_last_update
    _battery_last_update = now

    if engine_on and _battery_level > 0:
        _battery_level = max(0.0, _battery_level - (BATTERY_DRAIN_RATE * elapsed))

    return int(round(_battery_level))


def get_engine_temperature():
    # Calculate engine temp increase while running
    global _engine_temp, _engine_temp_last_update

    now = time.time()
    elapsed = now - _engine_temp_last_update
    _engine_temp_last_update = now

    if engine_on and _engine_temp < ENGINE_TEMP_MAX:
        degrees_per_second = (ENGINE_TEMP_MAX - ENGINE_TEMP_START) / ENGINE_WARMUP_SECONDS
        _engine_temp = min(ENGINE_TEMP_MAX, _engine_temp + degrees_per_second * elapsed)

    return int(round(_engine_temp))


SETTINGS_FILE = "car_settings.json"


def load_settings():
    # Load settings from car_settings.json
    global ac_temp, engine_on, door_locked
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        if "ac_temp" in data:
            ac_temp = int(data["ac_temp"])
        if "engine_on" in data:
            engine_on = bool(data["engine_on"])
        if "door_locked" in data:
            door_locked = bool(data["door_locked"])
    except (FileNotFoundError, ValueError, OSError):
        pass


def save_settings():
    # Save current state to car_settings.json
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


# RFID session and debounce state
RFID_ACCESS = False
last_activity_time = time.time()
INACTIVITY_TIMEOUT = 10

last_key = None
last_key_time = 0.0
KEY_DEBOUNCE_SECONDS = 0.3

state_lock = threading.Lock()

# Hardware devices
lcd_display = lcd()
reader = rfid_init()


# LCD display helper functions
def display_idle():
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("RFID ACCESS", 1)
    lcd_display.lcd_display_string("REQUIRED", 2)


def display_goodbye():
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Goodbye!", 1)


def display_main_menu():
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("1:Control Sys", 1)
    lcd_display.lcd_display_string("2:Monitor Sys", 2)


def display_control_menu(page=0):
    lcd_display.lcd_clear()
    if page == 0:
        lcd_display.lcd_display_string("AC/Heat Control", 1)
        lcd_display.lcd_display_string("Start Engine", 2)
    else:
        lcd_display.lcd_display_string("Start Engine", 1)
        lcd_display.lcd_display_string("Lock/Unlock Door", 2)


def display_monitor_menu(page=0):
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("1:Battery/Fuel", 1)
    lcd_display.lcd_display_string("2:Engine Temp", 2)


def display_fuel_battery_levels(force=False):
    global _last_fuel_battery_shown
    fuel_level = get_fuel_level()
    battery_level = get_battery_level()
    if not force and (fuel_level, battery_level) == _last_fuel_battery_shown:
        return
    _last_fuel_battery_shown = (fuel_level, battery_level)
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string(f"Fuel: {fuel_level}%", 1)
    lcd_display.lcd_display_string(f"Battery: {battery_level}%", 2)


def display_engine_temp_reading(force=False):
    global _last_engine_temp_shown
    engine_temp = get_engine_temperature()
    if not force and engine_temp == _last_engine_temp_shown:
        return
    _last_engine_temp_shown = engine_temp
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Engine Temp:", 1)
    lcd_display.lcd_display_string(f"{engine_temp}C  *:Back", 2)


DHT_READ_RETRIES = 15
DHT_RETRY_DELAY = 0.1
DHT_MIN_READ_INTERVAL = 0.5
_dht_last_read_time = 0.0


def read_cabin_temp():
    # Read DHT11 sensor with retries
    global _dht_last_read_time

    wait = DHT_MIN_READ_INTERVAL - (time.time() - _dht_last_read_time)
    if wait > 0:
        time.sleep(wait)

    cabin_temp = -100
    for _ in range(DHT_READ_RETRIES):
        temp, _humidity = hal_temp_humidity_sensor.read_temp_humidity()
        if temp != -100:
            cabin_temp = temp
            break
        time.sleep(DHT_RETRY_DELAY)

    _dht_last_read_time = time.time()
    return cabin_temp


def display_ac_control(temp):
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string(f"Set:{temp}C", 1)

    cabin_temp = read_cabin_temp()
    if cabin_temp == -100:
        lcd_display.lcd_display_string("Cabin: N/A", 2)
    else:
        lcd_display.lcd_display_string(f"Cabin:{cabin_temp:.1f}C", 2)


def display_engine_control():
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Engine: " + ("ON" if engine_on else "OFF"), 1)
    lcd_display.lcd_display_string("1:Toggle *:Back", 2)


def display_door_control():
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("Door: " + ("LOCKED" if door_locked else "UNLOCKED"), 1)
    lcd_display.lcd_display_string("1:Toggle *:Back", 2)


def display_theft_alert():
    lcd_display.lcd_clear()
    lcd_display.lcd_display_string("!!! WARNING !!!", 1)
    lcd_display.lcd_display_string("Theft Detected!", 2)


def redraw_current_screen():
    # Redraw screen based on active menu state
    if not RFID_ACCESS:
        display_idle()
    elif current_menu == "MAIN":
        display_main_menu()
    elif current_menu == "CONTROL":
        display_control_menu(control_menu_page)
    elif current_menu == "AC_CONTROL":
        display_ac_control(ac_temp)
    elif current_menu == "ENGINE_CONTROL":
        display_engine_control()
    elif current_menu == "DOOR_CONTROL":
        display_door_control()
    elif current_menu == "MONITOR":
        display_monitor_menu(monitor_menu_page)
    elif current_menu == "FUEL_BATTERY_DISPLAY":
        display_fuel_battery_levels(force=True)
    elif current_menu == "ENGINE_TEMP_DISPLAY":
        display_engine_temp_reading(force=True)


# Telegram callback handlers
def get_status():
    with state_lock:
        return {
            "door_locked": door_locked,
            "engine_on": engine_on,
            "fuel": get_fuel_level(),
            "battery": get_battery_level(),
            "engine_temp": get_engine_temperature(),
            "cabin_temp": read_cabin_temp(),
        }


def remote_set_door_locked(locked):
    global door_locked
    with state_lock:
        door_locked = locked
        hal_servo.set_servo_position(DOOR_LOCKED_POS if door_locked else DOOR_UNLOCKED_POS)
        save_settings()
        if current_menu == "DOOR_CONTROL":
            display_door_control()


def remote_set_engine_on(on):
    global engine_on, _engine_temp
    with state_lock:
        engine_on = on
        hal_dc_motor.set_motor_speed(ENGINE_RUN_SPEED if engine_on else 0)
        if not engine_on:
            _engine_temp = float(ENGINE_TEMP_START)
        save_settings()
        if current_menu == "ENGINE_CONTROL":
            display_engine_control()


def remote_set_ac_temp(temp):
    global ac_temp
    with state_lock:
        if temp < AC_TEMP_MIN or temp > AC_TEMP_MAX:
            return False, f"Temperature must be between {AC_TEMP_MIN}C and {AC_TEMP_MAX}C."
        ac_temp = temp
        save_settings()
        if current_menu == "AC_CONTROL":
            display_ac_control(ac_temp)
        return True, f"AC temperature set to {ac_temp}C."


# Session helpers
def reset_inactivity_timer():
    global last_activity_time
    last_activity_time = time.time()


def start_session():
    global RFID_ACCESS, current_menu, control_menu_page, monitor_menu_page
    RFID_ACCESS = True
    current_menu = "MAIN"
    control_menu_page = 0
    monitor_menu_page = 0
    display_main_menu()
    reset_inactivity_timer()


def end_session(goodbye=True):
    global RFID_ACCESS, door_locked
    RFID_ACCESS = False
    if goodbye:
        door_locked = True
        hal_servo.set_servo_position(DOOR_LOCKED_POS)
        save_settings()
        display_goodbye()
        time.sleep(2)
    display_idle()


def key_pressed(key):
    # Keypad input handler
    global current_menu, control_menu_page, monitor_menu_page, ac_temp, engine_on, door_locked
    global last_key, last_key_time, _engine_temp
    key = str(key)

    with state_lock:
        if not RFID_ACCESS:
            return

        # Key debounce
        now = time.time()
        if (now - last_key_time) < KEY_DEBOUNCE_SECONDS:
            return
        last_key = key
        last_key_time = now

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
            if key == '1' and control_menu_page == 0:
                current_menu = "AC_CONTROL"
                display_ac_control(ac_temp)
            elif key == '2' and control_menu_page == 0:
                current_menu = "ENGINE_CONTROL"
                display_engine_control()
            elif key == '1' and control_menu_page == 1:
                current_menu = "ENGINE_CONTROL"
                display_engine_control()
            elif key == '2' and control_menu_page == 1:
                current_menu = "DOOR_CONTROL"
                display_door_control()
            elif key == '#':
                control_menu_page = 1 - control_menu_page
                display_control_menu(control_menu_page)
            elif key == '*':
                current_menu = "MAIN"
                display_main_menu()

        elif current_menu == "AC_CONTROL":
            if key == '2':
                ac_temp = min(AC_TEMP_MAX, ac_temp + AC_TEMP_STEP)
                display_ac_control(ac_temp)
                save_settings()
            elif key == '1':
                ac_temp = max(AC_TEMP_MIN, ac_temp - AC_TEMP_STEP)
                display_ac_control(ac_temp)
                save_settings()
            elif key == '*':
                current_menu = "CONTROL"
                display_control_menu(control_menu_page)

        elif current_menu == "ENGINE_CONTROL":
            if key == '1':
                engine_on = not engine_on
                hal_dc_motor.set_motor_speed(ENGINE_RUN_SPEED if engine_on else 0)
                if not engine_on:
                    _engine_temp = float(ENGINE_TEMP_START)
                save_settings()
                display_engine_control()
            elif key == '*':
                current_menu = "CONTROL"
                display_control_menu(control_menu_page)

        elif current_menu == "DOOR_CONTROL":
            if key == '1':
                door_locked = not door_locked
                hal_servo.set_servo_position(DOOR_LOCKED_POS if door_locked else DOOR_UNLOCKED_POS)
                save_settings()
                display_door_control()
            elif key == '*':
                current_menu = "CONTROL"
                display_control_menu(control_menu_page)

        elif current_menu == "MONITOR":
            if key == '1':
                current_menu = "FUEL_BATTERY_DISPLAY"
                display_fuel_battery_levels(force=True)
            elif key == '2':
                current_menu = "ENGINE_TEMP_DISPLAY"
                display_engine_temp_reading(force=True)
            elif key == '*':
                current_menu = "MAIN"
                display_main_menu()

        elif current_menu == "FUEL_BATTERY_DISPLAY":
            if key == '*':
                current_menu = "MONITOR"
                display_monitor_menu(monitor_menu_page)

        elif current_menu == "ENGINE_TEMP_DISPLAY":
            if key == '*':
                current_menu = "MONITOR"
                display_monitor_menu(monitor_menu_page)


def rfid_thread():
    # Background thread polling RFID reader
    while True:
        card_id = reader.read_id_no_block()
        if card_id:
            with state_lock:
                if not RFID_ACCESS:
                    start_session()
                else:
                    reset_inactivity_timer()
            time.sleep(1)
        else:
            time.sleep(0.2)


def timeout_watcher():
    # Background thread tracking session inactivity
    while True:
        time.sleep(0.5)
        with state_lock:
            elapsed = time.time() - last_activity_time
            if RFID_ACCESS and elapsed >= INACTIVITY_TIMEOUT:
                end_session(goodbye=True)


def monitor_refresh_watcher():
    # Periodically update sensor displays
    while True:
        time.sleep(1)
        with state_lock:
            if not RFID_ACCESS:
                continue
            if current_menu == "FUEL_BATTERY_DISPLAY":
                display_fuel_battery_levels()
            elif current_menu == "ENGINE_TEMP_DISPLAY":
                display_engine_temp_reading()


def keypad_thread():
    from hal.hal_keypad import get_key
    get_key()


def main():
    print("Car Control System Starting...")

    lcd_display.backlight(1)
    keypad_init(key_pressed)
    hal_temp_humidity_sensor.init()
    hal_dc_motor.init()
    hal_servo.init()

    load_settings()

    hal_dc_motor.set_motor_speed(ENGINE_RUN_SPEED if engine_on else 0)
    hal_servo.set_servo_position(DOOR_LOCKED_POS if door_locked else DOOR_UNLOCKED_POS)

    # Initialize Telegram bot
    telegram_bot = TelegramBot(
        controls={
            "get_status": get_status,
            "lock_door": lambda: remote_set_door_locked(True),
            "unlock_door": lambda: remote_set_door_locked(False),
            "engine_on": lambda: remote_set_engine_on(True),
            "engine_off": lambda: remote_set_engine_on(False),
            "set_ac_temp": remote_set_ac_temp,
        }
    )
    telegram_bot.start()

    def show_theft_alert():
        display_theft_alert()
        telegram_bot.notify_text("Possible theft detected! Sudden movement while doors were locked.")

    def on_theft_photo(photo_path):
        if photo_path:
            telegram_bot.notify_photo(
                photo_path,
                caption="ALERT: possible theft - sudden movement while doors were locked!",
            )
        else:
            telegram_bot.notify_text("ALERT: possible theft detected (no photo - camera unavailable).")

    # Initialize anti-theft monitor
    antitheft = AntiTheftMonitor(
        state_lock,
        is_locked=lambda: door_locked,
        show_alert=show_theft_alert,
        restore_screen=redraw_current_screen,
        on_alert=on_theft_photo,
    )
    antitheft.init()
    antitheft.start()
    print(f"Anti-theft monitoring armed. Door locked = {door_locked}.")

    display_idle()
    print("System ready. Tap an RFID card to bring up the menu.")

    try:
        threading.Thread(target=keypad_thread, daemon=True).start()
        threading.Thread(target=rfid_thread, daemon=True).start()
        threading.Thread(target=timeout_watcher, daemon=True).start()
        threading.Thread(target=monitor_refresh_watcher, daemon=True).start()

        while True:
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        antitheft.stop()
        telegram_bot.stop()
        lcd_display.lcd_clear()
        lcd_display.backlight(0)


if __name__ == "__main__":
    main()

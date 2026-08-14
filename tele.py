#!/usr/bin/env python3
"""
Telegram Remote Control Module

Reusable Telegram bot for the Car Control System, built on python-telegram-bot
(https://python-telegram-bot.org). Lets an authorized owner lock/unlock the
door, start/stop the engine, and check status (fuel/battery/engine temp/cabin
temp) remotely, and pushes a push notification when the anti-theft alarm
triggers.

The module is UI-agnostic like anti_theft.py: the host application
(car_control.py) passes a dict of callables it uses to read/change car state:
  - get_status:  callable() -> dict with keys
                 door_locked, engine_on, fuel, battery, engine_temp, cabin_temp
  - lock_door:   callable()        lock the door
  - unlock_door: callable()        unlock the door
  - engine_on:   callable()        start the engine
  - engine_off:  callable()        stop the engine

Those callables are expected to acquire car_control.py's state_lock
themselves (same convention as anti_theft.py's callbacks), so this module
never touches that lock directly.

Configuration (bot token + allowed chat IDs) is read from environment
variables, falling back to a local tele_config.json (see
tele_config.example.json):
  TELEGRAM_BOT_TOKEN   the bot token from @BotFather
  TELEGRAM_CHAT_ID     comma-separated list of chat IDs allowed to issue commands

Only chat IDs in the allowlist can issue commands - anyone else gets an
"unauthorized" reply that includes their own chat ID, so the owner can grab
it and add it to the allowlist.
"""

import asyncio
import json
import os
import threading

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

CONFIG_FILE = "tele_config.json"


def _load_config():
    """Read bot token / allowed chat IDs from env vars, falling back to
    tele_config.json for whatever wasn't set via the environment."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = {c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()}
    chat_ids = {int(c) for c in chat_ids}

    if not token or not chat_ids:
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            token = token or data.get("token")
            if not chat_ids:
                chat_ids = {int(c) for c in data.get("chat_ids", [])}
        except (FileNotFoundError, ValueError, OSError):
            pass  # no config file - token/chat_ids stay whatever the env gave us (possibly empty)

    return token, chat_ids


class TelegramBot:
    def __init__(self, controls, token=None, allowed_chat_ids=None):
        env_token, env_chat_ids = _load_config()
        self.controls = controls
        self.token = token or env_token
        self.allowed_chat_ids = set(allowed_chat_ids) if allowed_chat_ids is not None else env_chat_ids

        self._application = None
        self._loop = None
        self._thread = None

    def _authorized(self, update: Update) -> bool:
        return update.effective_chat.id in self.allowed_chat_ids

    async def _reply_unauthorized(self, update: Update):
        await update.message.reply_text(
            "Not authorized for this bot.\n"
            f"Your chat ID is {update.effective_chat.id} - "
            "ask the car owner to add it to the allowlist."
        )

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        await update.message.reply_text(
            "Car Control remote\n"
            "/status - fuel, battery, engine & cabin temp\n"
            "/lock - lock the door\n"
            "/unlock - unlock the door\n"
            "/engine_on - start the engine\n"
            "/engine_off - stop the engine"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        status = self.controls["get_status"]()
        cabin_temp = status["cabin_temp"]
        cabin_str = "N/A" if cabin_temp == -100 else f"{cabin_temp:.1f}C"
        await update.message.reply_text(
            "Door: " + ("LOCKED" if status["door_locked"] else "UNLOCKED") + "\n"
            "Engine: " + ("ON" if status["engine_on"] else "OFF") + "\n"
            f"Fuel: {status['fuel']}%\n"
            f"Battery: {status['battery']}%\n"
            f"Engine temp: {status['engine_temp']}C\n"
            f"Cabin temp: {cabin_str}"
        )

    async def _cmd_lock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        self.controls["lock_door"]()
        await update.message.reply_text("Door locked.")

    async def _cmd_unlock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        self.controls["unlock_door"]()
        await update.message.reply_text("Door unlocked.")

    async def _cmd_engine_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        self.controls["engine_on"]()
        await update.message.reply_text("Engine started.")

    async def _cmd_engine_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        self.controls["engine_off"]()
        await update.message.reply_text("Engine stopped.")

    def start(self):
        """Start the bot in its own background thread (with its own asyncio
        event loop, since the rest of the app is plain threading, not
        asyncio). Returns False without starting anything if no bot token is
        configured, so a missing tele_config.json just disables remote
        control instead of crashing the car control app."""
        if not self.token:
            print("Telegram bot disabled: no bot token configured (see tele_config.example.json).")
            return False
        if not self.allowed_chat_ids:
            print("Telegram bot disabled: no allowed chat IDs configured.")
            return False

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._application = Application.builder().token(self.token).build()
        self._application.add_handler(CommandHandler(["start", "help"], self._cmd_start))
        self._application.add_handler(CommandHandler("status", self._cmd_status))
        self._application.add_handler(CommandHandler("lock", self._cmd_lock))
        self._application.add_handler(CommandHandler("unlock", self._cmd_unlock))
        self._application.add_handler(CommandHandler("engine_on", self._cmd_engine_on))
        self._application.add_handler(CommandHandler("engine_off", self._cmd_engine_off))

        self._loop.run_until_complete(self._application.initialize())
        self._loop.run_until_complete(self._application.start())
        self._loop.run_until_complete(self._application.updater.start_polling())
        print("Telegram bot started. Allowed chat IDs: " + str(self.allowed_chat_ids))

        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._application.updater.stop())
            self._loop.run_until_complete(self._application.stop())
            self._loop.run_until_complete(self._application.shutdown())

    def stop(self):
        """Stop the bot's event loop and wait for its thread to exit."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def notify_text(self, text):
        """Push a message to every allowed chat ID. Fire-and-forget and
        thread-safe - callable from any thread (e.g. the anti-theft alarm),
        not just the bot's own. Silently does nothing if the bot never
        started (no token configured)."""
        if self._loop is None or self._application is None:
            return
        for chat_id in self.allowed_chat_ids:
            asyncio.run_coroutine_threadsafe(
                self._application.bot.send_message(chat_id=chat_id, text=text),
                self._loop,
            )


if __name__ == "__main__":
    # Standalone smoke test (no hardware): status is fixed, lock/unlock and
    # engine on/off just print instead of driving actuators.
    def fake_status():
        return {
            "door_locked": True,
            "engine_on": False,
            "fuel": 87,
            "battery": 95,
            "engine_temp": 30,
            "cabin_temp": 24.5,
        }

    bot = TelegramBot(
        controls={
            "get_status": fake_status,
            "lock_door": lambda: print("(test) door locked"),
            "unlock_door": lambda: print("(test) door unlocked"),
            "engine_on": lambda: print("(test) engine on"),
            "engine_off": lambda: print("(test) engine off"),
        }
    )
    if bot.start():
        print("Bot running standalone. Ctrl+C to stop.")
        try:
            while True:
                threading.Event().wait(1)
        except KeyboardInterrupt:
            bot.stop()

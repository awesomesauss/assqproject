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

Configuration (bot token + allowed chat IDs + owner password) is read from
environment variables, falling back to a local tele_config.json (see
tele_config.example.json):
  TELEGRAM_BOT_TOKEN   the bot token from @BotFather
  TELEGRAM_CHAT_ID     comma-separated list of chat IDs allowed to issue commands
  TELEGRAM_PASSWORD    the single owner password (users must /login before controlling)

Only chat IDs in the allowlist can issue commands - anyone else gets an
"unauthorized" reply that includes their own chat ID, so the owner can grab
it and add it to the allowlist. In addition, control commands require the
chat to have successfully authenticated via /login <password> (passwords are
not stored after startup; login state lives only in memory for the chat).

Digital keys (REQ): the owner generates a single-use share code with /share
and sends it to a co-owner, who redeems it with /accept <code>. On success
the co-owner's chat ID is added to the allowlist and persisted back into
tele_config.json so it survives a restart. The code itself is one-time-only
and lives only in memory.
"""

import asyncio
import json
import os
import threading

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

CONFIG_FILE = "tele_config.json"


def _load_config():
    """Read bot token / allowed chat IDs / owner password from env vars,
    falling back to tele_config.json for whatever wasn't set via the
    environment. Returns (token, chat_ids, password)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = {c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()}
    chat_ids = {int(c) for c in chat_ids}
    password = os.environ.get("TELEGRAM_PASSWORD")

    if not token or not chat_ids or not password:
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            token = token or data.get("token")
            if not chat_ids:
                chat_ids = {int(c) for c in data.get("chat_ids", [])}
            password = password or data.get("password")
        except (FileNotFoundError, ValueError, OSError):
            pass  # no config file - config stays whatever the env gave us (possibly empty)

    return token, chat_ids, password


class TelegramBot:
    def __init__(self, controls, token=None, allowed_chat_ids=None, password=None):
        env_token, env_chat_ids, env_password = _load_config()
        self.controls = controls
        self.token = token or env_token
        self.allowed_chat_ids = set(allowed_chat_ids) if allowed_chat_ids is not None else env_chat_ids
        self.password = password or env_password

        self._authenticated = set()  # chat IDs that have successfully /login'd
        self._share_codes = {}       # share code -> used flag (digital keys, single-use)
        self._application = None
        self._loop = None
        self._thread = None

    def _authorized(self, update: Update) -> bool:
        return update.effective_chat.id in self.allowed_chat_ids

    def _persist_chat_ids(self):
        """Write the current allowlist back into tele_config.json (creating it
        fresh with the loaded token/password) so an accepted co-owner's chat
        ID survives a restart. Best-effort - failures are logged, not raised."""
        try:
            data = {"token": self.token, "chat_ids": sorted(self.allowed_chat_ids)}
            if self.password:
                data["password"] = self.password
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as exc:
            print("Warning: could not persist chat IDs (" + str(exc) + ")")

    async def _reply_unauthorized(self, update: Update):
        await update.message.reply_text(
            "Not authorized for this bot.\n"
            f"Your chat ID is {update.effective_chat.id} - "
            "ask the car owner to add it to the allowlist."
        )

    async def _require_auth(self, update: Update) -> bool:
        """Rejects chats that aren't authorized OR haven't logged in yet.
        Returns True if the caller may proceed."""
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return False
        if update.effective_chat.id not in self._authenticated:
            await update.message.reply_text(
                "Please log in first: /login <password>"
            )
            return False
        return True

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        logged_in = update.effective_chat.id in self._authenticated
        await update.message.reply_text(
            "Car Control remote\n"
            "/status - fuel, battery, engine & cabin temp\n"
            "/lock - lock the door\n"
            "/unlock - unlock the door\n"
            "/engine_on - start the engine\n"
            "/engine_off - stop the engine\n"
            "/share - generate a one-time co-owner key\n"
            "/accept <code> - redeem a shared key (co-owners)\n"
            "/revoke <chat_id> - remove a co-owner's access (owner)\n"
            "/list - show authorized chat IDs (owner)\n"
            "/login <password> - authenticate\n"
            "/logout - end your session\n\n"
            "Status: " + ("logged in" if logged_in else "NOT logged in. Run /login <password> to send commands.")
        )

    async def _cmd_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._reply_unauthorized(update)
            return
        if not context.args:
            await update.message.reply_text("Usage: /login <password>")
            return
        if not self.password:
            await update.message.reply_text("No password is configured on this bot - can't log in.")
            return
        if context.args[0] == self.password:
            self._authenticated.add(update.effective_chat.id)
            await update.message.reply_text("Logged in. You can now control the car.")
        else:
            await update.message.reply_text("Wrong password.")

    async def _cmd_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._authenticated.discard(update.effective_chat.id)
        await update.message.reply_text("Logged out.")

    async def _cmd_share(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Mint a one-time digital key for sharing access with a co-owner.
        Only an allowlisted, logged-in user (the owner) can do this. The code
        is returned to the caller to send to a co-owner, who redeems it with
        /accept <code>."""
        if not await self._require_auth(update):
            return
        code = os.urandom(4).hex()  # 8 hex chars, single-use
        self._share_codes[code] = False
        await update.message.reply_text(
            "Share code: /accept " + code + "\n"
            "Send this to your co-owner. It can be used once."
        )

    async def _cmd_accept(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Redeem a digital key to become an authorized co-owner. Anyone can
        try, but only a valid, unused share code grants access. On success the
        caller's chat ID is added to the allowlist and persisted so it
        survives a restart."""
        if not context.args:
            await update.message.reply_text("Usage: /accept <share-code>")
            return
        code = context.args[0]
        if code not in self._share_codes or self._share_codes[code]:
            await update.message.reply_text("Invalid or already used share code.")
            return

        self._share_codes[code] = True  # single-use
        chat_id = update.effective_chat.id
        self.allowed_chat_ids.add(chat_id)
        self._authenticated.add(chat_id)  # grant implies immediate access
        self._persist_chat_ids()
        await update.message.reply_text(
            "Access granted! You are now a co-owner of this car.\n"
            "Run /start for a list of commands."
        )

    async def _cmd_revoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remove a chat ID from the allowlist, revoking its access. Only the
        owner (allowlisted + logged in) can do this. The co-owner's access is
        removed immediately and persisted, so it also survives a restart."""
        if not await self._require_auth(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: /revoke <chat_id>")
            return
        try:
            chat_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Invalid chat ID - it should be a number.")
            return

        if chat_id not in self.allowed_chat_ids:
            await update.message.reply_text(f"Chat {chat_id} is not authorized - nothing to revoke.")
            return

        self.allowed_chat_ids.discard(chat_id)
        self._authenticated.discard(chat_id)
        self._persist_chat_ids()
        await update.message.reply_text(f"Revoked access for chat {chat_id}.")

    async def _cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all authorized chat IDs, marking which are currently logged in.
        Owner-only - lets the owner see who has access (useful before /revoke)."""
        if not await self._require_auth(update):
            return
        if not self.allowed_chat_ids:
            await update.message.reply_text("No authorized chat IDs.")
            return
        lines = ["Authorized chat IDs:"]
        for chat_id in sorted(self.allowed_chat_ids):
            marker = " (logged in)" if chat_id in self._authenticated else ""
            lines.append(f"  {chat_id}{marker}")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_auth(update):
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
        if not await self._require_auth(update):
            return
        self.controls["lock_door"]()
        await update.message.reply_text("Door locked.")

    async def _cmd_unlock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_auth(update):
            return
        self.controls["unlock_door"]()
        await update.message.reply_text("Door unlocked.")

    async def _cmd_engine_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_auth(update):
            return
        self.controls["engine_on"]()
        await update.message.reply_text("Engine started.")

    async def _cmd_engine_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_auth(update):
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
        self._application.add_handler(CommandHandler("login", self._cmd_login))
        self._application.add_handler(CommandHandler("logout", self._cmd_logout))
        self._application.add_handler(CommandHandler("share", self._cmd_share))
        self._application.add_handler(CommandHandler("accept", self._cmd_accept))
        self._application.add_handler(CommandHandler("revoke", self._cmd_revoke))
        self._application.add_handler(CommandHandler("list", self._cmd_list))
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

    def notify_photo(self, photo_path, caption=None):
        """Push a photo (by file path) as an image message to every allowed
        chat ID, with an optional caption. Same fire-and-forget, thread-safe
        contract as notify_text. Silently does nothing if the bot never
        started, and the photo is skipped if the file can't be opened."""
        if self._loop is None or self._application is None:
            return
        for chat_id in self.allowed_chat_ids:
            asyncio.run_coroutine_threadsafe(
                self._application.bot.send_photo(chat_id=chat_id, photo=open(photo_path, "rb"), caption=caption),
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
        },
        password="testpass",
    )
    if bot.start():
        print("Bot running standalone. Ctrl+C to stop.")
        try:
            while True:
                threading.Event().wait(1)
        except KeyboardInterrupt:
            bot.stop()

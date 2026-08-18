import asyncio
import json
import os
import threading

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

CONFIG_FILE = "tele_config.json"


def _load_config():
    # Read bot token, allowed chat IDs, and password from env or config file
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
            pass

    return token, chat_ids, password


class TelegramBot:
    def __init__(self, controls, token=None, allowed_chat_ids=None, password=None):
        env_token, env_chat_ids, env_password = _load_config()
        self.controls = controls
        self.token = token or env_token
        self.allowed_chat_ids = set(allowed_chat_ids) if allowed_chat_ids is not None else env_chat_ids
        self.password = password or env_password

        self._authenticated = set()
        self._share_codes = {}
        self._application = None
        self._loop = None
        self._thread = None

    def _authorized(self, update: Update) -> bool:
        return update.effective_chat.id in self.allowed_chat_ids

    def _persist_chat_ids(self):
        # Save updated chat IDs to tele_config.json
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
        # Check authorization and login state
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
            "/set_temp <temp> - set AC temperature (16-30C)\n"
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
        # Create a single-use share code
        if not await self._require_auth(update):
            return
        code = os.urandom(4).hex()
        self._share_codes[code] = False
        await update.message.reply_text(
            "Share code: /accept " + code + "\n"
            "Send this to your co-owner. It can be used once."
        )

    async def _cmd_accept(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Redeem a share code to get co-owner access
        if not context.args:
            await update.message.reply_text("Usage: /accept <share-code>")
            return
        code = context.args[0]
        if code not in self._share_codes or self._share_codes[code]:
            await update.message.reply_text("Invalid or already used share code.")
            return

        self._share_codes[code] = True
        chat_id = update.effective_chat.id
        self.allowed_chat_ids.add(chat_id)
        self._authenticated.add(chat_id)
        self._persist_chat_ids()
        await update.message.reply_text(
            "Access granted! You are now a co-owner of this car.\n"
            "Run /start for a list of commands."
        )

    async def _cmd_revoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Revoke access for a co-owner chat ID
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
        # Show all authorized chat IDs
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
        # Display car status
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

    async def _cmd_set_temp(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Set AC temperature
        if not await self._require_auth(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: /set_temp <temperature> (e.g. /set_temp 25)")
            return
        try:
            temp = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Invalid temperature - must be a whole number between 16 and 30.")
            return

        if "set_ac_temp" not in self.controls:
            await update.message.reply_text("AC temperature control is not supported.")
            return

        success, msg = self.controls["set_ac_temp"](temp)
        if success:
            await update.message.reply_text(f"AC temperature set to {temp}C.")
        else:
            await update.message.reply_text(msg or "Failed to set AC temperature.")

    def start(self):
        # Start Telegram polling thread
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
        self._application.add_handler(CommandHandler("set_temp", self._cmd_set_temp))

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
        # Stop event loop and join thread
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def notify_text(self, text):
        # Send text notification to all allowed chats
        if self._loop is None or self._application is None:
            return
        for chat_id in self.allowed_chat_ids:
            asyncio.run_coroutine_threadsafe(
                self._application.bot.send_message(chat_id=chat_id, text=text),
                self._loop,
            )

    def notify_photo(self, photo_path, caption=None):
        # Send photo notification to all allowed chats
        if self._loop is None or self._application is None:
            return
        for chat_id in self.allowed_chat_ids:
            asyncio.run_coroutine_threadsafe(
                self._application.bot.send_photo(chat_id=chat_id, photo=open(photo_path, "rb"), caption=caption),
                self._loop,
            )


if __name__ == "__main__":
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

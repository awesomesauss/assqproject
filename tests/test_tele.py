#!/usr/bin/env python3
"""
Unit tests for tele.py module
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import os
import sys
import threading
import asyncio

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the module - we'll need to mock the telegram imports
with patch.dict('sys.modules', {
    'telegram': Mock(),
    'telegram.ext': Mock()
}):
    import tele


class TestTelegramBotInitialization:
    """Test cases for TelegramBot initialization"""

    def setup_method(self):
        """Set up test fixtures"""
        # Mock controls dict
        self.controls = {
            "get_status": Mock(),
            "lock_door": Mock(),
            "unlock_door": Mock(),
            "engine_on": Mock(),
            "engine_off": Mock()
        }

    def test_init_with_all_parameters(self):
        """Test initialization with all parameters provided"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={123, 456},
            password="test_password"
        )

        assert bot.controls == self.controls
        assert bot.token == "test_token"
        assert bot.allowed_chat_ids == {123, 456}
        assert bot.password == "test_password"
        assert bot._authenticated == set()
        assert bot._share_codes == {}
        assert bot._application is None
        assert bot._loop is None
        assert bot._thread is None

    def test_init_with_env_fallback(self):
        """Test initialization falling back to environment variables"""
        with patch.dict('os.environ', {
            'TELEGRAM_BOT_TOKEN': 'env_token',
            'TELEGRAM_CHAT_ID': '111,222',
            'TELEGRAM_PASSWORD': 'env_password'
        }):
            bot = tele.TelegramBot(controls=self.controls)

            assert bot.token == 'env_token'
            assert bot.allowed_chat_ids == {111, 222}
            assert bot.password == 'env_password'

    def test_init_with_config_file_fallback(self):
        """Test initialization falling back to config file"""
        # Clear relevant environment variables
        with patch.dict('os.environ', {
            'TELEGRAM_BOT_TOKEN': '',
            'TELEGRAM_CHAT_ID': '',
            'TELEGRAM_PASSWORD': ''
        }):
            # Mock config file
            mock_config = {
                "token": "file_token",
                "chat_ids": [333, 444],
                "password": "file_password"
            }

            with patch('builtins.open', mock_open_read_data=json.dumps(mock_config)), \
                 patch('os.path.exists', return_value=True):
                bot = tele.TelegramBot(controls=self.controls)

                assert bot.token == "file_token"
                assert bot.allowed_chat_ids == {333, 444}
                assert bot.password == "file_password"

    def test_init_no_config(self):
        """Test initialization with no config anywhere"""
        with patch.dict('os.environ', {
            'TELEGRAM_BOT_TOKEN': '',
            'TELEGRAM_CHAT_ID': '',
            'TELEGRAM_PASSWORD': ''
        }):
            # Mock no config file
            with patch('builtins.open', side_effect=FileNotFoundError):
                bot = tele.TelegramBot(controls=self.controls)

                assert bot.token is None
                assert bot.allowed_chat_ids == set()
                assert bot.password is None


class TestTelegramBotAuthorization:
    """Test cases for authorization methods"""

    def setup_method(self):
        """Set up test fixtures"""
        self.controls = {
            "get_status": Mock(),
            "lock_door": Mock(),
            "unlock_door": Mock(),
            "engine_on": Mock(),
            "engine_off": Mock()
        }
        self.bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100, 200},
            password="test_password"
        )

    def test_authorized_true(self):
        """Test _authorized returns True for allowed chat ID"""
        update = Mock()
        update.effective_chat.id = 100

        assert self.bot._authorized(update) == True

    def test_authorized_false(self):
        """Test _authorized returns False for non-allowed chat ID"""
        update = Mock()
        update.effective_chat.id = 999

        assert self.bot._authorized(update) == False

    def test_persist_chat_ids(self):
        """Test persisting chat IDs to config file"""
        self.bot.token = "test_token"
        self.bot.password = "test_password"
        self.bot.allowed_chat_ids = {100, 200, 300}

        with patch('builtins.open', mock_open()) as mock_file:
            self.bot._persist_chat_ids()

            # Should have opened config file for writing
            mock_file.assert_called_once_with("tele_config.json", "w")
            # Should have written JSON data
            handle = mock_file()
            handle.write.assert_called()

    def test_persist_chat_ids_io_error(self):
        """Test handling IO error when persisting chat IDs"""
        self.bot.token = "test_token"
        self.bot.password = "test_password"
        self.bot.allowed_chat_ids = {100}

        with patch('builtins.open', side_effect=OSError("Disk full")):
            # Should not raise exception
            self.bot._persist_chat_ids()
            # If we get here, test passes

    def test_reply_unauthorized(self):
        """Test sending unauthorized reply"""
        update = Mock()
        update.effective_chat.id = 999
        update.message.reply_text = Mock()

        # This is an async method, so we need to run it
        async def run_test():
            await self.bot._reply_unauthorized(update)

        asyncio.run(run_test())

        # Should have replied with unauthorized message
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Not authorized" in args[0]
        assert "999" in args[0]  # Should contain chat ID

    def test_require_auth_authorized_and_logged_in(self):
        """Test _require_auth returns True for authorized, logged-in user"""
        update = Mock()
        update.effective_chat.id = 100  # Authorized
        self.bot._authenticated.add(100)  # Logged in

        # This is an async method
        async def run_test():
            return await self.bot._require_auth(update)

        result = asyncio.run(run_test())
        assert result == True

    def test_require_auth_not_authorized(self):
        """Test _require_auth returns False for non-authorized user"""
        update = Mock()
        update.effective_chat.id = 999  # Not authorized
        update.message.reply_text = Mock()

        async def run_test():
            return await self.bot._require_auth(update)

        result = asyncio.run(run_test())
        assert result == False
        update.message.reply_text.assert_called_once()  # Should have sent unauthorized reply

    def test_require_auth_authorized_not_logged_in(self):
        """Test _require_auth returns False for authorized but not logged-in user"""
        update = Mock()
        update.effective_chat.id = 100  # Authorized
        # Not in _authenticated set
        update.message.reply_text = Mock()

        async def run_test():
            return await self.bot._require_auth(update)

        result = asyncio.run(run_test())
        assert result == False
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Please log in first" in args[0]


class TestTelegramBotCommandHandlers:
    """Test cases for command handlers"""

    def setup_method(self):
        """Set up test fixtures"""
        self.controls = {
            "get_status": Mock(return_value={
                "door_locked": True,
                "engine_on": False,
                "fuel": 75,
                "battery": 80,
                "engine_temp": 60,
                "cabin_temp": 22.5
            }),
            "lock_door": Mock(),
            "unlock_door": Mock(),
            "engine_on": Mock(),
            "engine_off": Mock()
        }
        self.bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100},
            password="test_password"
        )
        self.bot._authenticated.add(100)  # Pre-authenticate for most tests

    def test_cmd_start(self):
        """Test /start command"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()

        async def run_test():
            await self.bot._cmd_start(update, Mock())

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        message = args[0]
        assert "Car Control remote" in message
        assert "/status" in message
        assert "/lock" in message
        assert "Status:" in message

    def test_cmd_start_not_authorized(self):
        """Test /start command for non-authorized user"""
        update = Mock()
        update.effective_chat.id = 999  # Not authorized
        update.message.reply_text = Mock()

        async def run_test():
            await self.bot._cmd_start(update, Mock())

        asyncio.run(run_test())

        # Should have sent unauthorized reply
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Not authorized" in args[0]

    def test_cmd_login_success(self):
        """Test /login command with correct password"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["test_password"]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_login(update, context)

        asyncio.run(run_test())

        # Should have added to authenticated set
        assert 100 in self.bot._authenticated
        update.message.reply_text.assert_called_once()

    def test_cmd_login_wrong_password(self):
        """Test /login command with wrong password"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["wrong_password"]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_login(update, context)

        asyncio.run(run_test())

        # Should NOT have added to authenticated set
        assert 100 not in self.bot._authenticated
        update.message.reply_text.assert_called_once()

    def test_cmd_login_no_args(self):
        """Test /login command with no arguments"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()
        context.args = []

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_login(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()

    def test_cmd_login_no_password_configureed(self):
        """Test /login command when no password is configured"""
        bot_no_pass = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100},
            password=None  # No password
        )
        bot_no_pass._authenticated.add(100)  # Authorize for test

        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["any_password"]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await bot_no_pass._cmd_login(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()

    def test_cmd_logout(self):
        """Test /logout command"""
        update = Mock()
        update.effective_chat.id = 100
        self.bot._authenticated.add(100)
        update.message.reply_text = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_logout(update, Mock())

        asyncio.run(run_test())

        # Should have removed from authenticated set
        assert 100 not in self.bot._authenticated
        update.message.reply_text.assert_called_once()

    def test_cmd_share_success(self):
        """Test /share command success"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_share(update, context)

        asyncio.run(run_test())

        # Should have generated a share code
        assert len(self.bot._share_codes) == 1
        share_code = list(self.bot._share_codes.keys())[0]
        assert len(share_code) == 8  # 8 hex characters from 4 random bytes
        assert self.bot._share_codes[share_code] == False  # Not used yet

        update.message.reply_text.assert_called_once()

    def test_cmd_share_not_authorized(self):
        """Test /share command for non-authorized user"""
        update = Mock()
        update.effective_chat.id = 999  # Not authorized
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_share(update, context)

        asyncio.run(run_test())

        # Should not have generated share code
        assert len(self.bot._share_codes) == 0
        # Should have sent unauthorized reply
        update.message.reply_text.assert_called_once()

    def test_cmd_share_not_logged_in(self):
        """Test /share command for authorized but not logged-in user"""
        update = Mock()
        update.effective_chat.id = 100  # Authorized
        # Not in _authenticated set
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_share(update, context)

        asyncio.run(run_test())

        # Should not have generated share code
        assert len(self.bot._share_codes) == 0
        # Should have sent login required message via _require_auth

    def test_cmd_accept_success(self):
        """Test /accept command with valid, unused share code"""
        # First generate a share code
        share_code = "abcd1234"
        self.bot._share_codes[share_code] = False

        update = Mock()
        update.effective_chat.id = 200  # New user trying to accept
        update.message.reply_text = Mock()
        context = Mock()
        context.args = [share_code]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_accept(update, context)

        asyncio.run(run_test())

        # Share code should now be marked as used
        assert self.bot._share_codes[share_code] == True
        # User's chat ID should be added to allowed list
        assert 200 in self.bot._allowed_chat_ids
        # User should be automatically authenticated
        assert 200 in self.bot._authenticated
        # Should have persisted chat IDs
        # (we'd need to mock _persist_chat_ids to verify)

        update.message.reply_text.assert_called_once()

    def test_cmd_accept_invalid_code(self):
        """Test /accept command with invalid share code"""
        update = Mock()
        update.effective_chat.id = 200
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["invalid"]  # Not in _share_codes

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_accept(update, context)

        asyncio.run(run_test())

        # Should not have modified anything
        assert 200 not in self.bot._allowed_chat_ids
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Invalid or already used share code" in args[0]

    def test_cmd_accept_used_code(self):
        """Test /accept command with already used share code"""
        # Mark share code as used
        share_code = "abcd1234"
        self.bot._share_codes[share_code] = True

        update = Mock()
        update.effective_chat.id = 200
        update.message.reply_text = Mock()
        context = Mock()
        context.args = [share_code]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_accept(update, context)

        asyncio.run(run_test())

        # Should not have modified anything
        assert 200 not in self.bot._allowed_chat_ids
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Invalid or already used share code" in args[0]

    def test_cmd_accept_no_args(self):
        """Test /accept command with no arguments"""
        update = Mock()
        update.effective_chat.id = 200
        update.message.reply_text = Mock()
        context = Mock()
        context.args = []

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_accept(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Usage: /accept <share-code>" in args[0]

    def test_cmd_revoke_success(self):
        """Test /revoke command success"""
        # Set up: user 200 is authorized
        self.bot._allowed_chat_ids.add(200)
        self.bot._authenticated.add(200)

        update = Mock()
        update.effective_chat.id = 100  # Owner (authorized and logged in)
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["200"]  # Chat ID to revoke

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_revoke(update, context)

        asyncio.run(run_test())

        # User 200 should be removed from allowed list
        assert 200 not in self.bot._allowed_chat_ids
        # User 200 should be removed from authenticated set
        assert 200 not in self.bot._authenticated
        # Should have persisted chat IDs

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Revoked access for chat 200" in args[0]

    def test_cmd_revoke_not_owner(self):
        """Test /revoke command by non-owner"""
        update = Mock()
        update.effective_chat.id = 200  # Not owner (not logged in)
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["300"]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_revoke(update, context)

        asyncio.run(run_test())

        # Should not have revoked anything
        assert 300 in self.bot._allowed_chat_ids  # Assuming we added it elsewhere
        # Should have sent login required message
        update.message.reply_text.assert_called_once()

    def test_cmd_revoke_not_authorized(self):
        """Test /revoke command by non-authorized user"""
        update = Mock()
        update.effective_chat.id = 999  # Not authorized
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["300"]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_revoke(update, context)

        asyncio.run(run_test())

        # Should not have revoked anything
        # Should have sent unauthorized reply
        update.message.reply_text.assert_called_once()

    def test_cmd_revoke_invalid_chat_id(self):
        """Test /revoke command with invalid chat ID"""
        update = Mock()
        update.effective_chat.id = 100  # Owner
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["not_a_number"]

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_revoke(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Invalid chat ID" in args[0]

    def test_cmd_revoke_nonexistent_user(self):
        """Test /revoke command for user not in allowed list"""
        update = Mock()
        update.effective_chat.id = 100  # Owner
        update.message.reply_text = Mock()
        context = Mock()
        context.args = ["999"]  # Not in allowed list

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_revoke(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "is not authorized" in args[0]

    def test_cmd_list(self):
        """Test /list command"""
        # Set up some authorized users
        self.bot._allowed_chat_ids = {100, 200, 300}
        self.bot._authenticated.add(100)  # 100 is logged in
        # 200 and 300 are not logged in

        update = Mock()
        update.effective_chat.id = 100  # Owner
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_list(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        message = args[0]
        assert "Authorized chat IDs:" in message
        assert "100 (logged in)" in message
        assert "200" in message and "(logged in)" not in message.split("200")[1][:10]
        assert "300" in message and "(logged in)" not in message.split("300")[1][:10]

    def test_cmd_list_no_authorized(self):
        """Test /list command when no users authorized"""
        self.bot._allowed_chat_ids = set()

        update = Mock()
        update.effective_chat.id = 100  # Owner
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_list(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "No authorized chat IDs" in args[0]

    def test_cmd_status(self):
        """Test /status command"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_status(update, context)

        asyncio.run(run_test())

        # Should have called get_status
        self.controls["get_status"].assert_called_once()
        # Should have replied with formatted status
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        message = args[0]
        assert "Door: LOCKED" in message  # Based on our mock return value
        assert "Engine: OFF" in message
        assert "Fuel: 75%" in message
        assert "Battery: 80%" in message
        assert "Engine temp: 60C" in message
        assert "Cabin temp: 22.5C" in message

    def test_cmd_status_cabin_temp_unavailable(self):
        """Test /status command when cabin temp is unavailable"""
        # Mock get_status to return invalid cabin temp
        self.controls["get_status"].return_value = {
            "door_locked": True,
            "engine_on": False,
            "fuel": 75,
            "battery": 80,
            "engine_temp": 60,
            "cabin_temp": -100  # Invalid reading
        }

        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_status(update, context)

        asyncio.run(run_test())

        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        message = args[0]
        assert "Cabin temp: N/A" in message  # Should show N/A for invalid temp

    def test_cmd_lock(self):
        """Test /lock command"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_lock(update, context)

        asyncio.run(run_test())

        # Should have called lock_door control
        self.controls["lock_door"].assert_called_once()
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Door locked." in args[0]

    def test_cmd_unlock(self):
        """Test /unlock command"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_unlock(update, context)

        asyncio.run(run_test())

        # Should have called unlock_door control
        self.controls["unlock_door"].assert_called_once()
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Door unlocked." in args[0]

    def test_cmd_engine_on(self):
        """Test /engine_on command"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_engine_on(update, context)

        asyncio.run(run_test())

        # Should have called engine_on control
        self.controls["engine_on"].assert_called_once()
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Engine started." in args[0]

    def test_cmd_engine_off(self):
        """Test /engine_off command"""
        update = Mock()
        update.effective_chat.id = 100
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_engine_off(update, context)

        asyncio.run(run_test())

        # Should have called engine_off control
        self.controls["engine_off"].assert_called_once()
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Engine stopped." in args[0]

    def test_cmd_status_not_authorized(self):
        """Test /status command for non-authorized user"""
        update = Mock()
        update.effective_chat.id = 999  # Not authorized
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_status(update, context)

        asyncio.run(run_test())

        # Should not have called get_status
        self.controls["get_status"].assert_not_called()
        # Should have sent unauthorized reply
        update.message.reply_text.assert_called_once()

    def test_cmd_status_not_logged_in(self):
        """Test /status command for authorized but not logged-in user"""
        update = Mock()
        update.effective_chat.id = 100  # Authorized
        # Not in _authenticated set
        update.message.reply_text = Mock()
        context = Mock()

        # Create a proper mock for the async method
        async def mock_reply_text(*args, **kwargs):
            pass

        update.message.reply_text = mock_reply_text

        async def run_test():
            await self.bot._cmd_status(update, context)

        asyncio.run(run_test())

        # Should not have called get_status
        self.controls["get_status"].assert_not_called()
        # Should have sent login required message
        update.message.reply_text.assert_called_once()


class TestTelegramBotStartStop:
    """Test cases for starting and stopping the bot"""

    def setup_method(self):
        """Set up test fixtures"""
        self.controls = {
            "get_status": Mock(),
            "lock_door": Mock(),
            "unlock_door": Mock(),
            "engine_on": Mock(),
            "engine_off": Mock()
        }

    def test_start_no_token(self):
        """Test starting bot when no token is configured"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token=None,  # No token
            allowed_chat_ids={100}
        )

        result = bot.start()
        assert result == False
        assert bot._application is None
        assert bot._thread is None

    def test_start_no_allowed_chat_ids(self):
        """Test starting bot when no allowed chat IDs are configured"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids=set()  # No allowed IDs
        )

        result = bot.start()
        assert result == False

    def test_start_success(self):
        """Test successful bot start"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100}
        )

        # Mock the telegram bot components
        with patch('tele.Application.builder') as mock_builder, \
             patch('telegram.ext.Application') as mock_app_class:

            mock_builder.return_value.token.return_value.build.return_value = Mock()
            mock_app_instance = Mock()
            mock_builder.return_value.token.return_value.build.return_value = mock_app_instance

            result = bot.start()

            # Should return True on success
            assert result == True
            # Should have started a thread
            assert bot._thread is not None
            assert isinstance(bot._thread, threading.Thread)

    def test_stop_not_started(self):
        """Test stopping bot that was never started"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100}
        )
        # Don't call start()

        # Should not raise exception
        bot.stop()
        # If we get here, test passes

    def test_stop_started_bot(self):
        """Test stopping bot that was started"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100}
        )

        # Mock the internal state as if started
        bot._loop = Mock()
        bot._thread = Mock()

        bot.stop()

        # Should have called stop on the loop
        bot._loop.call_soon_threadsafe.assert_called_once_with(bot._loop.stop)
        # Should have joined the thread
        bot._thread.join.assert_called_once_with(timeout=5)


class TestTelegramBotNotifications:
    """Test cases for notification methods"""

    def setup_method(self):
        """Set up test fixtures"""
        self.controls = {
            "get_status": Mock(),
            "lock_door": Mock(),
            "unlock_door": Mock(),
            "engine_on": Mock(),
            "engine_off": Mock()
        }
        self.bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100, 200}
        )
        # Mock the internal state as if started
        self.bot._loop = Mock()
        self.bot._application = Mock()
        self.bot._application.bot = Mock()

    def test_notify_text_not_started(self):
        """Test notify_text when bot not started"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100}
        )
        # _loop and _application are None (not started)

        # Should not raise exception
        bot.notify_text("Test message")
        # If we get here, test passes

    def test_notify_text_success(self):
        """Test successful text notification"""
        # Set up as started
        self.bot._loop = Mock()
        self.bot._application = Mock()
        self.bot._application.bot = Mock()

        # Send notification
        self.bot.notify_text("Test alert")

        # Should have called send_message for each allowed chat ID
        assert self.bot._application.bot.send_message.call_count == 2
        # Check that it was called with correct parameters
        calls = self.bot._application.bot.send_message.call_args_list
        assert any(call[1]['chat_id'] == 100 and call[1]['text'] == "Test alert" for call in calls)
        assert any(call[1]['chat_id'] == 200 and call[1]['text'] == "Test alert" for call in calls)
        # Should have used run_coroutine_threadsafe for each
        assert self.bot._loop.run_coroutine_threadsafe.call_count == 2

    def test_notify_photo_not_started(self):
        """Test notify_photo when bot not started"""
        bot = tele.TelegramBot(
            controls=self.controls,
            token="test_token",
            allowed_chat_ids={100}
        )
        # _loop and _application are None

        # Should not raise exception
        bot.notify_photo("/fake/path.jpg")
        # If we get here, test passes

    def test_notify_photo_success(self):
        """Test successful photo notification"""
        # Set up as started
        self.bot._loop = Mock()
        self.bot._application = Mock()
        self.bot._application.bot = Mock()

        # Send notification
        self.bot.notify_photo("/fake/path.jpg", "Test caption")

        # Should have called send_photo for each allowed chat ID
        assert self.bot._application.bot.send_photo.call_count == 2
        # Check that it was called with correct parameters
        calls = self.bot._application.bot.send_photo.call_args_list
        assert any(call[1]['chat_id'] == 100 and
                  call[1]['photo'].name == "/fake/path.jpg" and
                  call[1]['caption'] == "Test caption" for call in calls)
        assert any(call[1]['chat_id'] == 200 and
                  call[1]['photo'].name == "/fake/path.jpg" and
                  call[1]['caption'] == "Test caption" for call in calls)
        # Should have used run_coroutine_threadsafe for each
        assert self.bot._loop.run_coroutine_threadsafe.call_count == 2

    def test_notify_photo_file_error(self):
        """Test notify_photo when photo file cannot be opened"""
        # Set up as started
        self.bot._loop = Mock()
        self.bot._application = Mock()
        self.bot._application.bot = Mock()

        # Mock open to raise an exception
        with patch('builtins.open', side_effect=OSError("File not found")):
            # Should not raise exception
            self.bot.notify_photo("/fake/path.jpg", "Test caption")
            # If we get here, test passes
            # Should not have attempted to send any photos
            self.bot._application.bot.send_photo.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
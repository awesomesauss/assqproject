import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import tele
from tele import TelegramBot, _load_config


def test_config_loading_and_persistence(monkeypatch, tmp_tele_config_file):
    """Test configuration loading from environment variables vs JSON file, and allowlist persistence."""
    # 1. Environment variables take priority
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env_token_123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111,222")
    monkeypatch.setenv("TELEGRAM_PASSWORD", "env_secret")

    token, chat_ids, password = _load_config()
    assert token == "env_token_123"
    assert chat_ids == {111, 222}
    assert password == "env_secret"

    # 2. File fallback when env vars are absent
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_PASSWORD", raising=False)

    with open(tmp_tele_config_file, "w") as f:
        json.dump({"token": "file_token_456", "chat_ids": [333, 444], "password": "file_pass"}, f)

    token, chat_ids, password = _load_config()
    assert token == "file_token_456"
    assert chat_ids == {333, 444}
    assert password == "file_pass"

    # 3. Persistence of chat IDs back to JSON file
    bot = TelegramBot(controls={}, token="tok", allowed_chat_ids={12345}, password="pwd")
    bot._persist_chat_ids()
    with open(tmp_tele_config_file, "r") as f:
        data = json.load(f)
    assert data["chat_ids"] == [12345]


@pytest.mark.asyncio
async def test_auth_and_login_flow(tmp_tele_config_file):
    """Test allowlist enforcement, /login with valid/invalid passwords, and /logout."""
    bot = TelegramBot(controls={}, token="tok", allowed_chat_ids={100}, password="secretpassword")

    # 1. Reject unauthorized chat ID
    unauth_update = MagicMock()
    unauth_update.effective_chat.id = 999
    unauth_update.message.reply_text = AsyncMock()
    assert not bot._authorized(unauth_update)
    assert not await bot._require_auth(unauth_update)
    unauth_update.message.reply_text.assert_called_once()

    # 2. Authorized chat requires login first
    auth_update = MagicMock()
    auth_update.effective_chat.id = 100
    auth_update.message.reply_text = AsyncMock()
    assert bot._authorized(auth_update)
    assert not await bot._require_auth(auth_update)

    # 3. Failed login with wrong password
    context = MagicMock()
    context.args = ["wrong_pwd"]
    await bot._cmd_login(auth_update, context)
    assert 100 not in bot._authenticated

    # 4. Successful login
    context.args = ["secretpassword"]
    await bot._cmd_login(auth_update, context)
    assert 100 in bot._authenticated
    assert await bot._require_auth(auth_update)

    # 5. Logout
    await bot._cmd_logout(auth_update, context)
    assert 100 not in bot._authenticated


@pytest.mark.asyncio
async def test_command_status_reporting(tmp_tele_config_file):
    """Test /status command output with formatted metrics."""
    mock_controls = {
        "get_status": MagicMock(return_value={
            "door_locked": True,
            "engine_on": False,
            "fuel": 85,
            "battery": 90,
            "engine_temp": 45,
            "cabin_temp": 21.5,
        })
    }
    bot = TelegramBot(controls=mock_controls, token="tok", allowed_chat_ids={100}, password="pwd")
    bot._authenticated.add(100)

    update = MagicMock()
    update.effective_chat.id = 100
    update.message.reply_text = AsyncMock()

    await bot._cmd_status(update, MagicMock())
    update.message.reply_text.assert_called_once()
    reply_msg = update.message.reply_text.call_args[0][0]
    assert "Door: LOCKED" in reply_msg
    assert "Engine: OFF" in reply_msg
    assert "Fuel: 85%" in reply_msg
    assert "Battery: 90%" in reply_msg
    assert "Engine temp: 45C" in reply_msg
    assert "Cabin temp: 21.5C" in reply_msg


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd_name,control_key,expected_reply", [
    ("_cmd_lock", "lock_door", "Door locked."),
    ("_cmd_unlock", "unlock_door", "Door unlocked."),
    ("_cmd_engine_on", "engine_on", "Engine started."),
    ("_cmd_engine_off", "engine_off", "Engine stopped."),
])
async def test_vehicle_remote_control_commands(cmd_name, control_key, expected_reply, tmp_tele_config_file):
    """Test /lock, /unlock, /engine_on, /engine_off remote commands."""
    mock_callback = MagicMock()
    mock_controls = {control_key: mock_callback}
    bot = TelegramBot(controls=mock_controls, token="tok", allowed_chat_ids={100}, password="pwd")
    bot._authenticated.add(100)

    update = MagicMock()
    update.effective_chat.id = 100
    update.message.reply_text = AsyncMock()

    cmd_method = getattr(bot, cmd_name)
    await cmd_method(update, MagicMock())

    mock_callback.assert_called_once()
    update.message.reply_text.assert_called_once_with(expected_reply)


@pytest.mark.asyncio
async def test_set_temp_command(tmp_tele_config_file):
    """Test /set_temp command handling valid values, invalid formats, and range errors."""
    mock_set_temp = MagicMock(return_value=(True, "AC temperature set to 25C."))
    bot = TelegramBot(controls={"set_ac_temp": mock_set_temp}, token="tok", allowed_chat_ids={100}, password="pwd")
    bot._authenticated.add(100)

    update = MagicMock()
    update.effective_chat.id = 100
    update.message.reply_text = AsyncMock()

    # 1. Missing argument
    context = MagicMock()
    context.args = []
    await bot._cmd_set_temp(update, context)
    assert "Usage: /set_temp" in update.message.reply_text.call_args[0][0]

    # 2. Invalid non-integer argument
    update.message.reply_text.reset_mock()
    context.args = ["cold"]
    await bot._cmd_set_temp(update, context)
    assert "Invalid temperature" in update.message.reply_text.call_args[0][0]

    # 3. Valid temperature setting (e.g. 25)
    update.message.reply_text.reset_mock()
    context.args = ["25"]
    await bot._cmd_set_temp(update, context)
    mock_set_temp.assert_called_once_with(25)
    assert "AC temperature set to 25C." in update.message.reply_text.call_args[0][0]

    # 4. Out of range failure from callback
    mock_set_temp.reset_mock()
    mock_set_temp.return_value = (False, "Temperature must be between 16C and 30C.")
    update.message.reply_text.reset_mock()
    context.args = ["35"]
    await bot._cmd_set_temp(update, context)
    mock_set_temp.assert_called_once_with(35)
    assert "Temperature must be between 16C and 30C." in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_digital_key_share_accept_revoke_lifecycle(tmp_tele_config_file):
    """Test full digital key lifecycle: /share, /accept, replay rejection, /list, and /revoke."""
    bot = TelegramBot(controls={}, token="tok", allowed_chat_ids={100}, password="pwd")
    bot._authenticated.add(100)

    owner_update = MagicMock()
    owner_update.effective_chat.id = 100
    owner_update.message.reply_text = AsyncMock()

    # 1. Owner generates share key via /share
    await bot._cmd_share(owner_update, MagicMock())
    assert len(bot._share_codes) == 1
    code = list(bot._share_codes.keys())[0]

    # 2. Co-owner redeems key via /accept <code>
    co_owner_update = MagicMock()
    co_owner_update.effective_chat.id = 200
    co_owner_update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = [code]
    await bot._cmd_accept(co_owner_update, context)
    assert 200 in bot.allowed_chat_ids
    assert 200 in bot._authenticated

    # 3. Attempting to reuse same share key fails (single-use)
    replay_update = MagicMock()
    replay_update.effective_chat.id = 300
    replay_update.message.reply_text = AsyncMock()
    await bot._cmd_accept(replay_update, context)
    assert 300 not in bot.allowed_chat_ids

    # 4. /list shows both authorized chat IDs
    await bot._cmd_list(owner_update, MagicMock())
    list_reply = owner_update.message.reply_text.call_args[0][0]
    assert "100 (logged in)" in list_reply
    assert "200 (logged in)" in list_reply

    # 5. Owner revokes co-owner access via /revoke 200
    revoke_context = MagicMock()
    revoke_context.args = ["200"]
    await bot._cmd_revoke(owner_update, revoke_context)
    assert 200 not in bot.allowed_chat_ids
    assert 200 not in bot._authenticated


def test_push_notifications():
    """Test notify_text and notify_photo notification dispatchers."""
    bot = TelegramBot(controls={}, token="tok", allowed_chat_ids={100, 200}, password="pwd")

    # When bot is not running (_loop is None), notifications safely no-op
    bot.notify_text("Test alert")
    bot.notify_photo("nonexistent.jpg")

    # With mocked loop & application
    mock_loop = MagicMock()
    mock_app = MagicMock()
    bot._loop = mock_loop
    bot._application = mock_app

    with patch("asyncio.run_coroutine_threadsafe") as mock_dispatch:
        bot.notify_text("Alarm sounding!")
        assert mock_dispatch.call_count == 2  # One per chat_id (100, 200)

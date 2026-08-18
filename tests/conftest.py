import sys
import os
import json
import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Hardware mocks for non-Raspberry Pi / CI test execution
# ---------------------------------------------------------------------------

def _setup_hardware_mocks():
    # Mock RPi and RPi.GPIO
    if "RPi" not in sys.modules:
        mock_rpi = MagicMock()
        mock_gpio = MagicMock()
        mock_gpio.BCM = 11
        mock_gpio.BOARD = 10
        mock_gpio.OUT = 0
        mock_gpio.IN = 1
        mock_gpio.PUD_UP = 2
        mock_gpio.PUD_DOWN = 1
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0
        mock_gpio.PWM = MagicMock(return_value=MagicMock())
        mock_rpi.GPIO = mock_gpio
        sys.modules["RPi"] = mock_rpi
        sys.modules["RPi.GPIO"] = mock_gpio

    # Mock smbus
    if "smbus" not in sys.modules:
        mock_smbus = MagicMock()
        mock_smbus.SMBus = MagicMock()
        sys.modules["smbus"] = mock_smbus

    # Mock spi / spidev
    if "spi" not in sys.modules:
        sys.modules["spi"] = MagicMock()
    if "spidev" not in sys.modules:
        sys.modules["spidev"] = MagicMock()

    # Mock picamera and picamera2
    if "picamera" not in sys.modules:
        sys.modules["picamera"] = MagicMock()
    if "picamera2" not in sys.modules:
        sys.modules["picamera2"] = MagicMock()

_setup_hardware_mocks()

# Ensure repository root is in sys.path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Initialize hal sensors to avoid uninitialized global errors
from hal import hal_temp_humidity_sensor
try:
    hal_temp_humidity_sensor.init()
except Exception:
    pass


@pytest.fixture
def tmp_settings_file(tmp_path, monkeypatch):
    """Fixture providing an isolated car_settings.json file."""
    import car_control
    settings_path = str(tmp_path / "car_settings.json")
    monkeypatch.setattr(car_control, "SETTINGS_FILE", settings_path)
    return settings_path


@pytest.fixture
def tmp_tele_config_file(tmp_path, monkeypatch):
    """Fixture providing an isolated tele_config.json file."""
    import tele
    config_path = str(tmp_path / "tele_config.json")
    monkeypatch.setattr(tele, "CONFIG_FILE", config_path)
    return config_path

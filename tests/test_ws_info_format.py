import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

config_module = sys.modules.get("config")
if config_module is None:
  config_module = types.SimpleNamespace()
  sys.modules["config"] = config_module

if not hasattr(config_module, "WEB_HOST_ADDRESS"):
  config_module.WEB_HOST_ADDRESS = "127.0.0.1"
if not hasattr(config_module, "WEB_SERVER_PORT"):
  config_module.WEB_SERVER_PORT = 8080
if not hasattr(config_module, "WEB_ALLOWED_IPS"):
  config_module.WEB_ALLOWED_IPS = ["127.0.0.1"]
if not hasattr(config_module, "API_KEY"):
  config_module.API_KEY = ""

from observer.observer_client import Color
from webserver.ws_client import format_info_message


def test_format_info_message_with_round_time_scores_and_bomb():
  message = format_info_message(
    map_name="cs_office32",
    current_players=[
      {"name": ">|< Mep3ocTb", "steam_id": "STEAM_0:1:45686725", "stats": [0, 16, 1]},
      {"name": "49.5 | Pheonix", "steam_id": "BOT", "stats": [4, 8, 2]},
    ],
    max_players=32,
    player_count_override=2,
    map_timeleft_sec=125,
    round_number=4,
    score_t=2,
    score_ct=1,
    bomb_carrier_steam_id="STEAM_0:1:45686725",
  )

  assert "Название карты: cs_office32" in message
  assert "Количество игроков: 2 / 32" in message
  assert "До конца карты: 02:05" in message
  assert "Номер раунда: 4" in message
  assert "Terrorists(2):" in message
  assert "Counter-Terrorists(1):" in message
  assert ">|< Mep3ocTb - 0/16" in message
  assert f"{Color.Green}(bomb){Color.Default}" in message


def test_format_info_message_time_fallback():
  message = format_info_message(
    map_name="de_dust2",
    current_players=[],
    max_players=32,
    player_count_override=0,
    map_timeleft_sec=None,
    round_number=None,
  )

  assert "До конца карты: --:--" in message
  assert "Номер раунда: 0" in message

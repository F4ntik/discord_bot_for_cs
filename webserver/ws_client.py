from enum import Enum
from observer.observer_client import logger, observer, Event, nsroute, Color, TextStyle
from webserver.webhook_type import normalize_webhook_type, normalize_webhook_type_code
from webserver.web_server import WebServer, WebServerError

from aiohttp import web

from datetime import datetime
import config

# -- init
ws: WebServer = WebServer(host=config.WEB_HOST_ADDRESS,
                          port=config.WEB_SERVER_PORT,
                          allowed_ips=config.WEB_ALLOWED_IPS)

# -- Team labels
TEAM_DEFAULT_LABEL = "SPEC"
TEAM_LABELS = {
  1: "T",
  2: "CT",
  3: TEAM_DEFAULT_LABEL,
}

# -- Events
@observer.subscribe(Event.BE_READY)
async def run_ws():
  try:
    await ws.run_webserver()
    logger.info(f"WebServer: Сервер запущен на {ws.host}:{ws.port}. Список разрешенных IP: {ws.allowed_ips}")
  except Exception as err:
    logger.error(err)
  

# SECTION Utilities

# -- format_message
def format_message(nick, cs_message, team, channel_prefix):
  timestamp = datetime.now().strftime('%H:%M:%S')
    
  if team == 1:
    nick_color = Color.Red
  elif team == 2:
    nick_color = Color.Blue
  else:
    nick_color = Color.White

  return f"{Color.Green}{timestamp}{Color.Default} {channel_prefix} {nick_color}{nick}{Color.Default}: {cs_message}\n"

# -- _safe_int
def _safe_int(value, default=0):
  try:
    return int(value)
  except (TypeError, ValueError):
    return default

# -- _format_mmss
def _format_mmss(seconds):
  if not isinstance(seconds, int) or seconds < 0:
    return "--:--"
  minutes, secs = divmod(seconds, 60)
  return f"{minutes:02d}:{secs:02d}"

# -- format_info_message
def format_info_message(
  map_name,
  current_players,
  max_players,
  player_count_override=None,
  map_timeleft_sec=None,
  round_number=None,
  score_t=None,
  score_ct=None,
  bomb_carrier_steam_id=None,
  bomb_carrier_slot=None,
):
  current_players = current_players if isinstance(current_players, list) else []
  player_count = (
    player_count_override
    if isinstance(player_count_override, int) and player_count_override >= 0
    else len(current_players)
  )
  max_players = _safe_int(max_players, 0)
  score_t = _safe_int(score_t, 0)
  score_ct = _safe_int(score_ct, 0)
  round_number = _safe_int(round_number, 0)
  map_timeleft = _safe_int(map_timeleft_sec, -1)
  bomb_carrier_steam_id = str(bomb_carrier_steam_id or "")
  bomb_carrier_slot = _safe_int(bomb_carrier_slot, -1)
  team_players = {1: [], 2: [], 3: []}

  for player in current_players:
    if not isinstance(player, dict):
      continue

    player_name = str(player.get('name', 'Unknown'))
    stats = player.get('stats', [])
    if not isinstance(stats, list):
      stats = []
    frags = _safe_int(stats[0] if len(stats) > 0 else 0, 0)
    deaths = _safe_int(stats[1] if len(stats) > 1 else 0, 0)
    team = _safe_int(stats[2] if len(stats) > 2 else 3, 3)
    player_steam_id = str(player.get('steam_id', ''))
    player_slot = _safe_int(player.get('slot'), -1)

    bomb_suffix = ""
    is_bomb_carrier = False
    if bomb_carrier_slot > 0:
      is_bomb_carrier = player_slot == bomb_carrier_slot
    elif bomb_carrier_steam_id and player_steam_id:
      # Для ботов steam_id часто одинаковый ('BOT'), поэтому fallback по steam_id
      # используем только для неботов.
      is_bomb_carrier = (
        bomb_carrier_steam_id.upper() != "BOT"
        and player_steam_id == bomb_carrier_steam_id
      )

    if is_bomb_carrier:
      bomb_suffix = f" {Color.Green}(bomb){Color.Default}"

    if team in team_players:
      team_players[team].append(f"{player_name} - {frags}/{deaths}{bomb_suffix}")
    else: # По идее это UNASSIGNED, суем в спектров
      team_players[3].append(f"{player_name} - {frags}/{deaths}{bomb_suffix}")

  formatted_info = []
  formatted_info.append(f"Время: {datetime.now().strftime('%H:%M')}")
  formatted_info.append(f"Название карты: {map_name}")
  formatted_info.append(f"Количество игроков: {player_count} / {max_players}")
  formatted_info.append(f"До конца карты: {_format_mmss(map_timeleft)}")
  formatted_info.append(f"Номер раунда: {round_number}")

  if team_players[1]:
    formatted_info.append(f"\n{TextStyle.Bold}{Color.Red}Terrorists({score_t}):{TextStyle.Default}")
    formatted_info.append("\n".join(f"\t{player}" for player in team_players[1]))

  if team_players[2]:
    formatted_info.append(f"\n{TextStyle.Bold}{Color.Blue}Counter-Terrorists({score_ct}):{TextStyle.Default}")
    formatted_info.append("\n".join(f"\t{player}" for player in team_players[2]))

  if team_players[3]:
    formatted_info.append(f"\n{TextStyle.Bold}{Color.White}Spectators:{TextStyle.Default}")
    formatted_info.append("\n".join(f"\t{player}" for player in team_players[3]))

  return "\n".join(formatted_info)

# -- safe_request_url
def safe_request_url(request: web.Request) -> str:
  try:
    return str(request.url)
  except Exception:
    try:
      return str(request.rel_url)
    except Exception:
      return "<unknown>"

# -- normalize_webhook_payload
def normalize_webhook_payload(data):
  if isinstance(data, dict):
    return data

  # Некоторые HTTP-клиенты могут оборачивать объект в одноэлементный массив.
  if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
    return data[0]

  return None

# -- check_api_key
def check_api_key(request, request_url: str = "<unknown>"):
  expected_key = getattr(config, "API_KEY", None)

  # Если ключ не задан (пустая строка/None), авторизацию отключаем.
  # Основной барьер безопасности в этом случае — WEB_ALLOWED_IPS.
  if not expected_key:
    return True

  provided_key = request.headers.get("Authorization") or request.headers.get("X-Api-Key")
  if provided_key == expected_key:
    return True

  logger.info(
    "Webhook unauthorized: ip=%s method=%s url=%s content_length=%s content_type=%s user_agent=%s",
    request.remote,
    request.method,
    request_url,
    request.content_length,
    request.content_type,
    request.headers.get("User-Agent"),
  )
  return False

# !SECTION

# SECTION Web Hooks

# -- handle_message
async def handle_message(data: dict):
  import asyncio

  cs_message = data['message']
  nick = data['nick']
  team = data['team']
  team_number = None
  if isinstance(team, int):
    team_number = team
  else:
    try:
      team_number = int(team)
    except (TypeError, ValueError):
      team_number = None
  team_for_formatting = team_number if team_number is not None else team
  channel_prefix = data.get('channel', '')
  steam_id = data.get('steam_id', '')

  if not (cs_message and nick and team is not None ):
    return
  
  # Логируем получение сообщения из CS
  logger.info(f"Получено сообщение из CS: {nick}: {cs_message[:30]}...")
  
  # Получаем Discord ID с использованием кеша и таймаута
  prefix = ""
  try:
    # Таймаут 1 секунда для запроса к базе
    discord_id = await asyncio.wait_for(nsroute.call_route("/CheckSteam", steam_id=steam_id), timeout=1.0)
    
    if discord_id:
      # Если нашелся Discord ID, получаем данные о пользователе
      try:
        # Таймаут 1 секунда для получения Member
        member = await asyncio.wait_for(
          nsroute.call_route("/GetMember", discord_id=discord_id),
          timeout=1.0
        )
        if member:
          prefix = f"[{member.display_name}] "
      except asyncio.TimeoutError:
        # Если не смогли получить данные о мембере, используем хотя бы Discord ID
        prefix = f"[ID:{discord_id}] "
      except Exception as e:
        logger.error(f"Ошибка при получении данных о пользователе Discord: {e}")
  except asyncio.TimeoutError:
    logger.error(f"Таймаут при получении Discord ID для Steam ID {steam_id}")
    # Даже при таймауте пытаемся отправить осмысленное сообщение
    if team is not None:
      label = TEAM_LABELS.get(team_number, TEAM_DEFAULT_LABEL)
      prefix = f"({label}) "
  except Exception as e:
    logger.error(f"Ошибка при получении данных пользователя: {e}")
  
  # Отправляем сообщение в любом случае, даже если не смогли получить префикс
  formatted_message = format_message(nick, cs_message, team_for_formatting, prefix + channel_prefix)
  
  await observer.notify(Event.WBH_MESSAGE, {
    "message": formatted_message
  })

# -- handle_info
async def handle_info(data):
  try:
    map_name = data.get('map')
    current_players = data.get('current_players', [])
    max_players = data.get('max_players')
    player_count = data.get('player_count')
    map_timeleft_sec = data.get('map_timeleft_sec')
    round_number = data.get('round_number')
    score_t = data.get('score_t')
    score_ct = data.get('score_ct')
    bomb_carrier_steam_id = data.get('bomb_carrier_steam_id')
    bomb_carrier_slot = data.get('bomb_carrier_slot')

    formatted_info = format_info_message(
      map_name,
      current_players,
      max_players,
      player_count_override=player_count,
      map_timeleft_sec=map_timeleft_sec,
      round_number=round_number,
      score_t=score_t,
      score_ct=score_ct,
      bomb_carrier_steam_id=bomb_carrier_steam_id,
      bomb_carrier_slot=bomb_carrier_slot,
    )

    logger.info(
      "Webhook info received: map=%s players=%s/%s round=%s score_t=%s score_ct=%s",
      map_name,
      player_count if isinstance(player_count, int) else (len(current_players) if isinstance(current_players, list) else "?"),
      max_players,
      round_number,
      score_t,
      score_ct,
    )

    await observer.notify(Event.WBH_INFO, {
      "info_message": formatted_info,
      "current_players": current_players
    })
  except Exception as err:
    logger.exception(f"Ошибка обработки webhook info: {err}")

# !SECTION

# SECTION class WebHooksType
class WebHooksType(Enum):
  Message = 'message'
  Info = 'info'

  # Deprecated
  # Notify = 'notify' 

# !SECTION

# -- handle_webhook
async def handle_webhook(request: web.Request):
  request_url = safe_request_url(request)

  if not check_api_key(request, request_url=request_url):
    return web.Response(text='Unauthorized', status=401)
  
  try:
    data_raw = await request.json()
  except Exception as err:
    logger.error(
      "Webhook bad json: ip=%s method=%s url=%s content_length=%s content_type=%s error=%s",
      request.remote,
      request.method,
      request_url,
      request.content_length,
      request.content_type,
      err,
    )
    return web.Response(text="Bad Request: bad_json", status=400)

  data: dict = normalize_webhook_payload(data_raw)
  if data is None:
    logger.error(
      "Webhook bad payload type: payload_type=%s ip=%s method=%s url=%s content_length=%s",
      type(data_raw).__name__,
      request.remote,
      request.method,
      request_url,
      request.content_length,
    )
    return web.Response(text="Bad Request: bad_payload_type", status=400)

  raw_message_type = data.get('type')
  raw_message_type_code = data.get('type_code')
  has_text_type = not (
    raw_message_type is None
    or (isinstance(raw_message_type, str) and not raw_message_type.strip())
  )

  if not has_text_type and raw_message_type_code is None:
    logger.error(
      "Webhook missing type: ip=%s method=%s url=%s content_length=%s",
      request.remote,
      request.method,
      request_url,
      request.content_length,
    )
    return web.Response(text="Bad Request: missing_type", status=400)

  message_type = normalize_webhook_type(raw_message_type) if has_text_type else None
  fallback_message_type = normalize_webhook_type_code(raw_message_type_code)
  if not message_type and fallback_message_type:
    message_type = fallback_message_type
    logger.warning(
      "Webhook type recovered by type_code: type=%r type_code=%r normalized=%s ip=%s method=%s url=%s",
      raw_message_type,
      raw_message_type_code,
      message_type,
      request.remote,
      request.method,
      request_url,
    )

  if not message_type:
    logger.error(
      "Webhook unknown type: type=%r type_code=%r len=%s ip=%s method=%s url=%s",
      raw_message_type,
      raw_message_type_code,
      len(raw_message_type) if isinstance(raw_message_type, str) else "?",
      request.remote,
      request.method,
      request_url,
    )
    return web.Response(text="Bad Request: unknown_type", status=400)

  if isinstance(raw_message_type, str) and raw_message_type.strip().lower() != message_type:
    logger.warning(
      "Webhook normalized type: raw=%r normalized=%s ip=%s method=%s url=%s",
      raw_message_type,
      message_type,
      request.remote,
      request.method,
      request_url,
    )

  if message_type == WebHooksType.Message.value:
    try:
      await handle_message(data)
    except Exception as err:
      logger.exception(f"Ошибка обработки webhook message: {err}")
  elif message_type == WebHooksType.Info.value:
    await handle_info(data)

  return web.Response(text='OK')

# -- webhook route
ws.add_post('/webhook', handle_webhook)

@observer.subscribe(Event.WS_IP_NOT_ALLOWED)
async def ev_ip_not_allowed(data):
  logger.info(
    "IP NOT ALLOWED: ip=%s method=%s url=%s content_length=%s content_type=%s user_agent=%s",
    data.get("request_remote"),
    data.get("request_method"),
    data.get("request_url"),
    data.get("request_content_length"),
    data.get("request_content_type"),
    data.get("request_user_agent"),
  )

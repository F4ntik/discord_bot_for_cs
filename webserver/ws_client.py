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

# -- format_info_message
def format_info_message(map_name, current_players, max_players):
  player_count = len(current_players)
  team_players = {1: [], 2: [], 3: []}

  for player in current_players:
    player_name = player['name']
    stats = player['stats']
    frags = stats[0]
    deaths = stats[1]
    team = stats[2]

    if team in team_players:
      team_players[team].append(f"{player_name} - {frags}/{deaths}")
    else: # По идее это UNASSIGNED, суем в спектров
      team_players[3].append(f"{player_name} - {frags}/{deaths}")

  formatted_info = []
  formatted_info.append(f"Время: {datetime.now().strftime('%H:%M')}")
  formatted_info.append(f"Название карты: {map_name}")
  formatted_info.append(f"Количество игроков: {player_count} / {max_players}")

  if team_players[1]:
    formatted_info.append(f"\n{TextStyle.Bold}{Color.Red}Terrorists:{TextStyle.Default}")
    formatted_info.append("\n".join(f"\t{player}" for player in team_players[1]))

  if team_players[2]:
    formatted_info.append(f"\n{TextStyle.Bold}{Color.Blue}Counter-Terrorists:{TextStyle.Default}")
    formatted_info.append("\n".join(f"\t{player}" for player in team_players[2]))

  if team_players[3]:
    formatted_info.append(f"\n{TextStyle.Bold}{Color.White}Spectators:{TextStyle.Default}")
    formatted_info.append("\n".join(f"\t{player}" for player in team_players[3]))

  return "\n".join(formatted_info)

# -- check_api_key
def check_api_key(request):
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
    request.url,
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

    formatted_info = format_info_message(map_name, current_players, max_players)

    logger.info(
      "Webhook info received: map=%s players=%s/%s",
      map_name,
      len(current_players) if isinstance(current_players, list) else "?",
      max_players,
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
  if not check_api_key(request):
    return web.Response(text='Unauthorized', status=401)
  
  try:
    data: dict = await request.json()
  except Exception as err:
    logger.error(
      "Webhook bad json: ip=%s method=%s url=%s content_length=%s content_type=%s error=%s",
      request.remote,
      request.method,
      request.url,
      request.content_length,
      request.content_type,
      err,
    )
    return web.Response(text="Bad Request: bad_json", status=400)

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
      request.url,
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
      request.url,
    )

  if not message_type:
    logger.error(
      "Webhook unknown type: type=%r type_code=%r len=%s ip=%s method=%s url=%s",
      raw_message_type,
      raw_message_type_code,
      len(raw_message_type) if isinstance(raw_message_type, str) else "?",
      request.remote,
      request.method,
      request.url,
    )
    return web.Response(text="Bad Request: unknown_type", status=400)

  if isinstance(raw_message_type, str) and raw_message_type.strip().lower() != message_type:
    logger.warning(
      "Webhook normalized type: raw=%r normalized=%s ip=%s method=%s url=%s",
      raw_message_type,
      message_type,
      request.remote,
      request.method,
      request.url,
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

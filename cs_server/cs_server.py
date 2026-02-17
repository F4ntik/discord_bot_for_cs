from observer.observer_client import logger, observer, Event, Param, Color, nsroute
from cs_server.csrcon import CSRCON, ConnectionError as CSConnectionError, CommandExecutionError

import discord
import asyncio
import time
from typing import List

import config

# -- init
cs_server: CSRCON = CSRCON(host=config.CS_HOST,
                           password=config.CS_RCON_PASSWORD)

_connect_guard_lock: asyncio.Lock = asyncio.Lock()
_last_connect_attempt_at: float = 0.0
_disconnect_notified: bool = False

MAPS_OUTPUT_BEGIN = "ULTRAHC_MAPS_BEGIN"
MAPS_OUTPUT_END = "ULTRAHC_MAPS_END"
MAPS_OUTPUT_ERROR = "ULTRAHC_MAPS_ERROR"
MAPS_PAGE_DEFAULT = 1
MAPS_PER_PAGE_DEFAULT = 20
MAPS_PER_PAGE_MAX = 50
DISCORD_MESSAGE_SAFE_LIMIT = 1800
AMXX_DS_SEND_CMD_TEXT_LENGTH = 256
AMXX_DS_SEND_AUTHOR_LENGTH = 64
AMXX_DS_SEND_MESSAGE_LENGTH = 192


def _mark_connected() -> None:
  """Сбрасываем флаг рассылки события отключения после успешного коннекта."""
  global _disconnect_notified
  _disconnect_notified = False


async def _notify_cs_disconnected_once() -> None:
  """Отправляет CS_DISCONNECTED максимум один раз до следующего подключения."""
  global _disconnect_notified
  if _disconnect_notified:
    return
  _disconnect_notified = True
  await observer.notify(Event.CS_DISCONNECTED)


def _validate_rcon_response(command: str, response: str) -> None:
  """Проверяет ответ RCON на типовые ошибки и поднимает исключение."""
  if not response:
    return

  lowered = str(response).lower()
  if "unknown command" in lowered or "bad rcon_password" in lowered or "bad password" in lowered:
    raise CommandExecutionError(f"Команда {command} вернула ошибку: {response}")

# SECTION Utlities

# -- @require_connection
def require_connection(func) -> callable:

  async def wrapper(*args, **kwargs) -> callable:
    if cs_server.connected:
      return await func(*args, **kwargs)

    if 'data' in kwargs and kwargs['data']:
      await kwargs['data'][Param.Interaction].followup.send('Нет подключения к серверу', ephemeral=True)
    
    logger.error("CS Server: Нет связи с CS")

  return wrapper

def escape_rcon_param(value) -> str:
  """Подготавливает аргументы RCON, заменяя опасные символы."""

  if value is None:
    return ""

  text = str(value)
  text = text.replace('"', "'")
  text = text.replace("\\", "\\\\")

  return text


def _prepare_ds_send_payload(author: str, content: str) -> tuple[str, str, bool]:
  """Приводит автора/сообщение к ограничениям AMXX-плагина ultrahc_discord."""
  safe_author = escape_rcon_param(author).replace("\r", " ").replace("\n", " ").strip()
  safe_content = escape_rcon_param(content).replace("\r", " ").replace("\n", " ").strip()

  if not safe_author:
    safe_author = "Discord"

  author_limit = AMXX_DS_SEND_AUTHOR_LENGTH - 1
  if len(safe_author) > author_limit:
    safe_author = safe_author[:author_limit]

  # read_args(cmd_text, charsmax) в плагине: payload `"author" "content"` должен влезть в 255 символов.
  cmd_payload_limit = AMXX_DS_SEND_CMD_TEXT_LENGTH - 1
  max_content_by_cmd = cmd_payload_limit - (len(safe_author) + 5)  # 5 = две пары кавычек + пробел
  max_content_by_msg = AMXX_DS_SEND_MESSAGE_LENGTH - 1
  content_limit = max(0, min(max_content_by_cmd, max_content_by_msg))

  truncated = len(safe_content) > content_limit
  if content_limit == 0:
    safe_content = ""
  elif truncated:
    safe_content = safe_content[:content_limit]

  return safe_author, safe_content, truncated


def _normalize_maps_pagination(page: int, per_page: int) -> tuple[int, int]:
  page = page or MAPS_PAGE_DEFAULT
  per_page = per_page or MAPS_PER_PAGE_DEFAULT

  if page < 1:
    raise ValueError("Параметр page должен быть не меньше 1.")

  if per_page < 1 or per_page > MAPS_PER_PAGE_MAX:
    raise ValueError(f"Параметр per_page должен быть в диапазоне 1..{MAPS_PER_PAGE_MAX}.")

  return page, per_page


def _parse_server_maps_response(command: str, expected_mode: str, response: str) -> List[str]:
  if response is None:
    raise CommandExecutionError(f"Команда {command} не вернула данные.")

  lines = [line.strip() for line in str(response).splitlines() if line.strip()]
  maps: List[str] = []
  in_section = False
  end_found = False
  errors: List[str] = []

  for line in lines:
    if line.startswith(MAPS_OUTPUT_BEGIN):
      in_section = True
      begin_parts = line.split(maxsplit=1)
      if len(begin_parts) > 1:
        mode = begin_parts[1].strip().lower()
        if mode and mode != expected_mode.lower():
          raise CommandExecutionError(
            f"Команда {command} вернула режим {mode}, ожидался {expected_mode}."
          )
      continue

    if not in_section:
      continue

    if line.startswith(MAPS_OUTPUT_ERROR):
      errors.append(line[len(MAPS_OUTPUT_ERROR):].strip() or "unknown_error")
      continue

    if line.startswith(MAPS_OUTPUT_END):
      end_found = True
      break

    maps.append(line)

  if not in_section:
    raise CommandExecutionError(f"Команда {command} не вернула маркер {MAPS_OUTPUT_BEGIN}.")

  if not end_found:
    raise CommandExecutionError(
      f"Команда {command} вернула неполный ответ (нет маркера {MAPS_OUTPUT_END})."
    )

  if errors:
    raise CommandExecutionError(f"Команда {command} вернула ошибку: {', '.join(errors)}")

  return maps


async def _reply_server_maps(
  interaction: discord.Interaction,
  *,
  mode: str,
  source_label: str,
  page: int,
  per_page: int,
  sort_result: bool,
) -> None:
  try:
    page, per_page = _normalize_maps_pagination(page, per_page)
  except ValueError as err:
    await interaction.followup.send(content=str(err), ephemeral=True)
    return

  command = f"ultrahc_ds_get_maps {mode}"

  try:
    response = await cs_server.exec(command)
    _validate_rcon_response(command, response)
    maps = _parse_server_maps_response(command, mode, response)
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(
      content="Не удалось получить список карт с сервера. Проверьте логи.",
      ephemeral=True,
    )
    return

  if sort_result:
    maps = sorted(set(maps), key=str.lower)

  total = len(maps)
  if total == 0:
    await interaction.followup.send(
      content=f"Источник: {source_label}\nСписок карт пуст.",
      ephemeral=True,
    )
    return

  total_pages = (total + per_page - 1) // per_page
  if page > total_pages:
    await interaction.followup.send(
      content=f"Страница {page} недоступна. Доступно страниц: 1..{total_pages}.",
      ephemeral=True,
    )
    return

  start = (page - 1) * per_page
  end = min(start + per_page, total)
  lines = [
    f"Источник: {source_label}",
    f"Всего карт: {total}",
    f"Страница {page}/{total_pages}",
    "",
  ]
  lines.extend(f"{idx}. {name}" for idx, name in enumerate(maps[start:end], start=start + 1))

  content = "\n".join(lines)
  if len(content) > DISCORD_MESSAGE_SAFE_LIMIT:
    await interaction.followup.send(
      content=(
        "Ответ не помещается в лимит Discord для текущего per_page. "
        "Уменьшите per_page (например, до 20)."
      ),
      ephemeral=True,
    )
    return

  await interaction.followup.send(content=content, ephemeral=True)

# !SECTION

# SECTION Events
# -- on_ready connect
@observer.subscribe(Event.BE_READY)
@nsroute.create_route("/connect_to_cs")
async def connect():
  global _last_connect_attempt_at

  if cs_server.connected:
    return

  async with _connect_guard_lock:
    now = time.monotonic()
    min_interval = getattr(config, "CS_CONNECT_MIN_INTERVAL", 2)
    if min_interval and (now - _last_connect_attempt_at) < float(min_interval):
      return
    _last_connect_attempt_at = now

  try:
    await cs_server.connect_to_server()
    logger.info(f"CS Server: Успешно подключен")
    
    _mark_connected()
    await observer.notify(Event.CS_CONNECTED)

  except CSConnectionError as err:
    logger.error(f"CS Server: {err}")
    await _notify_cs_disconnected_once()

@observer.subscribe(Event.BE_MESSAGE)
@require_connection
async def send_message(data):
  message: discord.Message = data[Param.Message]

  author, content, truncated = _prepare_ds_send_payload(message.author.display_name, message.content)
  if not content:
    logger.info("CS Server: пропуск отправки пустого сообщения в CS")
    return

  command = f"ultrahc_ds_send_msg \"{author}\" \"{content}\""
  
  try:
    if truncated:
      logger.info(f"CS Server: сообщение от {author} обрезано до {len(content)} символов под лимиты AMXX")
    logger.info(f"CS Server: отправка сообщения в CS от {author} (len={len(content)})")
    response = await cs_server.exec(command)
    _validate_rcon_response("ultrahc_ds_send_msg", response)
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await cs_server.disconnect()
    await _notify_cs_disconnected_once()



# !SECTION
# SECTION BotCommand Events

# -- connect_to_cs
@observer.subscribe(Event.BC_CONNECT_TO_CS)
async def cmd_connect_to_cs(data):
  await cs_server.disconnect()
  interaction: discord.Interaction = data[Param.Interaction]

  try:
    await cs_server.connect_to_server()
    logger.info(f"CS Server: Успешно подключен")
    _mark_connected()
    await observer.notify(Event.CS_CONNECTED)
    await interaction.followup.send(content="Успешно подключено!", ephemeral=True)
  except CSConnectionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Невозможно подключиться!", ephemeral=True)

# -- rcon
@observer.subscribe(Event.BC_CS_RCON)
@require_connection
async def cmd_rcon(data):
  interaction: discord.Interaction = data[Param.Interaction]
  command: str = data["command"]
  
  try:
    await cs_server.exec(command)
    logger.info(f"CS Server: выполнена команда: {command}")
    await interaction.followup.send(content="Команда выполнена!", ephemeral=True)
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Не удалось выполнить команду!", ephemeral=True)

# -- kick
@observer.subscribe(Event.BC_CS_KICK)
@require_connection
async def cmd_kick(data):
  interaction: discord.Interaction = data[Param.Interaction]
  caller_name: str = interaction.user.display_name
  target: str = data['target']
  reason: str = data['reason']

  safe_target = escape_rcon_param(target)
  safe_reason = escape_rcon_param(reason)
  command = f"ultrahc_ds_kick_player \"{safe_target}\" \"{safe_reason}\""
  
  try:
    await cs_server.exec(command)
    logger.info(f"CS Server: {caller_name} кикнул игрока {target} по причине {reason}")

    snd = f"```ansi\n{Color.Blue}{caller_name}{Color.Default} кикнул игрока: {Color.Blue}{target}{Color.Default} по причине: {reason}```"
    await interaction.channel.send(content=snd)
    await interaction.delete_original_response()
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Не удалось кикнуть игрока", ephemeral=True)

# -- ban
@observer.subscribe(Event.BC_CS_BAN)
@require_connection
async def cmd_ban(data):
  interaction: discord.Interaction = data[Param.Interaction]
  caller_name: str = interaction.user.display_name
  target: str = data['target']
  minutes: int = data['minutes']
  reason: str = data['reason']

  safe_target = escape_rcon_param(target)
  safe_minutes = escape_rcon_param(minutes)
  safe_reason = escape_rcon_param(reason)
  command = f"amx_ban \"{safe_target}\" \"{safe_minutes}\" \"{safe_reason}\""
  
  try:
    await cs_server.exec(command)
    logger.info(f"CS Server: {caller_name} забанил игрока {target} на {minutes} минут по причине {reason}")

    snd = f"```ansi\n{Color.Blue}{caller_name}{Color.Default} забанил игрока: {Color.Blue}{target}{Color.Default} на {minutes} минут по причине: {reason}```"
    await interaction.channel.send(content=snd)
    await interaction.delete_original_response()
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Не удалось забанить игрока", ephemeral=True)

# -- ban_offline
@observer.subscribe(Event.BC_CS_BAN_OFFLINE)
@require_connection
async def cmd_ban_offline(data):
  interaction: discord.Interaction = data[Param.Interaction]
  caller_name: str = interaction.user.display_name
  target: str = data['target']
  minutes: int = data['minutes']
  reason: str = data['reason']

  safe_target = escape_rcon_param(target)
  safe_minutes = escape_rcon_param(minutes)
  safe_reason = escape_rcon_param(reason)
  command = f"amx_addban \"{safe_target}\" \"{safe_minutes}\" \"{safe_reason}\""
  
  try:
    await cs_server.exec(command)
    logger.info(f"CS Server: {caller_name} забанил игрока {target} на {minutes} минут по причине {reason}")

    snd = f"```ansi\n{Color.Blue}{caller_name}{Color.Default} забанил игрока: {Color.Blue}{target}{Color.Default} на {minutes} минут по причине: {reason}```"
    await interaction.channel.send(content=snd)
    await interaction.delete_original_response()
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Не удалось забанить игрока", ephemeral=True)

# -- unban
@observer.subscribe(Event.BC_CS_UNBAN)
@require_connection
async def cmd_unban(data):
  interaction: discord.Interaction = data[Param.Interaction]
  caller_name: str = interaction.user.display_name
  target: str = data['target']

  safe_target = escape_rcon_param(target)
  command = f"amx_unban \"{safe_target}\""
  
  try:
    await cs_server.exec(command)
    logger.info(f"CS Server: {caller_name} разбанил игрока {target}")

    snd = f"```ansi\n{Color.Blue}{caller_name}{Color.Default} разбанил игрока: {Color.Blue}{target}{Color.Default}```"
    await interaction.channel.send(content=snd)
    await interaction.delete_original_response()
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Не удалось разбанить игрока", ephemeral=True)

# -- sync_maps
@observer.subscribe(Event.BC_CS_SYNC_MAPS)
@require_connection
async def cmd_sync_maps(data):
  interaction: discord.Interaction = data[Param.Interaction]
  caller_name: str = interaction.user.display_name

  command = "ultrahc_ds_reload_map_list"
  
  try:
    await cs_server.exec(command)

    logger.info(f"CS Server: {caller_name} синхронизировал карты")
    await interaction.followup.send(content="Успешно", ephemeral=True)
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Не удалось", ephemeral=True)


# -- server_maps
@observer.subscribe(Event.BC_CS_SERVER_MAPS)
@require_connection
async def cmd_server_maps(data):
  interaction: discord.Interaction = data[Param.Interaction]

  await _reply_server_maps(
    interaction,
    mode="rotation",
    source_label="CS server (active rotation)",
    page=int(data.get("page", MAPS_PAGE_DEFAULT)),
    per_page=int(data.get("per_page", MAPS_PER_PAGE_DEFAULT)),
    sort_result=False,
  )


# -- server_maps_installed
@observer.subscribe(Event.BC_CS_SERVER_MAPS_INSTALLED)
@require_connection
async def cmd_server_maps_installed(data):
  interaction: discord.Interaction = data[Param.Interaction]

  await _reply_server_maps(
    interaction,
    mode="installed",
    source_label="CS server (maps folder)",
    page=int(data.get("page", MAPS_PAGE_DEFAULT)),
    per_page=int(data.get("per_page", MAPS_PER_PAGE_DEFAULT)),
    sort_result=True,
  )

# -- map_change
@observer.subscribe(Event.BC_CS_MAP_CHANGE)
@require_connection
async def cmd_map_change(data):
  interaction: discord.Interaction = data[Param.Interaction]
  caller_name: str = interaction.user.display_name
  mapname: str = data['map']

  command = f"ultrahc_ds_change_map {mapname}"
  
  try:
    await cs_server.exec(command)

    logger.info(f"CS Server: {caller_name} сменил карту на {mapname}")

    snd = f"```ansi\n{Color.Blue}{caller_name}{Color.Default} сменил карту на {Color.Blue}{mapname}{Color.Default}```"
    await interaction.channel.send(content=snd)
    await interaction.delete_original_response()
  except CommandExecutionError as err:
    logger.error(f"CS Server: {err}")
    await interaction.followup.send(content="Не удалось сменить карту", ephemeral=True)

# !SECTION

@nsroute.create_route("/cs/reload_map_list")
async def route_cs_reload_map_list():
  if not cs_server.connected:
    return {"status": "not_connected"}

  command = "ultrahc_ds_reload_map_list"
  try:
    response = await cs_server.exec(command)
    _validate_rcon_response(command, response)
    return {"status": "ok"}
  except CommandExecutionError as err:
    logger.error(f"CS Server: route /cs/reload_map_list failed: {err}")
    return {"status": "error", "error": str(err)}

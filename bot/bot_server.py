from bot.dbot import DBot
from observer.observer_client import observer, Event, logger, nsroute
from bot.wow_moments import (
  HltvDemoResolver,
  MomentCluster,
  MomentState,
  format_clowns_emoji,
  format_mmss,
  format_stars_emoji,
  normalize_moment_kind,
  normalize_map_name_for_match,
  parse_moment_vote_payload,
)

import discord
import asyncio
from collections import deque
import os
from pathlib import Path
import time

import config

MomentChannel = discord.TextChannel | discord.Thread

def _load_local_env_file() -> None:
  env_path = Path(__file__).resolve().parents[1] / ".env"
  if not env_path.exists():
    return

  try:
    lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
  except OSError:
    return

  for raw_line in lines:
    line = raw_line.strip()
    if not line or line.startswith("#"):
      continue
    if line.startswith("export "):
      line = line[7:].strip()
    if "=" not in line:
      continue

    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
      continue

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
      value = value[1:-1]

    os.environ.setdefault(key, value)


def _cfg_or_env_str(name: str, default: str = "") -> str:
  raw = getattr(config, name, None)
  if raw not in (None, ""):
    return str(raw)
  return str(os.getenv(name, default))


def _cfg_or_env_int(name: str, default: int) -> int:
  raw = getattr(config, name, None)
  if raw in (None, ""):
    raw = os.getenv(name, "")

  try:
    return int(raw)
  except (TypeError, ValueError):
    return int(default)


def _to_bool(raw, default: bool) -> bool:
  if isinstance(raw, bool):
    return raw
  if raw in (None, ""):
    return default
  value = str(raw).strip().lower()
  if value in {"1", "true", "yes", "y", "on"}:
    return True
  if value in {"0", "false", "no", "n", "off"}:
    return False
  return default


def _cfg_or_env_bool(name: str, default: bool) -> bool:
  raw = getattr(config, name, None)
  if raw in (None, ""):
    raw = os.getenv(name, "")
  return _to_bool(raw, default)


_load_local_env_file()

dbot: DBot = DBot(_cfg_or_env_str("BOT_TOKEN", ""))

cs_chat_duser_msg: bool = False
cs_chat_max_chars: int = 1000
cs_chat_last_message: discord.Message = None

cs_status_message: discord.Message = None
moment_messages: dict[int, discord.Message] = {}
moments_channel_id = _cfg_or_env_int("MOMENTS_CHANNEL_ID", 0)
moment_state = MomentState(
  window_sec=_cfg_or_env_int("WOW_MOMENT_WINDOW_SEC", 30),
  session_idle_sec=_cfg_or_env_int("WOW_MOMENT_SESSION_IDLE_SEC", 900),
)
demo_resolver = HltvDemoResolver(
  host=_cfg_or_env_str("HLTV_HOST", ""),
  port=_cfg_or_env_int("HLTV_PORT", 27020),
  password=_cfg_or_env_str("HLTV_RCON_PASSWORD", ""),
  timeout_sec=_cfg_or_env_int("HLTV_RCON_TIMEOUT_SEC", 6),
  myarena_host=_cfg_or_env_str("MYARENA_DEMO_BASE_HOST", ""),
  myarena_hid=_cfg_or_env_str("MYARENA_HID", ""),
  ftp_host=_cfg_or_env_str("WOW_DEMO_FTP_HOST", ""),
  ftp_port=_cfg_or_env_int("WOW_DEMO_FTP_PORT", 21),
  ftp_user=_cfg_or_env_str("WOW_DEMO_FTP_USER", ""),
  ftp_password=_cfg_or_env_str("WOW_DEMO_FTP_PASSWORD", ""),
  ftp_demo_dir=_cfg_or_env_str("WOW_DEMO_FTP_DIR", "/cstrike"),
  prefer_ftp=_cfg_or_env_bool("WOW_DEMO_PREFER_FTP", True),
)
WOW_DEMO_RETRY_WINDOW_SEC = 35
WOW_DEMO_RETRY_STEP_SEC = 5
WOW_VOTERS_PREVIEW_LIMIT = 10

# Буфер для накопления сообщений из CS
cs_message_buffer = deque()
cs_buffer_lock = asyncio.Lock()  # Блокировка для безопасного доступа к буферу
cs_last_flush_time = time.time()  # Время последней отправки сообщений - сразу инициализируем текущим временем
cs_flush_interval = 1.5  # Интервал отправки буфера (в секундах)
cs_buffer_task = None  # Задача для периодической обработки буфера

# SECTION Utilities

# -- concat_message
def concat_message(old_message: str, new_message: str) -> str:
  delete_closing = old_message[:-3] if old_message.endswith('```') else old_message

  return delete_closing + new_message + '```'

# -- send_message
async def send_message(message: str, channel: discord.TextChannel) -> None:
  global cs_chat_last_message, cs_chat_duser_msg

  try:
    cs_chat_last_message = await channel.send(f"```ansi\n{message}```")
    cs_chat_duser_msg = False
  except Exception as e:
    logger.error(f"Ошибка при отправке сообщения в Discord: {e}")

# -- edit_message
async def edit_message(message: str, channel: discord.TextChannel, skip_size_check: bool = False) -> None:
  global cs_chat_last_message, cs_chat_max_chars

  formatted_message = concat_message(cs_chat_last_message.content, message)

  # Проверка размера только если не указано пропустить
  if not skip_size_check and len(formatted_message) > cs_chat_max_chars:
    content = formatted_message
    prefix = "```ansi\n"
    suffix = "```"

    if content.startswith(prefix) and content.endswith(suffix):
      content = content[len(prefix):-len(suffix)]

    await send_message(content, channel)
    return
  
  try:
    cs_chat_last_message = await cs_chat_last_message.edit(content=formatted_message)
  except Exception as e:
    logger.error(f"Dbot: Ошибка при обновлении CS_CHAT в Discord: {e}")

# -- edit_status_message
async def edit_status_message(message: str, channel: discord.TextChannel):
  global cs_status_message

  # Проверка на существование сообщения
  try:
    cs_status_message = await channel.fetch_message(cs_status_message.id)
  except discord.NotFound as err:
    cs_status_message = None
    await send_status_message(message, channel)
    return
  except discord.HTTPException as err:
    logger.error(f"Dbot: Ошибка HTTP при получении CS_STATUS в Discord: {err}")
    cs_status_message = None
    await send_status_message(message, channel)
    return

  try:
    cs_status_message = await cs_status_message.edit(content=f"```ansi\n{message}```")
  except discord.Forbidden as err:
    logger.error(f"Dbot: Нет прав для обновления CS_STATUS в Discord: {err}")
    cs_status_message = None
    await send_status_message(message, channel)
  except discord.HTTPException as err:
    logger.error(f"Dbot: Ошибка HTTP при обновлении CS_STATUS в Discord: {err}")
    cs_status_message = None
    await send_status_message(message, channel)
  except Exception as e:
    logger.error(f"Dbot: Ошибка при обновлении CS_STATUS в Discord: {e}")

# -- is_bot
def is_bot(message: discord.Message):
  return message.author == dbot.bot.user

# -- send_status_message
async def send_status_message(message: str, channel: discord.TextChannel):
  global cs_status_message
  try:
    await channel.purge(limit=10)
  except discord.Forbidden as err:
    logger.error(f"Dbot: Нет прав для очистки сообщений перед отправкой статуса: {err}")
  except discord.HTTPException as err:
    logger.error(f"Dbot: Ошибка HTTP при очистке сообщений перед отправкой статуса: {err}")
  except Exception as err:
    logger.error(f"Dbot: Неизвестная ошибка при очистке сообщений перед отправкой статуса: {err}")

  try:
    cs_status_message = await channel.send(f"```ansi\n{message}```")
  except discord.Forbidden as err:
    logger.error(f"Dbot: Нет прав для отправки CS_STATUS в Discord: {err}")
    cs_status_message = None
  except discord.HTTPException as err:
    logger.error(f"Dbot: Ошибка HTTP при отправке CS_STATUS в Discord: {err}")
    cs_status_message = None
  except Exception as err:
    logger.error(f"Dbot: Ошибка при отправке CS_STATUS в Discord: {err}")
    cs_status_message = None

# -- get_moments_channel
async def get_moments_channel() -> MomentChannel | None:
  channel_id = moments_channel_id
  if channel_id <= 0:
    return None

  channel = dbot.bot.get_channel(channel_id)
  if channel is None:
    try:
      channel = await dbot.bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as err:
      logger.error(f"DBot: MOMENTS_CHANNEL_ID={channel_id} недоступен: {err}")
      return None

  if isinstance(channel, (discord.TextChannel, discord.Thread)):
    return channel

  logger.error(f"DBot: MOMENTS_CHANNEL_ID={channel_id} не является текстовым каналом/веткой")
  return None


# -- format_moment_message
def format_moment_voters(cluster: MomentCluster) -> str:
  voters = [name.strip() for name in cluster.voter_names if name and name.strip()]
  if not voters:
    return "—"

  visible = voters[:WOW_VOTERS_PREVIEW_LIMIT]
  overflow = len(voters) - len(visible)
  if overflow > 0:
    return f"{', '.join(visible)}, и еще {overflow}"
  return ", ".join(visible)


def format_moment_message(cluster: MomentCluster) -> str:
  moment_kind = normalize_moment_kind(cluster.moment_kind)
  map_name = normalize_map_name_for_match(cluster.map_name) or cluster.map_name
  if moment_kind == "lol":
    title = "🤡 Кринж-момент"
    score_line = f"Клоуны: {format_clowns_emoji(cluster.stars)}"
  else:
    title = "🔥 WOW-момент"
    score_line = f"Звезды: {format_stars_emoji(cluster.stars)}"

  lines = [
    title,
    f"Игрок: **{cluster.target_name}**",
    score_line,
    f"Кто отметил: {format_moment_voters(cluster)}",
    f"K/D: `{cluster.target_frags}/{cluster.target_deaths}`",
    f"Карта: `{map_name}` | Раунд: `{cluster.round_number}`",
    f"Таймкод: `~{format_mmss(cluster.map_elapsed_sec)}` от старта карты",
  ]

  if cluster.demo_url:
    lines.append(f"Демо: {cluster.demo_url}")
  else:
    lines.append("Демо: недоступно")

  return "\n".join(lines)


async def resolve_moment_demo_url(cluster: MomentCluster) -> None:
  map_raw = cluster.map_name
  map_norm = normalize_map_name_for_match(map_raw)

  if cluster.demo_url or not demo_resolver.enabled:
    if not demo_resolver.enabled:
      logger.warning(
        "DBot: WOW demo resolve skipped: resolver disabled (cluster=%s map_raw=%s map_norm=%s)",
        cluster.cluster_id,
        map_raw,
        map_norm,
      )
    return

  logger.info(
    "DBot: WOW demo resolve start: cluster=%s map_raw=%s map_norm=%s round=%s stars=%s",
    cluster.cluster_id,
    map_raw,
    map_norm,
    cluster.round_number,
    cluster.stars,
  )

  result = await demo_resolver.resolve_demo(cluster.map_name)
  if result.demo_url:
    cluster.demo_url = result.demo_url
    logger.info(
      "DBot: WOW demo resolved: cluster=%s source=%s demo_map=%s demo_path=%s reason=%s attempted=%s",
      cluster.cluster_id,
      result.source or "-",
      result.map_found or "-",
      result.demo_path or "-",
      result.reason or "-",
      ",".join(result.attempted_sources) if result.attempted_sources else "-",
    )
    return

  should_retry = result.map_mismatch or result.reason == "no_demo_found"
  if not should_retry:
    logger.info(
      "DBot: WOW demo unresolved: cluster=%s map_raw=%s map_norm=%s reason=%s attempted=%s",
      cluster.cluster_id,
      map_raw,
      map_norm,
      result.reason or "-",
      ",".join(result.attempted_sources) if result.attempted_sources else "-",
    )
    return

  retries = max(1, WOW_DEMO_RETRY_WINDOW_SEC // WOW_DEMO_RETRY_STEP_SEC)
  retry_reason = "map_mismatch" if result.map_mismatch else (result.reason or "unresolved")
  logger.warning(
    "DBot: WOW demo resolve retry window started: cluster=%s reason=%s expected=%s got=%s source=%s demo_path=%s attempted=%s; waiting up to %ss",
    cluster.cluster_id,
    retry_reason,
    result.map_expected or map_norm or "-",
    result.map_found or "-",
    result.source or "-",
    result.demo_path or "-",
    ",".join(result.attempted_sources) if result.attempted_sources else "-",
    WOW_DEMO_RETRY_WINDOW_SEC,
  )
  last_result = result
  for attempt in range(1, retries + 1):
    await asyncio.sleep(WOW_DEMO_RETRY_STEP_SEC)
    retry_result = await demo_resolver.resolve_demo(cluster.map_name, force_refresh=True)
    last_result = retry_result
    if retry_result.demo_url:
      cluster.demo_url = retry_result.demo_url
      logger.info(
        "DBot: WOW demo resolved on retry %s/%s: cluster=%s source=%s demo_map=%s demo_path=%s reason=%s attempted=%s",
        attempt,
        retries,
        cluster.cluster_id,
        retry_result.source or "-",
        retry_result.map_found or "-",
        retry_result.demo_path or "-",
        retry_result.reason or "-",
        ",".join(retry_result.attempted_sources) if retry_result.attempted_sources else "-",
      )
      return
    if retry_result.map_mismatch:
      logger.warning(
        "DBot: WOW demo retry collision %s/%s: cluster=%s expected=%s got=%s source=%s demo_path=%s attempted=%s",
        attempt,
        retries,
        cluster.cluster_id,
        retry_result.map_expected or map_norm or "-",
        retry_result.map_found or "-",
        retry_result.source or "-",
        retry_result.demo_path or "-",
        ",".join(retry_result.attempted_sources) if retry_result.attempted_sources else "-",
      )
      continue
    if retry_result.reason == "no_demo_found":
      logger.info(
        "DBot: WOW demo retry %s/%s still has no candidate: cluster=%s attempted=%s",
        attempt,
        retries,
        cluster.cluster_id,
        ",".join(retry_result.attempted_sources) if retry_result.attempted_sources else "-",
      )
      continue
    logger.info(
      "DBot: WOW demo retry %s/%s stopped: cluster=%s reason=%s attempted=%s",
      attempt,
      retries,
      cluster.cluster_id,
      retry_result.reason or "-",
      ",".join(retry_result.attempted_sources) if retry_result.attempted_sources else "-",
    )
    break

  logger.warning(
    "DBot: WOW demo unresolved after retries: cluster=%s map_raw=%s map_norm=%s retry_reason=%s final_reason=%s attempted=%s",
    cluster.cluster_id,
    map_raw,
    map_norm,
    retry_reason,
    last_result.reason or "-",
    ",".join(last_result.attempted_sources) if last_result.attempted_sources else "-",
  )


# -- upsert_moment_message
async def upsert_moment_message(cluster: MomentCluster) -> None:
  channel = await get_moments_channel()
  if not channel:
    logger.error("DBot: MOMENTS_CHANNEL_ID не настроен или недоступен")
    return

  await resolve_moment_demo_url(cluster)

  content = format_moment_message(cluster)
  cached_message = moment_messages.get(cluster.cluster_id)

  if cached_message is None and cluster.discord_message_id:
    try:
      cached_message = await channel.fetch_message(cluster.discord_message_id)
      moment_messages[cluster.cluster_id] = cached_message
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
      cached_message = None

  if cached_message is None:
    try:
      created = await channel.send(content)
      cluster.discord_message_id = created.id
      moment_messages[cluster.cluster_id] = created
      return
    except (discord.Forbidden, discord.HTTPException) as err:
      logger.error(f"DBot: не удалось отправить WOW-момент в Discord: {err}")
      return

  try:
    edited = await cached_message.edit(content=content)
    moment_messages[cluster.cluster_id] = edited
    cluster.discord_message_id = edited.id
  except (discord.Forbidden, discord.HTTPException) as err:
    logger.error(f"DBot: не удалось обновить WOW-момент в Discord: {err}")

# !SECTION

# -- (route) get_member
@nsroute.create_route("/GetMember")
async def get_member(discord_id: int) -> discord.Member:
  guild = dbot.bot.get_guild(config.GUILD_ID)
  member: discord.Member 

  try:
    member = await guild.fetch_member(discord_id)
  except discord.NotFound as err:
    member = None


  return member
  
# -- ev_message_from_cs
@observer.subscribe(Event.WBH_MESSAGE)
async def ev_message_from_cs(data) -> None:
  global cs_chat_duser_msg
  message = data['message']

  # Добавляем дополнительное логирование для отслеживания сообщений
  logger.info(f"Получено сообщение из CS для пересылки в Discord: {message[:50]}...")

  channel = dbot.bot.get_channel(config.CS_CHAT_CHNL_ID)

  if not channel:
    logger.error("DBot: CS_CHAT_CHANNEL Не найден")
    return

# -- ev_info
@observer.subscribe(Event.WBH_INFO)
async def ev_info(data) -> None:
  global cs_status_message

  info_message = data['info_message']
  map_name = data.get("map_name")
  round_number = data.get("round_number", 0)

  if map_name:
    if moment_state.touch_info(map_name, round_number):
      moment_messages.clear()
      logger.info(
        "DBot: WOW moments session reset by info snapshot (map=%s round=%s)",
        map_name,
        round_number,
      )

  channel = dbot.bot.get_channel(config.INFO_CHANNEL_ID)

  if not channel:
    logger.error("DBot: CS_INFO_CHANNEL Не найден")
    return

  if cs_status_message:
    await edit_status_message(info_message, channel)
  else:
    await send_status_message(info_message, channel)


@observer.subscribe(Event.WBH_MOMENT_VOTE)
async def ev_moment_vote(data) -> None:
  payload = data.get("moment_vote", {})
  vote = parse_moment_vote_payload(payload)
  if vote is None:
    logger.error("DBot: moment payload rejected: %r", payload)
    return

  result = moment_state.process_vote(vote)
  if result.session_reset:
    moment_messages.clear()
    logger.info("DBot: moments session reset on vote (kind=%s map=%s)", vote.moment_kind, vote.map_name)

  if result.duplicate_vote:
    logger.info(
      "DBot: duplicate moment vote ignored: kind=%s map=%s voter=%s target=%s",
      vote.moment_kind,
      vote.map_name,
      vote.voter_name,
      vote.target_name,
    )
    return

  await upsert_moment_message(result.cluster)
  logger.info(
    "DBot: moment %s: kind=%s map=%s target=%s reactions=%s",
    "created" if result.created else "updated",
    result.cluster.moment_kind,
    result.cluster.map_name,
    result.cluster.target_name,
    result.cluster.stars,
  )

# -- ev_message_from_dis
@observer.subscribe(Event.BE_MESSAGE)
async def ev_message_from_dis(data) -> None:
  global cs_chat_duser_msg
  cs_chat_duser_msg = True

# -- ev_message_from_cs
@observer.subscribe(Event.WBH_MESSAGE)
async def ev_message_from_cs(data) -> None:
  global cs_last_flush_time, cs_buffer_task
  message = data['message']
  
  # Добавляем сообщение в буфер
  async with cs_buffer_lock:
    cs_message_buffer.append(message)
  
  # Убедимся, что обработчик буфера запущен
  if cs_buffer_task is None or cs_buffer_task.done():
    await start_buffer_processor()

# -- Функция для запуска таймера обработки буфера
async def start_buffer_processor():
  global cs_buffer_task
  
  if cs_buffer_task is None or cs_buffer_task.done():
    cs_buffer_task = asyncio.create_task(buffer_processor())
    logger.info("DBot: Запущен обработчик буфера сообщений")

# -- Периодическая обработка буфера сообщений
async def buffer_processor():
  global cs_last_flush_time
  
  while True:
    try:
      # Проверяем, прошло ли достаточно времени для следующей обработки
      current_time = time.time()
      if current_time - cs_last_flush_time >= cs_flush_interval:
        await flush_message_buffer()
        cs_last_flush_time = current_time
      
      # Ждем небольшой интервал перед следующей проверкой
      await asyncio.sleep(0.1)  # Проверяем буфер 10 раз в секунду
    except Exception as e:
      logger.error(f"DBot: Ошибка в обработчике буфера: {e}")
      await asyncio.sleep(1)  # Пауза при ошибке

# -- Обработка буфера сообщений
async def flush_message_buffer():
  global cs_chat_last_message, cs_chat_duser_msg
  
  channel = dbot.bot.get_channel(config.CS_CHAT_CHNL_ID)
  if not channel:
    logger.error("DBot: CS_CHAT_CHANNEL Не найден при обработке буфера")
    return
  
  async with cs_buffer_lock:
    if not cs_message_buffer:  # Если буфер пуст, ничего не делаем
      return
    
    # Собираем все сообщения из буфера, сохраняя построчное форматирование
    messages = []
    while cs_message_buffer:
      messages.append(cs_message_buffer.popleft())
    
    # Объединяем сообщения, каждое на своей строке
    combined_message = "".join(messages)
    
    # Проверка на превышение максимального размера сообщения
    max_discord_message_length = 1500  # Уменьшаем лимит для большего запаса
    formatted_message = f"```ansi\n{combined_message}```"  # оцениваем размер с учетом форматирования
    
    # Должны отправить новое сообщение в следующих случаях:
    send_new_message = False
    
    # 1. Если новое сообщение слишком большое
    if len(formatted_message) > max_discord_message_length:
      logger.info(f"DBot: Буфер сообщений превысил максимальный размер ({len(formatted_message)} > {max_discord_message_length})")
      send_new_message = True
    
    # 2. Если сообщение из Discord или нет последнего сообщения
    if cs_chat_duser_msg or not cs_chat_last_message:
      send_new_message = True
    
    # 3. Если последнее сообщение уже достаточно большое
    if not send_new_message and cs_chat_last_message:
      # Проверяем максимальный размер после редактирования
      current_content = cs_chat_last_message.content
      potential_content = concat_message(current_content, combined_message)
      
      if len(potential_content) > max_discord_message_length:
        logger.info(f"DBot: После редактирования размер сообщения превысит лимит ({len(potential_content)} > {max_discord_message_length})")
        send_new_message = True
    
    # Отправляем или редактируем сообщение в зависимости от ситуации
    if send_new_message:
      await send_message(combined_message, channel)
    else:
      try:
        # Используем skip_size_check=True, т.к. проверка размера уже сделана выше
        await edit_message(combined_message, channel, skip_size_check=True)
      except Exception as e:
        # Если редактирование не удалось, отправляем новое сообщение
        logger.error(f"DBot: Ошибка при редактировании, отправляем новое сообщение: {e}")
        await send_message(combined_message, channel)
    
    cs_chat_duser_msg = False

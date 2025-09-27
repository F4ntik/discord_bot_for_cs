from bot.dbot import DBot
from observer.observer_client import observer, Event, logger, nsroute

import discord
import asyncio
from collections import deque
import time

import config

dbot: DBot = DBot(config.BOT_TOKEN)

cs_chat_duser_msg: bool = False
cs_chat_max_chars: int = 1000
cs_chat_last_message: discord.Message = None

cs_status_message: discord.Message = None

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
    send_message(formatted_message, channel)
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

  try:
    cs_status_message = await cs_status_message.edit(content=f"```ansi\n{message}```")
  except Exception as e:
    logger.error(f"Dbot: Ошибка при обновлении CS_STATUS в Discord: {e}")

# -- is_bot
def is_bot(message: discord.Message):
  return message.author == dbot.bot.user

# -- send_status_message
async def send_status_message(message: str, channel: discord.TextChannel):
  global cs_status_message
  await channel.purge(limit=10)

  cs_status_message = await channel.send(f"```ansi\n{message}```")

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
  channel = dbot.bot.get_channel(config.INFO_CHANNEL_ID)

  if not channel:
    logger.error("DBot: CS_INFO_CHANNEL Не найден")
    return

  if cs_status_message:
    await edit_status_message(info_message, channel)
  else:
    await send_status_message(info_message, channel)

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
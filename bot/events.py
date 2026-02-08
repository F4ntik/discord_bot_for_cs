import discord.ext.tasks
from observer.observer_client import logger, observer, Event, Param, nsroute

import discord
import discord.ext

from bot.bot_server import dbot

import config

from bot import utilities as bot_utilities


bot = dbot.bot
_startup_ready_sent: bool = False

# -- on_ready
@bot.event
async def on_ready():
  global _startup_ready_sent
  if _startup_ready_sent:
    logger.info(f"DBot {bot.user.name} переподключился к Discord")
    return
  _startup_ready_sent = True
  logger.info(f"DBot {bot.user.name} запущен")

  admin_channel_id = getattr(config, "ADMIN_CHANNEL_ID", None)
  if admin_channel_id:
    admin_channel = bot.get_channel(admin_channel_id)

    if admin_channel is None:
      try:
        admin_channel = await bot.fetch_channel(admin_channel_id)
      except (discord.NotFound, discord.Forbidden, discord.HTTPException) as fetch_error:
        logger.warning(
          "Не удалось получить админский канал %s для уведомления о запуске: %s",
          admin_channel_id,
          fetch_error,
        )

    if admin_channel is not None:
      help_mention = bot_utilities.get_command_mention("help")
      startup_message = (
        f"✅ {bot.user.name} запущен и готов к работе!\n"
        f"Используйте {help_mention}, чтобы узнать обо всех доступных командах и возможностях."
      )

      try:
        await admin_channel.send(startup_message)
      except (discord.Forbidden, discord.HTTPException) as send_error:
        logger.warning(
          "Не удалось отправить сообщение о запуске в канал %s: %s",
          admin_channel_id,
          send_error,
        )

  await observer.notify(Event.BE_READY)

# -- on_message
@bot.event
async def on_message(message: discord.Message):
  if message.author == bot.user:
    return
  
  if message.channel.id == config.CS_CHAT_CHNL_ID:
    await observer.notify(Event.BE_MESSAGE, {
      Param.Message: message
    })

  await bot.process_commands(message)

# -- on_member_update
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
  if before.display_name == after.display_name:
    return

  await observer.notify(Event.BE_MEMBER_UPDATE, {
    "user_id": after.id,
    "new_username": after.display_name
  })

# -- setup_hook
@bot.event
async def setup_hook():
  guild = bot.get_guild(config.GUILD_ID)
  await bot.tree.sync(guild=guild)

# -- (task) cs_connect_task
@discord.ext.tasks.loop(seconds=config.CS_RECONNECT_INTERVAL)
async def cs_connect_task():
  await nsroute.call_route("/connect_to_cs") 

@cs_connect_task.error
async def cs_connect_task_error(error: Exception):
  logger.error(f"DBot: cs_connect_task завершился с ошибкой: {error}")
  try:
    cs_connect_task.restart()
  except Exception as restart_error:
    logger.error(f"DBot: не удалось перезапустить cs_connect_task: {restart_error}")

# -- ev_cs_connected
@observer.subscribe(Event.CS_CONNECTED)
async def ev_cs_connected():
  if cs_connect_task.is_running():
    cs_connect_task.cancel()

# -- ev_cs_disconnected
@observer.subscribe(Event.CS_DISCONNECTED)
async def ev_cs_disconnected():
  if not cs_connect_task.is_running():
    cs_connect_task.start()

from __future__ import annotations

import asyncio
from typing import Optional

import discord

import config
from bot.bot_server import dbot
from cs_server.map_deploy_service import (
  DeployResult,
  deploy_map_from_payload,
  max_upload_bytes,
  render_deploy_result,
)
from observer.observer_client import Event, Param, logger, observer


_background_watchers: set[asyncio.Task] = set()


def _track_background_task(task: asyncio.Task) -> None:
  _background_watchers.add(task)
  task.add_done_callback(_background_watchers.discard)


async def _resolve_notification_channel(interaction: discord.Interaction) -> Optional[discord.abc.Messageable]:
  if interaction.channel is not None:
    return interaction.channel

  admin_channel_id = getattr(config, "ADMIN_CHANNEL_ID", None)
  if not admin_channel_id:
    return None

  channel = dbot.bot.get_channel(admin_channel_id)
  if channel is not None:
    return channel

  try:
    return await dbot.bot.fetch_channel(admin_channel_id)
  except Exception as err:
    logger.error(f"Map installer: не удалось получить ADMIN_CHANNEL_ID={admin_channel_id}: {err}")
    return None


async def _send_sync_response(interaction: discord.Interaction, text: str) -> None:
  try:
    await interaction.followup.send(text, ephemeral=True)
  except Exception as err:
    logger.error(f"Map installer: не удалось отправить sync followup: {err}")


async def _send_background_response(interaction: discord.Interaction, text: str) -> None:
  try:
    await interaction.followup.send(text, ephemeral=True)
    return
  except Exception as err:
    logger.warning(f"Map installer: background followup недоступен, пробую канал: {err}")

  channel = await _resolve_notification_channel(interaction)
  if channel is None:
    logger.error("Map installer: не найден канал для отправки результата фоновой установки.")
    return

  mention = interaction.user.mention if interaction.user else ""
  prefix = f"{mention}\n" if mention else ""
  try:
    await channel.send(f"{prefix}{text}")
  except Exception as err:
    logger.error(f"Map installer: не удалось отправить background-результат в канал: {err}")


async def _finalize_result(
  interaction: discord.Interaction,
  result: DeployResult,
  background: bool,
) -> None:
  message = render_deploy_result(result, background=background)

  event_payload = {
    Param.Interaction: interaction,
    "result": result,
    "message": message,
  }
  if result.success:
    await observer.notify(Event.CS_MAP_INSTALL_DONE, event_payload)
  else:
    await observer.notify(Event.CS_MAP_INSTALL_FAILED, event_payload)

  if background:
    await _send_background_response(interaction, message)
  else:
    await _send_sync_response(interaction, message)


async def _watch_background_deploy(
  interaction: discord.Interaction,
  deploy_task: asyncio.Task,
) -> None:
  try:
    result: DeployResult = await deploy_task
  except Exception as err:
    logger.error(f"Map installer: background deploy crashed: {err}")
    result = DeployResult(
      success=False,
      source_file="background",
      error_code="MAP_INSTALL_ERROR",
      error_message=str(err),
    )

  await _finalize_result(interaction, result, background=True)


@observer.subscribe(Event.BC_CS_MAP_INSTALL)
async def install_map_from_attachment(data: dict) -> None:
  interaction: discord.Interaction = data[Param.Interaction]
  attachment: discord.Attachment = data["file"]
  map_name: Optional[str] = data.get("map_name")
  min_players: Optional[int] = data.get("min_players")
  max_players: Optional[int] = data.get("max_players")
  add_to_rotation: bool = bool(data.get("add_to_rotation", False))
  priority: Optional[int] = data.get("priority")

  max_bytes = max_upload_bytes()
  if attachment.size is not None and attachment.size > max_bytes:
    await _send_sync_response(
      interaction,
      (
        f"Файл слишком большой: {attachment.size / (1024 * 1024):.1f} МБ. "
        f"Лимит: {max_bytes // (1024 * 1024)} МБ."
      ),
    )
    return

  try:
    file_bytes = await attachment.read()
  except Exception as err:
    logger.error(f"Map installer: не удалось скачать вложение {attachment.filename}: {err}")
    await _send_sync_response(interaction, "Не удалось получить файл карты из вложения.")
    return

  timeout_sec = float(getattr(config, "MAP_INSTALL_SYNC_TIMEOUT_SEC", 12))
  deploy_task = asyncio.create_task(
    deploy_map_from_payload(
      source_file=attachment.filename or "uploaded_map",
      file_bytes=file_bytes,
      map_name=map_name,
      min_players=min_players,
      max_players=max_players,
      add_to_rotation=add_to_rotation,
      priority=priority,
    )
  )

  try:
    result: DeployResult = await asyncio.wait_for(asyncio.shield(deploy_task), timeout=timeout_sec)
    await _finalize_result(interaction, result, background=False)
  except asyncio.TimeoutError:
    logger.info(
      "Map installer: операция перешла в фон (timeout=%s, file=%s)",
      timeout_sec,
      attachment.filename,
    )
    await _send_sync_response(
      interaction,
      (
        f"Установка карты выполняется дольше {int(timeout_sec)} секунд и продолжена в фоне. "
        "Итог отправлю отдельным сообщением."
      ),
    )
    watcher = asyncio.create_task(_watch_background_deploy(interaction, deploy_task))
    _track_background_task(watcher)


@observer.subscribe(Event.CS_MAP_INSTALL_DONE)
async def on_map_install_done(data: dict) -> None:
  result: DeployResult = data["result"]
  logger.info(
    "Map installer: success map=%s remote=%s rotation=%s warnings=%s",
    result.map_name,
    result.remote_path,
    result.add_to_rotation,
    len(result.warnings),
  )


@observer.subscribe(Event.CS_MAP_INSTALL_FAILED)
async def on_map_install_failed(data: dict) -> None:
  result: DeployResult = data["result"]
  logger.error(
    "Map installer: failed code=%s message=%s source=%s",
    result.error_code,
    result.error_message,
    result.source_file,
  )

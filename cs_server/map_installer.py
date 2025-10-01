from __future__ import annotations

from pathlib import Path
from typing import Optional

import discord

from observer.observer_client import Event, Param, logger, observer


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploaded_maps"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_name(value: str, fallback: str) -> str:
  cleaned = "".join(ch for ch in value if ch.isalnum() or ch in {"_", "-"})
  return cleaned or fallback


@observer.subscribe(Event.BC_CS_MAP_INSTALL)
async def install_map_from_attachment(data: dict) -> None:
  interaction: discord.Interaction = data[Param.Interaction]
  attachment: discord.Attachment = data["file"]
  map_name: Optional[str] = data.get("map_name")
  min_players: Optional[int] = data.get("min_players")
  max_players: Optional[int] = data.get("max_players")

  try:
    file_bytes = await attachment.read()
  except Exception as err:
    logger.error(f"Map installer: не удалось скачать вложение {attachment.filename}: {err}")
    await interaction.followup.send("Не удалось получить файл карты.", ephemeral=True)
    return

  original_name = Path(attachment.filename or "uploaded_map")
  safe_stem = _sanitize_name(original_name.stem, "map")
  safe_filename = safe_stem + original_name.suffix
  file_path = UPLOAD_DIR / safe_filename

  try:
    file_path.write_bytes(file_bytes)
  except Exception as err:
    logger.error(f"Map installer: не удалось сохранить файл {file_path}: {err}")
    await interaction.followup.send("Не удалось сохранить файл карты.", ephemeral=True)
    return

  effective_map_name = _sanitize_name(map_name, safe_stem) if map_name else safe_stem
  meta_path = UPLOAD_DIR / f"{effective_map_name}.txt"
  meta_lines = [
    f"map_name={effective_map_name}",
    f"source_file={file_path.name}",
    f"min_players={'' if min_players is None else min_players}",
    f"max_players={'' if max_players is None else max_players}",
  ]

  try:
    meta_path.write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
  except Exception as err:
    logger.error(f"Map installer: не удалось создать метаданные {meta_path}: {err}")
    await interaction.followup.send("Файл карты сохранён, но не удалось записать параметры.", ephemeral=True)
    return

  logger.info(
    f"Map installer: файл {file_path.name} сохранён, параметры записаны в {meta_path.name}"
  )

  await interaction.followup.send(
    f"Файл `{file_path.name}` сохранён в `{UPLOAD_DIR.name}`. Параметры записаны в `{meta_path.name}`.",
    ephemeral=True,
  )

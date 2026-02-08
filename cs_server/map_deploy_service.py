from __future__ import annotations

import asyncio
import ftplib
import io
import socket
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Optional

import config

from observer.observer_client import logger, nsroute


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / getattr(config, "MAP_INSTALL_WORKDIR", "uploaded_maps")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_MIN_PLAYERS = 0
DEFAULT_MAX_PLAYERS = 32
DEFAULT_PRIORITY = 100


class DeployError(Exception):
  def __init__(self, code: str, message: str):
    super().__init__(message)
    self.code = code
    self.message = message


@dataclass(slots=True)
class DeployResult:
  success: bool
  source_file: str
  map_name: str = ""
  remote_path: str = ""
  source_kind: str = ""
  add_to_rotation: bool = False
  db_action: str = "skipped"
  reload_action: str = "skipped"
  warnings: list[str] = field(default_factory=list)
  error_code: str = ""
  error_message: str = ""
  local_source_path: str = ""
  local_map_path: str = ""


def sanitize_name(value: Optional[str], fallback: str) -> str:
  raw = (value or "").strip()
  cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})
  return cleaned or fallback


def sanitize_filename(value: Optional[str], fallback: str) -> str:
  raw = (value or "").strip()
  cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-", "."})
  return cleaned or fallback


def _max_upload_bytes() -> int:
  max_mb = int(getattr(config, "MAP_INSTALL_MAX_FILE_MB", 200))
  return max_mb * 1024 * 1024


def max_upload_bytes() -> int:
  return _max_upload_bytes()


def _validate_payload_size(file_bytes: bytes) -> None:
  max_bytes = _max_upload_bytes()
  if len(file_bytes) > max_bytes:
    raise DeployError(
      "FILE_TOO_LARGE",
      f"Размер файла превышает лимит {max_bytes // (1024 * 1024)} МБ.",
    )


def _extract_map_from_zip(file_bytes: bytes) -> tuple[str, bytes]:
  try:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
      bsp_entries = [
        item for item in archive.infolist()
        if not item.is_dir() and Path(item.filename).suffix.lower() == ".bsp"
      ]
      if not bsp_entries:
        raise DeployError("ZIP_NO_BSP", "Архив не содержит .bsp файла карты.")
      if len(bsp_entries) > 1:
        names = ", ".join(Path(item.filename).name for item in bsp_entries[:5])
        raise DeployError(
          "ZIP_MULTI_BSP",
          f"В архиве найдено несколько .bsp: {names}. Поддерживается только одна карта.",
        )

      member = bsp_entries[0]
      map_bytes = archive.read(member)
      map_stem = sanitize_name(Path(member.filename).stem, "map")
      return map_stem, map_bytes
  except DeployError:
    raise
  except zipfile.BadZipFile as err:
    raise DeployError("UNSUPPORTED_FORMAT", f"Повреждённый zip-архив: {err}") from err


def _resolve_map_payload(
  source_file: str,
  file_bytes: bytes,
  requested_map_name: Optional[str],
) -> tuple[str, bytes, str]:
  source_name = Path(source_file or "uploaded_map")
  suffix = source_name.suffix.lower()

  if suffix == ".bsp":
    fallback_map_name = sanitize_name(source_name.stem, "map")
    map_name = sanitize_name(requested_map_name, fallback_map_name)
    return map_name, file_bytes, "bsp"

  if suffix == ".zip":
    zip_map_name, map_bytes = _extract_map_from_zip(file_bytes)
    map_name = sanitize_name(requested_map_name, zip_map_name)
    return map_name, map_bytes, "zip"

  raise DeployError("UNSUPPORTED_FORMAT", "Поддерживаются только .bsp или .zip.")


def _write_local_artifacts(source_file: str, source_bytes: bytes, map_name: str, map_bytes: bytes) -> tuple[Path, Path]:
  timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
  safe_source = sanitize_filename(source_file, "uploaded_map.bin")
  source_path = UPLOAD_DIR / f"{timestamp}_{safe_source}"
  map_path = UPLOAD_DIR / f"{map_name}.bsp"

  source_path.write_bytes(source_bytes)
  map_path.write_bytes(map_bytes)

  return source_path, map_path


def _ensure_remote_dir(ftp: ftplib.FTP, remote_dir: str) -> None:
  normalized = PurePosixPath(remote_dir or "/").as_posix()
  segments = [segment for segment in normalized.split("/") if segment]

  if normalized.startswith("/"):
    ftp.cwd("/")

  for segment in segments:
    try:
      ftp.cwd(segment)
      continue
    except ftplib.error_perm:
      pass

    try:
      ftp.mkd(segment)
    except ftplib.error_perm:
      # Папка могла быть создана конкурентно или нет прав; в обоих случаях
      # следующая команда cwd даст точный итог.
      pass

    ftp.cwd(segment)


def _upload_map_sync(remote_filename: str, map_bytes: bytes) -> str:
  protocol = str(getattr(config, "MAP_DEPLOY_PROTOCOL", "ftps")).strip().lower()
  host = str(getattr(config, "MAP_FTP_HOST", "")).strip()
  port = int(getattr(config, "MAP_FTP_PORT", 21))
  user = str(getattr(config, "MAP_FTP_USER", "")).strip()
  password = str(getattr(config, "MAP_FTP_PASSWORD", "")).strip()
  remote_dir = str(getattr(config, "MAP_REMOTE_MAPS_DIR", "/cstrike/maps")).strip() or "/cstrike/maps"
  timeout = int(getattr(config, "MAP_FTP_TIMEOUT_SEC", 30))
  passive_mode = bool(getattr(config, "MAP_FTP_PASSIVE", True))
  use_tls = protocol == "ftps" or bool(getattr(config, "MAP_FTP_USE_TLS", False))

  if not host or not user:
    raise DeployError("FTP_CONNECT_ERROR", "Не заполнены MAP_FTP_HOST или MAP_FTP_USER в config.py.")

  ftp: ftplib.FTP
  try:
    ftp = ftplib.FTP_TLS() if use_tls else ftplib.FTP()
    ftp.connect(host=host, port=port, timeout=timeout)
    ftp.login(user=user, passwd=password)

    if use_tls and isinstance(ftp, ftplib.FTP_TLS):
      ftp.prot_p()

    ftp.set_pasv(passive_mode)
    _ensure_remote_dir(ftp, remote_dir)
    ftp.storbinary(f"STOR {remote_filename}", io.BytesIO(map_bytes))

    remote_path = f"{PurePosixPath(remote_dir).as_posix().rstrip('/')}/{remote_filename}"
    return remote_path
  except DeployError:
    raise
  except (socket.timeout, TimeoutError) as err:
    raise DeployError("FTP_CONNECT_ERROR", f"Таймаут подключения к FTP/FTPS: {err}") from err
  except ftplib.error_perm as err:
    message = str(err)
    if message.startswith("530"):
      raise DeployError("FTP_AUTH_ERROR", f"Ошибка авторизации FTP/FTPS: {message}") from err
    raise DeployError("FTP_UPLOAD_ERROR", f"FTP/FTPS отклонил загрузку: {message}") from err
  except ftplib.all_errors as err:
    raise DeployError("FTP_UPLOAD_ERROR", f"Ошибка загрузки карты на FTP/FTPS: {err}") from err
  finally:
    try:
      if "ftp" in locals():
        ftp.quit()
    except Exception:
      pass


async def _upload_map(remote_filename: str, map_bytes: bytes) -> str:
  return await asyncio.to_thread(_upload_map_sync, remote_filename, map_bytes)


async def _apply_rotation(
  map_name: str,
  min_players: Optional[int],
  max_players: Optional[int],
  priority: Optional[int],
  result: DeployResult,
) -> None:
  final_min = DEFAULT_MIN_PLAYERS if min_players is None else int(min_players)
  final_max = DEFAULT_MAX_PLAYERS if max_players is None else int(max_players)
  final_priority = DEFAULT_PRIORITY if priority is None else int(priority)

  if final_min > final_max:
    raise DeployError("VALIDATION_ERROR", "min_players не может быть больше max_players.")

  exists = await nsroute.call_route("/db/map_exists", map_name=map_name)
  if exists is None:
    raise DeployError("DB_ERROR", "Не удалось проверить карту в базе данных.")

  if exists:
    result.db_action = "duplicate_skipped"
    result.warnings.append("DB_DUPLICATE_SKIPPED: карта уже есть в БД, шаг добавления пропущен.")
  else:
    add_result = await nsroute.call_route(
      "/db/map_add_internal",
      map_name=map_name,
      activated=1,
      min_players=final_min,
      max_players=final_max,
      priority=final_priority,
    )
    status = (add_result or {}).get("status")
    if status != "added":
      raise DeployError("DB_ERROR", f"Не удалось добавить карту в БД (status={status}).")
    result.db_action = "inserted"

  reload_result = await nsroute.call_route("/cs/reload_map_list")
  reload_status = (reload_result or {}).get("status")
  if reload_status == "ok":
    result.reload_action = "ok"
  else:
    result.reload_action = "failed"
    result.warnings.append(
      "MAPLIST_RELOAD_ERROR: карта загружена, но перезагрузка map list на сервере не выполнена."
    )


async def deploy_map_from_payload(
  source_file: str,
  file_bytes: bytes,
  map_name: Optional[str],
  min_players: Optional[int],
  max_players: Optional[int],
  add_to_rotation: bool,
  priority: Optional[int],
) -> DeployResult:
  result = DeployResult(
    success=False,
    source_file=source_file,
    add_to_rotation=add_to_rotation,
  )

  try:
    _validate_payload_size(file_bytes)
    resolved_name, resolved_map_bytes, source_kind = _resolve_map_payload(
      source_file=source_file,
      file_bytes=file_bytes,
      requested_map_name=map_name,
    )

    remote_filename = f"{resolved_name}.bsp"
    source_path, map_path = _write_local_artifacts(
      source_file=source_file,
      source_bytes=file_bytes,
      map_name=resolved_name,
      map_bytes=resolved_map_bytes,
    )

    remote_path = await _upload_map(remote_filename=remote_filename, map_bytes=resolved_map_bytes)

    result.map_name = resolved_name
    result.remote_path = remote_path
    result.source_kind = source_kind
    result.local_source_path = str(source_path)
    result.local_map_path = str(map_path)

    if add_to_rotation:
      await _apply_rotation(
        map_name=resolved_name,
        min_players=min_players,
        max_players=max_players,
        priority=priority,
        result=result,
      )

    result.success = True
    return result
  except DeployError as err:
    result.error_code = err.code
    result.error_message = err.message
    return result
  except Exception as err:
    logger.error(f"Map deploy: unexpected error: {err}")
    result.error_code = "MAP_INSTALL_ERROR"
    result.error_message = str(err)
    return result


def render_deploy_result(result: DeployResult, background: bool = False) -> str:
  lines: list[str] = []

  if background:
    lines.append("Фоновая установка карты завершена.")

  if result.success:
    lines.append(
      f"Карта `{result.map_name}` загружена на сервер в `{result.remote_path}` "
      f"(источник: `{result.source_kind}`)."
    )

    if result.add_to_rotation:
      if result.db_action == "inserted":
        lines.append("Карта добавлена в ротацию (БД/Redis) и передана на reload списка.")
      elif result.db_action == "duplicate_skipped":
        lines.append("Карта уже была в БД: шаг добавления в ротацию пропущен.")

      if result.reload_action == "ok":
        lines.append("Список карт на сервере перезагружен.")
      elif result.reload_action == "failed":
        lines.append("Внимание: не удалось перезагрузить список карт на сервере автоматически.")
    else:
      lines.append("Добавление в ротацию не запрашивалось.")
  else:
    lines.append(
      f"Установка карты завершилась ошибкой `{result.error_code}`: {result.error_message}"
    )

  if result.warnings:
    lines.append("")
    lines.append("Предупреждения:")
    for warning in result.warnings:
      lines.append(f"- {warning}")

  return "\n".join(lines)

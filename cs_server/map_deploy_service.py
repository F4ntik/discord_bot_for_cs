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

ALLOWED_ARCHIVE_ROOTS: tuple[str, ...] = (
  "maps",
  "models",
  "sound",
  "sprites",
  "gfx",
  "overviews",
  "resource",
)
ALLOWED_ARCHIVE_ROOTS_SET = set(ALLOWED_ARCHIVE_ROOTS)

FLAT_EXTENSION_MAP: dict[str, str] = {
  ".bsp": "maps",
  ".res": "maps",
  ".nav": "maps",
  ".wad": "maps",
  ".wav": "sound",
  ".mp3": "sound",
  ".mdl": "models",
  ".spr": "sprites",
  ".txt": "overviews",
  ".bmp": "overviews",
  ".tga": "overviews",
}

UPLOAD_SAMPLE_LIMIT = 12


class DeployError(Exception):
  def __init__(self, code: str, message: str, details: Optional[dict] = None):
    super().__init__(message)
    self.code = code
    self.message = message
    self.details = details or {}


@dataclass(slots=True)
class UploadEntry:
  source_name: str
  remote_rel_path: str
  payload: bytes
  is_bsp: bool = False


@dataclass(slots=True)
class ResolvedPayload:
  map_name: str
  map_bytes: bytes
  source_kind: str
  map_remote_rel_path: str
  upload_entries: list[UploadEntry]
  warnings: list[str] = field(default_factory=list)
  rejected_files_count: int = 0


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
  uploaded_files_count: int = 0
  skipped_files_count: int = 0
  rejected_files_count: int = 0
  bsp_uploaded: bool = False
  install_clean: bool = False
  uploaded_paths_sample: list[str] = field(default_factory=list)


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


def _normalize_archive_member_path(raw_path: str) -> list[str]:
  normalized = (raw_path or "").replace("\\", "/").strip()
  if not normalized:
    raise DeployError("ZIP_UNSAFE_PATH", "Архив содержит пустой путь файла.")

  if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
    raise DeployError("ZIP_UNSAFE_PATH", f"Архив содержит абсолютный путь: {raw_path}")

  parts = [part for part in normalized.split("/") if part not in {"", "."}]
  if not parts:
    raise DeployError("ZIP_UNSAFE_PATH", f"Архив содержит некорректный путь: {raw_path}")

  for part in parts:
    if part == "..":
      raise DeployError("ZIP_UNSAFE_PATH", f"Архив содержит запрещённый путь: {raw_path}")
    if "\x00" in part:
      raise DeployError("ZIP_UNSAFE_PATH", f"Архив содержит некорректный путь: {raw_path}")

  return parts


def _resolve_zip_member_destination(parts: list[str], filename: str) -> tuple[Optional[str], Optional[str]]:
  root = parts[0].lower()

  if root in ALLOWED_ARCHIVE_ROOTS_SET:
    canonical_root = root
    remote_parts = [canonical_root, *parts[1:]]
    return "/".join(remote_parts), None

  if len(parts) == 1:
    ext = Path(filename).suffix.lower()
    mapped_root = FLAT_EXTENSION_MAP.get(ext)
    if not mapped_root:
      return None, f"ZIP_FLAT_UNSUPPORTED_EXT: {filename} (расширение {ext or '<none>'} не поддерживается)"
    return f"{mapped_root}/{filename}", None

  return None, f"ZIP_UNSUPPORTED_ROOT: {filename} (разрешены корни: {', '.join(ALLOWED_ARCHIVE_ROOTS)})"


def _extract_payload_from_zip(file_bytes: bytes, requested_map_name: Optional[str]) -> ResolvedPayload:
  try:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
      entries: list[UploadEntry] = []
      warnings: list[str] = []
      rejected_count = 0
      seen_targets: set[str] = set()

      for item in archive.infolist():
        if item.is_dir():
          continue

        parts = _normalize_archive_member_path(item.filename)
        filename = parts[-1]
        if not filename:
          raise DeployError("ZIP_UNSAFE_PATH", f"Архив содержит путь без имени файла: {item.filename}")

        remote_rel_path, rejection_reason = _resolve_zip_member_destination(parts, filename)
        if remote_rel_path is None:
          rejected_count += 1
          warnings.append(rejection_reason or f"ZIP_REJECTED: {item.filename}")
          continue

        target_key = remote_rel_path.lower()
        if target_key in seen_targets:
          rejected_count += 1
          warnings.append(
            f"ZIP_DUPLICATE_TARGET: {item.filename} -> {remote_rel_path} (повторный путь, файл пропущен)"
          )
          continue

        payload = archive.read(item)
        seen_targets.add(target_key)
        entries.append(
          UploadEntry(
            source_name=item.filename,
            remote_rel_path=remote_rel_path,
            payload=payload,
            is_bsp=False,
          )
        )

      bsp_entries = [
        entry
        for entry in entries
        if entry.remote_rel_path.lower().startswith("maps/") and entry.remote_rel_path.lower().endswith(".bsp")
      ]
      if not bsp_entries:
        raise DeployError("ZIP_NO_BSP", "Архив не содержит .bsp карты в корне maps или в плоском формате.")
      if len(bsp_entries) > 1:
        names = ", ".join(Path(entry.remote_rel_path).name for entry in bsp_entries[:5])
        raise DeployError(
          "ZIP_MULTI_BSP",
          f"В архиве найдено несколько .bsp: {names}. Поддерживается только одна карта.",
        )

      bsp_entry = bsp_entries[0]
      detected_map_name = sanitize_name(Path(bsp_entry.remote_rel_path).stem, "map")
      final_map_name = sanitize_name(requested_map_name, detected_map_name)
      map_remote_rel_path = f"maps/{final_map_name}.bsp"

      if map_remote_rel_path.lower() != bsp_entry.remote_rel_path.lower():
        if any(entry.remote_rel_path.lower() == map_remote_rel_path.lower() for entry in entries if entry is not bsp_entry):
          raise DeployError(
            "ZIP_BSP_RENAME_CONFLICT",
            f"Невозможно переименовать .bsp в {map_remote_rel_path}: путь уже занят другим файлом из архива.",
          )
        warnings.append(
          f"ZIP_BSP_RENAMED: {bsp_entry.remote_rel_path} -> {map_remote_rel_path} (из-за map_name)"
        )
        bsp_entry.remote_rel_path = map_remote_rel_path

      bsp_entry.is_bsp = True

      return ResolvedPayload(
        map_name=final_map_name,
        map_bytes=bsp_entry.payload,
        source_kind="zip",
        map_remote_rel_path=map_remote_rel_path,
        upload_entries=entries,
        warnings=warnings,
        rejected_files_count=rejected_count,
      )
  except DeployError:
    raise
  except zipfile.BadZipFile as err:
    raise DeployError("UNSUPPORTED_FORMAT", f"Повреждённый zip-архив: {err}") from err


def _resolve_map_payload(
  source_file: str,
  file_bytes: bytes,
  requested_map_name: Optional[str],
) -> ResolvedPayload:
  source_name = Path(source_file or "uploaded_map")
  suffix = source_name.suffix.lower()

  if suffix == ".bsp":
    fallback_map_name = sanitize_name(source_name.stem, "map")
    map_name = sanitize_name(requested_map_name, fallback_map_name)
    remote_rel_path = f"maps/{map_name}.bsp"
    return ResolvedPayload(
      map_name=map_name,
      map_bytes=file_bytes,
      source_kind="bsp",
      map_remote_rel_path=remote_rel_path,
      upload_entries=[
        UploadEntry(
          source_name=source_file,
          remote_rel_path=remote_rel_path,
          payload=file_bytes,
          is_bsp=True,
        )
      ],
    )

  if suffix == ".zip":
    return _extract_payload_from_zip(file_bytes, requested_map_name)

  raise DeployError("UNSUPPORTED_FORMAT", "Поддерживаются только .bsp или .zip.")


def _write_local_artifacts(
  source_file: str,
  source_bytes: bytes,
  map_name: str,
  map_bytes: bytes,
) -> tuple[Path, Path]:
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
      pass

    ftp.cwd(segment)


def _get_remote_root_map() -> dict[str, str]:
  def _normalize_remote_dir(config_key: str, value: str) -> str:
    normalized = PurePosixPath(value).as_posix().strip()
    if not normalized.startswith("/"):
      raise DeployError(
        "FTP_CONFIG_ERROR",
        f"{config_key} должен быть абсолютным путём (например, /cstrike/maps).",
      )
    return normalized

  maps_dir = str(getattr(config, "MAP_REMOTE_MAPS_DIR", "/cstrike/maps")).strip() or "/cstrike/maps"
  maps_dir = _normalize_remote_dir("MAP_REMOTE_MAPS_DIR", maps_dir)
  base_dir = str(getattr(config, "MAP_REMOTE_BASE_DIR", "")).strip()
  if not base_dir:
    base_dir = PurePosixPath(maps_dir).parent.as_posix()
  base_dir = _normalize_remote_dir("MAP_REMOTE_BASE_DIR", base_dir)

  default_map = {
    "maps": maps_dir,
    "models": PurePosixPath(base_dir, "models").as_posix(),
    "sound": PurePosixPath(base_dir, "sound").as_posix(),
    "sprites": PurePosixPath(base_dir, "sprites").as_posix(),
    "gfx": PurePosixPath(base_dir, "gfx").as_posix(),
    "overviews": PurePosixPath(base_dir, "overviews").as_posix(),
    "resource": PurePosixPath(base_dir, "resource").as_posix(),
  }

  return {
    "maps": _normalize_remote_dir(
      "MAP_REMOTE_MAPS_DIR",
      str(getattr(config, "MAP_REMOTE_MAPS_DIR", default_map["maps"])).strip() or default_map["maps"],
    ),
    "models": _normalize_remote_dir(
      "MAP_REMOTE_MODELS_DIR",
      str(getattr(config, "MAP_REMOTE_MODELS_DIR", default_map["models"])).strip() or default_map["models"],
    ),
    "sound": _normalize_remote_dir(
      "MAP_REMOTE_SOUND_DIR",
      str(getattr(config, "MAP_REMOTE_SOUND_DIR", default_map["sound"])).strip() or default_map["sound"],
    ),
    "sprites": _normalize_remote_dir(
      "MAP_REMOTE_SPRITES_DIR",
      str(getattr(config, "MAP_REMOTE_SPRITES_DIR", default_map["sprites"])).strip() or default_map["sprites"],
    ),
    "gfx": _normalize_remote_dir(
      "MAP_REMOTE_GFX_DIR",
      str(getattr(config, "MAP_REMOTE_GFX_DIR", default_map["gfx"])).strip() or default_map["gfx"],
    ),
    "overviews": _normalize_remote_dir(
      "MAP_REMOTE_OVERVIEWS_DIR",
      str(getattr(config, "MAP_REMOTE_OVERVIEWS_DIR", default_map["overviews"])).strip() or default_map["overviews"],
    ),
    "resource": _normalize_remote_dir(
      "MAP_REMOTE_RESOURCE_DIR",
      str(getattr(config, "MAP_REMOTE_RESOURCE_DIR", default_map["resource"])).strip() or default_map["resource"],
    ),
  }


def _resolve_remote_abs_path(remote_rel_path: str, root_map: dict[str, str]) -> str:
  parts = [part for part in PurePosixPath(remote_rel_path).parts if part and part != "/"]
  if len(parts) < 2:
    raise DeployError("ZIP_UNSAFE_PATH", f"Некорректный относительный путь для загрузки: {remote_rel_path}")

  root = parts[0].lower()
  base_dir = root_map.get(root)
  if not base_dir:
    raise DeployError("ZIP_UNSUPPORTED_ROOT", f"Неразрешённый корневой каталог: {root}")

  return PurePosixPath(base_dir, *parts[1:]).as_posix()


def _remote_file_exists(ftp: ftplib.FTP, remote_dir: str, filename: str) -> bool:
  origin = ftp.pwd()
  try:
    _ensure_remote_dir(ftp, remote_dir)
    try:
      size = ftp.size(filename)
      if size is not None:
        return True
    except ftplib.error_perm as err:
      message = str(err).lower()
      if not ("550" in message or "not found" in message):
        raise

    try:
      listing = ftp.nlst()
    except ftplib.error_perm as err:
      message = str(err).lower()
      if "550" in message or "not found" in message:
        return False
      raise

    listing_names = {Path(item).name for item in listing}
    return filename in listing_names
  finally:
    try:
      ftp.cwd(origin)
    except Exception:
      pass


def _split_remote_file(remote_path: str) -> tuple[str, str]:
  posix = PurePosixPath(remote_path).as_posix()
  directory = str(PurePosixPath(posix).parent)
  filename = PurePosixPath(posix).name
  if not filename:
    raise DeployError("FTP_UPLOAD_ERROR", f"Некорректный путь удалённого файла: {remote_path}")
  return directory, filename


def _open_ftp_connection() -> ftplib.FTP:
  protocol = str(getattr(config, "MAP_DEPLOY_PROTOCOL", "ftps")).strip().lower()
  host = str(getattr(config, "MAP_FTP_HOST", "")).strip()
  port = int(getattr(config, "MAP_FTP_PORT", 21))
  user = str(getattr(config, "MAP_FTP_USER", "")).strip()
  password = str(getattr(config, "MAP_FTP_PASSWORD", "")).strip()
  timeout = int(getattr(config, "MAP_FTP_TIMEOUT_SEC", 30))
  passive_mode = bool(getattr(config, "MAP_FTP_PASSIVE", True))
  use_tls = protocol == "ftps" or bool(getattr(config, "MAP_FTP_USE_TLS", False))

  if not host or not user:
    raise DeployError("FTP_CONNECT_ERROR", "Не заполнены MAP_FTP_HOST или MAP_FTP_USER в config.py.")

  try:
    ftp = ftplib.FTP_TLS() if use_tls else ftplib.FTP()
    ftp.connect(host=host, port=port, timeout=timeout)
    ftp.login(user=user, passwd=password)

    if use_tls and isinstance(ftp, ftplib.FTP_TLS):
      ftp.prot_p()

    ftp.set_pasv(passive_mode)
    return ftp
  except (socket.timeout, TimeoutError) as err:
    raise DeployError("FTP_CONNECT_ERROR", f"Таймаут подключения к FTP/FTPS: {err}") from err
  except ftplib.error_perm as err:
    message = str(err)
    if message.startswith("530"):
      raise DeployError("FTP_AUTH_ERROR", f"Ошибка авторизации FTP/FTPS: {message}") from err
    raise DeployError("FTP_CONNECT_ERROR", f"FTP/FTPS отклонил подключение: {message}") from err
  except ftplib.all_errors as err:
    raise DeployError("FTP_CONNECT_ERROR", f"Ошибка подключения к FTP/FTPS: {err}") from err


def _upload_entries_sync(entries: list[UploadEntry]) -> tuple[list[str], list[str], list[str]]:
  root_map = _get_remote_root_map()
  ftp: Optional[ftplib.FTP] = None
  uploaded_paths: list[str] = []
  skipped_paths: list[str] = []
  upload_warnings: list[str] = []

  try:
    ftp = _open_ftp_connection()

    for entry in entries:
      remote_abs_path = _resolve_remote_abs_path(entry.remote_rel_path, root_map)
      remote_dir, remote_filename = _split_remote_file(remote_abs_path)

      try:
        exists = _remote_file_exists(ftp, remote_dir, remote_filename)
      except ftplib.all_errors as err:
        raise DeployError(
          "FTP_UPLOAD_ERROR",
          f"Не удалось проверить наличие файла на FTP/FTPS: {remote_abs_path}: {err}",
          details={
            "uploaded_paths": uploaded_paths,
            "skipped_paths": skipped_paths,
            "warnings": upload_warnings,
            "failed_remote_path": remote_abs_path,
          },
        ) from err

      if exists:
        skipped_paths.append(remote_abs_path)
        upload_warnings.append(f"FTP_FILE_EXISTS_SKIPPED: {remote_abs_path}")
        continue

      try:
        _ensure_remote_dir(ftp, remote_dir)
        ftp.storbinary(f"STOR {remote_filename}", io.BytesIO(entry.payload))
        uploaded_paths.append(remote_abs_path)
      except ftplib.error_perm as err:
        raise DeployError(
          "FTP_UPLOAD_ERROR",
          f"FTP/FTPS отклонил загрузку {remote_abs_path}: {err}",
          details={
            "uploaded_paths": uploaded_paths,
            "skipped_paths": skipped_paths,
            "warnings": upload_warnings,
            "failed_remote_path": remote_abs_path,
          },
        ) from err
      except ftplib.all_errors as err:
        raise DeployError(
          "FTP_UPLOAD_ERROR",
          f"Ошибка загрузки {remote_abs_path}: {err}",
          details={
            "uploaded_paths": uploaded_paths,
            "skipped_paths": skipped_paths,
            "warnings": upload_warnings,
            "failed_remote_path": remote_abs_path,
          },
        ) from err

    return uploaded_paths, skipped_paths, upload_warnings
  finally:
    if ftp is not None:
      try:
        ftp.quit()
      except Exception:
        pass


async def _upload_entries(entries: list[UploadEntry]) -> tuple[list[str], list[str], list[str]]:
  return await asyncio.to_thread(_upload_entries_sync, entries)


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


def _compute_sample(paths: list[str], skipped: list[str], limit: int = UPLOAD_SAMPLE_LIMIT) -> list[str]:
  sample: list[str] = []
  for path in paths:
    sample.append(f"uploaded: {path}")
    if len(sample) >= limit:
      return sample
  for path in skipped:
    sample.append(f"skipped:  {path}")
    if len(sample) >= limit:
      return sample
  return sample


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
    resolved = _resolve_map_payload(
      source_file=source_file,
      file_bytes=file_bytes,
      requested_map_name=map_name,
    )

    source_path, map_path = _write_local_artifacts(
      source_file=source_file,
      source_bytes=file_bytes,
      map_name=resolved.map_name,
      map_bytes=resolved.map_bytes,
    )

    map_remote_abs_path = _resolve_remote_abs_path(resolved.map_remote_rel_path, _get_remote_root_map())

    result.map_name = resolved.map_name
    result.remote_path = map_remote_abs_path
    result.source_kind = resolved.source_kind
    result.local_source_path = str(source_path)
    result.local_map_path = str(map_path)
    result.rejected_files_count = resolved.rejected_files_count
    result.warnings.extend(resolved.warnings)

    uploaded_paths, skipped_paths, upload_warnings = await _upload_entries(resolved.upload_entries)

    result.uploaded_files_count = len(uploaded_paths)
    result.skipped_files_count = len(skipped_paths)
    result.bsp_uploaded = map_remote_abs_path in set(uploaded_paths)
    result.uploaded_paths_sample = _compute_sample(uploaded_paths, skipped_paths)
    result.warnings.extend(upload_warnings)

    result.install_clean = (
      result.bsp_uploaded
      and result.skipped_files_count == 0
      and result.rejected_files_count == 0
    )

    if add_to_rotation:
      if result.install_clean:
        await _apply_rotation(
          map_name=resolved.map_name,
          min_players=min_players,
          max_players=max_players,
          priority=priority,
          result=result,
        )
      else:
        result.db_action = "skipped_non_clean"
        result.reload_action = "skipped_non_clean"
        result.warnings.append(
          "ROTATION_SKIPPED_NON_CLEAN: add_to_rotation=true требует чистой установки "
          "(0 skipped, 0 rejected и успешную загрузку .bsp)."
        )

    result.success = True
    return result
  except DeployError as err:
    details = err.details or {}
    uploaded_paths = list(details.get("uploaded_paths", []))
    skipped_paths = list(details.get("skipped_paths", []))
    detail_warnings = list(details.get("warnings", []))

    if uploaded_paths or skipped_paths:
      result.uploaded_files_count = len(uploaded_paths)
      result.skipped_files_count = len(skipped_paths)
      result.uploaded_paths_sample = _compute_sample(uploaded_paths, skipped_paths)
      result.warnings.extend(detail_warnings)
      failed_remote_path = details.get("failed_remote_path")
      if failed_remote_path:
        result.warnings.append(
          f"PARTIAL_UPLOAD: часть файлов уже загружена до ошибки. Последний неуспешный путь: {failed_remote_path}"
        )

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
      f"Карта `{result.map_name}` обработана, целевой `.bsp`: `{result.remote_path}` "
      f"(источник: `{result.source_kind}`)."
    )
    lines.append(
      f"Файлы: загружено `{result.uploaded_files_count}`, "
      f"пропущено `{result.skipped_files_count}`, отклонено `{result.rejected_files_count}`."
    )

    if result.add_to_rotation:
      if result.install_clean:
        if result.db_action == "inserted":
          lines.append("Карта добавлена в ротацию (БД/Redis) и передана на reload списка.")
        elif result.db_action == "duplicate_skipped":
          lines.append("Карта уже была в БД: шаг добавления в ротацию пропущен.")

        if result.reload_action == "ok":
          lines.append("Список карт на сервере перезагружен.")
        elif result.reload_action == "failed":
          lines.append("Внимание: не удалось перезагрузить список карт на сервере автоматически.")
      else:
        lines.append(
          "Добавление в ротацию пропущено: установка не полностью чистая "
          "(есть пропуски/отклонения или .bsp не загружен)."
        )
    else:
      lines.append("Добавление в ротацию не запрашивалось.")
  else:
    lines.append(
      f"Установка карты завершилась ошибкой `{result.error_code}`: {result.error_message}"
    )
    if result.uploaded_files_count or result.skipped_files_count:
      lines.append(
        f"До ошибки: загружено `{result.uploaded_files_count}`, пропущено `{result.skipped_files_count}`."
      )

  if result.uploaded_paths_sample:
    lines.append("")
    lines.append("Пути (sample):")
    for item in result.uploaded_paths_sample:
      lines.append(f"- {item}")

  if result.warnings:
    lines.append("")
    lines.append("Предупреждения:")
    for warning in result.warnings:
      lines.append(f"- {warning}")

  return "\n".join(lines)

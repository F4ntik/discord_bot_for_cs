from __future__ import annotations

import asyncio
import calendar
from ftplib import FTP
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Set
from urllib.parse import quote, unquote

from rehlds.rcon import RCON


_RECORDING_RE = re.compile(r"recording to\s+\"?([^\",\r\n]+?\.dem)\"?", re.IGNORECASE)
_MAP_SUFFIX_MODE_RE = re.compile(r"_\d+x\d+$", re.IGNORECASE)
_DEMO_WITH_STAMP_RE = re.compile(r"(?:^|[-_])\d{10}-(.+)$", re.IGNORECASE)
_log = logging.getLogger(__name__)


def _safe_int(value, default=0) -> int:
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def _safe_str(value, default="") -> str:
  if value is None:
    return default
  return str(value)


def format_mmss(seconds: int) -> str:
  if not isinstance(seconds, int) or seconds < 0:
    return "--:--"
  minutes, secs = divmod(seconds, 60)
  return f"{minutes:02d}:{secs:02d}"


def format_stars_emoji(stars: int, *, preview_limit: int = 10) -> str:
  stars_value = max(0, _safe_int(stars, 0))
  limit = max(1, _safe_int(preview_limit, 10))
  if stars_value <= 0:
    return "—"

  visible = min(stars_value, limit)
  icons = "⭐" * visible
  if stars_value > limit:
    return f"{icons} x{stars_value}"
  return icons


def normalize_map_name_for_match(map_name: str) -> str:
  value = _safe_str(map_name).strip().lower()
  if not value:
    return ""
  return _MAP_SUFFIX_MODE_RE.sub("", value)


def parse_hltv_recording_path(status_text: str) -> Optional[str]:
  if not status_text:
    return None

  match = _RECORDING_RE.search(status_text)
  if not match:
    return None

  demo_path = match.group(1).strip()
  if not demo_path:
    return None

  return demo_path.replace("\\", "/")


def _is_plain_demo_file(name: str) -> bool:
  lowered = name.lower()
  return lowered.endswith(".dem") and not lowered.endswith(".dem.zip")


def _extract_ftp_name(raw_name: str) -> str:
  value = _safe_str(raw_name).strip().replace("\\", "/")
  if not value:
    return ""
  if "/" in value:
    return value.rsplit("/", 1)[-1]
  return value


def _extract_demo_stamp_unix(name: str) -> int:
  if not name:
    return 0

  match = re.search(r"(?:^|[-_])(\d{10})(?:[-_]|$)", name)
  if not match:
    return 0

  stamp = match.group(1)
  try:
    day = int(stamp[0:2])
    month = int(stamp[2:4])
    year = 2000 + int(stamp[4:6])
    hour = int(stamp[6:8])
    minute = int(stamp[8:10])
    return int(calendar.timegm((year, month, day, hour, minute, 0)))
  except (TypeError, ValueError):
    return 0


def extract_map_from_demo_path(demo_path: str) -> str:
  raw_path = _safe_str(demo_path).strip().replace("\\", "/")
  if not raw_path:
    return ""

  filename = raw_path.rsplit("/", 1)[-1]
  lowered = filename.lower()
  if lowered.endswith(".dem.zip"):
    filename = filename[:-8]
  elif lowered.endswith(".dem"):
    filename = filename[:-4]
  else:
    return ""

  match = _DEMO_WITH_STAMP_RE.search(filename)
  if match:
    return normalize_map_name_for_match(match.group(1))

  if "-" in filename:
    return normalize_map_name_for_match(filename.rsplit("-", 1)[-1])

  return normalize_map_name_for_match(filename)


def is_demo_map_compatible(moment_map_name: str, demo_path: str) -> bool:
  moment_map = normalize_map_name_for_match(moment_map_name)
  if not moment_map:
    return True

  demo_map = extract_map_from_demo_path(demo_path)
  if not demo_map:
    return True

  return demo_map == moment_map


def pick_ftp_demo_filename(candidates: list[tuple[str, int]], map_name: str = "") -> Optional[str]:
  if not candidates:
    return None

  map_name = normalize_map_name_for_match(map_name)
  filtered = [(name, ts) for name, ts in candidates if name]
  if not filtered:
    return None

  if map_name:
    map_matches = [(name, ts) for name, ts in filtered if is_demo_map_compatible(map_name, name)]
    if map_matches:
      filtered = map_matches

  filtered.sort(
    key=lambda item: (_extract_demo_stamp_unix(item[0]), item[1], item[0]),
    reverse=True,
  )
  return filtered[0][0]


def build_myarena_demo_url(base_host: str, hid: str, demo_path: str) -> Optional[str]:
  base_host = _safe_str(base_host).strip()
  hid = _safe_str(hid).strip()
  demo_path = _safe_str(demo_path).strip()

  if not base_host or not hid or not demo_path:
    return None

  if base_host.startswith("http://"):
    base_host = base_host[len("http://"):]
  elif base_host.startswith("https://"):
    base_host = base_host[len("https://"):]

  encoded_demo_path = quote(demo_path, safe="/._-")
  return f"https://{base_host}/getzipdemo.php?hid={hid}&dem={encoded_demo_path}"


@dataclass
class MomentVote:
  map_name: str
  round_number: int
  map_timeleft_sec: int
  map_elapsed_sec: int
  event_unix: int
  voter_name: str
  voter_steam_id: str
  voter_slot: int
  target_name: str
  target_steam_id: str
  target_slot: int
  target_team: int
  target_frags: int
  target_deaths: int

  @property
  def target_key(self) -> str:
    steam_id = self.target_steam_id.strip()
    if steam_id and steam_id.upper() != "BOT":
      return f"steam:{steam_id}"
    return f"slot:{self.target_slot}"

  @property
  def voter_key(self) -> str:
    steam_id = self.voter_steam_id.strip()
    if steam_id and steam_id.upper() != "BOT":
      return f"steam:{steam_id}"
    return f"slot:{self.voter_slot}"


def parse_moment_vote_payload(data: dict) -> Optional[MomentVote]:
  if not isinstance(data, dict):
    return None

  map_name = _safe_str(data.get("map")).strip()
  voter_name = _safe_str(data.get("voter_name")).strip()
  target_name = _safe_str(data.get("target_name")).strip()
  if not map_name or not voter_name or not target_name:
    return None

  event_unix = _safe_int(data.get("event_unix"), int(time.time()))
  if event_unix <= 0:
    event_unix = int(time.time())

  return MomentVote(
    map_name=map_name,
    round_number=max(0, _safe_int(data.get("round_number"), 0)),
    map_timeleft_sec=max(-1, _safe_int(data.get("map_timeleft_sec"), -1)),
    map_elapsed_sec=max(-1, _safe_int(data.get("map_elapsed_sec"), -1)),
    event_unix=event_unix,
    voter_name=voter_name,
    voter_steam_id=_safe_str(data.get("voter_steam_id")).strip(),
    voter_slot=max(0, _safe_int(data.get("voter_slot"), 0)),
    target_name=target_name,
    target_steam_id=_safe_str(data.get("target_steam_id")).strip(),
    target_slot=max(0, _safe_int(data.get("target_slot"), 0)),
    target_team=max(0, _safe_int(data.get("target_team"), 0)),
    target_frags=_safe_int(data.get("target_frags"), 0),
    target_deaths=_safe_int(data.get("target_deaths"), 0),
  )


@dataclass
class MomentCluster:
  cluster_id: int
  map_name: str
  target_key: str
  target_name: str
  target_steam_id: str
  target_slot: int
  target_team: int
  target_frags: int
  target_deaths: int
  first_event_unix: int
  last_event_unix: int
  center_event_unix: int
  map_timeleft_sec: int
  map_elapsed_sec: int
  round_number: int
  stars: int = 1
  voters: Set[str] = field(default_factory=set)
  voter_names: list[str] = field(default_factory=list)
  discord_message_id: Optional[int] = None
  demo_url: Optional[str] = None

  def apply_vote(self, vote: MomentVote) -> None:
    self.stars += 1
    self.last_event_unix = vote.event_unix
    self.center_event_unix = int(
      round(((self.center_event_unix * (self.stars - 1)) + vote.event_unix) / self.stars)
    )
    self.target_name = vote.target_name
    self.target_steam_id = vote.target_steam_id
    self.target_slot = vote.target_slot
    self.target_team = vote.target_team
    self.target_frags = vote.target_frags
    self.target_deaths = vote.target_deaths
    self.map_timeleft_sec = vote.map_timeleft_sec
    self.map_elapsed_sec = vote.map_elapsed_sec
    self.round_number = vote.round_number


@dataclass
class MomentProcessResult:
  cluster: MomentCluster
  created: bool
  duplicate_vote: bool
  session_reset: bool


class MomentState:
  def __init__(self, *, window_sec: int = 30, session_idle_sec: int = 900):
    self.window_sec = max(1, int(window_sec))
    self.session_idle_sec = max(60, int(session_idle_sec))
    self._map_name = ""
    self._map_norm_name = ""
    self._last_round_number = 0
    self._last_event_unix = 0
    self._clusters: list[MomentCluster] = []
    self._next_cluster_id = 1

  def reset(self) -> None:
    self._map_name = ""
    self._map_norm_name = ""
    self._last_round_number = 0
    self._last_event_unix = 0
    self._clusters = []
    self._next_cluster_id = 1

  def touch_info(self, map_name: str, round_number: int, event_unix: Optional[int] = None) -> bool:
    now = int(event_unix or time.time())
    map_name = _safe_str(map_name).strip()
    map_norm = normalize_map_name_for_match(map_name)
    round_number = max(0, _safe_int(round_number, 0))
    if not map_name:
      return False

    should_reset = False
    if self._map_norm_name and map_norm != self._map_norm_name:
      should_reset = True
    elif (
      self._map_norm_name == map_norm
      and self._last_round_number >= 3
      and round_number > 0
      and round_number + 2 < self._last_round_number
    ):
      should_reset = True

    if should_reset:
      self.reset()

    self._map_name = map_name
    self._map_norm_name = map_norm
    self._last_round_number = round_number
    self._last_event_unix = now
    return should_reset

  def _should_reset_for_vote(self, vote: MomentVote) -> bool:
    vote_map_norm = normalize_map_name_for_match(vote.map_name)
    if not self._map_norm_name:
      return False

    if vote_map_norm != self._map_norm_name:
      return True

    if self._last_event_unix and (vote.event_unix - self._last_event_unix) > self.session_idle_sec:
      return True

    if (
      self._last_round_number >= 3
      and vote.round_number > 0
      and vote.round_number + 2 < self._last_round_number
    ):
      return True

    return False

  def _find_cluster(self, vote: MomentVote) -> Optional[MomentCluster]:
    best_cluster = None
    best_distance = None
    vote_map_norm = normalize_map_name_for_match(vote.map_name)

    for cluster in self._clusters:
      if normalize_map_name_for_match(cluster.map_name) != vote_map_norm:
        continue
      if cluster.target_key != vote.target_key:
        continue

      distance = abs(cluster.center_event_unix - vote.event_unix)
      if distance > self.window_sec:
        continue

      if best_cluster is None or distance < best_distance:
        best_cluster = cluster
        best_distance = distance

    return best_cluster

  def process_vote(self, vote: MomentVote) -> MomentProcessResult:
    session_reset = False
    if self._should_reset_for_vote(vote):
      self.reset()
      session_reset = True

    self._map_name = vote.map_name
    self._map_norm_name = normalize_map_name_for_match(vote.map_name)
    self._last_round_number = vote.round_number
    self._last_event_unix = vote.event_unix

    cluster = self._find_cluster(vote)
    voter_key = vote.voter_key
    if cluster is not None:
      if voter_key and voter_key in cluster.voters:
        return MomentProcessResult(cluster=cluster, created=False, duplicate_vote=True, session_reset=session_reset)

      if voter_key:
        cluster.voters.add(voter_key)
      voter_name = vote.voter_name.strip()
      if voter_name:
        cluster.voter_names.append(voter_name)
      cluster.apply_vote(vote)
      return MomentProcessResult(cluster=cluster, created=False, duplicate_vote=False, session_reset=session_reset)

    cluster = MomentCluster(
      cluster_id=self._next_cluster_id,
      map_name=vote.map_name,
      target_key=vote.target_key,
      target_name=vote.target_name,
      target_steam_id=vote.target_steam_id,
      target_slot=vote.target_slot,
      target_team=vote.target_team,
      target_frags=vote.target_frags,
      target_deaths=vote.target_deaths,
      first_event_unix=vote.event_unix,
      last_event_unix=vote.event_unix,
      center_event_unix=vote.event_unix,
      map_timeleft_sec=vote.map_timeleft_sec,
      map_elapsed_sec=vote.map_elapsed_sec,
      round_number=vote.round_number,
    )
    if voter_key:
      cluster.voters.add(voter_key)
    voter_name = vote.voter_name.strip()
    if voter_name:
      cluster.voter_names.append(voter_name)

    self._next_cluster_id += 1
    self._clusters.append(cluster)
    return MomentProcessResult(cluster=cluster, created=True, duplicate_vote=False, session_reset=session_reset)


@dataclass
class DemoResolveResult:
  demo_url: Optional[str] = None
  demo_path: Optional[str] = None
  map_mismatch: bool = False
  source: str = ""
  map_expected: str = ""
  map_found: str = ""
  reason: str = ""
  attempted_sources: list[str] = field(default_factory=list)


class HltvDemoResolver:
  def __init__(
    self,
    *,
    host: str,
    port: int,
    password: str,
    timeout_sec: int,
    myarena_host: str,
    myarena_hid: str,
    ftp_host: str = "",
    ftp_port: int = 21,
    ftp_user: str = "",
    ftp_password: str = "",
    ftp_demo_dir: str = "/cstrike",
    prefer_ftp: bool = False,
    cache_ttl_sec: int = 20,
  ):
    self.host = _safe_str(host).strip()
    self.port = max(1, _safe_int(port, 27020))
    self.password = _safe_str(password)
    self.timeout_sec = max(1, _safe_int(timeout_sec, 6))
    self.myarena_host = _safe_str(myarena_host).strip()
    self.myarena_hid = _safe_str(myarena_hid).strip()
    self.ftp_host = _safe_str(ftp_host).strip()
    self.ftp_port = max(1, _safe_int(ftp_port, 21))
    self.ftp_user = _safe_str(ftp_user).strip()
    self.ftp_password = _safe_str(ftp_password)
    self.ftp_demo_dir = _safe_str(ftp_demo_dir, "/cstrike").strip() or "/cstrike"
    self.prefer_ftp = bool(prefer_ftp)
    self.cache_ttl_sec = max(3, _safe_int(cache_ttl_sec, 20))

    self._cache_url: Optional[str] = None
    self._cache_path: Optional[str] = None
    self._cache_at: float = 0.0

  @property
  def enabled(self) -> bool:
    return bool(self.myarena_host and self.myarena_hid and (self.hltv_enabled or self.ftp_enabled))

  @property
  def hltv_enabled(self) -> bool:
    return bool(self.host and self.password)

  @property
  def ftp_enabled(self) -> bool:
    return bool(self.ftp_host and self.ftp_user and self.ftp_password)

  def _fetch_status_sync(self) -> str:
    rcon = RCON(host=self.host, port=self.port, password=self.password)
    rcon.connect(timeout=self.timeout_sec, validate_password=True)
    try:
      return rcon.execute("status")
    finally:
      rcon.disconnect()

  def _mdtm_to_unix(self, mdtm_response: str) -> int:
    match = re.search(r"(\d{14})$", _safe_str(mdtm_response))
    if not match:
      return 0

    stamp = match.group(1)
    try:
      year = int(stamp[0:4])
      month = int(stamp[4:6])
      day = int(stamp[6:8])
      hour = int(stamp[8:10])
      minute = int(stamp[10:12])
      second = int(stamp[12:14])
      return int(calendar.timegm((year, month, day, hour, minute, second)))
    except (TypeError, ValueError):
      return 0

  def _build_demo_path_from_ftp(self, filename: str) -> str:
    filename = _extract_ftp_name(filename)
    prefix = self.ftp_demo_dir.strip().strip("/").replace("\\", "/")
    if prefix:
      return f"{prefix}/{filename}"
    return filename

  def _fetch_ftp_demo_path_sync(self, map_name: str = "") -> Optional[str]:
    map_name = normalize_map_name_for_match(map_name)
    ftp = FTP()
    ftp.connect(self.ftp_host, self.ftp_port, timeout=self.timeout_sec)
    ftp.login(self.ftp_user, self.ftp_password)
    try:
      ftp.cwd(self.ftp_demo_dir)
      names = ftp.nlst()
      candidates: list[tuple[str, int]] = []
      for raw_name in names:
        name = _extract_ftp_name(raw_name)
        if not _is_plain_demo_file(name):
          continue
        modified_at = 0
        try:
          modified_at = self._mdtm_to_unix(ftp.sendcmd(f"MDTM {name}"))
        except Exception:
          modified_at = 0
        candidates.append((name, modified_at))

      total_candidates = len(candidates)
      map_matches_count = (
        sum(1 for name, _ in candidates if is_demo_map_compatible(map_name, name))
        if map_name
        else total_candidates
      )

      chosen = pick_ftp_demo_filename(candidates, map_name=map_name)
      if not chosen:
        _log.info(
          "WOW demo resolve: FTP no suitable demo: dir=%s total_candidates=%s map_expected=%s map_matches=%s",
          self.ftp_demo_dir,
          total_candidates,
          map_name or "-",
          map_matches_count,
        )
        return None

      _log.info(
        "WOW demo resolve: FTP candidate selected: dir=%s total_candidates=%s map_expected=%s map_matches=%s chosen=%s",
        self.ftp_demo_dir,
        total_candidates,
        map_name or "-",
        map_matches_count,
        chosen,
      )
      return self._build_demo_path_from_ftp(chosen)
    finally:
      try:
        ftp.quit()
      except Exception:
        ftp.close()

  async def _resolve_via_hltv(self, map_name: str) -> Optional[str]:
    if not self.hltv_enabled:
      return None

    try:
      status_text = await asyncio.to_thread(self._fetch_status_sync)
    except Exception as err:
      _log.warning("WOW demo resolve: HLTV status failed: host=%s port=%s error=%s", self.host, self.port, err)
      return None

    demo_path = parse_hltv_recording_path(status_text)
    if not demo_path:
      _log.info("WOW demo resolve: HLTV status has no recording path")
      return None

    _log.info(
      "WOW demo resolve: HLTV candidate path=%s demo_map=%s map_expected=%s",
      demo_path,
      extract_map_from_demo_path(demo_path) or "-",
      map_name or "-",
    )

    demo_url = build_myarena_demo_url(self.myarena_host, self.myarena_hid, demo_path)
    if not demo_url:
      _log.info("WOW demo resolve: HLTV demo URL build failed for path=%s", demo_path)
      return None

    return demo_url

  async def _resolve_via_ftp(self, map_name: str) -> Optional[str]:
    if not self.ftp_enabled:
      return None

    try:
      demo_path = await asyncio.to_thread(self._fetch_ftp_demo_path_sync, map_name)
    except Exception as err:
      _log.warning(
        "WOW demo resolve: FTP lookup failed: host=%s port=%s dir=%s error=%s",
        self.ftp_host,
        self.ftp_port,
        self.ftp_demo_dir,
        err,
      )
      return None

    if not demo_path:
      _log.info("WOW demo resolve: FTP lookup returned no demo path for map=%s", map_name)
      return None

    demo_url = build_myarena_demo_url(self.myarena_host, self.myarena_hid, demo_path)
    if not demo_url:
      _log.info("WOW demo resolve: FTP demo URL build failed for path=%s", demo_path)
      return None

    return demo_url

  async def resolve_demo(self, map_name: str = "", *, force_refresh: bool = False) -> DemoResolveResult:
    expected_map = normalize_map_name_for_match(map_name)
    if not self.enabled:
      return DemoResolveResult(map_expected=expected_map, reason="resolver_disabled")

    now = time.monotonic()
    map_name = expected_map

    if not force_refresh and self._cache_url and (now - self._cache_at) <= self.cache_ttl_sec:
      if not map_name or is_demo_map_compatible(map_name, _safe_str(self._cache_path)):
        return DemoResolveResult(
          demo_url=self._cache_url,
          demo_path=self._cache_path,
          source="cache",
          map_expected=expected_map,
          map_found=extract_map_from_demo_path(_safe_str(self._cache_path)),
          reason="cache_hit",
          attempted_sources=["cache"],
        )

    ordered_resolvers: tuple[tuple[str, callable], ...] = (
      (("ftp", self._resolve_via_ftp), ("hltv", self._resolve_via_hltv))
      if self.prefer_ftp
      else (("hltv", self._resolve_via_hltv), ("ftp", self._resolve_via_ftp))
    )

    attempted_sources: list[str] = []
    mismatch_result: Optional[DemoResolveResult] = None

    for source_name, resolver in ordered_resolvers:
      attempted_sources.append(source_name)
      demo_url = await resolver(map_name)
      if not demo_url:
        continue

      demo_path = unquote(demo_url.split("&dem=", 1)[-1])
      demo_map = extract_map_from_demo_path(demo_path)
      if map_name and demo_path and not is_demo_map_compatible(map_name, demo_path):
        if mismatch_result is None:
          mismatch_result = DemoResolveResult(
            map_mismatch=True,
            source=source_name,
            demo_path=demo_path,
            map_expected=expected_map,
            map_found=demo_map,
            reason="map_mismatch",
            attempted_sources=attempted_sources.copy(),
          )
        continue

      self._cache_path = demo_path
      self._cache_url = demo_url
      self._cache_at = now
      return DemoResolveResult(
        demo_url=demo_url,
        demo_path=demo_path,
        source=source_name,
        map_expected=expected_map,
        map_found=demo_map,
        reason="resolved",
        attempted_sources=attempted_sources,
      )

    if mismatch_result is not None:
      mismatch_result.attempted_sources = attempted_sources
      return mismatch_result

    return DemoResolveResult(
      map_expected=expected_map,
      reason="no_demo_found",
      attempted_sources=attempted_sources,
    )

  async def resolve_demo_url(self, map_name: str = "", *, force_refresh: bool = False) -> Optional[str]:
    result = await self.resolve_demo(map_name, force_refresh=force_refresh)
    return result.demo_url

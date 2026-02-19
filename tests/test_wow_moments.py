import asyncio
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from bot.wow_moments import (
  HltvDemoResolver,
  MomentState,
  build_myarena_demo_url,
  extract_map_from_demo_path,
  format_stars_emoji,
  is_demo_map_compatible,
  normalize_map_name_for_match,
  parse_hltv_recording_path,
  parse_moment_vote_payload,
  pick_ftp_demo_filename,
)


def _vote_payload(**kwargs):
  data = {
    "map": "de_dust2",
    "round_number": 4,
    "map_timeleft_sec": 777,
    "map_elapsed_sec": 83,
    "event_unix": 1_700_000_000,
    "voter_name": "Vasya",
    "voter_steam_id": "STEAM_0:1:1",
    "voter_slot": 1,
    "target_name": "Pro",
    "target_steam_id": "STEAM_0:1:2",
    "target_slot": 2,
    "target_team": 1,
    "target_frags": 15,
    "target_deaths": 3,
  }
  data.update(kwargs)
  return data


def test_parse_moment_vote_payload_requires_core_fields():
  assert parse_moment_vote_payload({}) is None
  assert parse_moment_vote_payload(_vote_payload(map="")) is None
  assert parse_moment_vote_payload(_vote_payload(voter_name="")) is None
  assert parse_moment_vote_payload(_vote_payload(target_name="")) is None
  assert parse_moment_vote_payload(_vote_payload()) is not None


def test_moment_state_deduplicates_same_voter():
  state = MomentState(window_sec=30, session_idle_sec=900)
  vote = parse_moment_vote_payload(_vote_payload())
  assert vote is not None

  first = state.process_vote(vote)
  assert first.created is True
  assert first.cluster.stars == 1

  second = state.process_vote(vote)
  assert second.duplicate_vote is True
  assert second.cluster.stars == 1


def test_moment_state_aggregates_other_voter_in_window():
  state = MomentState(window_sec=30, session_idle_sec=900)
  vote_a = parse_moment_vote_payload(_vote_payload(voter_steam_id="STEAM_0:1:11", voter_slot=11))
  vote_b = parse_moment_vote_payload(
    _vote_payload(voter_name="Petya", voter_steam_id="STEAM_0:1:12", voter_slot=12, event_unix=1_700_000_020)
  )
  assert vote_a is not None
  assert vote_b is not None

  first = state.process_vote(vote_a)
  second = state.process_vote(vote_b)
  assert first.cluster.cluster_id == second.cluster.cluster_id
  assert second.cluster.stars == 2
  assert second.cluster.voter_names == ["Vasya", "Petya"]


def test_moment_state_creates_new_cluster_outside_window():
  state = MomentState(window_sec=30, session_idle_sec=900)
  vote_a = parse_moment_vote_payload(_vote_payload(event_unix=1_700_000_000))
  vote_b = parse_moment_vote_payload(_vote_payload(event_unix=1_700_000_090, voter_steam_id="STEAM_0:1:7"))
  assert vote_a is not None
  assert vote_b is not None

  first = state.process_vote(vote_a)
  second = state.process_vote(vote_b)
  assert first.cluster.cluster_id != second.cluster.cluster_id


def test_moment_state_does_not_reset_between_map_suffix_variants():
  state = MomentState(window_sec=30, session_idle_sec=900)
  assert state.touch_info("de_dust2", 4, event_unix=1_700_000_000) is False
  assert state.touch_info("de_dust2_2x2", 5, event_unix=1_700_000_010) is False


def test_moment_state_aggregates_votes_with_map_suffix_variants():
  state = MomentState(window_sec=30, session_idle_sec=900)
  vote_a = parse_moment_vote_payload(_vote_payload(map="de_dust2", event_unix=1_700_000_000))
  vote_b = parse_moment_vote_payload(
    _vote_payload(
      map="de_dust2_2x2",
      voter_name="Petya",
      voter_steam_id="STEAM_0:1:12",
      voter_slot=12,
      event_unix=1_700_000_012,
    )
  )
  assert vote_a is not None
  assert vote_b is not None

  first = state.process_vote(vote_a)
  second = state.process_vote(vote_b)
  assert first.cluster.cluster_id == second.cluster.cluster_id
  assert second.cluster.stars == 2


def test_parse_hltv_recording_path_from_status():
  status = "HLTV proxy status\nRecording to cstrike/autorec-2602170929-de_dust2.dem, Length 31.2 sec\n"
  assert parse_hltv_recording_path(status) == "cstrike/autorec-2602170929-de_dust2.dem"


def test_build_myarena_demo_url():
  url = build_myarena_demo_url(
    "gsxx.myarena.pro",
    "12345",
    "cstrike/autorec-2602170929-de_dust2.dem",
  )
  assert url == "https://gsxx.myarena.pro/getzipdemo.php?hid=12345&dem=cstrike/autorec-2602170929-de_dust2.dem"


def test_pick_ftp_demo_filename_prefers_latest_and_map():
  candidates = [
    ("autorec-2602171520-de_dust2.dem", 10),
    ("autorec-2602171525-de_inferno.dem", 15),
    ("autorec-2602171530-de_dust2.dem", 20),
  ]
  assert pick_ftp_demo_filename(candidates, map_name="de_dust2") == "autorec-2602171530-de_dust2.dem"
  assert pick_ftp_demo_filename(candidates, map_name="de_nuke") == "autorec-2602171530-de_dust2.dem"
  assert pick_ftp_demo_filename(candidates, map_name="de_dust2_2x2") == "autorec-2602171530-de_dust2.dem"


def test_pick_ftp_demo_filename_uses_stamp_when_two_plain_demos_overlap():
  candidates = [
    ("autorec-2602171526-de_dust2.dem", 500),
    ("autorec-2602171527-de_inferno.dem", 100),
  ]
  assert pick_ftp_demo_filename(candidates) == "autorec-2602171527-de_inferno.dem"


def test_normalize_map_name_for_match():
  assert normalize_map_name_for_match("de_dust2_2x2") == "de_dust2"
  assert normalize_map_name_for_match("de_train_winter") == "de_train_winter"


def test_extract_map_from_demo_path():
  assert extract_map_from_demo_path("cstrike/autorec-2602180736-de_dust2.dem") == "de_dust2"
  assert extract_map_from_demo_path("cstrike/autorec-2602180736-de_dust2_2x2.dem") == "de_dust2"
  assert extract_map_from_demo_path("cstrike/de_train_winter.dem") == "de_train_winter"


def test_is_demo_map_compatible_with_mode_suffix():
  demo = "cstrike/autorec-2602180736-de_dust2.dem"
  assert is_demo_map_compatible("de_dust2_2x2", demo) is True
  assert is_demo_map_compatible("de_train_winter", demo) is False


def test_hltv_resolver_reports_mismatch_before_map_switch():
  resolver = HltvDemoResolver(
    host="127.0.0.1",
    port=27020,
    password="x",
    timeout_sec=2,
    myarena_host="gs13.myarena.pro",
    myarena_hid="89000",
  )

  replies = [
    "https://gs13.myarena.pro/getzipdemo.php?hid=89000&dem=cstrike/autorec-2602180816-de_train_winter.dem",
    "https://gs13.myarena.pro/getzipdemo.php?hid=89000&dem=cstrike/autorec-2602180816-de_dust2.dem",
  ]

  async def fake_hltv(_map_name):
    if replies:
      return replies.pop(0)
    return None

  async def fake_ftp(_map_name):
    return None

  resolver._resolve_via_hltv = fake_hltv  # type: ignore[method-assign]
  resolver._resolve_via_ftp = fake_ftp  # type: ignore[method-assign]

  first = asyncio.run(resolver.resolve_demo("de_dust2_2x2", force_refresh=True))
  assert first.demo_url is None
  assert first.map_mismatch is True
  assert first.reason == "map_mismatch"
  assert first.source == "hltv"
  assert first.map_expected == "de_dust2"
  assert first.map_found == "de_train_winter"

  second = asyncio.run(resolver.resolve_demo("de_dust2_2x2", force_refresh=True))
  assert second.demo_url is not None
  assert second.map_mismatch is False
  assert second.reason == "resolved"


def test_hltv_resolver_reports_no_demo_found_with_attempted_sources():
  resolver = HltvDemoResolver(
    host="127.0.0.1",
    port=27020,
    password="x",
    timeout_sec=2,
    myarena_host="gs13.myarena.pro",
    myarena_hid="89000",
    ftp_host="127.0.0.1",
    ftp_port=21,
    ftp_user="user",
    ftp_password="pass",
  )

  async def fake_hltv(_map_name):
    return None

  async def fake_ftp(_map_name):
    return None

  resolver._resolve_via_hltv = fake_hltv  # type: ignore[method-assign]
  resolver._resolve_via_ftp = fake_ftp  # type: ignore[method-assign]

  result = asyncio.run(resolver.resolve_demo("de_dust2_2x2", force_refresh=True))
  assert result.demo_url is None
  assert result.reason == "no_demo_found"
  assert result.map_expected == "de_dust2"
  assert result.attempted_sources == ["hltv", "ftp"]


def test_format_stars_emoji_compact():
  assert format_stars_emoji(1) == "⭐"
  assert format_stars_emoji(7) == "⭐⭐⭐⭐⭐⭐⭐"
  assert format_stars_emoji(10) == "⭐⭐⭐⭐⭐⭐⭐⭐⭐⭐"


def test_format_stars_emoji_with_cap_and_suffix():
  assert format_stars_emoji(11) == "⭐⭐⭐⭐⭐⭐⭐⭐⭐⭐ x11"
  assert format_stars_emoji(25) == "⭐⭐⭐⭐⭐⭐⭐⭐⭐⭐ x25"

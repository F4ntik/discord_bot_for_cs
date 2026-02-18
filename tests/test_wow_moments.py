import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from bot.wow_moments import (
  MomentState,
  build_myarena_demo_url,
  parse_hltv_recording_path,
  parse_moment_vote_payload,
  pick_ftp_demo_filename,
)


def _vote_payload(**kwargs):
  data = {
    "map": "de_dust2",
    "round_number": 4,
    "map_timeleft_sec": 777,
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
    _vote_payload(voter_steam_id="STEAM_0:1:12", voter_slot=12, event_unix=1_700_000_020)
  )
  assert vote_a is not None
  assert vote_b is not None

  first = state.process_vote(vote_a)
  second = state.process_vote(vote_b)
  assert first.cluster.cluster_id == second.cluster.cluster_id
  assert second.cluster.stars == 2


def test_moment_state_creates_new_cluster_outside_window():
  state = MomentState(window_sec=30, session_idle_sec=900)
  vote_a = parse_moment_vote_payload(_vote_payload(event_unix=1_700_000_000))
  vote_b = parse_moment_vote_payload(_vote_payload(event_unix=1_700_000_090, voter_steam_id="STEAM_0:1:7"))
  assert vote_a is not None
  assert vote_b is not None

  first = state.process_vote(vote_a)
  second = state.process_vote(vote_b)
  assert first.cluster.cluster_id != second.cluster.cluster_id


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


def test_pick_ftp_demo_filename_uses_stamp_when_two_plain_demos_overlap():
  candidates = [
    ("autorec-2602171526-de_dust2.dem", 500),
    ("autorec-2602171527-de_inferno.dem", 100),
  ]
  assert pick_ftp_demo_filename(candidates) == "autorec-2602171527-de_inferno.dem"

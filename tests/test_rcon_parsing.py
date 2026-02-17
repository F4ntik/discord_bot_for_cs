import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rehlds.rcon import RCON, startBytes


def test_parse_challenge_packet_with_rcon_keyword():
    raw = startBytes + b"challenge rcon 1753011797\n"
    assert RCON._parse_challenge_packet(raw) == "1753011797"


def test_parse_challenge_packet_plain_format():
    raw = startBytes + b"challenge 1753011797\n"
    assert RCON._parse_challenge_packet(raw) == "1753011797"


def test_parse_command_packet_print_prefix():
    raw = startBytes + b"print\nUnknown command: foo\n\x00"
    assert RCON._parse_command_packet(raw) == "Unknown command: foo"


def test_parse_command_packet_l_prefix():
    raw = startBytes + b"lServer cvar \"sv_cheats\" = \"0\"\n\x00"
    assert RCON._parse_command_packet(raw) == "Server cvar \"sv_cheats\" = \"0\""


def test_parse_command_packets_combines_split_payload():
    raw_packets = [
        startBytes + b"print\nULTRAHC_MAPS_BEGIN installed\nde_dust2\n",
        startBytes + b"de_inferno\nULTRAHC_MAPS_END 2\n\x00",
    ]
    parsed = RCON._parse_command_packets(raw_packets)
    assert "ULTRAHC_MAPS_BEGIN installed" in parsed
    assert "de_dust2" in parsed
    assert "de_inferno" in parsed
    assert "ULTRAHC_MAPS_END 2" in parsed

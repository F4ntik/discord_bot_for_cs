import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "config" not in sys.modules:
    sys.modules["config"] = types.SimpleNamespace(
        CS_HOST="127.0.0.1",
        CS_RCON_PASSWORD="test",
    )

from cs_server.cs_server import escape_rcon_param


@pytest.mark.parametrize(
    "value, expected",
    [
        ('Привет "всем"', "Привет 'всем'"),
        ("C:\\cfg\\autoexec.cfg", "C:\\cfg\\autoexec.cfg".replace('\\', '\\\\')),
        ('мяу мя"ку"', "мяу мя'ку'"),
        ('C:/Игры/"Counter"', "C:/Игры/'Counter'".replace('\\', '\\\\')),
        (15, "15"),
        (None, ""),
    ],
)
def test_escape_rcon_param(value, expected):
    assert escape_rcon_param(value) == expected

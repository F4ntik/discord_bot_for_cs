import pytest

from cs_server.cs_server import escape_rcon_param


@pytest.mark.parametrize(
    "value, expected",
    [
        ('Привет "всем"', 'Привет "всем"'.replace('"', '\\"')),
        ("C:\\cfg\\autoexec.cfg", "C:\\cfg\\autoexec.cfg".replace('\\', '\\\\')),
        (15, "15"),
        (None, ""),
    ],
)
def test_escape_rcon_param(value, expected):
    assert escape_rcon_param(value) == expected

import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from webserver.webhook_type import normalize_webhook_type


@pytest.mark.parametrize(
  "raw_type, expected",
  [
    ("info", "info"),
    ("message", "message"),
    (" info ", "info"),
    ("mes\nsage", "message"),
    ("in\x00fo", "info"),
    (" nfo", "info"),
    ("in o", "info"),
    ("mesage", "message"),
    ("m e s s a g e", "message"),
    ("notify", None),
    ("stats", None),
    ("", None),
    ("   ", None),
    (None, None),
    (123, None),
  ],
)
def test_normalize_webhook_type(raw_type, expected):
  assert normalize_webhook_type(raw_type) == expected

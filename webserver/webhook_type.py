import re


_KNOWN_WEBHOOK_TYPES = {"message", "info", "moment_vote"}
_WEBHOOK_TYPE_CODES = {
  1: "info",
  2: "message",
  3: "moment_vote",
}
_COLLAPSED_WEBHOOK_TYPES = {
  "".join(re.findall(r"[a-z]+", known_type)): known_type
  for known_type in _KNOWN_WEBHOOK_TYPES
}


def _is_edit_distance_le_one(value, target):
  value_len = len(value)
  target_len = len(target)
  if abs(value_len - target_len) > 1:
    return False

  i = 0
  j = 0
  edits = 0

  while i < value_len and j < target_len:
    if value[i] == target[j]:
      i += 1
      j += 1
      continue

    edits += 1
    if edits > 1:
      return False

    if value_len > target_len:
      i += 1
    elif value_len < target_len:
      j += 1
    else:
      i += 1
      j += 1

  if i < value_len or j < target_len:
    edits += 1

  return edits <= 1


def normalize_webhook_type(raw_type):
  if not isinstance(raw_type, str):
    return None

  normalized = raw_type.strip().lower()
  if not normalized:
    return None

  if normalized in _KNOWN_WEBHOOK_TYPES:
    return normalized

  collapsed = "".join(re.findall(r"[a-z]+", normalized))
  direct_match = _COLLAPSED_WEBHOOK_TYPES.get(collapsed)
  if direct_match:
    return direct_match

  for known in _COLLAPSED_WEBHOOK_TYPES:
    if _is_edit_distance_le_one(collapsed, known):
      return _COLLAPSED_WEBHOOK_TYPES[known]

  return None


def normalize_webhook_type_code(raw_type_code):
  if isinstance(raw_type_code, bool):
    return None

  try:
    code = int(raw_type_code)
  except (TypeError, ValueError):
    return None

  return _WEBHOOK_TYPE_CODES.get(code)

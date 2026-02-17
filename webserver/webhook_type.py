import re


_KNOWN_WEBHOOK_TYPES = {"message", "info"}


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
  if collapsed in _KNOWN_WEBHOOK_TYPES:
    return collapsed

  for known in _KNOWN_WEBHOOK_TYPES:
    if _is_edit_distance_le_one(collapsed, known):
      return known

  return None

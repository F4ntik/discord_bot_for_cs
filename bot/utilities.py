from __future__ import annotations

from bot.bot_server import dbot


def get_command_mention(command_name: str) -> str:
  """Return slash-command mention string or fallback to /name."""

  command = dbot.bot.tree.get_command(command_name)
  if command is None:
    return f"/{command_name}"

  mention = getattr(command, "mention", None)
  if mention is None:
    return f"/{command_name}"

  return mention

import config

from observer.observer_client import logger

from bot.bot_server import dbot
from pathlib import Path

import bot.commands
import bot.events

import webserver.ws_client

import data_server.redis_server
import data_server.sql_server

import cs_server.cs_server
import cs_server.map_installer


def read_app_version() -> str:
  version_file = Path(__file__).resolve().with_name("VERSION")
  try:
    version = version_file.read_text(encoding="utf-8").strip()
    return version or "0.0.0"
  except FileNotFoundError:
    return "0.0.0"


app_info = {
  'name': 'Ultra disBot',
  'version': read_app_version(),
  'author': 'Asura, Mep3ocTb',
  'description': 'Bot for connecting discord and cs server'
}

if __name__ == "__main__":
  logger.info("==================================")
  logger.info(f"=== {app_info['name']} v{app_info['version']} by {app_info['author']} ===")
  logger.info("==================================")


  dbot.run()

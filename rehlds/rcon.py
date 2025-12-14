from io import BytesIO
from typing import Optional
import socket
import re

startBytes = b'\xFF\xFF\xFF\xFF'
endBytes = b'\n'
packetSize = 8192

# SECTION Исключения RCON
# -- RCONError
class RCONError(Exception):
  """Базовый класс для исключений RCON."""
  pass

# -- BadRCONPassword
class BadRCONPassword(RCONError):
  """Исключение для неверного пароля RCON."""
  pass

# -- BadConnection
class BadConnection(RCONError):
  """Исключение для ошибок соединения."""
  pass

# -- ServerOffline
class ServerOffline(RCONError):
  """Исключение для оффлайн сервера."""
  pass

# -- NoConnection
class NoConnection(RCONError):
  """Исключение для отсутствия соединения."""
  pass

# !SECTION

# SECTION Class RCON
class RCON:
  # -- __init__()
  def __init__(self, *, host: str, port: int = 27015, password: str):
    """
    Инициализация класса RCON.

    :param host: Адрес хоста сервера.
    :param port: Порт сервера (по умолчанию 27015).
    :param password: Пароль для RCON.
    """
    self.host: str = host
    self.port: int = port
    self.password: str = password
    self.sock: Optional[socket.socket] = None

  # -- connect()
  def connect(self, timeout: int = 6) -> None:
    """
    Подключение к RCON серверу.

    :param timeout: Время ожидания подключения в секундах.
    :raises BadConnection: Если подключение не удалось.
    :raises BadRCONPassword: Если неверный пароль RCON.
    """
    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.sock.settimeout(timeout)

    try:
      self.sock.connect((self.host, int(self.port)))
      if self.execute('stats') == 'Bad rcon_password.':
        raise BadRCONPassword("Неверный пароль RCON.")
    except Exception as e:
      self.disconnect()
      raise BadConnection(f"Ошибка при соединении с RCON: {str(e)}")

  # -- disconnect()
  def disconnect(self) -> None:
    """Отключение от RCON сервера."""
    if self.sock:
      self.sock.close()
      self.sock = None

  # -- getChallenge()
  def getChallenge(self) -> str:
    """
    Получение вызова (challenge) от сервера.

    :return: Строка вызова.
    :raises NoConnection: Если нет соединения.
    :raises ServerOffline: Если сервер оффлайн.
    """
    if not self.sock:
      raise NoConnection("Нет соединения с RCON.")

    try:
      msg = BytesIO()
      msg.write(startBytes)
      msg.write(b'getchallenge')
      msg.write(endBytes)
      self.sock.send(msg.getvalue())

      raw = self.sock.recv(packetSize)

      if raw.startswith(startBytes):
        raw = raw[len(startBytes):]

      text = raw.decode(errors="ignore").strip("\x00\r\n ")

      match = re.search(r"challenge(?:\s+rcon)?\s+(-?\d+)", text, flags=re.IGNORECASE)
      if match:
        return match.group(1)

      # Фоллбек: иногда в пакете есть мусор/префиксы, но число всё равно присутствует.
      numbers = re.findall(r"-?\d+", text)
      if numbers:
        return numbers[-1]

      raise ServerOffline(f"Некорректный ответ challenge: {text!r}")
    except Exception as e:
      self.disconnect()
      raise ServerOffline(f"Ошибка в getChallenge (RCON) (Возможно, сервер оффлайн): {str(e)}")

  # -- execute()
  def execute(self, cmd: str) -> str:
    """
    Выполнение команды на сервере.

    :param cmd: Команда для выполнения.
    :return: Результат выполнения команды.
    :raises ServerOffline: Если сервер оффлайн.
    """
    try:
      challenge = self.getChallenge()

      msg = BytesIO()
      msg.write(startBytes)
      msg.write(b'rcon ')
      msg.write(challenge.encode())
      msg.write(b' ')
      msg.write(self.password.encode())
      msg.write(b' ')
      msg.write(cmd.encode())
      msg.write(endBytes)

      self.sock.send(msg.getvalue())
      raw = self.sock.recv(packetSize)

      if raw.startswith(startBytes):
        raw = raw[len(startBytes):]

      # Типичный ответ HLDS: "print\n<text>\n\0" или "l<text>\n\0"
      if raw.startswith(b'print'):
        raw = raw[len(b'print'):]
        if raw.startswith(b'\n'):
          raw = raw[1:]
      elif raw.startswith(b'l'):
        raw = raw[1:]

      return raw.decode(errors="ignore").strip("\x00\r\n ")
    except Exception as e:
      self.disconnect()
      raise ServerOffline(f"Ошибка в execute (RCON) (Возможно, сервер оффлайн): {str(e)}")

# !SECTION

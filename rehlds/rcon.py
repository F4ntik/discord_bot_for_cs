from io import BytesIO
from typing import Optional
import socket
import re

startBytes = b'\xFF\xFF\xFF\xFF'
endBytes = b'\n'
packetSize = 8192
RCON_TEXT_ENCODING = 'cp1251'
RCON_PACKET_BURST_TIMEOUT = 0.08

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
  def connect(self, timeout: int = 6, validate_password: bool = True) -> None:
    """
    Подключение к RCON серверу.

    :param timeout: Время ожидания подключения в секундах.
    :param validate_password: Выполнять ли проверку пароля через команду stats.
    :raises BadConnection: Если подключение не удалось.
    :raises BadRCONPassword: Если неверный пароль RCON.
    """
    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.sock.settimeout(timeout)

    try:
      self.sock.connect((self.host, int(self.port)))
      if validate_password and self.execute('stats') == 'Bad rcon_password.':
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

  @staticmethod
  def _strip_connectionless_prefix(raw: bytes) -> bytes:
    if raw.startswith(startBytes):
      return raw[len(startBytes):]
    return raw

  @staticmethod
  def _parse_challenge_packet(raw: bytes) -> str:
    cleaned = RCON._strip_connectionless_prefix(raw)
    try:
      text = cleaned.decode(RCON_TEXT_ENCODING, errors="ignore").strip("\x00\r\n ")
    except Exception:
      text = cleaned.decode(errors="ignore").strip("\x00\r\n ")

    match = re.search(r"challenge(?:\s+rcon)?\s+(-?\d+)", text, flags=re.IGNORECASE)
    if match:
      return match.group(1)

    numbers = re.findall(r"-?\d+", text)
    if numbers:
      return numbers[-1]

    raise ValueError(f"Некорректный ответ challenge: {text!r}")

  @staticmethod
  def _parse_command_packet(raw: bytes) -> str:
    cleaned = RCON._strip_connectionless_prefix(raw)

    # Типичный ответ HLDS: "print\n<text>\n\0" или "l<text>\n\0".
    if cleaned.startswith(b'print'):
      cleaned = cleaned[len(b'print'):]
      if cleaned.startswith(b'\n'):
        cleaned = cleaned[1:]
    elif cleaned.startswith(b'l'):
      cleaned = cleaned[1:]

    try:
      return cleaned.decode(RCON_TEXT_ENCODING, errors="ignore").strip("\x00\r\n ")
    except Exception:
      return cleaned.decode(errors="ignore").strip("\x00\r\n ")

  @staticmethod
  def _parse_command_packets(raw_packets: list[bytes]) -> str:
    if not raw_packets:
      return ""

    chunks = []
    for raw in raw_packets:
      text = RCON._parse_command_packet(raw)
      if text:
        chunks.append(text)

    return "\n".join(chunks).strip("\x00\r\n ")

  def _recv_command_packets(self) -> list[bytes]:
    if not self.sock:
      raise NoConnection("Нет соединения с RCON.")

    raw_packets: list[bytes] = []
    raw_packets.append(self.sock.recv(packetSize))

    original_timeout = self.sock.gettimeout()
    try:
      self.sock.settimeout(RCON_PACKET_BURST_TIMEOUT)
      while True:
        try:
          raw_packets.append(self.sock.recv(packetSize))
        except socket.timeout:
          break
    finally:
      self.sock.settimeout(original_timeout)

    return raw_packets

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
      return self._parse_challenge_packet(raw)
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
      msg.write(challenge.encode('ascii', errors='ignore'))
      msg.write(b' ')
      msg.write(self.password.encode(RCON_TEXT_ENCODING, errors='ignore'))
      msg.write(b' ')
      msg.write(cmd.encode(RCON_TEXT_ENCODING, errors='replace'))
      msg.write(endBytes)

      self.sock.send(msg.getvalue())
      raw_packets = self._recv_command_packets()
      return self._parse_command_packets(raw_packets)
    except Exception as e:
      self.disconnect()
      raise ServerOffline(f"Ошибка в execute (RCON) (Возможно, сервер оффлайн): {str(e)}")

# !SECTION

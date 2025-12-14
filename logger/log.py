import datetime
import logging
import traceback
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
import re

# SECTION class Log
class Log:
  # -- __init__()
  def __init__(self) -> None:
    """
    Инициализирует экземпляр класса Log и настраивает логирование.

    Создает два логгера: один для информационных сообщений, другой для сообщений об ошибках.
    """
    # Защита от дублирования хендлеров при повторной инициализации (например, при перезапуске/перезагрузке).
    logging.raiseExceptions = False

    # INFO
    self.info_logger = logging.getLogger("LogInfo")
    self.info_logger.setLevel(logging.INFO)
    self.info_logger.propagate = False
    self.info_logger.handlers.clear()

    # Создаем обработчик для вывода логов в консоль
    console_handler_info = logging.StreamHandler()
    formatter_info = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
    console_handler_info.setFormatter(formatter_info)
    self.info_logger.addHandler(console_handler_info)

    # Создаем обработчик для записи логов в файл с ежедневной ротацией.
    # Активный файл: logs/log.txt
    # Ротация: в полночь -> logs/log.txt.YYYY-MM-DD
    # Хранение: 7 дней (удаляются самые старые)
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "log.txt"

    # Если файл остался с прошлого дня (например, бот не работал в момент полуночной ротации),
    # переименуем его при старте, чтобы "log.txt" всегда был текущим днём.
    if log_file.exists():
      last_write_date = datetime.datetime.fromtimestamp(log_file.stat().st_mtime).date()
      today = datetime.datetime.now().date()
      if last_write_date < today:
        rotated = log_dir / f"log.txt.{last_write_date.isoformat()}"
        if rotated.exists():
          suffix = 1
          while True:
            candidate = log_dir / f"log.txt.{last_write_date.isoformat()}.{suffix}"
            if not candidate.exists():
              rotated = candidate
              break
            suffix += 1
        try:
          log_file.rename(rotated)
        except OSError:
          # Если файл занят/не удалось переименовать — оставим как есть, ротация сработает на следующем цикле.
          pass

    file_handler_info = TimedRotatingFileHandler(
      filename=str(log_file),
      when="midnight",
      interval=1,
      backupCount=7,
      encoding="utf-8",
      delay=True,
      utc=False,
    )
    file_handler_info.suffix = "%Y-%m-%d"
    file_handler_info.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.\d+)?$")
    file_handler_info.setFormatter(formatter_info)
    self.info_logger.addHandler(file_handler_info)

    # ERROR
    self.error_logger = logging.getLogger("LogError")
    self.error_logger.setLevel(logging.ERROR)
    self.error_logger.propagate = False
    self.error_logger.handlers.clear()

    # Создаем обработчик для вывода логов в консоль
    console_handler_error = logging.StreamHandler()
    formatter_error = logging.Formatter('[%(asctime)s] %(levelname)s:( %(filename)s) (%(lineno)d): %(message)s')
    console_handler_error.setFormatter(formatter_error)
    self.error_logger.addHandler(console_handler_error)
    self.error_logger.addHandler(file_handler_info)

    # Применяем политику хранения на старте.
    try:
      for old_file in file_handler_info.getFilesToDelete():
        Path(old_file).unlink(missing_ok=True)
    except Exception:
      pass

  # -- info()
  def info(self, message: str) -> None:
    """
    Выводит информационное сообщение.

    :param message: Сообщение для вывода.
    """
    self.info_logger.info(message)

  # -- error()
  def error(self, message: str) -> None:
    """
    Выводит сообщение об ошибке.

    :param message: Сообщение об ошибке для вывода.
    """
    self.error_logger.error(message)

  # -- exception()
  def exception(self, message: str) -> None:
    """
    Выводит сообщение об исключении с трассировкой.

    :param message: Сообщение об исключении для вывода.
    """
    self.error_logger.error(f"{message}\n{traceback.format_exc()}")

# !SECTION

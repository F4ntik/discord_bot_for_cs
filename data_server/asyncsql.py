import aiomysql
from typing import Any, List, Optional, Tuple, Dict, AsyncIterator, Iterator, Callable
import logging
import asyncio
from observer.observer_client import logger

# SECTION AioMysqlError
class AioMysqlError(Exception):
  """Базовый класс для исключений AioMysql."""
  pass

# -- ConnectionError
class ConnectionError(AioMysqlError):
  """Исключение для ошибок подключения к базе данных."""
  pass

# -- QueryError
class QueryError(AioMysqlError):
  """Исключение для ошибок выполнения SQL-запросов."""
  pass

# -- MultipleQueryError
class MultipleQueryError(AioMysqlError):
  """Исключение для ошибок выполнения нескольких SQL-запросов."""
  pass

# -- TransactionError
class TransactionError(AioMysqlError):
  """Исключение для ошибок, связанных с транзакциями."""
  pass

# !SECTION

# SECTION AioMysql
class AioMysql:
  # -- __init__()
  def __init__(self, host: str, port: int, user: str, password: str, db: str) -> None:
    self.host: str = host
    self.port: int = port
    self.user: str = user
    self.password: str = password
    self.db: str = db
    self.pool: Optional[aiomysql.Pool] = None
    self.conn: Optional[aiomysql.Connection] = None
    self._connecting: bool = False
    self._connection_attempts: int = 0
    self._reconnect_backoff_time: int = 5  # Начальное время задержки перед повторной попыткой (в секундах)
    self._max_reconnect_attempts: int = 10  # Максимальное количество попыток переподключения
    self._is_healthy: bool = False
    self._monitoring_task: Optional[asyncio.Task] = None
    self._monitor_interval: int = 30  # Interval in seconds for monitoring
    
  # -- is_connected
  def is_connected(self) -> bool:
    """Проверяет, существует ли соединение с базой данных и оно здорово."""
    return self.pool is not None and not self.pool.closed and self._is_healthy

  # -- _monitor_connection_loop()
  async def _monitor_connection_loop(self) -> None:
    """Periodically checks connection health and attempts to restore if unhealthy."""
    while True:
      await asyncio.sleep(self._monitor_interval)
      # check_connection will update _is_healthy
      is_currently_healthy = await self.check_connection() 
      if not is_currently_healthy:
        logger.warning("AioMysql: Monitoring detected unhealthy connection. Attempting to restore...")
        try:
          # connect() itself has retry/backoff logic and updates _is_healthy
          await self.connect() 
          if self._is_healthy:
            logger.info("AioMysql: Connection restored by monitoring task.")
          else:
            # This case might occur if connect() fails despite its own retries
            logger.error("AioMysql: Failed to restore connection via monitoring task after connect() attempt.")
        except ConnectionError as e:
          logger.error(f"AioMysql: Error during scheduled reconnect by monitor: {e}")
        except Exception as e:
          logger.error(f"AioMysql: Unexpected error during scheduled reconnect by monitor: {e}")
      # If connection is healthy, check_connection already confirmed it.
      # No specific action needed here if healthy, loop continues.

  # -- _start_monitoring_task()
  def _start_monitoring_task(self) -> None:
    """Starts the background connection monitoring task if not already running."""
    if self._monitoring_task is None or self._monitoring_task.done():
      self._monitoring_task = asyncio.create_task(self._monitor_connection_loop())
      logger.info("AioMysql: Started background connection monitoring task.")

  # -- connect()
  async def connect(self) -> None:
    """Создает пул соединений с базой данных."""
    # Предотвращаем повторные попытки подключения во время выполнения
    if self._connecting:
      return
      
    self._connecting = True
    self._connection_attempts = 0
    self._is_healthy = False
    
    while self._connection_attempts < self._max_reconnect_attempts:
      try:
        # Если уже есть пул, но он не работает - закрываем его
        if self.pool:
          try:
            self.pool.close()
            await self.pool.wait_closed()
          except Exception:
            pass
            
        self.pool = await aiomysql.create_pool(
          host=self.host,
          port=self.port,
          user=self.user,
          password=self.password,
          db=self.db,
          autocommit=True,  # Автоматический коммит для операций
          maxsize=10,  # Максимальное количество соединений в пуле
          minsize=1,  # Минимальное количество соединений в пуле
          loop=asyncio.get_event_loop()
        )
        
        # Проверка соединения
        async with self.pool.acquire() as conn:
          async with conn.cursor() as cursor:
            await cursor.execute('SELECT 1')  # Простой запрос для проверки
        
        self._is_healthy = True
        self._connecting = False
        self._start_monitoring_task() # Start monitoring after successful connection
        return
        
      except aiomysql.Error as e:
        self._is_healthy = False # Ensure unhealthy on connect failure
        self._connection_attempts += 1
        wait_time = min(60, self._reconnect_backoff_time * (2 ** (self._connection_attempts - 1)))  # Exponential backoff
        logger.error(f"Ошибка при подключении к MySQL ({self._connection_attempts}/{self._max_reconnect_attempts}): {e}. Повторная попытка через {wait_time} сек.")
        await asyncio.sleep(wait_time)
      except Exception as e:
        self._is_healthy = False # Ensure unhealthy on connect failure
        logger.error(f"Неожиданная ошибка при подключении к MySQL: {e}")
        self._connection_attempts += 1
        await asyncio.sleep(self._reconnect_backoff_time)

    # Если исчерпаны все попытки
    self._connecting = False
    self._is_healthy = False # Explicitly set to false if all attempts fail
    raise ConnectionError(f"Не удалось подключиться к базе данных после {self._max_reconnect_attempts} попыток.")
    
  # -- _execute_one_internal
  async def _execute_one_internal(self, query: str, args: Optional[Tuple[Any, ...]] = ()) -> Tuple[int, Optional[List[Tuple[Any, ...]]]]:
    async with self.pool.acquire() as conn:
      async with conn.cursor() as cursor:
        await cursor.execute(query, args)
        affected_rows = cursor.rowcount  # Получаем количество затронутых строк
        
        if cursor.description:  # Проверяем, есть ли результат
          result = await cursor.fetchall()
          return affected_rows, result
        
        await conn.commit()
        return affected_rows, None  # Если нет результата
  
  # -- execute_one()
  async def execute_one(self, query: str, args: Optional[Tuple[Any, ...]] = ()) -> Tuple[int, Optional[List[Tuple[Any, ...]]]]:
    """Выполняет SQL-запрос и возвращает количество затронутых строк и результат."""
    try:
      return await self.execute_with_retry(self._execute_one_internal, query, args)
    except aiomysql.Error as e:
      raise QueryError(f"Ошибка при выполнении запроса: {e}. Запрос: {query}, Параметры: {args}")
    except Exception as e:
      raise QueryError(f"Неожиданная ошибка: {e}. Запрос: {query}, Параметры: {args}")

  # -- _execute_change_internal
  async def _execute_change_internal(self, query: str, args: Optional[Tuple[Any, ...]] = ()) -> int:
    async with self.pool.acquire() as conn:
      async with conn.cursor() as cursor:
        await cursor.execute(query, args)
        affected_rows = cursor.rowcount
        await conn.commit()  # Коммитим изменения
        return affected_rows
        
  # -- execute_change()
  async def execute_change(self, query: str, args: Optional[Tuple[Any, ...]] = ()) -> int:
    """Выполняет SQL-запрос, изменяющий данные, и возвращает количество затронутых строк."""
    try:
      return await self.execute_with_retry(self._execute_change_internal, query, args)
    except aiomysql.Error as e:
      raise QueryError(f"Ошибка при выполнении запроса: {e}. Запрос: {query}, Параметры: {args}")
    except Exception as e:
      raise QueryError(f"Неожиданная ошибка: {e}. Запрос: {query}, Параметры: {args}")

  # -- check_connection
  async def check_connection(self) -> bool:
    """Performs a health check and updates _is_healthy. Does not reconnect."""
    if not self.pool or self.pool.closed:
        logger.warning("AioMysql: Connection check failed - pool is None or closed.")
        self._is_healthy = False
        return False
    try:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT 1')
        self._is_healthy = True
        return True
    except Exception as e:
        logger.warning(f"AioMysql: Connection check failed: {e}")
        self._is_healthy = False
        # Do not try to connect here, let the monitor loop do it
        return False

  # -- execute_with_retry
  async def execute_with_retry(self, func: Callable, *args, **kwargs) -> Any:
    """Executes a function with retries, considering connection health."""
    max_retries = 3 # Retries for a specific operation, not for overall connection health
    retry_delay = 1 # seconds

    for attempt in range(max_retries):
        if not self._is_healthy:
            # If connection is not healthy, wait for the monitoring task to potentially recover
            wait_duration = self._monitor_interval / 2 if self._monitor_interval > 2 else 1
            logger.warning(
                f"AioMysql: Connection unhealthy for {func.__name__}. "
                f"Waiting up to {wait_duration}s for recovery before attempt {attempt + 1}/{max_retries}."
            )
            await asyncio.sleep(wait_duration) 
            
            # Re-check health after waiting. The monitor might have fixed it.
            # Or, if an operation just failed and marked it unhealthy, this gives monitor a chance.
            await self.check_connection() 
            if not self._is_healthy:
                if attempt == max_retries - 1:
                    logger.error(f"AioMysql: Connection persistently unhealthy after {max_retries} checks for {func.__name__}.")
                    raise ConnectionError(f"AioMysql: Connection persistently unhealthy after {max_retries} checks for {func.__name__}.")
                logger.warning(f"AioMysql: Still unhealthy before attempt {attempt + 1}, retrying operation for {func.__name__}.")
                continue # Go to next attempt if still not healthy

        try:
            return await func(*args, **kwargs)
        except (aiomysql.OperationalError, aiomysql.InterfaceError) as e:
            logger.error(f"AioMysql: Connection error during {func.__name__}: {e}. Attempt {attempt + 1}/{max_retries}.")
            self._is_healthy = False # Mark as unhealthy, monitor should pick it up
            # Potentially trigger an immediate check/reconnect by monitor if possible,
            # otherwise, wait for its natural cycle.
            if attempt == max_retries - 1:
                logger.error(f"AioMysql: Failed {func.__name__} after {max_retries} retries due to: {e}")
                raise ConnectionError(f"AioMysql: Failed {func.__name__} after {max_retries} retries due to: {e}")
            await asyncio.sleep(retry_delay * (attempt + 1)) # Linear backoff for operation retry
        except Exception as e:
            # Non-connection related errors
            logger.error(f"AioMysql: Non-connection error during {func.__name__}: {e}")
            raise # Re-raise original error

  # -- _execute_select_internal
  async def _execute_select_internal(self, query: str, args: Optional[Tuple[Any, ...]] = ()) -> List[Tuple[Any, ...]]:
    async with self.pool.acquire() as conn:
      async with conn.cursor() as cursor:
        await cursor.execute(query, args)
        result = await cursor.fetchall()  # Получаем результат
        return result

  # -- execute_select()
  async def execute_select(self, query: str, args: Optional[Tuple[Any, ...]] = ()) -> List[Tuple[Any, ...]]:
    """Выполняет SQL-запрос на выборку данных и возвращает результат."""
    try:
      return await self.execute_with_retry(self._execute_select_internal, query, args)
    except aiomysql.Error as e:
      raise QueryError(f"Ошибка при выполнении запроса: {e}. Запрос: {query}, Параметры: {args}")
    except Exception as e:
      raise QueryError(f"Неожиданная ошибка: {e}. Запрос: {query}, Параметры: {args}")


  # -- _exec_many_internal
  async def _exec_many_internal(self, query: str, args_list: List[Tuple[Any, ...]]) -> None:
    async with self.pool.acquire() as conn:
      async with conn.cursor() as cursor:
        await cursor.executemany(query, args_list)
        await conn.commit()
  
  # -- exec_many()
  async def exec_many(self, query: str, args_list: List[Tuple[Any, ...]]) -> None:
    """Выполняет один и тот же SQL-запрос несколько раз с разными наборами параметров."""
    try:
      return await self.execute_with_retry(self._exec_many_internal, query, args_list)
    except aiomysql.Error as e:
      raise MultipleQueryError(f"Ошибка при выполнении нескольких запросов: {e}. Запрос: {query}, Параметры: {args_list}")
    except Exception as e:
      raise MultipleQueryError(f"Неожиданная ошибка: {e}. Запрос: {query}, Параметры: {args_list}")

  # -- fetch_iter()
  async def fetch_iter(self, query: str, *, args: Optional[Tuple[Any, ...]] = (), batch_size: int = 100) -> AsyncIterator[Tuple[Any, ...]]:
    """Асинхронный итератор для выборки данных по частям."""
    # The execute_with_retry logic is complex for true async iterators.
    # A simpler approach for iterators is to ensure connection is healthy at start,
    # and rely on the monitor for long-term health. Short-lived errors might still break iteration.
    # For robust iteration over flaky connections, a more complex iterator wrapper would be needed.
    
    # Initial health check
    if not self._is_healthy:
        logger.warning(f"AioMysql: Connection unhealthy before starting fetch_iter for query: {query[:100]}...")
        await self.check_connection() # Attempt a quick check
        if not self._is_healthy:
             # Try to wait for monitor to recover
            logger.warning(f"AioMysql: Waiting for monitor to recover before fetch_iter for query: {query[:100]}...")
            await asyncio.sleep(self._monitor_interval / 2 if self._monitor_interval > 2 else 1)
            await self.check_connection() # Final check
            if not self._is_healthy:
                raise ConnectionError(f"AioMysql: Connection unhealthy, cannot start fetch_iter for query: {query[:100]}...")

    try:
      async with self.pool.acquire() as conn:
        async with conn.cursor() as cursor:
          await cursor.execute(query, args)
          while True:
            rows = await cursor.fetchmany(size=batch_size)
            if not rows:
              break
            for row in rows:
              yield row
    except (aiomysql.OperationalError, aiomysql.InterfaceError) as e:
      self._is_healthy = False # Mark as unhealthy
      logger.error(f"AioMysql: Connection error during fetch_iter: {e}. Query: {query[:100]}...")
      # Iteration is likely broken. Rely on monitor for future, but this op fails.
      raise QueryError(f"Ошибка при выборке данных (Operational/Interface Error): {e}. Запрос: {query}, Параметры: {args}")
    except aiomysql.Error as e:
      # Other aiomysql errors
      raise QueryError(f"Ошибка при выборке данных: {e}. Запрос: {query}, Параметры: {args}")
    except Exception as e:
      # Other unexpected errors
      raise QueryError(f"Неожиданная ошибка: {e}. Запрос: {query}, Параметры: {args}")

  # -- close()
  async def close(self) -> None:
    """Закрывает пул соединений и останавливает мониторинг."""
    logger.info("AioMysql: Closing connection pool and stopping monitoring task.")
    if self._monitoring_task and not self._monitoring_task.done():
        self._monitoring_task.cancel()
        try:
            await self._monitoring_task  # Wait for task to acknowledge cancellation
        except asyncio.CancelledError:
            logger.info("AioMysql: Monitoring task successfully cancelled.")
        except Exception as e:
            # Log other potential errors during task cancellation/awaiting
            logger.error(f"AioMysql: Error during monitoring task shutdown: {e}")
        self._monitoring_task = None

    if self.pool:
        try:
            self.pool.close()
            await self.pool.wait_closed()
            logger.info("AioMysql: Connection pool closed.")
        except Exception as e:
            logger.error(f"AioMysql: Error closing connection pool: {e}")
        self.pool = None
    
    self._is_healthy = False # Mark as unhealthy after closing
      
# !SECTION

# SECTION Transaction
class Transaction:
  # -- __init__()
  def __init__(self, pool: aiomysql.Pool) -> None:
    """Инициализирует объект транзакции с пулом соединений."""
    self.pool: aiomysql.Pool = pool
    self.conn: Optional[aiomysql.Connection] = None  # Соединение, используемое в транзакции
  
  # -- begin()
  async def begin(self) -> None:
    """Начинает транзакцию."""
    if not self.pool:
      raise ConnectionError("Пул соединений не инициализирован.")
    try:
      self.conn = await self.pool.acquire()  # Получаем соединение
      await self.conn.begin()  # Начинаем транзакцию
    except aiomysql.Error as e:
      raise TransactionError(f"Ошибка при начале транзакции: {e}")
  
  # -- execute()
  async def execute(self, query: str, args: Optional[Tuple[Any, ...]] = ()) -> Tuple[int, Optional[List[Tuple[Any, ...]]]]:
    """Выполняет SQL-запрос в рамках текущей транзакции.
    
    Args:
      query (str): SQL-запрос для выполнения.
      args (Optional[Tuple[Any, ...]]): Параметры для SQL-запроса.

    Returns:
      Tuple[int, Optional[List[Tuple[Any, ...]]]]: Количество затронутых строк и результат запроса.
    """
    if not self.conn:
      raise TransactionError("Нет активной транзакции.")
    try:
      async with self.conn.cursor() as cursor:
        await cursor.execute(query, args)  # Выполняем запрос
        affected_rows = cursor.rowcount  # Получаем количество затронутых строк
        result = await cursor.fetchall()  # Возвращаем результаты
        return affected_rows, result
    except aiomysql.Error as e:
      raise TransactionError(f"Ошибка при выполнении запроса: {e}. Запрос: {query}, Параметры: {args}")
  
  # -- commit()
  async def commit(self) -> None:
    """Коммитит текущую транзакцию."""
    if not self.conn:
      raise TransactionError("Нет активной транзакции.")
    try:
      await self.conn.commit()  # Коммитим изменения
    except aiomysql.Error as e:
      raise TransactionError(f"Ошибка при коммите транзакции: {e}")
  
  # -- rollback()
  async def rollback(self) -> None:
    """Откатывает текущую транзакцию."""
    if not self.conn:
      raise TransactionError("Нет активной транзакции.")
    try:
      await self.conn.rollback()  # Откатываем изменения
    except aiomysql.Error as e:
      raise TransactionError(f"Ошибка при откате транзакции: {e}")
  
  # -- close()
  async def close(self) -> None:
    """Закрывает соединение."""
    if self.conn:
      await self.pool.release(self.conn)  # Освобождаем соединение
      self.conn = None  # Сбрасываем соединение

# !SECTION
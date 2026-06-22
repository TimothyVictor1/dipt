"""PostgreSQL connection-pool management.

Provides a thread-safe connection pool and a context manager that hands out
connections and reliably returns them, rolling back on error and committing on
success. Centralising this here keeps transaction handling consistent across
every repository call.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extensions import connection as PGConnection

from dipt.config import Settings
from dipt.exceptions import ConnectionPoolError

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Thread-safe wrapper around :class:`psycopg2.pool.ThreadedConnectionPool`.

    Args:
        settings: Validated application settings supplying DSN and pool bounds.

    Raises:
        ConnectionPoolError: If the underlying pool cannot be created.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        try:
            self._pool = pg_pool.ThreadedConnectionPool(
                minconn=settings.db_pool_min,
                maxconn=settings.db_pool_max,
                dsn=settings.database_dsn,
            )
        except psycopg2.Error as exc:
            raise ConnectionPoolError(
                f"Failed to initialise connection pool: {exc}"
            ) from exc
        logger.info(
            "Connection pool created (min=%d, max=%d)",
            settings.db_pool_min,
            settings.db_pool_max,
        )

    @contextmanager
    def connection(self) -> Iterator[PGConnection]:
        """Yield a pooled connection, committing on success.

        The connection is committed if the block completes normally and rolled
        back if any exception propagates. The connection is always returned to
        the pool.

        Yields:
            A live psycopg2 connection.

        Raises:
            ConnectionPoolError: If a connection cannot be acquired.
        """
        conn: PGConnection | None = None
        try:
            conn = self._pool.getconn()
        except psycopg2.Error as exc:
            raise ConnectionPoolError(
                f"Failed to acquire connection from pool: {exc}"
            ) from exc

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Transaction rolled back due to an error")
            raise
        finally:
            self._pool.putconn(conn)

    def close(self) -> None:
        """Close all connections held by the pool."""
        try:
            self._pool.closeall()
            logger.info("Connection pool closed")
        except psycopg2.Error:
            logger.exception("Error while closing connection pool")

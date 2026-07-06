"""
SQL Server connection management.
Uses a simple connection pool to handle concurrent requests.
"""

import pyodbc
from contextlib import contextmanager
from threading import Lock
from queue import Queue, Empty
import logging

logger = logging.getLogger(__name__)

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=thtrdinfradb1;"
    "Database=InfrastructureMonitorDB;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)


class ConnectionPool:
    """Simple thread-safe connection pool for pyodbc."""

    def __init__(self, conn_str: str, max_size: int = 5):
        self.conn_str = conn_str
        self.max_size = max_size
        self._pool: Queue = Queue(maxsize=max_size)
        self._created = 0
        self._lock = Lock()

    def _create_connection(self):
        return pyodbc.connect(self.conn_str, timeout=10, autocommit=True)

    @contextmanager
    def get_connection(self):
        """Get a connection from the pool (context manager)."""
        conn = None
        try:
            try:
                conn = self._pool.get_nowait()
            except Empty:
                with self._lock:
                    if self._created < self.max_size:
                        conn = self._create_connection()
                        self._created += 1
                if conn is None:
                    conn = self._pool.get(timeout=10)
            
            # Test the connection
            try:
                conn.cursor().execute("SELECT 1").fetchone()
            except Exception:
                conn = self._create_connection()
            
            yield conn
        except Exception as e:
            logger.error(f"Connection pool error: {e}")
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
                with self._lock:
                    self._created -= 1
            raise
        else:
            try:
                self._pool.put_nowait(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass

    def close_all(self):
        """Close all connections in the pool."""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Exception:
                pass


# Global connection pool
pool = ConnectionPool(CONN_STR, max_size=10)

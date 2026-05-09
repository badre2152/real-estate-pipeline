"""
db.py — Database utilities with connection pooling.

FIX #28: fetch_all now consistently uses `with conn` context manager.
FIX #53: Connection pool (ThreadedConnectionPool) replaces repeated open/close.
"""

import os
import threading
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from src.utils.logger import get_logger
from src.config import DB_POOL_MIN, DB_POOL_MAX

load_dotenv()
logger = get_logger("db")

# ── Connection Pool ──────────────────────────────────────────────────────────
_pool: pg_pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                _pool = pg_pool.ThreadedConnectionPool(
                    minconn=DB_POOL_MIN,
                    maxconn=DB_POOL_MAX,
                    host=os.getenv("DB_HOST", "localhost"),
                    port=int(os.getenv("DB_PORT", 5432)),
                    dbname=os.getenv("DB_NAME", "avito_db"),
                    user=os.getenv("DB_USER", "postgres"),
                    password=os.getenv("DB_PASSWORD"),
                )
                logger.info(
                    f"Connection pool initialised (min={DB_POOL_MIN}, max={DB_POOL_MAX}).")
    return _pool


def get_connection() -> psycopg2.extensions.connection:
    """Borrow a connection from the pool."""
    return _get_pool().getconn()


def release_connection(conn: psycopg2.extensions.connection) -> None:
    """Return a connection to the pool."""
    if _pool and not _pool.closed:
        _pool.putconn(conn)


def close_pool() -> None:
    """Tear down the pool (call at end of pipeline run)."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        logger.info("Connection pool closed.")
    _pool = None


# ── Query helpers ────────────────────────────────────────────────────────────

def execute_query(query: str, params=None) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
        logger.debug("Query executed.")
    except Exception as e:
        logger.error(f"Query failed: {e}")
        raise
    finally:
        release_connection(conn)


def bulk_insert(query: str, rows: list) -> None:
    if not rows:
        logger.warning("bulk_insert called with empty rows — skipping.")
        return
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                execute_values(cur, query, rows)
        logger.info(f"Bulk insert: {len(rows)} rows.")
    except Exception as e:
        logger.error(f"Bulk insert failed: {e}")
        raise
    finally:
        release_connection(conn)


def fetch_all(query: str, params=None) -> list:
    # FIX #28: consistently use `with conn` (transaction context) like
    # execute_query.
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
    except Exception as e:
        logger.error(f"fetch_all failed: {e}")
        raise
    finally:
        release_connection(conn)

"""SQLite connection handling.

The database lives in ``DATA_DIR`` so it sits alongside the hosted sites and
is covered by whatever backs that directory up. (WAL adds ``-wal``/``-shm``
files beside it: a consistent backup is ``sqlite3 ... ".backup ..."`` or a
cold copy of all three, never a copy of the db file alone.)

Several processes share the file — uvicorn's workers and the job worker —
which SQLite supports on a local disk given the settings applied to every
connection here: WAL, so readers never block the writer; a busy timeout, so
contending writers wait their turn instead of erroring; and ``IMMEDIATE``
transactions, so a write transaction takes the write lock when it begins —
where the timeout applies — rather than upgrading to it mid-way, where SQLite
reports contention immediately.
"""

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from cervo import config
from cervo.errors import AppError

_BUSY_TIMEOUT = 5  # seconds a contended write waits before giving up


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection and commit on the way out.

    An :class:`~cervo.errors.AppError` still commits: it means the app decided
    to refuse something, and what it wrote while deciding — a wrong-code count,
    a cleared challenge — has to survive. Anything else is a real failure and
    rolls back.

    A fresh connection per operation keeps things simple under the HTTP
    server's threads — SQLite connections are not shared safely between them.
    """
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        config.DATABASE_PATH, timeout=_BUSY_TIMEOUT, isolation_level="IMMEDIATE"
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")  # WAL's intended pairing
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    except AppError:
        conn.commit()
        raise
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


async def transact[T](fn: Callable[[sqlite3.Connection], T]) -> T:
    """Run ``fn`` inside :func:`connect`, off the event loop.

    The sqlite3 driver is synchronous, so a transaction run directly in an
    async handler would stall every other request in the process. This hands
    the whole transaction — connection, ``fn``, commit — to a worker thread;
    the connection never crosses threads, and the event loop stays free.
    Context variables propagate, so request identity (``get_access_token``)
    still works inside ``fn``.
    """
    return await asyncio.to_thread(_transact, fn)


def _transact[T](fn: Callable[[sqlite3.Connection], T]) -> T:
    with connect() as conn:
        return fn(conn)

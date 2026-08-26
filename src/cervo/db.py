"""SQLite connection handling.

The database lives in ``DATA_DIR`` so it sits alongside the hosted sites and is
covered by whatever backs that directory up.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from cervo import config
from cervo.errors import AppError


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
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
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

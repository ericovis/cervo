"""The ASGI application, importable as ``cervo.asgi:application``.

Uvicorn is pointed at this import string (see :func:`cervo.main`) so it can
run several worker processes, each importing the app afresh. Stateless HTTP
is what makes that safe: no MCP session lives in any one process's memory,
so any worker can serve any request — state worth keeping is in the
database.
"""

from cervo import monitoring
from cervo.server import app

monitoring.setup()  # per worker process; a no-op outside production
application = monitoring.wrap(app.http_app(stateless_http=True))

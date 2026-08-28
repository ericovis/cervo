def main() -> None:
    """Start the MCP server over HTTP.

    Uvicorn gets an import string, not the app object, so it can spawn
    ``WEB_CONCURRENCY`` worker processes, each importing ``cervo.asgi`` in a
    process of its own; the tables are created here first, once, before any
    of them serves. Imports are deferred so that importing any submodule of
    ``cervo`` does not drag the server in — and cannot loop back through
    this module.
    """
    import uvicorn

    from cervo import config
    from cervo.schema import create_tables

    create_tables()
    uvicorn.run(
        "cervo.asgi:application",
        host=config.MCP_HOST,
        port=config.MCP_PORT,
        workers=config.WEB_CONCURRENCY,
    )

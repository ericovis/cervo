def main() -> None:
    """Start the MCP server over HTTP.

    Imports are deferred so that importing any submodule of ``cervo`` does not
    drag the server in — and cannot loop back through this module.
    """
    from cervo import config
    from cervo.schema import create_tables
    from cervo.server import app

    create_tables()
    app.run(transport="http", host=config.MCP_HOST, port=config.MCP_PORT)

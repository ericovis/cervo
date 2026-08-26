from cervo import config
from cervo.server import app


def main() -> None:
    app.run(transport="http", host=config.MCP_HOST, port=config.MCP_PORT)

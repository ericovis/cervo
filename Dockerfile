FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Everything runs as this unprivileged user. /mnt/data is baked in with its
# ownership so a fresh named volume mounted there inherits it — the first
# container to mount one seeds it from the image.
RUN useradd --create-home --uid 1000 cervo \
    && mkdir -p /app /mnt/data \
    && chown cervo:cervo /app /mnt/data
USER cervo
WORKDIR /app

COPY --chown=cervo:cervo pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY --chown=cervo:cervo . .
RUN uv sync --locked

ENTRYPOINT ["uv", "run"]
CMD ["cervo"]

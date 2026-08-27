"""Shapes for the hosted sites."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field

from cervo import config

Slug = Annotated[
    str,
    Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Lowercase DNS-safe label used as the site's subdomain.",
    ),
]

WebsiteStatus = Literal["pending", "deploying", "live", "failed"]


class Website(BaseModel):
    """A static website hosted on the VPS, owned by exactly one user.

    ``status`` and ``error`` are not stored on the site: they describe its
    latest deployment job, and the service fills them in when it reads a
    site out of the database.
    """

    slug: Slug
    user_id: int
    created_at: datetime
    updated_at: datetime
    status: WebsiteStatus = "pending"
    error: str | None = None

    @computed_field
    @property
    def url(self) -> str:
        """Where the site is served once its deployment is live."""
        return config.origin(f"{self.slug}.{config.DOMAIN}")

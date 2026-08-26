"""Shapes for the hosted sites."""

from typing import Annotated

from pydantic import BaseModel, Field

Slug = Annotated[
    str,
    Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Lowercase DNS-safe label used as the site's subdomain.",
    ),
]


class Website(BaseModel):
    """A static website hosted on the VPS, owned by exactly one user."""

    slug: Slug
    user_id: int

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

FilePath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        pattern=r"^(?:[a-z0-9][a-z0-9._-]*/)*[a-z0-9][a-z0-9._-]*\.(?:html|css)$",
        description="Path inside the site, e.g. 'blog/post.html' or 'css/main.css'.",
    ),
]

FileStatus = Literal["pending", "working", "done", "failed"]


class Route(BaseModel):
    """One site as the web server needs it, for rendering its config.

    The owner's email is registered with the certificate authority as the
    ACME contact for the site's own certificate — the operator's
    ``ACME_EMAIL`` covers only cervo's own hostname.
    """

    slug: Slug
    owner_email: str


class Website(BaseModel):
    """A static website hosted on the VPS, owned by exactly one user.

    ``status``, ``error``, and the step fields are not stored on the site:
    they describe its latest deployment job, and the service fills them in
    when it reads a site out of the database. A deployment is a chain of
    jobs, so ``step`` names what is happening right now and ``steps_done``
    of ``steps_total`` says how far along it is.
    """

    slug: Slug
    user_id: int
    created_at: datetime
    updated_at: datetime
    status: WebsiteStatus = "pending"
    error: str | None = None
    step: str | None = None
    steps_done: int = 0
    steps_total: int = 0

    @computed_field
    @property
    def url(self) -> str:
        """Where the site is served once its deployment is live."""
        return config.origin(f"{self.slug}.{config.DOMAIN}")


class FileWrite(BaseModel):
    """A file submitted for writing into a site, and how that work stands.

    Like a deployment, the write runs as a chain of jobs; ``status``,
    ``error``, and the step fields describe the latest job of that chain,
    filled in by the service whenever the state is read.
    """

    slug: Slug
    path: FilePath
    status: FileStatus = "pending"
    error: str | None = None
    step: str | None = None
    steps_done: int = 0
    steps_total: int = 0

    @computed_field
    @property
    def url(self) -> str:
        """Where the file is served once it is written."""
        site = config.origin(f"{self.slug}.{config.DOMAIN}")
        return f"{site}/{self.path}"


class FileDeletion(BaseModel):
    """A file submitted for deletion from a site, and how that work stands.

    Like a write, the deletion runs as a chain of jobs; ``status``,
    ``error``, and the step fields describe the latest job of that chain,
    filled in by the service whenever the state is read. There is no url:
    nothing is served afterwards.
    """

    slug: Slug
    path: FilePath
    status: FileStatus = "pending"
    error: str | None = None
    step: str | None = None
    steps_done: int = 0
    steps_total: int = 0

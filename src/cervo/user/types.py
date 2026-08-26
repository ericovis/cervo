"""Shapes for the people who own sites."""

from pydantic import BaseModel, EmailStr


class User(BaseModel):
    """Someone who has confirmed an email address at least once.

    A user is created the first time an address signs in, and owns any number
    of sites from then on.
    """

    id: int
    email: EmailStr

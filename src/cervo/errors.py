"""The one exception the app raises on purpose."""


class AppError(Exception):
    """A failure the user is meant to read.

    Work already written when one of these is raised is kept, because the
    bookkeeping on a failure path — counting a wrong code, clearing a spent
    challenge — is the point. Any other exception rolls the transaction back.
    """

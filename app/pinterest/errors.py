"""Typed Pinterest failures. Messages are safe to show in the UI (never contain tokens)."""


class PinterestError(Exception):
    code = "error"
    retryable = False        # a later retry may succeed without changing anything
    needs_reconnect = False  # the user must re-authorize the app
    ambiguous = False        # the request may have reached Pinterest and succeeded

    def __init__(self, message: str | None = None, *, retry_after: int | None = None, ambiguous: bool | None = None):
        super().__init__(message or self.default_message)
        self.retry_after = retry_after
        if ambiguous is not None:
            self.ambiguous = ambiguous

    default_message = "Pinterest returned an error."


class NotConnected(PinterestError):
    code, needs_reconnect = "not_connected", True
    default_message = "Pinterest is not connected. Connect your account first."


class AuthExpired(PinterestError):
    code, needs_reconnect = "auth_expired", True
    default_message = "Pinterest authorization expired or was revoked. Please reconnect Pinterest."


class PermissionDenied(PinterestError):
    code = "permission_denied"
    default_message = ("Pinterest refused this action (missing permission or app access level). Reconnect with all "
                       "requested permissions, or check that your Pinterest app has the needed access.")


class InvalidBoard(PinterestError):
    code = "invalid_board"
    default_message = "The selected board is not available on this Pinterest account. Refresh boards and choose again."


class InvalidImage(PinterestError):
    code = "invalid_image"
    default_message = "Pinterest rejected the pin image."


class InvalidURL(PinterestError):
    code = "invalid_url"
    default_message = "Pinterest rejected the destination URL."


class BadRequest(PinterestError):
    code = "bad_request"
    default_message = "Pinterest rejected the request as malformed."


class RateLimited(PinterestError):
    code, retryable = "rate_limited", True
    default_message = "Pinterest rate limit reached. Try again later."


class ServiceUnavailable(PinterestError):
    code, retryable = "unavailable", True
    default_message = "Pinterest is temporarily unavailable. Try again later."

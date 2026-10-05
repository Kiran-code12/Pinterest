"""Stand-in provider used whenever DIRECT_PUBLISHING_ENABLED is false (the MVP default).

It contains NO network code: every method refuses. Because main.py always installs this class when the flag is off,
no Pinterest API call can happen by accident, even if some code path forgot to check the flag.
"""
from __future__ import annotations

from .errors import DirectPublishingDisabled
from .provider import PinterestProvider
from .types import Account, Board, CreatedPin, PinMetrics, PinPayload, TokenSet


class DisabledPinterestProvider(PinterestProvider):
    name = "disabled"

    def is_configured(self) -> bool:
        return False

    def _no(self, *args, **kwargs):
        raise DirectPublishingDisabled()

    def authorization_url(self, state: str) -> str:
        self._no()

    def exchange_code(self, code: str) -> TokenSet:
        self._no()

    def refresh(self, refresh_token: str) -> TokenSet:
        self._no()

    def get_account(self, access_token: str) -> Account:
        self._no()

    def get_boards(self, access_token: str) -> list[Board]:
        self._no()

    def create_pin(self, access_token: str, payload: PinPayload) -> CreatedPin:
        self._no()

    def get_pin(self, access_token: str, pin_id: str) -> dict:
        self._no()

    def get_analytics(self, access_token: str, pin_id: str, days: int = 30) -> PinMetrics:
        self._no()

    def revoke(self, access_token: str) -> bool:
        return False

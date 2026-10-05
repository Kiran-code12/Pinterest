"""The ONLY place the rest of the app talks to Pinterest through (see real.py / mock.py).

Conceptual mapping:  connect() = authorization_url() + exchange_code()   getAccount() = get_account()
getBoards() = get_boards()   createPin() = create_pin()   getPin() = get_pin()   getAnalytics() = get_analytics()
disconnect() = revoke()
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .types import Account, Board, CreatedPin, PinMetrics, PinPayload, TokenSet


class PinterestProvider(ABC):
    name = "base"
    simulated = False  # True for the mock provider: nothing is sent to Pinterest

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def authorization_url(self, state: str) -> str: ...

    @abstractmethod
    def exchange_code(self, code: str) -> TokenSet: ...

    @abstractmethod
    def refresh(self, refresh_token: str) -> TokenSet: ...

    @abstractmethod
    def get_account(self, access_token: str) -> Account: ...

    @abstractmethod
    def get_boards(self, access_token: str) -> list[Board]: ...

    @abstractmethod
    def create_pin(self, access_token: str, payload: PinPayload) -> CreatedPin: ...

    @abstractmethod
    def get_pin(self, access_token: str, pin_id: str) -> dict: ...

    @abstractmethod
    def get_analytics(self, access_token: str, pin_id: str, days: int = 30) -> PinMetrics: ...

    @abstractmethod
    def revoke(self, access_token: str) -> bool:
        """Best-effort token revocation at Pinterest. Returns True if Pinterest confirmed."""

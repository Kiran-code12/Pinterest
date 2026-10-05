from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None          # naive UTC
    refresh_expires_at: datetime | None = None  # naive UTC
    scopes: list[str] = field(default_factory=list)


@dataclass
class Account:
    username: str
    id: str | None = None
    account_type: str | None = None


@dataclass
class Board:
    id: str
    name: str
    privacy: str | None = None


@dataclass
class PinPayload:
    board_id: str
    title: str
    description: str
    link: str
    alt_text: str
    image_png: bytes


@dataclass
class CreatedPin:
    pin_id: str
    url: str | None = None       # only when the API actually returns one
    board_id: str | None = None


@dataclass
class PinMetrics:
    impressions: int = 0
    saves: int = 0
    pin_clicks: int = 0
    outbound_clicks: int = 0
    raw: dict = field(default_factory=dict)  # exactly what Pinterest reported

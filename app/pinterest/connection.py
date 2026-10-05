"""Connection lifecycle: OAuth connect, encrypted token storage, refresh, boards, default board, disconnect."""
from __future__ import annotations

import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import PinterestBoard, PinterestConnection
from .crypto import TokenCrypto, TokenDecryptError
from .errors import AuthExpired, NotConnected, PermissionDenied, PinterestError
from .provider import PinterestProvider
from .types import Board, TokenSet

log = logging.getLogger("engine.pinterest")
T = TypeVar("T")
REFRESH_MARGIN = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConnectionService:
    def __init__(self, provider: PinterestProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.crypto = TokenCrypto(settings.secret_key)

    # ---- state --------------------------------------------------------------------------------------
    def get(self, db: Session) -> PinterestConnection | None:
        return db.scalar(select(PinterestConnection).order_by(PinterestConnection.id.desc()))

    def is_connected(self, db: Session) -> bool:
        c = self.get(db)
        return c is not None and c.status == "connected"

    @staticmethod
    def can_publish(conn: PinterestConnection | None) -> bool:
        return bool(conn and conn.status == "connected" and "pins:write" in (conn.scopes or [])
                    and "boards:read" in (conn.scopes or []))

    # ---- connect (OAuth authorization-code flow) -----------------------------------------------------
    def begin(self, session: dict) -> str:
        state = secrets.token_urlsafe(32)
        session["pinterest_state"] = state
        return self.provider.authorization_url(state)

    def complete(self, db: Session, session: dict, code: str, state: str) -> PinterestConnection:
        expected = session.pop("pinterest_state", None)
        if not expected or not state or not hmac.compare_digest(expected, state):
            raise PinterestError("The authorization response did not match this session (possible forged "
                                 "request). Start the connection again.")
        if not code:
            raise PinterestError("Pinterest did not return an authorization code.")
        tokens = self.provider.exchange_code(code)
        account = self.provider.get_account(tokens.access_token)
        boards = self.provider.get_boards(tokens.access_token)
        old = self.get(db)
        default_id = old.default_board_id if old and old.account_id == account.id else None
        if old is not None:
            db.delete(old)
            db.flush()
        conn = PinterestConnection(provider=self.provider.name, account_id=account.id, username=account.username,
                                   account_type=account.account_type, status="connected")
        self._apply_tokens(conn, tokens)
        db.add(conn)
        db.flush()
        self._store_boards(db, conn, boards)
        known = {b.id: b for b in boards}
        if default_id in known:
            conn.default_board_id, conn.default_board_name = default_id, known[default_id].name
        elif len(boards) == 1:
            conn.default_board_id, conn.default_board_name = boards[0].id, boards[0].name
        db.commit()
        log.info("Pinterest account connected (%s)", account.username)
        return conn

    def _apply_tokens(self, conn: PinterestConnection, t: TokenSet) -> None:
        conn.access_token_enc = self.crypto.encrypt(t.access_token)
        if t.refresh_token:
            conn.refresh_token_enc = self.crypto.encrypt(t.refresh_token)
        conn.expires_at, conn.refresh_expires_at = t.expires_at, t.refresh_expires_at
        if t.scopes:
            conn.scopes = t.scopes
        conn.status, conn.last_error = "connected", None

    # ---- tokens -------------------------------------------------------------------------------------
    def _mark_expired(self, db: Session, conn: PinterestConnection, reason: str) -> None:
        conn.status, conn.last_error = "expired", reason[:300]
        db.commit()

    def _refresh(self, db: Session, conn: PinterestConnection) -> None:
        if not conn.refresh_token_enc:
            self._mark_expired(db, conn, "No refresh token; reconnect Pinterest.")
            raise AuthExpired()
        try:
            tokens = self.provider.refresh(self.crypto.decrypt(conn.refresh_token_enc))
        except (AuthExpired, TokenDecryptError):
            self._mark_expired(db, conn, "Authorization expired or revoked.")
            raise AuthExpired() from None
        self._apply_tokens(conn, tokens)
        db.commit()

    def access_token(self, db: Session) -> str:
        conn = self.get(db)
        if conn is None:
            raise NotConnected()
        if conn.status != "connected":
            raise AuthExpired()
        if conn.expires_at and conn.expires_at - _now() < REFRESH_MARGIN:
            self._refresh(db, conn)
        try:
            return self.crypto.decrypt(conn.access_token_enc)
        except TokenDecryptError:
            self._mark_expired(db, conn, "Stored authorization unreadable.")
            raise AuthExpired() from None

    def call(self, db: Session, fn: Callable[[str], T]) -> T:
        """Run fn(access_token); on 401 refresh once and retry; flag the connection if it stays invalid."""
        token = self.access_token(db)
        try:
            return fn(token)
        except AuthExpired:
            conn = self.get(db)
            if conn is None:
                raise NotConnected() from None
            self._refresh(db, conn)  # raises AuthExpired (and marks expired) when refresh also fails
            return fn(self.crypto.decrypt(conn.access_token_enc))
        except PermissionDenied as ex:
            conn = self.get(db)
            if conn is not None:
                conn.last_error = str(ex)[:300]
                db.commit()
            raise

    # ---- boards / default board -------------------------------------------------------------------------
    def _store_boards(self, db: Session, conn: PinterestConnection, boards: list[Board]) -> None:
        existing = {b.board_id: b for b in conn.boards}
        seen = set()
        for b in boards:
            seen.add(b.id)
            row = existing.get(b.id)
            if row is None:
                conn.boards.append(PinterestBoard(board_id=b.id, name=b.name, privacy=b.privacy))
            else:
                row.name, row.privacy, row.fetched_at = b.name, b.privacy, _now()
        for bid, row in existing.items():
            if bid not in seen:
                db.delete(row)
        if conn.default_board_id and conn.default_board_id not in seen:
            conn.default_board_id = conn.default_board_name = None
        elif conn.default_board_id:
            conn.default_board_name = next((b.name for b in boards if b.id == conn.default_board_id), None)

    def refresh_boards(self, db: Session) -> list[PinterestBoard]:
        conn = self.get(db)
        if conn is None:
            raise NotConnected()
        boards = self.call(db, self.provider.get_boards)
        self._store_boards(db, conn, boards)
        db.commit()
        db.refresh(conn)
        return list(conn.boards)

    def set_default_board(self, db: Session, board_id: str) -> PinterestBoard:
        conn = self.get(db)
        if conn is None:
            raise NotConnected()
        board = next((b for b in conn.boards if b.board_id == board_id), None)
        if board is None:
            raise PinterestError("That board is not in the list for this account. Refresh boards and try again.")
        conn.default_board_id, conn.default_board_name = board.board_id, board.name
        db.commit()
        return board

    def board_name(self, db: Session, board_id: str | None) -> str | None:
        conn = self.get(db)
        if conn is None or not board_id:
            return None
        return next((b.name for b in conn.boards if b.board_id == board_id), None)

    # ---- disconnect -------------------------------------------------------------------------------------
    def disconnect(self, db: Session) -> bool:
        """Revoke at Pinterest where supported (best effort), then always delete local tokens."""
        conn = self.get(db)
        if conn is None:
            return False
        revoked = False
        try:
            revoked = self.provider.revoke(self.crypto.decrypt(conn.access_token_enc))
        except (TokenDecryptError, PinterestError):
            revoked = False
        db.delete(conn)
        db.commit()
        log.info("Pinterest disconnected (revoked_at_pinterest=%s)", revoked)
        return revoked

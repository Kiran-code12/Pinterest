from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url or url == "sqlite://":
                from sqlalchemy.pool import StaticPool
                kwargs["poolclass"] = StaticPool
            else:
                path = url.split("sqlite:///", 1)[-1]
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _fk(dbapi_conn, _):  # enforce foreign keys in SQLite
                dbapi_conn.execute("PRAGMA foreign_keys=ON")
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from . import models  # noqa: F401  (register tables)
        Base.metadata.create_all(self.engine)

    def session(self):
        return self.session_factory()

    def ping(self) -> bool:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT 1")).scalar() == 1

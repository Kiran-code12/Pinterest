from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
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
        self.add_missing_columns()
        if self.engine.url.drivername.startswith("sqlite") and self.engine.url.database not in (None, "", ":memory:"):
            try:  # the file holds (encrypted) OAuth tokens: owner-only access
                Path(self.engine.url.database).chmod(0o600)
            except OSError:
                pass

    def add_missing_columns(self) -> list[str]:
        """Lightweight upgrade for existing databases: ADD COLUMN for new nullable/defaulted model columns.
        (create_all only creates missing TABLES.) Returns the columns it added."""
        added = []
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have or col.primary_key:
                        continue
                    if not col.nullable and col.default is None and col.server_default is None:
                        continue  # cannot add a NOT NULL column without a default to existing rows
                    ddl = col.type.compile(self.engine.dialect)
                    default = ""
                    if col.default is not None and getattr(col.default, "is_scalar", False):
                        v = col.default.arg
                        default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v)}"
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{default}'))
                    added.append(f"{table.name}.{col.name}")
        return added

    def session(self):
        return self.session_factory()

    def ping(self) -> bool:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT 1")).scalar() == 1

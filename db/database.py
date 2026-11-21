"""Database engine and session configuration.

SQLite remains the zero-dependency default. MySQL is an explicit opt-in via
``DATABASE_URL`` (``mysql+pymysql://...``); SQLite-only pragmas and connection
arguments are never applied to MySQL. Unsupported/unknown URL schemes fail
closed instead of silently falling back to a local SQLite file.

Schema ownership:

* SQLite demos use ``Base.metadata.create_all`` (``init_db``) exactly as before.
* MySQL deployments should run Alembic (``python -m db.migrate`` /
  ``alembic upgrade head``). ``DB_AUTO_CREATE=true`` is available for the
  self-contained demo container, but is not the production migration path.
"""

import logging
import os
import stat
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "gaokao.db")

SUPPORTED_SCHEMES = ("sqlite", "mysql", "mysql+pymysql")


def _normalize_url(raw: str) -> str:
    value = raw.strip()
    scheme = value.split("://", 1)[0].lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(
            f"Unsupported DATABASE_URL scheme '{scheme}'. "
            "Supported: sqlite:///..., mysql+pymysql://... (mysql:// is normalized to PyMySQL)."
        )
    if value.startswith("mysql://"):
        value = "mysql+pymysql://" + value[len("mysql://") :]
    return value


def redact_database_url(url: str) -> str:
    """Return a log-safe URL with the password removed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid database url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username}:***@{netloc}" if netloc else f"{parts.username}:***"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


_RAW_DB_URL = os.getenv("DATABASE_URL", "").strip()
if _RAW_DB_URL:
    DATABASE_URL = _normalize_url(_RAW_DB_URL)
else:
    DATABASE_URL = f"sqlite:///{DB_PATH}"

IS_SQLITE = _is_sqlite(DATABASE_URL)


def _create_engine(url: str) -> Engine:
    if _is_sqlite(url):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            echo=False,
        )

        @event.listens_for(engine, "connect")
        def _set_wal_mode(dbapi_conn, connection_record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine

    logger.info("Using MySQL engine at %s", redact_database_url(url))
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        connect_args={
            "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
            "charset": "utf8mb4",
        },
        echo=False,
    )


engine = _create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_session():
    """获取数据库会话（用完记得关闭）"""
    return SessionLocal()


def _lock_db_permissions(db_path: str):
    """收紧数据库及其 WAL/SHM 附属文件的权限为 0600（仅 owner 可读写）。"""
    for suffix in ("", "-wal", "-shm"):
        path = db_path + suffix
        if os.path.exists(path):
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            except OSError as _e:
                pass


def is_db_connected() -> bool:
    """Check database engine connectivity.

    Returns:
        True if the engine can execute a simple query, False otherwise.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _sqlite_path_from_url(url: str) -> str | None:
    if not _is_sqlite(url):
        return None
    path = url.split("sqlite:///", 1)[-1]
    if not path or path == ":memory:":
        return None
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


def init_db():
    """Create all tables.

    SQLite: existing demo behaviour, kept for backward compatibility.
    MySQL: only when ``DB_AUTO_CREATE=true`` (demo bootstrap). Production must
    use ``python -m db.migrate`` instead.
    """
    from db.models import (  # noqa: F401 — import to register tables with Base.metadata
        AdminTokenRevocation,
        AdmissionScore,
        CareerTrend,
        Conversation,
        ConversationMessage,
        DataQualityIssue,
        DataQualityJob,
        DatasetSnapshot,
        EnrollmentPlan,
        Feedback,
        GraduateProgram,
        GraduateScore,
        Highlight,
        Major,
        OperationAuditLog,
        QualityScore,
        QualityTaskOutbox,
        School,
        SubjectRanking,
        UserMemoryEntry,
        UserMemoryVault,
        YiFenYiDuan,
    )

    if IS_SQLITE:
        db_path = _sqlite_path_from_url(DATABASE_URL) or DB_PATH
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        Base.metadata.create_all(bind=engine)
        _lock_db_permissions(db_path)
        return

    if os.getenv("DB_AUTO_CREATE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        logger.warning("DB_AUTO_CREATE=true on MySQL: bootstrapping all tables (demo only)")
        Base.metadata.create_all(bind=engine)
    else:
        logger.info("MySQL: skipping create_all; run `python -m db.migrate` for schema migrations")

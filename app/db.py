"""Подключение к SQLite и фабрика сессий."""
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app import config


class Base(DeclarativeBase):
    """Базовый класс для всех таблиц."""


def _make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            # сборщик работает в фоновом потоке, поэтому разрешаем доступ из разных потоков
            "check_same_thread": False,
            # если база занята другой записью — ждём до 30 с, а не падаем сразу
            "timeout": 30,
        },
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        # Драйвер sqlite3 сам управляет транзакциями и ломает вложенные транзакции (SAVEPOINT).
        # Отключаем это и открываем транзакции сами (рецепт из документации SQLAlchemy).
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        # без этого SQLite не удаляет связанные строки каскадом
        cursor.execute("PRAGMA foreign_keys=ON")
        # чтение не блокируется, пока фоновый сборщик пишет
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_transaction(connection):
        # IMMEDIATE: блокировка на запись берётся сразу в начале транзакции.
        # Иначе две транзакции, которые сначала читали, а потом пишут, ловят "database is locked".
        # Транзакции у нас короткие (сеть — вне транзакций), так что очередь почти не заметна.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


# Создаются при первом обращении (см. configure), чтобы тесты могли подменить путь к базе.
_engine: Optional[Engine] = None
SessionLocal = sessionmaker(expire_on_commit=False)


def configure(db_path: Optional[Path] = None) -> None:
    """Подключается к базе по указанному пути (по умолчанию — из config)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(db_path or config.DB_PATH)
    SessionLocal.configure(bind=_engine)


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""
    from app import models  # noqa: F401  — регистрирует таблицы в Base.metadata

    if _engine is None:
        configure()
    Base.metadata.create_all(_engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия для фоновых задач: коммит при успехе, откат при ошибке."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI: одна сессия на запрос."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

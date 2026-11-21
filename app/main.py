"""Точка входа: uvicorn app.main:app"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.api import router, rss_router
from app.db import init_db, session_scope
from app.matching import preload_matchers
from app.own_channel import ensure_own_channel
from app.scheduler import FetchScheduler

STATIC_DIR = Path(__file__).resolve().parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(start_scheduler: Optional[bool] = None) -> FastAPI:
    if start_scheduler is None:
        start_scheduler = config.SCHEDULER_ENABLED

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        with session_scope() as session:
            ensure_own_channel(session)
        # модели грузим при старте, а не на первом запросе: если модели нет, это сразу видно в логе
        preload_matchers()
        scheduler = FetchScheduler() if start_scheduler else None
        if scheduler:
            scheduler.start()
        yield
        if scheduler:
            scheduler.stop()

    app = FastAPI(
        title="RSS News Filter",
        description="Агрегатор новостей из RSS с фильтрами по ключевым словам и темам",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(rss_router)

    @app.get("/health", tags=["service"])
    def health():
        return {"status": "ok"}

    # интерфейс (роль 5): обычные HTML + JS без сборки, работают без Node и без интернета
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        # no-cache: после обновления кода браузер не покажет старую версию страницы
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()

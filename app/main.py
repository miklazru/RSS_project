"""Точка входа: uvicorn app.main:app"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import config
from app.api import router, rss_router
from app.db import init_db, session_scope
from app.matching import preload_matchers
from app.own_channel import ensure_own_channel
from app.scheduler import FetchScheduler

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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        # временная страница, пока нет интерфейса (роль 5)
        return (
            "<h1>RSS News Filter</h1>"
            "<p>Интерфейс в разработке. API и документация: <a href='/docs'>/docs</a></p>"
        )

    return app


app = create_app()

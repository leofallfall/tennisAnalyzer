"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from tennis_api.config import Settings, get_settings
from tennis_api.db import Database
from tennis_api.errors import install_error_handlers
from tennis_api.routers import ai, analyses, strokes_review, system, videos
from tennis_api.routers.system import API_VERSION


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    database = Database(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        cfg.storage_dir.mkdir(parents=True, exist_ok=True)
        database.migrate()
        yield
        database.engine.dispose()

    app = FastAPI(
        title="Tennis Performance Analyzer API",
        version=API_VERSION,
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.db = database
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # M1.10: JSON (pose.json ~5-10x smaller on the wire) is gzip-compressed when the
    # client accepts it. Starlette never compresses video/* or 206 range responses, so
    # video seeking is unaffected.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    install_error_handlers(app)
    app.include_router(system.router)
    app.include_router(videos.router)
    app.include_router(analyses.router)
    app.include_router(strokes_review.router)
    app.include_router(ai.router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    cfg = get_settings()
    uvicorn.run("tennis_api.main:app", host=cfg.api_host, port=cfg.api_port, reload=False)

"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tennis_api.config import Settings, get_settings
from tennis_api.db import Database
from tennis_api.errors import install_error_handlers
from tennis_api.routers import ai, analyses, system, videos
from tennis_api.routers.system import API_VERSION


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    database = Database(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        cfg.storage_dir.mkdir(parents=True, exist_ok=True)
        database.create_all()
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
    install_error_handlers(app)
    app.include_router(system.router)
    app.include_router(videos.router)
    app.include_router(analyses.router)
    app.include_router(ai.router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    cfg = get_settings()
    uvicorn.run("tennis_api.main:app", host=cfg.api_host, port=cfg.api_port, reload=False)

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from candly import __version__
from candly.api.routes import analytics, platform
from candly.core.log import setup_logging
from candly.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    if not get_settings().scheduler_enabled:
        yield
        return
    from candly.jobs.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title="candly", version=__version__, lifespan=lifespan)
    app.include_router(platform.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    return app


app = create_app()

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from candly import __version__
from candly.api.routes import alerts, analytics, platform
from candly.core.log import setup_logging
from candly.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    if not get_settings().scheduler_enabled:
        yield
        return
    from candly.jobs.pipeline import register_pipeline_jobs
    from candly.jobs.scheduler import get_scheduler, start_scheduler, stop_scheduler

    register_pipeline_jobs(get_scheduler())
    start_scheduler()
    # Fill the scanner cache in the background so the first scanner request after a restart is fast.
    threading.Thread(target=analytics.warm_scanner, name="warm-scanner", daemon=True).start()
    try:
        yield
    finally:
        stop_scheduler()


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'][1:]) or 'request'}: {err['msg']}" for err in exc.errors()
    )
    return JSONResponse(status_code=400, content={"detail": problems})


def create_app() -> FastAPI:
    app = FastAPI(title="candly", version=__version__, lifespan=lifespan)
    # Blocks DNS-rebinding style requests from other sites; "testserver" is Starlette's TestClient host.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.include_router(platform.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")
    return app


app = create_app()

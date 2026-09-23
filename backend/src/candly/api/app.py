from fastapi import FastAPI

from candly import __version__
from candly.api.routes import analytics, platform


def create_app() -> FastAPI:
    app = FastAPI(title="candly", version=__version__)
    app.include_router(platform.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    return app


app = create_app()

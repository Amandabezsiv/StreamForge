from fastapi import FastAPI

from streamforge.api.routes.health import router as health_router
from streamforge.api.routes.metrics import router as metrics_router
from streamforge.api.routes.videos import router as videos_router
from streamforge.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(videos_router, prefix="/api/v1")

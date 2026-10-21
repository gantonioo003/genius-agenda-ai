"""API inicial. As integrações serão adicionadas nas próximas etapas."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import database
from app.settings import settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.initialize()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Assistente de agenda para entrevistas de emprego.",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"], summary="Verificar se a API está respondendo")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }

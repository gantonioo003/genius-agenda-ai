"""Permite iniciar a API com python -m app."""

import uvicorn

from app.settings import settings


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level,
        access_log=False,
    )

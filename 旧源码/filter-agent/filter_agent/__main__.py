"""Run the local HTTP service with ``python -m filter_agent``."""

import uvicorn

from .config import AppSettings

if __name__ == "__main__":
    settings = AppSettings.from_env()
    uvicorn.run(
        "filter_agent.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )

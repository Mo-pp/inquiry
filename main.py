from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging
from starlette.concurrency import run_in_threadpool
import mysql.connector

from app.settings import load_settings
from app.leads.gateway_client import GoogleSheetsGatewayClient
from app.leads.lead_repository import LeadRepository
from app.leads.sync_service import LeadSyncService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """只在启动时加载配置；等待同步结束后再释放 HTTP 连接。"""
    settings = load_settings()
    gateway = GoogleSheetsGatewayClient(settings.google_sheets)
    repository = LeadRepository(lambda: mysql.connector.connect(**settings.mysql.connect_kwargs()))
    service = LeadSyncService(settings.google_sheets, gateway, repository)
    app.state.lead_sync = service
    try:
        await run_in_threadpool(service.start)
        yield
    finally:
        try:
            await run_in_threadpool(service.stop)
        finally:
            await run_in_threadpool(gateway.close)

app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # 单进程运行；多个 worker 会各自启动同步任务。
    uvicorn.run(app, host="127.0.0.1", port=8000)

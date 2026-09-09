"""FastAPI application assembly and boundary error mapping."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..classification.classifier import RelevanceClassifier
from ..config import AppSettings
from ..persistence.mysql_repository import MySQLCustomerChatRepository, PersistenceError
from ..persistence.inbox_repository import InboxPersistenceError, MySQLInboxRepository
from ..inbound import InboundMessageError
from ..services.process_unread_batch import (
    Classifier,
    CustomerChatRepository,
    ModelUnavailableError,
    ProcessUnreadBatch,
)
from ..services.customer_exists import CustomerExists
from ..services.inbound_ingestion import InboundIngestor
from .routes import build_router


def create_app(
    *,
    classifier: Classifier | None = None,
    repository: CustomerChatRepository | None = None,
    inbound_ingestor: InboundIngestor | None = None,
    settings: AppSettings | None = None,
) -> FastAPI:
    app_settings = settings or AppSettings.from_env()
    app_classifier = classifier or RelevanceClassifier(settings=app_settings)
    app_repository = repository or MySQLCustomerChatRepository(app_settings)
    service = ProcessUnreadBatch(app_classifier, app_repository)
    customer_lookup = CustomerExists(app_repository)
    app_inbound_ingestor = inbound_ingestor or InboundIngestor(
        MySQLInboxRepository(app_settings)
    )

    application = FastAPI(title="Lintratek unread message filter", version="1.0.0")
    application.include_router(
        build_router(
            service,
            customer_lookup,
            app_inbound_ingestor,
            inbound_enabled=app_settings.whatsapp_inbound_enabled,
        )
    )

    @application.exception_handler(ModelUnavailableError)
    async def model_unavailable(_request: Request, _exc: ModelUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": "relevance model unavailable"})

    @application.exception_handler(PersistenceError)
    async def persistence_unavailable(_request: Request, _exc: PersistenceError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "MySQL unavailable"})

    @application.exception_handler(InboxPersistenceError)
    async def inbox_persistence_unavailable(
        _request: Request, _exc: InboxPersistenceError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "MySQL inbox unavailable"})

    @application.exception_handler(InboundMessageError)
    async def invalid_inbound_event(
        _request: Request, exc: InboundMessageError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return application


app = create_app()

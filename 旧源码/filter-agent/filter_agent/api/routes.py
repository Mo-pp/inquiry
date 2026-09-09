"""HTTP endpoints for unread batches."""

from typing import Annotated
from fastapi import APIRouter, Path

from ..classification.models import UnreadBatch
from ..services.process_unread_batch import ProcessUnreadBatch
from ..services.inbound_ingestion import InboundIngestor
from ..services.customer_exists import CustomerExists
from .schemas import (
    BatchResponse,
    CustomerExistsResponse,
    HealthResponse,
    InboundEventRequest,
    InboundEventResponse,
)


def build_router(
    service: ProcessUnreadBatch,
    customer_lookup: CustomerExists,
    inbound_ingestor: InboundIngestor | None = None,
    inbound_enabled: bool = False,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @router.post("/api/v1/unread-batches", response_model=BatchResponse)
    def process_batch(batch: UnreadBatch) -> BatchResponse:
        result = service.process(batch)
        return BatchResponse(
            status=result.status,
            is_relevant=result.is_relevant,
            appended_count=result.appended_count,
        )

    @router.post(
        "/api/v1/whatsapp/inbound",
        response_model=InboundEventResponse,
    )
    def ingest_inbound(event: InboundEventRequest) -> InboundEventResponse:
        if inbound_ingestor is None or not inbound_enabled:
            # This should only be reachable when a caller builds a custom
            # router outside ``create_app``.  Keep the endpoint explicit rather
            # than silently dropping a bridge event.
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="inbound queue unavailable")
        result = inbound_ingestor.accept(event.model_dump())
        return InboundEventResponse(
            status=result.status,
            message_key=result.message_key,
            job_id=result.job_id,
            detail=result.detail,
        )

    @router.get("/api/v1/customers/{customer_phone}/exists", response_model=CustomerExistsResponse)
    def customer_exists(customer_phone: Annotated[str, Path(pattern=r"^\+[1-9]\d{5,14}$")]) -> CustomerExistsResponse:
        return CustomerExistsResponse(exists=customer_lookup.check(customer_phone))

    return router

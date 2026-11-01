from decimal import Decimal
import hmac
import logging
import os
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from payment_service.application.pay_order import PayOrderUseCase
from payment_service.domain.payment import Payment, PaymentStatus
from payment_service.ports.inbound import PayOrderPort
from payment_service.ports.outbound import PaymentRepository
from payment_service.adapters.inbound.trace_middleware import TraceIdMiddleware
from payment_service.adapters.outbound.memory_repository import InMemoryPaymentRepository
from payment_service.adapters.outbound.sqlite_repository import SqlitePaymentRepository
from payment_service.adapters.outbound.mock_gateway import MockPaymentGateway
from payment_service.adapters.outbound.memory_event_publisher import InMemoryEventPublisher
from payment_service.application.errors import PaymentPersistenceError
from payment_service.observability.json_logging import configure_logging
from payment_service.observability.trace import bind_trace_id, current_trace_id, new_trace_id, trace_scope

logger = logging.getLogger(__name__)


class CreatePaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orderId: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("orderId")
    @classmethod
    def order_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("orderId must not be blank")
        return value


class PaymentResponse(BaseModel):
    paymentId: str
    orderId: str
    amount: Decimal
    currency: str
    status: PaymentStatus

    @classmethod
    def from_domain(cls, payment: Payment) -> "PaymentResponse":
        return cls(
            paymentId=payment.payment_id,
            orderId=payment.order_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
        )


class OrderCreatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orderId: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class OrderCreatedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: str
    occurredAt: str
    traceId: str | None = None
    payload: OrderCreatedPayload


class GatewayScenarioRequest(BaseModel):
    scenario: str = Field(pattern=r"^(normal|declined|timeout|exception)$")


def create_repository_from_env() -> PaymentRepository:
    """Factory: picks the PaymentRepository adapter without touching the use case."""
    unavailable = os.getenv("PAYMENT_REPOSITORY_FAIL", "false").lower() == "true"
    kind = os.getenv("PAYMENT_REPOSITORY", "memory").lower()
    if kind == "memory":
        return InMemoryPaymentRepository(unavailable=unavailable)
    if kind == "sqlite":
        return SqlitePaymentRepository(os.getenv("PAYMENT_DB_PATH", "payments.db"), unavailable=unavailable)
    raise ValueError(f"Unsupported PAYMENT_REPOSITORY: {kind} (expected memory or sqlite)")


def create_app(
    pay_order: PayOrderPort | None = None,
    event_publisher: InMemoryEventPublisher | None = None,
    internal_token: str | None = None,
) -> FastAPI:
    log_store = configure_logging()
    simulated_gateway: MockPaymentGateway | None = None
    if pay_order is None:
        repository = create_repository_from_env()
        simulated_gateway = MockPaymentGateway(os.getenv("PAYMENT_GATEWAY_SCENARIO", "normal"))
        event_publisher = event_publisher or InMemoryEventPublisher()
        pay_order = PayOrderUseCase(repository, simulated_gateway, event_publisher)
    internal_token = internal_token if internal_token is not None else os.getenv("PAYMENT_INTERNAL_TOKEN")

    app = FastAPI(title="Payment Service", version="0.1.0")
    app.add_middleware(TraceIdMiddleware)

    @app.post("/payments", response_model=PaymentResponse, responses={402: {"description": "Payment declined"}})
    def create_payment(request: CreatePaymentRequest) -> PaymentResponse:
        try:
            payment = pay_order.pay_order(request.orderId, request.amount, request.currency, current_trace_id())
        except PaymentPersistenceError as exc:
            return JSONResponse(
                status_code=503,
                content={"detail": "Payment result could not be saved", "paymentId": exc.payment.payment_id, "persisted": False},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = PaymentResponse.from_domain(payment)
        if payment.status == PaymentStatus.FAILED:
            raise HTTPException(status_code=402, detail={"payment": response.model_dump(mode="json"), "message": "Payment declined"})
        if payment.status == PaymentStatus.PENDING:
            return JSONResponse(
                status_code=202,
                content={"payment": response.model_dump(mode="json"), "message": "Confirmation pending", "persisted": True},
            )
        return response

    @app.get("/payments", response_model=list[PaymentResponse])
    def list_payments() -> list[PaymentResponse]:
        try:
            return [PaymentResponse.from_domain(payment) for payment in pay_order.list_payments()]
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Payment storage is temporarily unavailable") from exc

    @app.get("/payments/{payment_id}", response_model=PaymentResponse)
    def get_payment(payment_id: str) -> PaymentResponse:
        try:
            payment = pay_order.get_payment(payment_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Payment storage is temporarily unavailable") from exc
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        return PaymentResponse.from_domain(payment)

    @app.post("/internal/payments/{payment_id}/retry")
    def retry_payment(payment_id: str, x_internal_token: str | None = Header(default=None)):
        if not internal_token or not x_internal_token or not hmac.compare_digest(internal_token, x_internal_token):
            raise HTTPException(status_code=404, detail="Not found")
        try:
            payment = pay_order.retry_pending(payment_id, current_trace_id())
        except PaymentPersistenceError as exc:
            return JSONResponse(
                status_code=503,
                content={"detail": "Payment result could not be saved", "paymentId": exc.payment.payment_id, "persisted": False},
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Payment service is temporarily unavailable") from exc
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        response = PaymentResponse.from_domain(payment)
        if payment.status == PaymentStatus.PENDING:
            return JSONResponse(status_code=202, content={"payment": response.model_dump(mode="json"), "message": "Confirmation pending", "persisted": True})
        if payment.status == PaymentStatus.FAILED:
            return JSONResponse(status_code=402, content={"payment": response.model_dump(mode="json"), "message": "Payment declined"})
        return response

    if simulated_gateway is not None and os.getenv("PAYMENT_ENABLE_SIMULATION", "false").lower() == "true":
        @app.post("/internal/gateway-scenario")
        def set_gateway_scenario(request: GatewayScenarioRequest, x_internal_token: str | None = Header(default=None)):
            if not internal_token or not x_internal_token or not hmac.compare_digest(internal_token, x_internal_token):
                raise HTTPException(status_code=404, detail="Not found")
            simulated_gateway.scenario = request.scenario
            return {"scenario": simulated_gateway.scenario}

    def process_order_created(event: OrderCreatedEvent, trace_id: str) -> None:
        # Background consumer: a failure is logged under the event's traceId, never raised to the server.
        with trace_scope(trace_id):
            try:
                pay_order.pay_order(event.payload.orderId, event.payload.amount, event.payload.currency, trace_id)
            except PaymentPersistenceError as exc:
                logger.error(
                    "OrderCreated processing failed: payment %s not persisted",
                    exc.payment.payment_id,
                    extra={"paymentId": exc.payment.payment_id},
                )
            except Exception:
                logger.exception("OrderCreated processing failed")

    @app.post("/events/order-created", status_code=202)
    async def receive_order_created(event: OrderCreatedEvent, background_tasks: BackgroundTasks) -> dict[str, str]:
        if event.type != "OrderCreated":
            raise HTTPException(status_code=422, detail="Expected event type OrderCreated")
        # The event's traceId continues the upstream flow; without one, the HTTP traceId is used.
        trace_id = event.traceId or current_trace_id() or new_trace_id()
        bind_trace_id(trace_id)
        logger.info("OrderCreated event received")
        background_tasks.add_task(process_order_created, event, trace_id)
        return {"message": "OrderCreated event accepted", "eventId": event.id}

    @app.get("/logs")
    def get_logs(traceId: str | None = None) -> list[dict[str, Any]]:
        return log_store.find(trace_id=traceId)

    return app


app = create_app()

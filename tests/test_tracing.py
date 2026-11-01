from decimal import Decimal
import json
import logging
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

from payment_service.adapters.inbound.api import create_app
from payment_service.adapters.outbound.memory_event_publisher import InMemoryEventPublisher
from payment_service.adapters.outbound.memory_repository import InMemoryPaymentRepository
from payment_service.adapters.outbound.mock_gateway import MockPaymentGateway
from payment_service.application.pay_order import PayOrderUseCase
from payment_service.observability.json_logging import JsonFormatter
from payment_service.observability.trace import trace_scope

REQUIRED_FIELDS = {"service", "traceId", "msg", "level", "timestamp"}


def unique_trace() -> str:
    return f"test-{uuid4().hex[:12]}"


def build(gateway=None, repository=None):
    publisher = InMemoryEventPublisher()
    gateway = gateway or MockPaymentGateway("normal")
    service = PayOrderUseCase(repository or InMemoryPaymentRepository(), gateway, publisher)
    client = TestClient(create_app(service, event_publisher=publisher, internal_token="local-secret"))
    return client, publisher, gateway


def order_created(trace_id: str | None, order_id: str = "ORD-TRACE") -> dict:
    event = {
        "id": f"evt-{uuid4().hex[:8]}",
        "type": "OrderCreated",
        "occurredAt": "2026-10-21T09:00:00Z",
        "payload": {"orderId": order_id, "amount": 15.00, "currency": "EUR"},
    }
    if trace_id is not None:
        event["traceId"] = trace_id
    return event


def logs_for(client: TestClient, trace_id: str) -> list[dict]:
    response = client.get("/logs", params={"traceId": trace_id})
    assert response.status_code == 200
    return response.json()


def test_rest_entry_reuses_incoming_trace_id_in_logs_and_event():
    client, publisher, _ = build()
    trace_id = unique_trace()

    response = client.post(
        "/payments", json={"orderId": "ORD-1", "amount": 12.00, "currency": "EUR"}, headers={"X-Trace-Id": trace_id}
    )

    assert response.status_code == 200
    assert publisher.events[-1].trace_id == trace_id
    logs = logs_for(client, trace_id)
    messages = [entry["msg"] for entry in logs]
    assert messages == [
        "Payment requested",
        "Payment gateway responded",
        "Payment saved",
        "Event published: PaymentConfirmed",
        "Payment processed",
        "HTTP POST /payments completed with 200",
    ]
    assert all(REQUIRED_FIELDS <= entry.keys() and entry["traceId"] == trace_id for entry in logs)
    assert all(entry["service"] == "payment-service" for entry in logs)
    assert logs[-1]["duration_ms"] >= 0


def test_rest_entry_generates_trace_id_when_missing():
    client, publisher, _ = build()

    client.post("/payments", json={"orderId": "ORD-2", "amount": 12.00, "currency": "EUR"})

    generated = publisher.events[-1].trace_id
    assert len(generated) == 32
    assert "HTTP POST /payments completed with 200" in [entry["msg"] for entry in logs_for(client, generated)]


def test_order_created_to_payment_confirmed_chain_is_correlated():
    client, publisher, _ = build()
    trace_id = unique_trace()

    response = client.post("/events/order-created", json=order_created(trace_id))

    assert response.status_code == 202
    messages = [entry["msg"] for entry in logs_for(client, trace_id)]
    assert messages[0] == "OrderCreated event received"
    assert messages.index("OrderCreated event received") < messages.index("Event published: PaymentConfirmed")
    assert "HTTP POST /events/order-created completed with 202" in messages
    event = publisher.events[-1].to_dict()
    assert event["type"] == "PaymentConfirmed"
    assert event["traceId"] == trace_id


def test_order_created_without_trace_id_gets_one_generated():
    client, publisher, _ = build()

    client.post("/events/order-created", json=order_created(None))

    trace_id = publisher.events[-1].trace_id
    assert trace_id
    assert "OrderCreated event received" in [entry["msg"] for entry in logs_for(client, trace_id)]


def test_gateway_outage_then_retry_is_visible_under_one_trace_id():
    client, publisher, gateway = build(gateway=MockPaymentGateway("timeout"))
    trace_id = unique_trace()

    client.post("/events/order-created", json=order_created(trace_id, "ORD-OUTAGE"))
    failure = next(entry for entry in logs_for(client, trace_id) if "timed out" in entry["msg"])
    assert failure["level"] == "ERROR"
    assert failure["error"].startswith("TimeoutError")
    assert publisher.events == []

    gateway.scenario = "normal"
    retried = client.post(
        f"/internal/payments/{failure['paymentId']}/retry",
        headers={"X-Internal-Token": "local-secret", "X-Trace-Id": trace_id},
    )

    assert retried.status_code == 200
    messages = [entry["msg"] for entry in logs_for(client, trace_id)]
    assert messages.index("OrderCreated event received") < messages.index("Payment retry requested")
    assert messages[-2:] == ["Payment retry processed", f"HTTP POST /internal/payments/{failure['paymentId']}/retry completed with 200"]
    assert publisher.events[-1].type == "PaymentConfirmed"
    assert publisher.events[-1].trace_id == trace_id


def test_background_failure_is_logged_without_crashing():
    client, _, _ = build(repository=InMemoryPaymentRepository(unavailable=True))
    trace_id = unique_trace()

    response = client.post("/events/order-created", json=order_created(trace_id))

    assert response.status_code == 202
    messages = [entry["msg"] for entry in logs_for(client, trace_id)]
    assert any(message.startswith("OrderCreated processing failed") for message in messages)


def test_use_case_called_directly_generates_a_trace_id():
    publisher = InMemoryEventPublisher()
    service = PayOrderUseCase(InMemoryPaymentRepository(), MockPaymentGateway("normal"), publisher)

    service.pay_order("ORD-CLI", Decimal("10.00"), "EUR")

    assert publisher.events[-1].trace_id


def test_json_formatter_writes_one_object_per_line_with_required_fields():
    record = logging.LogRecord("payment_service.test", logging.ERROR, __file__, 1, "Gateway %s", ("down",), None)
    record.duration_ms = 12.5
    try:
        raise TimeoutError("simulated")
    except TimeoutError:
        record.exc_info = sys.exc_info()

    with trace_scope("trace-format"):
        line = JsonFormatter().format(record)

    assert "\n" not in line
    entry = json.loads(line)
    assert REQUIRED_FIELDS <= entry.keys()
    assert entry["traceId"] == "trace-format"
    assert entry["msg"] == "Gateway down"
    assert entry["duration_ms"] == 12.5
    assert entry["error"] == "TimeoutError: simulated"

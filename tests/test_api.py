from fastapi.testclient import TestClient

from payment_service.adapters.inbound.api import create_app
from payment_service.application.pay_order import PayOrderUseCase
from payment_service.adapters.outbound.memory_repository import InMemoryPaymentRepository
from payment_service.adapters.outbound.memory_event_publisher import InMemoryEventPublisher
from payment_service.adapters.outbound.mock_gateway import MockPaymentGateway


class Gateway:
    def __init__(self, accepted):
        self.accepted = accepted

    def charge(self, amount, currency, idempotency_key):
        return self.accepted


def client_for(accepted):
    service = PayOrderUseCase(InMemoryPaymentRepository(), Gateway(accepted), InMemoryEventPublisher())
    return TestClient(create_app(service))


def test_success_returns_payment():
    response = client_for(True).post("/payments", json={"orderId": "ORD-123", "amount": 49.99, "currency": "EUR"})
    assert response.status_code == 200
    assert response.json()["status"] == "PAID"


def test_decline_returns_saved_payment_details():
    response = client_for(False).post("/payments", json={"orderId": "ORD-123", "amount": 49.99, "currency": "EUR"})
    assert response.status_code == 402
    assert response.json()["detail"]["payment"]["status"] == "FAILED"


def test_invalid_request_is_rejected():
    response = client_for(True).post("/payments", json={"orderId": " ", "amount": 0, "currency": "eu"})
    assert response.status_code == 422


def test_payment_can_be_retrieved_after_creation():
    repository = InMemoryPaymentRepository()
    service = PayOrderUseCase(repository, Gateway(True), InMemoryEventPublisher())
    client = TestClient(create_app(service))
    created = client.post("/payments", json={"orderId": "ORD-123", "amount": 12.50, "currency": "EUR"})

    response = client.get(f"/payments/{created.json()['paymentId']}")
    assert response.status_code == 200
    assert response.json()["orderId"] == "ORD-123"

    missing = client.get("/payments/unknown")
    assert missing.status_code == 404


def test_order_created_event_is_accepted_and_processed_in_background():
    repository = InMemoryPaymentRepository()
    service = PayOrderUseCase(repository, Gateway(True), InMemoryEventPublisher())
    client = TestClient(create_app(service))
    response = client.post("/events/order-created", json={
        "id": "evt-order-1",
        "type": "OrderCreated",
        "occurredAt": "2026-10-13T09:00:00Z",
        "traceId": "trace-123",
        "payload": {"orderId": "ORD-123", "amount": 19.90, "currency": "EUR"},
    })

    assert response.status_code == 202
    assert response.json()["eventId"] == "evt-order-1"
    assert repository._payments


def test_order_created_listener_rejects_wrong_event_type():
    response = client_for(True).post("/events/order-created", json={
        "id": "evt-1",
        "type": "SomethingElse",
        "occurredAt": "2026-10-13T09:00:00Z",
        "payload": {"orderId": "ORD-123", "amount": 19.90, "currency": "EUR"},
    })
    assert response.status_code == 422


def test_pending_payment_is_returned_and_visible_in_list():
    repository = InMemoryPaymentRepository()
    service = PayOrderUseCase(repository, GatewayRaisesTimeout(), InMemoryEventPublisher())
    client = TestClient(create_app(service))

    created = client.post("/payments", json={"orderId": "ORD-P", "amount": 22.00, "currency": "EUR"})
    assert created.status_code == 202
    payment_id = created.json()["payment"]["paymentId"]
    assert created.json()["persisted"] is True
    listed = client.get("/payments")
    assert listed.status_code == 200
    assert any(item["paymentId"] == payment_id and item["status"] == "PENDING" for item in listed.json())


def test_repository_failure_returns_503_without_claiming_saved():
    service = PayOrderUseCase(InMemoryPaymentRepository(unavailable=True), Gateway(True), InMemoryEventPublisher())
    response = TestClient(create_app(service)).post(
        "/payments", json={"orderId": "ORD-R", "amount": 22.00, "currency": "EUR"}
    )
    assert response.status_code == 503
    assert response.json()["persisted"] is False


def test_internal_retry_requires_token_and_can_resolve_pending_payment():
    repository = InMemoryPaymentRepository()
    gateway = MockPaymentGateway("timeout")
    service = PayOrderUseCase(repository, gateway, InMemoryEventPublisher())
    client = TestClient(create_app(service, internal_token="local-secret"))
    created = client.post("/payments", json={"orderId": "ORD-RETRY", "amount": 22.00, "currency": "EUR"})
    payment_id = created.json()["payment"]["paymentId"]
    gateway.scenario = "normal"

    assert client.post(f"/internal/payments/{payment_id}/retry").status_code == 404
    retried = client.post(
        f"/internal/payments/{payment_id}/retry", headers={"X-Internal-Token": "local-secret"}
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "PAID"


class GatewayRaisesTimeout:
    def charge(self, amount, currency, idempotency_key):
        raise TimeoutError("simulated")

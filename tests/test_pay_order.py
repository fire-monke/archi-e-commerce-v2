from decimal import Decimal

import pytest

from payment_service.application.pay_order import PayOrderUseCase
from payment_service.domain.payment import PaymentStatus
from payment_service.adapters.outbound.memory_repository import InMemoryPaymentRepository
from payment_service.adapters.outbound.memory_event_publisher import InMemoryEventPublisher
from payment_service.adapters.outbound.mock_gateway import MockPaymentGateway
from payment_service.application.errors import PaymentPersistenceError


class Gateway:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted

    def charge(self, amount, currency, idempotency_key) -> bool:
        return self.accepted


@pytest.mark.parametrize("accepted,expected", [(True, PaymentStatus.PAID), (False, PaymentStatus.FAILED)])
def test_result_is_saved(accepted, expected):
    repository = InMemoryPaymentRepository()
    publisher = InMemoryEventPublisher()
    service = PayOrderUseCase(repository, Gateway(accepted), publisher)

    payment = service.pay_order("ORD-123", Decimal("49.99"), "EUR", "trace-1")

    assert payment.status == expected
    assert repository.get_by_id(payment.payment_id) == payment
    event = publisher.events[0].to_dict()
    assert event["type"] == ("PaymentConfirmed" if accepted else "PaymentDeclined")
    assert event["traceId"] == "trace-1"
    assert event["payload"]["paymentId"] == payment.payment_id


def test_invalid_amount_is_rejected_before_gateway_call():
    repository = InMemoryPaymentRepository()
    gateway = Gateway(True)
    service = PayOrderUseCase(repository, gateway, InMemoryEventPublisher())

    with pytest.raises(ValueError):
        service.pay_order("ORD-123", Decimal("0"), "EUR")
    assert repository._payments == {}


def test_gateway_timeout_keeps_payment_pending_and_logs(caplog):
    repository = InMemoryPaymentRepository()
    service = PayOrderUseCase(repository, MockPaymentGateway("timeout"), InMemoryEventPublisher())

    payment = service.pay_order("ORD-TIMEOUT", Decimal("22.00"), "EUR")

    assert payment.status == PaymentStatus.PENDING
    assert repository.get_by_id(payment.payment_id) == payment
    assert "timed out" in caplog.text


def test_gateway_exception_keeps_pending_and_does_not_crash():
    service = PayOrderUseCase(InMemoryPaymentRepository(), MockPaymentGateway("exception"), InMemoryEventPublisher())
    assert service.pay_order("ORD-ERR", Decimal("22.00"), "EUR").status == PaymentStatus.PENDING


def test_pending_payment_can_be_retried_with_same_idempotency_key():
    repository = InMemoryPaymentRepository()
    gateway = MockPaymentGateway("timeout")
    service = PayOrderUseCase(repository, gateway, InMemoryEventPublisher())
    pending = service.pay_order("ORD-RETRY", Decimal("22.00"), "EUR")
    gateway.scenario = "normal"

    paid = service.retry_pending(pending.payment_id)

    assert paid.payment_id == pending.payment_id
    assert paid.status == PaymentStatus.PAID
    assert service.get_payment(pending.payment_id) == paid


def test_repository_failure_is_reported_and_later_requests_can_succeed():
    repository = InMemoryPaymentRepository(unavailable=True)
    service = PayOrderUseCase(repository, Gateway(True), InMemoryEventPublisher())
    with pytest.raises(PaymentPersistenceError):
        service.pay_order("ORD-FAIL-SAVE", Decimal("22.00"), "EUR")

    repository.unavailable = False
    payment = service.pay_order("ORD-OK", Decimal("22.00"), "EUR")
    assert payment.status == PaymentStatus.PAID
    assert repository.get_by_id(payment.payment_id) == payment


def test_payment_can_be_retrieved_by_id():
    repository = InMemoryPaymentRepository()
    service = PayOrderUseCase(repository, Gateway(True), InMemoryEventPublisher())
    created = service.pay_order("ORD-123", Decimal("49.99"), "EUR")

    assert service.get_payment(created.payment_id) == created
    assert service.get_payment("unknown") is None

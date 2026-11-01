from decimal import Decimal

import pytest

from payment_service.adapters.outbound.memory_event_publisher import InMemoryEventPublisher
from payment_service.adapters.outbound.memory_repository import InMemoryPaymentRepository
from payment_service.adapters.outbound.mock_gateway import MockPaymentGateway
from payment_service.adapters.outbound.sqlite_repository import SqlitePaymentRepository
from payment_service.application.errors import PaymentPersistenceError
from payment_service.application.pay_order import PayOrderUseCase
from payment_service.domain.payment import Payment, PaymentStatus


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        return InMemoryPaymentRepository()
    return SqlitePaymentRepository(str(tmp_path / "payments.db"))


def test_use_case_behaves_the_same_with_any_repository_adapter(repository):
    service = PayOrderUseCase(repository, MockPaymentGateway("normal"), InMemoryEventPublisher())

    payment = service.pay_order("ORD-123", Decimal("12.50"), "EUR")

    assert repository.get_by_id(payment.payment_id) == payment
    assert repository.list_all() == [payment]
    assert repository.get_by_id("unknown") is None


def test_pending_payment_is_updated_after_retry_with_any_adapter(repository):
    gateway = MockPaymentGateway("timeout")
    service = PayOrderUseCase(repository, gateway, InMemoryEventPublisher())
    pending = service.pay_order("ORD-RETRY", Decimal("22.00"), "EUR")
    gateway.scenario = "normal"

    service.retry_pending(pending.payment_id)

    assert repository.get_by_id(pending.payment_id).status == PaymentStatus.PAID
    assert len(repository.list_all()) == 1


def test_sqlite_data_survives_a_new_adapter_instance(tmp_path):
    db_path = str(tmp_path / "payments.db")
    payment = Payment("PAY-1", "ORD-1", Decimal("19.90"), "EUR", PaymentStatus.PAID)
    SqlitePaymentRepository(db_path).save(payment)

    reloaded = SqlitePaymentRepository(db_path).get_by_id("PAY-1")

    assert reloaded == payment
    assert reloaded.amount == Decimal("19.90")


def test_sqlite_outage_is_reported_as_persistence_error(tmp_path):
    repository = SqlitePaymentRepository(str(tmp_path / "payments.db"), unavailable=True)
    service = PayOrderUseCase(repository, MockPaymentGateway("normal"), InMemoryEventPublisher())

    with pytest.raises(PaymentPersistenceError):
        service.pay_order("ORD-DOWN", Decimal("22.00"), "EUR")

    repository.unavailable = False
    assert repository.list_all() == []

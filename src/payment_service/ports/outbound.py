from decimal import Decimal
from typing import Protocol

from payment_service.domain.payment import Payment
from payment_service.domain.events import BusinessEvent


class PaymentRepository(Protocol):
    def save(self, payment: Payment) -> None: ...

    def get_by_id(self, payment_id: str) -> Payment | None: ...

    def list_all(self) -> list[Payment]: ...


class PaymentGateway(Protocol):
    def charge(self, amount: Decimal, currency: str, idempotency_key: str) -> bool: ...


class EventPublisher(Protocol):
    def publish(self, event: BusinessEvent) -> None: ...

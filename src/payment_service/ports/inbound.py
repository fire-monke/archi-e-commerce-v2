from decimal import Decimal
from typing import Protocol

from payment_service.domain.payment import Payment


class PayOrderPort(Protocol):
    """The single inbound port exposed by the application."""

    def pay_order(self, order_id: str, amount: Decimal, currency: str, trace_id: str | None = None) -> Payment: ...

    def get_payment(self, payment_id: str) -> Payment | None: ...

    def list_payments(self) -> list[Payment]: ...

    def retry_pending(self, payment_id: str, trace_id: str | None = None) -> Payment | None: ...

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class PaymentStatus(StrEnum):
    PAID = "PAID"
    FAILED = "FAILED"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class Payment:
    payment_id: str
    order_id: str
    amount: Decimal
    currency: str
    status: PaymentStatus

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("orderId must not be blank")
        if not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("amount must be a finite number greater than zero")
        if self.amount.as_tuple().exponent < -2:
            raise ValueError("amount must have at most two decimal places")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("currency must be a three-letter uppercase code")

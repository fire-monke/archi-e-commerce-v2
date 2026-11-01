from payment_service.domain.payment import Payment


class InMemoryPaymentRepository:
    def __init__(self, unavailable: bool = False) -> None:
        self._payments: dict[str, Payment] = {}
        self.unavailable = unavailable

    def _ensure_available(self) -> None:
        if self.unavailable:
            raise RuntimeError("Payment repository is unavailable")

    def save(self, payment: Payment) -> None:
        self._ensure_available()
        self._payments[payment.payment_id] = payment

    def get_by_id(self, payment_id: str) -> Payment | None:
        self._ensure_available()
        return self._payments.get(payment_id)

    def list_all(self) -> list[Payment]:
        self._ensure_available()
        return list(self._payments.values())

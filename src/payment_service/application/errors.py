from payment_service.domain.payment import Payment


class PaymentPersistenceError(RuntimeError):
    def __init__(self, payment: Payment) -> None:
        super().__init__("Payment could not be persisted")
        self.payment = payment

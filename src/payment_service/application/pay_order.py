from decimal import Decimal
import logging
from time import perf_counter
from uuid import uuid4

from payment_service.domain.payment import Payment, PaymentStatus
from payment_service.application.errors import PaymentPersistenceError
from payment_service.ports.inbound import PayOrderPort
from payment_service.domain.events import payment_event
from payment_service.ports.outbound import EventPublisher, PaymentGateway, PaymentRepository
from payment_service.observability.trace import trace_scope

logger = logging.getLogger(__name__)


class PayOrderUseCase(PayOrderPort):
    def __init__(self, repository: PaymentRepository, gateway: PaymentGateway, event_publisher: EventPublisher) -> None:
        self._repository = repository
        self._gateway = gateway
        self._event_publisher = event_publisher

    def pay_order(self, order_id: str, amount: Decimal, currency: str, trace_id: str | None = None) -> Payment:
        # Every log and event emitted below carries the same traceId (received or generated here).
        with trace_scope(trace_id) as trace_id:
            started = perf_counter()
            payment_id = f"PAY-{uuid4().hex[:12].upper()}"
            logger.info("Payment requested", extra={"paymentId": payment_id})
            payment = Payment(
                payment_id=payment_id,
                order_id=order_id,
                amount=amount,
                currency=currency,
                status=PaymentStatus.PENDING,
            )
            payment = self._charge(payment)
            self._save(payment)
            if payment.status != PaymentStatus.PENDING:
                self._publish(payment, trace_id)
            logger.info("Payment processed", extra={"paymentId": payment.payment_id, "duration_ms": _elapsed_ms(started)})
            return payment

    def get_payment(self, payment_id: str) -> Payment | None:
        try:
            return self._repository.get_by_id(payment_id)
        except Exception as exc:
            logger.exception("Could not retrieve payment %s", payment_id, extra={"paymentId": payment_id})
            raise RuntimeError("Payment storage is unavailable") from exc

    def list_payments(self) -> list[Payment]:
        try:
            return self._repository.list_all()
        except Exception as exc:
            logger.exception("Could not list payments")
            raise RuntimeError("Payment storage is unavailable") from exc

    def retry_pending(self, payment_id: str, trace_id: str | None = None) -> Payment | None:
        with trace_scope(trace_id) as trace_id:
            started = perf_counter()
            logger.info("Payment retry requested", extra={"paymentId": payment_id})
            payment = self.get_payment(payment_id)
            if payment is None or payment.status != PaymentStatus.PENDING:
                return payment
            retried = self._charge(payment)
            self._save(retried)
            if retried.status != PaymentStatus.PENDING:
                self._publish(retried, trace_id)
            logger.info("Payment retry processed", extra={"paymentId": retried.payment_id, "duration_ms": _elapsed_ms(started)})
            return retried

    def _charge(self, payment: Payment) -> Payment:
        started = perf_counter()
        try:
            accepted = self._gateway.charge(payment.amount, payment.currency, payment.payment_id)
            status = PaymentStatus.PAID if accepted else PaymentStatus.FAILED
            logger.info("Payment gateway responded", extra={"paymentId": payment.payment_id, "duration_ms": _elapsed_ms(started)})
        except TimeoutError:
            logger.exception(
                "Payment gateway timed out; payment %s remains PENDING",
                payment.payment_id,
                extra={"paymentId": payment.payment_id, "duration_ms": _elapsed_ms(started)},
            )
            status = PaymentStatus.PENDING
        except Exception:
            logger.exception(
                "Payment gateway failed; payment %s remains PENDING",
                payment.payment_id,
                extra={"paymentId": payment.payment_id, "duration_ms": _elapsed_ms(started)},
            )
            status = PaymentStatus.PENDING
        return Payment(
            payment_id=payment.payment_id,
            order_id=payment.order_id,
            amount=payment.amount,
            currency=payment.currency,
            status=status,
        )

    def _save(self, payment: Payment) -> None:
        started = perf_counter()
        try:
            self._repository.save(payment)
        except Exception as exc:
            logger.exception(
                "Could not persist payment %s",
                payment.payment_id,
                extra={"paymentId": payment.payment_id, "duration_ms": _elapsed_ms(started)},
            )
            raise PaymentPersistenceError(payment) from exc
        logger.info("Payment saved", extra={"paymentId": payment.payment_id, "duration_ms": _elapsed_ms(started)})

    def _publish(self, payment: Payment, trace_id: str | None) -> None:
        try:
            self._event_publisher.publish(payment_event(payment, trace_id))
        except Exception:
            logger.exception("Could not publish result event for payment %s", payment.payment_id, extra={"paymentId": payment.payment_id})


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 2)

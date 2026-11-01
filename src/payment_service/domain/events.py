from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from payment_service.domain.payment import Payment


@dataclass(frozen=True, slots=True)
class BusinessEvent:
    id: str
    type: str
    occurred_at: str
    payload: dict[str, str]
    trace_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        event: dict[str, object] = {
            "id": self.id,
            "type": self.type,
            "occurredAt": self.occurred_at,
            "payload": self.payload,
        }
        if self.trace_id is not None:
            event["traceId"] = self.trace_id
        return event


def payment_event(payment: Payment, trace_id: str | None = None) -> BusinessEvent:
    event_type = "PaymentConfirmed" if payment.status.value == "PAID" else "PaymentDeclined"
    return BusinessEvent(
        id=f"EVT-{uuid4().hex[:12].upper()}",
        type=event_type,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        trace_id=trace_id,
        payload={
            "paymentId": payment.payment_id,
            "orderId": payment.order_id,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "status": payment.status.value,
        },
    )

import logging

from payment_service.domain.events import BusinessEvent

logger = logging.getLogger(__name__)


class InMemoryEventPublisher:
    """Local adapter that records published events for demonstration."""

    def __init__(self) -> None:
        self.events: list[BusinessEvent] = []

    def publish(self, event: BusinessEvent) -> None:
        self.events.append(event)
        logger.info(
            "Event published: %s",
            event.type,
            extra={"traceId": event.trace_id, "paymentId": event.payload.get("paymentId"), "event": event.to_dict()},
        )

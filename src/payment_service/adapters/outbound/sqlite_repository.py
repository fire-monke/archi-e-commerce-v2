from collections.abc import Iterator
from contextlib import closing, contextmanager
from decimal import Decimal
import sqlite3

from payment_service.domain.payment import Payment, PaymentStatus


class SqlitePaymentRepository:
    """Database adapter for PaymentRepository: same contract as InMemoryPaymentRepository, data survives restarts."""

    def __init__(self, db_path: str = "payments.db", unavailable: bool = False) -> None:
        self._db_path = db_path
        self.unavailable = unavailable
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    order_id   TEXT NOT NULL,
                    amount     TEXT NOT NULL,
                    currency   TEXT NOT NULL,
                    status     TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per call: safe across FastAPI worker threads.
        with closing(sqlite3.connect(self._db_path, timeout=2)) as connection:
            with connection:
                yield connection

    def _ensure_available(self) -> None:
        if self.unavailable:
            raise RuntimeError("Payment database is unavailable")

    def save(self, payment: Payment) -> None:
        self._ensure_available()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO payments (payment_id, order_id, amount, currency, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(payment_id) DO UPDATE SET status = excluded.status
                """,
                (payment.payment_id, payment.order_id, str(payment.amount), payment.currency, payment.status.value),
            )

    def get_by_id(self, payment_id: str) -> Payment | None:
        self._ensure_available()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payment_id, order_id, amount, currency, status FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
        return _to_payment(row) if row else None

    def list_all(self) -> list[Payment]:
        self._ensure_available()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payment_id, order_id, amount, currency, status FROM payments ORDER BY rowid"
            ).fetchall()
        return [_to_payment(row) for row in rows]


def _to_payment(row: tuple[str, str, str, str, str]) -> Payment:
    payment_id, order_id, amount, currency, status = row
    return Payment(
        payment_id=payment_id,
        order_id=order_id,
        amount=Decimal(amount),
        currency=currency,
        status=PaymentStatus(status),
    )

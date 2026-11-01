from decimal import Decimal


class MockPaymentGateway:
    """Local gateway simulator with normal, decline, timeout and error scenarios."""

    def __init__(self, scenario: str = "normal") -> None:
        if scenario not in {"normal", "declined", "timeout", "exception"}:
            raise ValueError(f"Unsupported gateway scenario: {scenario}")
        self.scenario = scenario
        self._results: dict[str, bool] = {}

    def charge(self, amount: Decimal, currency: str, idempotency_key: str) -> bool:
        if idempotency_key in self._results:
            return self._results[idempotency_key]
        if self.scenario == "timeout":
            raise TimeoutError("Simulated payment gateway timeout")
        if self.scenario == "exception":
            raise RuntimeError("Simulated payment gateway failure")
        result = self.scenario != "declined" and amount % 10 != Decimal("0.99")
        self._results[idempotency_key] = result
        return result

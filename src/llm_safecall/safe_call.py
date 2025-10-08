from __future__ import annotations
from pydantic import BaseModel, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential_jitter
from typing import Optional
from .providers.base import Provider
from .moderation import check_input, check_output
from .redact import redact_text
from .metrics import CallReport, time_it
from .cache import Cache
from .errors import CircuitOpenError

class CircuitBreaker:
    def __init__(self, threshold: int = 5):
        self.threshold = threshold
        self.failures = 0
        self.open = False

    def record_success(self):
        self.failures = 0
        self.open = False

    def record_failure(self):
        self.failures += 1
        if self.failures >= self.threshold:
            self.open = True

class SafeCall:
    def __init__(
        self,
        llm: Provider,
        output: type[BaseModel] | None = None,
        moderation: bool = True,
        timeout_s: int = 20,
        retries: int = 2,
        redact: list[str] | None = None,
        cache: Cache | None = None,
        **params,
    ):
        self.llm, self.output, self.moderation = llm, output, moderation
        self.timeout_s, self.retries, self.params = timeout_s, retries, params
        self.redactors = redact or []
        self.cache = cache or Cache()
        self.circuit = CircuitBreaker()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(1, 4))
    def _call(self, prompt: str) -> str:
        return self.llm.complete(prompt, timeout_s=self.timeout_s, **self.params).text

    def generate(self, prompt: str):
        if self.circuit.open:
            raise CircuitOpenError("Circuit is open due to recent failures.")

        cleaned = redact_text(prompt, self.redactors)
        if self.moderation:
            check_input(cleaned)

        key = self.cache.key(cleaned, self.params, self.output) if self.cache else None
        if self.cache and (hit := self.cache.get(key)):
            return hit

        with time_it() as t:
            try:
                text = self._call(cleaned)
                self.circuit.record_success()
            except Exception:
                self.circuit.record_failure()
                raise

        if self.moderation:
            check_output(text)

        result = text  # default passthrough
        if self.output:
            try:
                result = self.output.model_validate_json(text)
            except ValidationError:
                repair_prompt = (
                    f"Repair this to valid {self.output.__name__} JSON only. "
                    f"Respond with JSON and nothing else:\n{text}"
                )
                text = self._call(repair_prompt)
                result = self.output.model_validate_json(text)

        report = CallReport(
            latency_ms=t.elapsed_ms,
            input_tokens=None,
            output_tokens=None,
            model=getattr(self.llm, "model", None),
            cost_estimate=None,
        )
        # attach for inspection
        setattr(result, "_report", report)

        if self.cache and key:
            self.cache.set(key, result)

        return result

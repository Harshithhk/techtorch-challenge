"""PII scrubbing before anything leaves the process (Langfuse traces and logs).

Masks emails, IBANs, card numbers and phone numbers with fixed tokens. Replaces customer names with
stable pseudonyms (CUST_xxxxxx) and user ids with hashes. Regex alone cannot find names, so the
scrubber is seeded with the customer names from the loaded ledger.

Dates and money are deliberately left alone: they are the evidence we need for debugging.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{6,}\d(?![\w])")
USER_KEYS = frozenset({"user_id", "userId", "x-user-id", "x_user_id"})


def _digest(value: str, prefix: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:8]}"


class PiiScrubber:
    """Scrubs strings and nested structures. Input: customer names to pseudonymise. Output: scrubbed copies."""

    def __init__(self, customer_names: Iterable[str] = ()) -> None:
        names = sorted({n for n in customer_names if n}, key=len, reverse=True)
        self._names = [(re.compile(re.escape(n), re.IGNORECASE), _digest(n.casefold(), "CUST")) for n in names]

    def scrub_text(self, text: str) -> str:
        """Mask one string. Order matters: email and IBAN first, then cards, then phones, then names."""
        text = EMAIL.sub("<EMAIL>", text)
        text = IBAN.sub("<IBAN>", text)
        text = CARD.sub("<CARD>", text)
        text = PHONE.sub(lambda m: "<PHONE>" if sum(c.isdigit() for c in m.group()) >= 9 else m.group(), text)
        for pattern, token in self._names:
            text = pattern.sub(token, text)
        return text

    def scrub(self, value: Any) -> Any:
        """Scrub any JSON-like value and return a JSON-serialisable copy.

        Input: dict, list, str, number, pydantic model or other object. Output: same shape, scrubbed.
        Keys named like user ids are replaced with a hash, whatever their value.
        """
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        if isinstance(value, dict):
            return {
                str(k): (_digest(str(v), "USER") if k in USER_KEYS and v is not None else self.scrub(v))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self.scrub(v) for v in value]
        if isinstance(value, str):
            return self.scrub_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.scrub_text(str(value))


def make_mask(scrubber: PiiScrubber):
    """Adapter for Langfuse's mask hook: receives data=..., returns the scrubbed copy."""

    def mask(*, data: Any, **_kwargs: Any) -> Any:
        return scrubber.scrub(data)

    return mask


class PiiLogFilter(logging.Filter):
    """Logging filter that scrubs every record's message. Attach to handlers that write to logs or files."""

    def __init__(self, scrubber: PiiScrubber) -> None:
        super().__init__()
        self._scrubber = scrubber

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrubber.scrub_text(record.getMessage())
        record.args = ()
        return True

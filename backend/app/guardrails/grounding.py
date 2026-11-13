"""Numeric grounding: every figure in a final answer must appear in a tool result or the user's own message.

The model must not compute numbers itself. Small integers (days, counts) and years are exempt,
because they are not money. Pure functions, no I/O.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Identifiers such as C-1001, I-9010, C-1007-A1 or INV-MG-41AC218E87 contain digits but are not money.
_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9][A-Za-z0-9_-]*")

_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d])"  # 12,345.67
    r"|(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])"  # 8000 or 8000.00
)


def extract_numbers(text: str) -> list[Decimal]:
    """All numeric tokens in text, as absolute Decimal values. Commas are removed; signs are ignored.

    Identifiers (plan, invoice and action ids) are removed first, so C-1001 yields nothing.
    """
    found: list[Decimal] = []
    for token in _NUMBER.findall(_IDENTIFIER.sub(" ", text)):
        try:
            found.append(abs(Decimal(token.replace(",", ""))))
        except InvalidOperation:
            continue
    return found


def _exempt(value: Decimal) -> bool:
    """Small whole numbers (day, month, counts) and plausible years are not money."""
    if value == value.to_integral_value() and (value <= 31 or 1900 <= value <= 2100):
        return True
    return False


def check_grounding(answer: str, tool_texts: list[str], user_texts: list[str]) -> list[str]:
    """Figures in `answer` that no tool result or user message contains.

    Input: draft answer, raw tool-result strings from this turn, user messages from this turn.
    Output: sorted list of unsupported figures as written (empty means grounded).
    """
    supported: set[Decimal] = set()
    for text in [*tool_texts, *user_texts]:
        supported.update(extract_numbers(text))
    unsupported: set[str] = set()
    for token in _NUMBER.findall(_IDENTIFIER.sub(" ", answer)):
        value = abs(Decimal(token.replace(",", "")))
        if value in supported or _exempt(value):
            continue
        unsupported.add(token)
    return sorted(unsupported)

"""Deterministic prompt-injection patterns. Pure, cheap, runs before any model call.

Catches attempts to change the assistant's role, reveal its instructions, or drive write tools
directly. A semantic classifier (topic.py) backs this up. Structural safety does not depend on
either: write tools are not in the model's toolset (see tools/registry.py).
"""
from __future__ import annotations

import re

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|messages?|rules?)",
        r"disregard\s+(all\s+|the\s+|your\s+)?(previous|prior|system|above|earlier)",
        r"(reveal|show|print|repeat|output|dump)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions|rules)",
        r"\byou\s+are\s+now\b",
        r"\bdeveloper\s+mode\b",
        r"\bjailbreak\b",
        r"\bpretend\s+(to\s+be|you\s+are)\b",
        r"\b(call|run|invoke|use)\s+(the\s+)?(apply|rollback)\b",
        r"\bapply_pending\b|\brevert_action\b|\bcommit_action\b",
        r"(approve|apply|rollback)\b.{0,30}\bwithout\s+(approval|confirmation|review)",
        r"<\s*/?\s*(system|assistant|instructions?)\s*>",
        r"\[\s*(system|instructions?)\s*\]",
    )
)


def detect_injection(text: str) -> bool:
    """True if the text matches any injection pattern. Input: user text. Output: bool."""
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)

"""PII scrubbing before traces or logs leave the process."""
import json
import logging
from decimal import Decimal

from app.observability.langfuse import build_observability
from app.observability.pii import PiiLogFilter, PiiScrubber, make_mask
from app.settings import Settings


def test_direct_identifiers_are_masked():
    """Emails, phone numbers, IBANs and card numbers never appear in clear text."""
    s = PiiScrubber()
    out = s.scrub_text("mail jane.doe@example.com, call +44 20 7946 0958, IBAN GB82WEST12345698765432, card 4111 1111 1111 1111")
    assert "jane.doe" not in out and "<EMAIL>" in out
    assert "7946" not in out and "<PHONE>" in out
    assert "GB82" not in out and "<IBAN>" in out
    assert "4111" not in out and "<CARD>" in out


def test_customer_names_are_pseudonymised_consistently_and_case_insensitively():
    """The same customer always gets the same token, whatever the casing."""
    s = PiiScrubber(["ACME Corp", "Globex Ltd"])
    first = s.scrub_text("ACME Corp renews")
    second = s.scrub_text("acme corp is late")
    assert "ACME" not in first and "acme" not in second
    assert first.split()[0] == second.split()[0]


def test_business_evidence_is_kept_for_debugging():
    """Dates, amounts and invoice ids stay so traces remain useful."""
    s = PiiScrubber()
    text = "invoice I-9010 of 8000.00 USD issued 2025-10-05"
    assert s.scrub_text(text) == text


def test_user_id_keys_are_hashed_and_nested_values_are_scrubbed():
    """A user_id key is hashed whatever its value. Nested lists and dicts are scrubbed too."""
    s = PiiScrubber(["ACME Corp"])
    out = s.scrub({"user_id": "alice", "events": [{"text": "ACME Corp paid", "amount": Decimal("8000.00")}]})
    assert "alice" not in json.dumps(out)
    assert out["user_id"].startswith("USER_") and out["user_id"] == s.scrub({"user_id": "alice"})["user_id"]
    assert out["events"][0]["amount"] == "8000.00"
    assert "ACME" not in out["events"][0]["text"]


def test_langfuse_mask_returns_json_serialisable_scrubbed_data():
    """The mask hook returns only JSON-safe, scrubbed data, as Langfuse requires."""
    mask = make_mask(PiiScrubber(["Globex Ltd"]))
    payload = {"user_id": "bob", "reply": "Globex Ltd owes 25000.00 EUR, contact ops@globex.example"}
    masked = mask(data=payload)
    dumped = json.dumps(masked)
    assert "bob" not in dumped and "Globex" not in dumped and "ops@globex" not in dumped
    assert "25000.00 EUR" in dumped


def test_log_records_are_scrubbed():
    """Log messages pass through the filter with PII masked."""
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "user %s wrote to jane@x.io", ("alice",), None)
    PiiLogFilter(PiiScrubber()).filter(record)
    assert "jane@x.io" not in record.getMessage()


def test_tracing_is_off_without_keys():
    """With no Langfuse keys, no client is built and nothing is sent anywhere."""
    assert build_observability(Settings(_env_file=None, langfuse_public_key="", langfuse_secret_key=""), PiiScrubber()) is None

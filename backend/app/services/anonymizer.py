from __future__ import annotations

import hashlib
import hmac

from backend.app.core.config import settings


def pseudonymize(value: str | int | None, prefix: str) -> str:
    """Create a stable pseudonymous identifier without exposing raw IDs."""
    raw = "" if value is None else str(value)
    digest = hmac.new(
        settings.anonymization_salt.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]
    return f"{prefix}_{digest}"


def anonymize_record(record: dict) -> dict:
    clean = dict(record)
    clean["patient_id"] = pseudonymize(record.get("patient_id"), "pac")
    clean["therapist_id"] = pseudonymize(record.get("therapist_id"), "ter")
    clean.pop("patient_name", None)
    clean.pop("therapist_name", None)
    return clean

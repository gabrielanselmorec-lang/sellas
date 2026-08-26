from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from backend.app.services.note_extraction import adapt_appointment_note


def list_appointments(
    records: list[dict[str, Any]],
    patient_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    appointments = _appointments_from_records(records, patient_id)
    if start_date:
        appointments = [item for item in appointments if _date_value(item.get("appointment_date")) >= start_date]
    if end_date:
        appointments = [item for item in appointments if _date_value(item.get("appointment_date")) <= end_date]
    return sorted(appointments, key=lambda item: (str(item.get("appointment_date") or ""), str(item.get("appointment_id") or "")))


def get_appointment(records: list[dict[str, Any]], appointment_id: str) -> dict[str, Any] | None:
    for appointment in _appointments_from_records(records):
        if str(appointment.get("appointment_id")) == str(appointment_id):
            return appointment
    return None


def get_appointment_notes(records: list[dict[str, Any]], appointment_id: str) -> dict[str, Any] | None:
    appointment = get_appointment(records, appointment_id)
    if not appointment:
        return None
    return {
        "appointment_id": appointment["appointment_id"],
        "patient_id": appointment.get("patient_id"),
        "appointment_date": appointment.get("appointment_date"),
        "raw_note_text": appointment.get("raw_note_text", ""),
        "notes": appointment.get("raw_note_text", ""),
        "authored_at": appointment.get("authored_at"),
        "recorded_at": appointment.get("recorded_at"),
        "note_scope": appointment.get("note_scope"),
    }


def _appointments_from_records(records: list[dict[str, Any]], patient_id: str | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        if patient_id and record.get("patient_id") != patient_id:
            continue
        note = adapt_appointment_note(record)
        appointment_id = str(note.get("appointment_id") or record.get("session_id") or "")
        if not appointment_id:
            continue
        existing = grouped.setdefault(
            appointment_id,
            {
                **note,
                "appointment_id": appointment_id,
                "behaviors": set(),
                "record_count": 0,
            },
        )
        existing["record_count"] += 1
        if record.get("behavior_name"):
            existing["behaviors"].add(str(record["behavior_name"]))
        if not existing.get("raw_note_text") and note.get("raw_note_text"):
            existing["raw_note_text"] = note["raw_note_text"]
    result = []
    for item in grouped.values():
        item["behaviors"] = sorted(item.get("behaviors", set()))
        result.append(item)
    return result


def _date_value(value: Any) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return date.min
    return parsed.date()

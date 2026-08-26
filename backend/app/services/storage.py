from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.core.config import DATA_DIR, settings

RECORDS_PATH = DATA_DIR / "behavior_records.json"
PREDICTIONS_PATH = DATA_DIR / "prediction_history.json"
METRICS_PATH = DATA_DIR / "model_metrics.json"
CONFIG_PATH = DATA_DIR / "bhave_config.json"
NOTE_EXTRACTIONS_PATH = DATA_DIR / "note_extractions.json"
NOTE_CORRECTIONS_PATH = DATA_DIR / "note_human_corrections.json"
ABC_SESSIONS_PATH = DATA_DIR / "abc_sessions.json"
ABC_INTERVALS_PATH = DATA_DIR / "abc_intervals.json"
ABC_CATEGORIES_PATH = DATA_DIR / "abc_categories.json"
ABC_EVENTS_PATH = DATA_DIR / "abc_interval_events.json"
ABC_INSTRUMENTS_PATH = DATA_DIR / "abc_instrument_versions.json"

try:
    from sqlalchemy import create_engine, text

    _ENGINE = create_engine(settings.database_url, future=True)
except Exception:
    _ENGINE = None


def save_records(records: list[dict[str, Any]]) -> None:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            conn.execute(text("DELETE FROM behavior_records"))
            if records:
                conn.execute(
                    text(
                        """
                        INSERT INTO behavior_records (patient_id, session_id, behavior_name, date, payload)
                        VALUES (:patient_id, :session_id, :behavior_name, :date, :payload)
                        """
                    ),
                    [
                        {
                            "patient_id": item.get("patient_id"),
                            "session_id": item.get("session_id"),
                            "behavior_name": item.get("behavior_name"),
                            "date": item.get("date"),
                            "payload": json.dumps(item, ensure_ascii=False),
                        }
                        for item in records
                    ],
                )
    _write_json(RECORDS_PATH, records)


def load_records() -> list[dict[str, Any]]:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            rows = conn.execute(text("SELECT payload FROM behavior_records ORDER BY date, session_id")).all()
        if rows:
            return [json.loads(row[0]) for row in rows]
    return _read_json(RECORDS_PATH, default=[])


def save_config(config: dict[str, Any]) -> None:
    redacted = dict(config)
    if redacted.get("api_token"):
        redacted["api_token"] = "***redacted***"
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            conn.execute(text("DELETE FROM bhave_config"))
            conn.execute(
                text("INSERT INTO bhave_config (payload) VALUES (:payload)"),
                {"payload": json.dumps(redacted, ensure_ascii=False)},
            )
    _write_json(CONFIG_PATH, redacted)


def append_prediction(prediction: dict[str, Any]) -> None:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO prediction_history (patient_id, behavior_name, risk, probability, payload)
                    VALUES (:patient_id, :behavior_name, :risk, :probability, :payload)
                    """
                ),
                {
                    "patient_id": prediction.get("patient_id"),
                    "behavior_name": prediction.get("behavior_name"),
                    "risk": prediction.get("risk"),
                    "probability": prediction.get("probability"),
                    "payload": json.dumps(prediction, ensure_ascii=False),
                },
            )
    history = load_prediction_history()
    history.append(prediction)
    _write_json(PREDICTIONS_PATH, history[-500:])


def load_prediction_history() -> list[dict[str, Any]]:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            rows = conn.execute(text("SELECT payload FROM prediction_history ORDER BY id")).all()
        if rows:
            return [json.loads(row[0]) for row in rows]
    return _read_json(PREDICTIONS_PATH, default=[])


def save_metrics(metrics: dict[str, Any]) -> None:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            conn.execute(text("DELETE FROM model_metrics"))
            conn.execute(
                text("INSERT INTO model_metrics (payload) VALUES (:payload)"),
                {"payload": json.dumps(metrics, ensure_ascii=False)},
            )
    _write_json(METRICS_PATH, metrics)


def load_metrics() -> dict[str, Any]:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            row = conn.execute(text("SELECT payload FROM model_metrics ORDER BY id DESC LIMIT 1")).first()
        if row:
            return json.loads(row[0])
    return _read_json(METRICS_PATH, default={})


def save_note_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            conn.execute(
                text("DELETE FROM behavior_note_extractions WHERE appointment_id = :appointment_id"),
                {"appointment_id": extraction.get("appointment_id")},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO behavior_note_extractions
                    (patient_id, appointment_id, appointment_date, raw_text_hash, payload)
                    VALUES (:patient_id, :appointment_id, :appointment_date, :raw_text_hash, :payload)
                    """
                ),
                {
                    "patient_id": extraction.get("patient_id"),
                    "appointment_id": extraction.get("appointment_id"),
                    "appointment_date": extraction.get("appointment_date"),
                    "raw_text_hash": extraction.get("raw_text_hash"),
                    "payload": json.dumps(extraction, ensure_ascii=False),
                },
            )
    extractions = [item for item in load_note_extractions() if item.get("appointment_id") != extraction.get("appointment_id")]
    extractions.append(extraction)
    _write_json(NOTE_EXTRACTIONS_PATH, extractions)
    return extraction


def load_note_extractions(patient_id: str | None = None) -> list[dict[str, Any]]:
    if _ENGINE is not None:
        _init_db()
        query = "SELECT payload FROM behavior_note_extractions"
        params = {}
        if patient_id:
            query += " WHERE patient_id = :patient_id"
            params["patient_id"] = patient_id
        query += " ORDER BY appointment_date, appointment_id"
        with _ENGINE.begin() as conn:
            rows = conn.execute(text(query), params).all()
        if rows:
            return [json.loads(row[0]) for row in rows]
    data = _read_json(NOTE_EXTRACTIONS_PATH, default=[])
    if patient_id:
        data = [item for item in data if item.get("patient_id") == patient_id]
    return data


def get_note_extraction(appointment_id: str) -> dict[str, Any] | None:
    for item in load_note_extractions():
        if str(item.get("appointment_id")) == str(appointment_id):
            return item
    return None


def save_note_correction(appointment_id: str, correction: dict[str, Any]) -> dict[str, Any]:
    payload = {"appointment_id": appointment_id, **correction}
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO note_human_corrections (appointment_id, corrected_by, payload)
                    VALUES (:appointment_id, :corrected_by, :payload)
                    """
                ),
                {
                    "appointment_id": appointment_id,
                    "corrected_by": correction.get("corrected_by"),
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            )
    corrections = load_note_corrections()
    corrections.append(payload)
    _write_json(NOTE_CORRECTIONS_PATH, corrections[-500:])
    extraction = get_note_extraction(appointment_id)
    if extraction:
        extraction["human_confirmed"] = bool(correction.get("human_confirmed", True))
        extraction["human_corrected"] = bool(correction.get("human_corrected", True))
        extraction["corrected_by"] = correction.get("corrected_by")
        extraction["correction"] = correction
        save_note_extraction(extraction)
        return extraction
    return payload


def load_note_corrections() -> list[dict[str, Any]]:
    if _ENGINE is not None:
        _init_db()
        with _ENGINE.begin() as conn:
            rows = conn.execute(text("SELECT payload FROM note_human_corrections ORDER BY id")).all()
        if rows:
            return [json.loads(row[0]) for row in rows]
    return _read_json(NOTE_CORRECTIONS_PATH, default=[])


def load_abc_sessions() -> list[dict[str, Any]]:
    return _read_json(ABC_SESSIONS_PATH, default=[])


def save_abc_sessions(rows: list[dict[str, Any]]) -> None:
    _write_json(ABC_SESSIONS_PATH, rows)


def load_abc_intervals() -> list[dict[str, Any]]:
    return _read_json(ABC_INTERVALS_PATH, default=[])


def save_abc_intervals(rows: list[dict[str, Any]]) -> None:
    _write_json(ABC_INTERVALS_PATH, rows)


def load_abc_categories() -> list[dict[str, Any]]:
    return _read_json(ABC_CATEGORIES_PATH, default=[])


def save_abc_categories(rows: list[dict[str, Any]]) -> None:
    _write_json(ABC_CATEGORIES_PATH, rows)


def load_abc_events() -> list[dict[str, Any]]:
    return _read_json(ABC_EVENTS_PATH, default=[])


def save_abc_events(rows: list[dict[str, Any]]) -> None:
    _write_json(ABC_EVENTS_PATH, rows)


def load_abc_instruments() -> list[dict[str, Any]]:
    return _read_json(ABC_INSTRUMENTS_PATH, default=[])


def save_abc_instruments(rows: list[dict[str, Any]]) -> None:
    _write_json(ABC_INSTRUMENTS_PATH, rows)


def _init_db() -> None:
    if _ENGINE is None:
        return
    with _ENGINE.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS behavior_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    behavior_name TEXT NOT NULL,
                    date TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    patient_id TEXT,
                    behavior_name TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    probability REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(text("CREATE TABLE IF NOT EXISTS model_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS bhave_config (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"))
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS behavior_note_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id TEXT,
                    appointment_id TEXT NOT NULL UNIQUE,
                    appointment_date TEXT,
                    raw_text_hash TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS note_human_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    appointment_id TEXT NOT NULL,
                    corrected_by TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    payload TEXT NOT NULL
                )
                """
            )
        )


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

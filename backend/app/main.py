from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.app.core.config import settings
from backend.app.ml.drift import drift_report
from backend.app.ml.features import FeatureConfig, build_feature_frame
from backend.app.ml.targets import build_session_landmarks
from backend.app.schemas.abc import ABCCategoryRequest, ABCEventRequest, ABCInstrumentVersionRequest, ABCIntervalRequest, ABCSessionRequest
from backend.app.ml.model_store import load_latest_metadata, load_latest_model, load_model_index
from backend.app.ml.training import feature_importance, predict_next_session, train_models
from backend.app.schemas.behavior import BHaveConfigRequest, FeatureRequest, NoteCorrectionRequest, PredictRequest, SyncRequest, TrainRequest
from backend.app.services.audit import audit_log
from backend.app.services.appointments import get_appointment, get_appointment_notes, list_appointments
from backend.app.services.abc_analysis import (
    abc_prediction_rows,
    conditional_probability_analysis,
    create_category,
    create_event,
    create_instrument,
    create_interval,
    create_session,
    ensure_default_categories,
    floor_timestamp_to_interval,
    timeline as abc_timeline,
)
from backend.app.services.bhave_client import BHaveClient
from backend.app.services.governance import governance_status
from backend.app.services.mock_data import generate_mock_records
from backend.app.services.note_extraction import extract_appointment_note, extraction_to_feature_rows
from backend.app.services.normalizer import normalize_records
from backend.app.services.storage import (
    append_prediction,
    get_note_extraction,
    load_metrics,
    load_note_extractions,
    load_prediction_history,
    load_records,
    save_config,
    load_abc_categories,
    load_abc_events,
    load_abc_instruments,
    load_abc_intervals,
    load_abc_sessions,
    save_metrics,
    save_abc_categories,
    save_abc_events,
    save_abc_instruments,
    save_abc_intervals,
    save_abc_sessions,
    save_note_correction,
    save_note_extraction,
    save_records,
)

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


@app.post("/api/config/bhave")
def configure_bhave(payload: BHaveConfigRequest) -> dict:
    save_config(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    contract = BHaveClient(
        payload.base_url or settings.bhave_base_url,
        payload.api_token or settings.bhave_api_token,
        records_path=payload.records_path,
        auth_scheme=payload.auth_scheme,
        auth_header=payload.auth_header,
        records_key=payload.records_key,
        page_param=payload.page_param,
        page_size_param=payload.page_size_param,
        next_page_key=payload.next_page_key,
    ).validate_contract()
    audit_log("bhave_configured", {"use_mock": payload.use_mock, "base_url_configured": bool(payload.base_url), "records_path": payload.records_path})
    return {"ok": True, "use_mock": payload.use_mock, "token_stored": bool(payload.api_token), "contract": contract}


@app.get("/api/config/bhave/contract")
def bhave_contract_status() -> dict:
    return BHaveClient(settings.bhave_base_url, settings.bhave_api_token).validate_contract()


@app.post("/api/sync/bhave")
def sync_bhave(payload: SyncRequest) -> dict:
    use_mock = settings.use_mock_data if payload.use_mock is None else payload.use_mock
    try:
        if use_mock:
            raw_records = generate_mock_records()
        else:
            client = BHaveClient(settings.bhave_base_url, settings.bhave_api_token)
            raw_records = client.fetch_behavior_records(
                start_date=payload.start_date.isoformat() if payload.start_date else None,
                end_date=payload.end_date.isoformat() if payload.end_date else None,
            )
        records = normalize_records(raw_records, field_map=payload.field_map)
        save_records(records)
        audit_log("bhave_synced", {"records": len(records), "use_mock": use_mock})
        return {"ok": True, "records": len(records), "use_mock": use_mock}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/patients")
def list_patients() -> list[dict]:
    records = load_records()
    patients = sorted({record["patient_id"] for record in records})
    return [{"patient_id": patient_id} for patient_id in patients]


@app.get("/api/patients/{patient_id}/behaviors")
def list_patient_behaviors(patient_id: str) -> list[dict]:
    records = [record for record in load_records() if record.get("patient_id") == patient_id]
    behaviors = sorted({record["behavior_name"] for record in records})
    return [{"behavior_name": behavior} for behavior in behaviors]


@app.get("/api/patients/{patient_id}/history")
def patient_history(
    patient_id: str,
    behavior_name: str | None = Query(None, max_length=120),
) -> list[dict]:
    records = [record for record in load_records() if record.get("patient_id") == patient_id]
    if behavior_name:
        records = [record for record in records if record.get("behavior_name", "").lower() == behavior_name.lower()]
    return records


@app.get("/api/patients/{patient_id}/appointments")
def patient_appointments(
    patient_id: str,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
) -> list[dict]:
    return list_appointments(load_records(), patient_id=patient_id, start_date=start_date, end_date=end_date)


@app.get("/api/appointments/{appointment_id}")
def appointment_detail(appointment_id: str) -> dict:
    appointment = get_appointment(load_records(), appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Atendimento nao encontrado.")
    return appointment


@app.get("/api/appointments/{appointment_id}/notes")
def appointment_notes(appointment_id: str) -> dict:
    notes = get_appointment_notes(load_records(), appointment_id)
    if not notes:
        raise HTTPException(status_code=404, detail="Anotacoes nao encontradas para o atendimento.")
    return notes


@app.post("/api/appointments/{appointment_id}/notes/extract")
def extract_appointment_notes(appointment_id: str) -> dict:
    notes = get_appointment_notes(load_records(), appointment_id)
    if not notes:
        raise HTTPException(status_code=404, detail="Anotacoes nao encontradas para o atendimento.")
    extraction = extract_appointment_note(notes)
    save_note_extraction(extraction)
    audit_log("appointment_note_extracted", {"appointment_id": appointment_id, "patient_id": extraction.get("patient_id")})
    return extraction


@app.post("/api/notes/batch-extract")
def batch_extract_notes(
    patient_id: str | None = Query(None, max_length=80),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
) -> dict:
    records = load_records()
    patient_ids = [patient_id] if patient_id else sorted({record.get("patient_id") for record in records if record.get("patient_id")})
    extracted = []
    for current_patient in patient_ids:
        for appointment in list_appointments(records, patient_id=current_patient, start_date=start_date, end_date=end_date):
            extraction = extract_appointment_note(appointment)
            save_note_extraction(extraction)
            extracted.append(extraction)
    audit_log("appointment_notes_batch_extracted", {"count": len(extracted), "patient_id": patient_id})
    return {"ok": True, "extractions": len(extracted)}


@app.get("/api/notes/extractions/{appointment_id}")
def note_extraction(appointment_id: str) -> dict:
    extraction = get_note_extraction(appointment_id)
    if not extraction:
        raise HTTPException(status_code=404, detail="Extracao nao encontrada.")
    return extraction


@app.post("/api/notes/extractions/{appointment_id}/confirm")
def confirm_note_extraction(appointment_id: str, payload: NoteCorrectionRequest) -> dict:
    correction = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    return save_note_correction(appointment_id, correction)


@app.post("/api/ml/features")
def features(payload: FeatureRequest) -> dict:
    try:
        config = _feature_config(payload)
        note_rows = extraction_to_feature_rows(load_note_extractions(patient_id=payload.patient_id))
        frame = build_feature_frame(
            load_records(),
            patient_id=payload.patient_id,
            behavior_name=payload.behavior_name,
            config=config,
            note_feature_rows=note_rows,
        )
        preview_columns = [
            "patient_id",
            "session_id",
            "date",
            "behavior_name",
            "occurred",
            "target_next_session",
            "frequency_recent_n",
            "frequency_recent_n_mean",
            "frequency_recent_days",
            "exponential_recent_risk",
            "historical_event_rate",
            "sessions_since_last_occurrence",
            "landmark_ts",
            "coverage_minutes",
            "censored",
            "event_in_progress",
            "abstain_candidate_reason",
            "occurrence_from_note",
            "note_extracted_frequency",
            "note_extracted_intensity",
            "note_extracted_duration",
            "note_extraction_confidence",
            "note_requires_human_review",
        ]
        return {
            "samples": int(len(frame)),
            "target": {
                "name": "target_next_session",
                "positive_class": "1 se o comportamento ocorrer na janela futura configurada",
                "horizon_sessions": config.horizon_sessions,
                "prediction_window": config.prediction_window,
            },
            "feature_config": config.__dict__,
            "features": [column for column in preview_columns if column in frame.columns],
            "preview": frame[[column for column in preview_columns if column in frame.columns]].head(20).to_dict(orient="records"),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ml/targets")
def targets(payload: FeatureRequest) -> dict:
    records = load_records()
    table = build_session_landmarks(records, horizon_sessions=payload.horizon_sessions, prediction_window=payload.prediction_window)
    if payload.patient_id:
        table = table[table["patient_id"] == payload.patient_id]
    return {
        "patient_id": payload.patient_id,
        "prediction_window": payload.prediction_window,
        "horizon_sessions": payload.horizon_sessions,
        "samples": int(len(table)),
        "censored": int(table["censored"].sum()) if "censored" in table.columns and not table.empty else 0,
        "target_contract": {
            "landmark_table": True,
            "negative_requires_observation": True,
            "event_in_progress_is_censored": True,
            "cooldown_supported": True,
        },
        "preview": table.head(50).to_dict(orient="records"),
    }


@app.get("/api/abc/config")
def abc_config() -> dict:
    return {
        "analysis_start_date": settings.abc_analysis_start_date,
        "analysis_end_date": settings.abc_analysis_end_date,
        "include_weekends": settings.abc_include_weekends,
        "interval_minutes": settings.abc_interval_minutes,
        "minimum_valid_intervals": settings.abc_minimum_valid_intervals,
    }


@app.post("/api/abc/time/floor")
def abc_floor_time(timestamp: datetime, interval_minutes: int = Query(5, ge=1, le=240)) -> dict:
    floored = floor_timestamp_to_interval(timestamp, interval_minutes)
    return {"timestamp_utc": timestamp.astimezone(timezone.utc).isoformat(), "floored_utc": floored.isoformat(), "interval_minutes": interval_minutes}


@app.post("/api/abc/instrument-versions")
def abc_create_instrument(payload: ABCInstrumentVersionRequest) -> dict:
    instruments = load_abc_instruments()
    item = create_instrument(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), instruments)
    save_abc_instruments(instruments)
    audit_log("abc_instrument_created", {"id": item["id"], "codigo": item.get("codigo")})
    return item


@app.get("/api/abc/categories")
def abc_categories(tipo: str | None = Query(None), active_only: bool = True) -> list[dict]:
    categories = ensure_default_categories(load_abc_categories())
    if not load_abc_categories():
        save_abc_categories(categories)
    if tipo:
        categories = [item for item in categories if item.get("tipo") == tipo]
    if active_only:
        categories = [item for item in categories if item.get("ativa", True)]
    return categories


@app.post("/api/abc/categories")
def abc_create_category(payload: ABCCategoryRequest) -> dict:
    categories = ensure_default_categories(load_abc_categories())
    try:
        item = create_category(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), categories)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_abc_categories(categories)
    audit_log("abc_category_created", {"id": item["id"], "codigo": item.get("codigo"), "tipo": item.get("tipo")})
    return item


@app.post("/api/abc/sessions")
def abc_create_session(payload: ABCSessionRequest) -> dict:
    sessions = load_abc_sessions()
    try:
        item = create_session(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), sessions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_abc_sessions(sessions)
    audit_log("abc_session_created", {"id": item["id"], "patient_token": item.get("patient_token")})
    return item


@app.get("/api/abc/sessions")
def abc_sessions(patient_token: str | None = Query(None)) -> list[dict]:
    sessions = load_abc_sessions()
    if patient_token:
        sessions = [item for item in sessions if item.get("patient_token") == patient_token]
    return sessions


@app.post("/api/abc/sessions/{session_id}/intervals")
def abc_create_interval(session_id: str, payload: ABCIntervalRequest) -> dict:
    sessions = load_abc_sessions()
    intervals = load_abc_intervals()
    try:
        item = create_interval(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), session_id, sessions, intervals)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_abc_intervals(intervals)
    audit_log("abc_interval_created", {"id": item["id"], "session_id": session_id, "inicio": item.get("inicio")})
    return item


@app.get("/api/abc/sessions/{session_id}/intervals")
def abc_session_intervals(session_id: str) -> list[dict]:
    return [item for item in load_abc_intervals() if item.get("sessao_id") == session_id]


@app.post("/api/abc/intervals/{interval_id}/events")
def abc_create_interval_event(interval_id: str, payload: ABCEventRequest) -> dict:
    intervals = load_abc_intervals()
    events = load_abc_events()
    categories = ensure_default_categories(load_abc_categories())
    if not load_abc_categories():
        save_abc_categories(categories)
    try:
        item = create_event(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), interval_id, intervals, categories, events)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_abc_events(events)
    audit_log("abc_event_created", {"id": item["id"], "interval_id": interval_id, "categoria_codigo": item.get("categoria_codigo")})
    return item


@app.get("/api/abc/analysis/conditional-probabilities")
def abc_conditional_probabilities(
    patient_token: str | None = Query(None),
    periodo_inicio: datetime | None = Query(None),
    periodo_fim: datetime | None = Query(None),
    include_weekends: bool | None = Query(None),
    minimum_valid_intervals: int | None = Query(None, ge=1),
) -> dict:
    return conditional_probability_analysis(
        load_abc_sessions(),
        load_abc_intervals(),
        ensure_default_categories(load_abc_categories()),
        load_abc_events(),
        patient_token=patient_token,
        periodo_inicio=periodo_inicio or settings.abc_analysis_start_date,
        periodo_fim=periodo_fim or settings.abc_analysis_end_date,
        include_weekends=settings.abc_include_weekends if include_weekends is None else include_weekends,
        minimum_valid_intervals=minimum_valid_intervals or settings.abc_minimum_valid_intervals,
    )


@app.get("/api/abc/analysis/associations")
def abc_associations(
    patient_token: str | None = Query(None),
    periodo_inicio: datetime | None = Query(None),
    periodo_fim: datetime | None = Query(None),
    include_weekends: bool | None = Query(None),
    minimum_valid_intervals: int | None = Query(None, ge=1),
) -> dict:
    antecedent_behavior = conditional_probability_analysis(
        load_abc_sessions(),
        load_abc_intervals(),
        ensure_default_categories(load_abc_categories()),
        load_abc_events(),
        patient_token=patient_token,
        periodo_inicio=periodo_inicio or settings.abc_analysis_start_date,
        periodo_fim=periodo_fim or settings.abc_analysis_end_date,
        include_weekends=settings.abc_include_weekends if include_weekends is None else include_weekends,
        minimum_valid_intervals=minimum_valid_intervals or settings.abc_minimum_valid_intervals,
        pair_type="antecedente_comportamento",
    )
    behavior_consequence = conditional_probability_analysis(
        load_abc_sessions(),
        load_abc_intervals(),
        ensure_default_categories(load_abc_categories()),
        load_abc_events(),
        patient_token=patient_token,
        periodo_inicio=periodo_inicio or settings.abc_analysis_start_date,
        periodo_fim=periodo_fim or settings.abc_analysis_end_date,
        include_weekends=settings.abc_include_weekends if include_weekends is None else include_weekends,
        minimum_valid_intervals=minimum_valid_intervals or settings.abc_minimum_valid_intervals,
        pair_type="comportamento_consequencia",
    )
    return {
        "titulo": "Mapa descritivo de associacoes ABC",
        "eixo_x": "associacao antecedente-comportamento",
        "eixo_y": "associacao comportamento-consequencia",
        "tamanho_ponto": "quantidade de intervalos validos",
        "aviso": "As associacoes apresentadas nao confirmam causa ou funcao comportamental e precisam ser interpretadas por profissional qualificado.",
        "antecedente_comportamento": antecedent_behavior["metricas"],
        "comportamento_consequencia": behavior_consequence["metricas"],
    }


@app.get("/api/abc/analysis/timeline")
def abc_analysis_timeline(patient_token: str | None = Query(None)) -> dict:
    return {
        "patient_token": patient_token,
        "timeline": abc_timeline(load_abc_sessions(), load_abc_intervals(), ensure_default_categories(load_abc_categories()), load_abc_events(), patient_token),
    }


@app.get("/api/abc/prediction/rows")
def abc_prediction_dataset(behavior_code: str = Query(..., min_length=1), patient_token: str | None = Query(None)) -> dict:
    intervals = abc_timeline(load_abc_sessions(), load_abc_intervals(), ensure_default_categories(load_abc_categories()), load_abc_events(), patient_token)
    rows = abc_prediction_rows(intervals, load_abc_events(), behavior_code)
    return {
        "behavior_code": behavior_code,
        "samples": len(rows),
        "features_allowed": [
            "antecedentes do intervalo atual",
            "comportamentos anteriores",
            "consequencias de intervalos anteriores ou do intervalo ja encerrado para prever o proximo",
            "frequencia recente",
            "hora e contexto",
        ],
        "features_forbidden": [
            "consequencia do mesmo comportamento que esta sendo previsto",
            "resumo final da sessao",
            "anotacao escrita depois do evento",
            "total futuro da sessao",
        ],
        "rows": rows[:200],
    }


@app.post("/api/ml/features/from-notes")
def features_from_notes(payload: FeatureRequest) -> dict:
    note_rows = extraction_to_feature_rows(load_note_extractions(patient_id=payload.patient_id))
    rows = [row for row in note_rows if row.get("behavior_name", "").lower() == payload.behavior_name.lower()]
    return {
        "patient_id": payload.patient_id,
        "behavior_name": payload.behavior_name,
        "samples": len(rows),
        "features": rows[:50],
    }


@app.post("/api/ml/train")
def train(payload: TrainRequest) -> dict:
    try:
        result = train_models(load_records(), payload.patient_id, payload.behavior_name, feature_config=_feature_config(payload))
        save_metrics(result)
        audit_log("model_trained", {"patient_id": payload.patient_id, "behavior_name": payload.behavior_name, "selected_model": result["selected_model"]})
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ml/predict")
def predict(payload: PredictRequest) -> dict:
    try:
        result = predict_next_session(load_records(), payload.patient_id, payload.behavior_name, feature_config=_feature_config(payload))
        result["created_at"] = datetime.now(timezone.utc).isoformat()
        append_prediction(result)
        audit_log("prediction_created", {"patient_id": payload.patient_id, "behavior_name": payload.behavior_name, "risk": result["risk"]})
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ml/metrics")
def metrics() -> dict:
    return load_metrics()


@app.get("/api/ml/calibration")
def calibration() -> dict:
    metrics = load_metrics()
    return {
        "selected_model": metrics.get("selected_model"),
        "calibration": metrics.get("calibration", {}),
        "model_metrics": {
            name: values.get("calibration", {})
            for name, values in metrics.get("metrics", {}).items()
            if isinstance(values, dict)
        },
        "interpretation": "Se o modelo retorna 0.70, espera-se que aproximadamente 70% dos casos semelhantes tenham ocorrencia real quando houver calibracao adequada.",
    }


@app.get("/api/ml/feature-importance")
def feature_importance_endpoint(
    patient_id: str | None = Query(None, max_length=80),
    behavior_name: str = Query(..., min_length=1, max_length=120),
) -> dict:
    stored = load_latest_model(patient_id, behavior_name)
    if not stored:
        raise HTTPException(status_code=404, detail="Modelo versionado nao encontrado.")
    model, metadata = stored
    return {
        "model_version_id": metadata.get("version_id"),
        "selected_model": metadata.get("selected_model"),
        "feature_importance": feature_importance(model),
    }


@app.get("/api/ml/models")
def model_versions() -> dict:
    return load_model_index()


@app.get("/api/ml/drift")
def model_drift(
    patient_id: str | None = Query(None, max_length=80),
    behavior_name: str = Query(..., min_length=1, max_length=120),
) -> dict:
    try:
        metadata = load_latest_metadata(patient_id, behavior_name)
        if not metadata:
            raise HTTPException(status_code=404, detail="Modelo versionado nao encontrado para paciente/comportamento.")
        note_rows = extraction_to_feature_rows(load_note_extractions(patient_id=patient_id))
        frame = build_feature_frame(load_records(), patient_id=patient_id, behavior_name=behavior_name, note_feature_rows=note_rows)
        report = drift_report(frame, metadata.get("reference_stats", {}))
        audit_log("drift_checked", {"patient_id": patient_id, "behavior_name": behavior_name, "status": report["status"]})
        return {"model_version_id": metadata.get("version_id"), "behavior_name": behavior_name, "patient_id": patient_id, "drift": report}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/governance/lgpd")
def lgpd_governance() -> dict:
    return governance_status()


@app.get("/api/predictions/history")
def prediction_history(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return load_prediction_history()[-limit:]


@app.get("/api/reports/export")
def export_report(limit: int = Query(20, ge=1, le=100)) -> dict:
    history = load_prediction_history()[-limit:]
    metrics = load_metrics()
    note_extractions = load_note_extractions()[-limit:]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clinical_disclaimer": "Esta previsao e uma ferramenta de apoio e nao substitui a avaliacao clinica do analista do comportamento.",
        "metrics": metrics,
        "predictions": history,
        "note_extractions": note_extractions,
        "governance": governance_status(),
    }


def _feature_config(payload: FeatureRequest | TrainRequest | PredictRequest) -> FeatureConfig:
    return FeatureConfig(
        recent_sessions=payload.recent_sessions,
        recent_days=payload.recent_days,
        alpha=payload.alpha,
        horizon_sessions=payload.horizon_sessions,
        prediction_window=payload.prediction_window,
    )

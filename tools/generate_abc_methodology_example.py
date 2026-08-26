"""Gera DOCX e PDF sintéticos do relatório ABC v2, sem dados identificáveis."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from app.services.abc_closed import build_abc_analysis, build_abc_printable_summary, predict_abc_behavior
from app.services.abc_prediction_report import build_abc_report_docx, build_abc_report_pdf


OUTPUT_DIR = PROJECT_DIR / "output" / "abc_methodology_v3"


def synthetic_records() -> list[dict]:
    records = []
    start = datetime(2026, 6, 1, 9, tzinfo=timezone(timedelta(hours=-3)))
    for index in range(36):
        primary = index % 4 != 0
        records.append(
            {
                "intervalo_id": f"synthetic-i-{index:03d}",
                "event_id": f"synthetic-e-{index:03d}",
                "session_id": f"synthetic-s-{index // 6:02d}",
                "data_hora": (start + timedelta(days=index // 6, minutes=5 * (index % 6))).isoformat(),
                "ambiente": "Sala de terapia" if index < 24 else "Casa",
                "antecedente": "Demanda apresentada" if primary else "Do nada",
                "comportamento": "Choro" if primary else "Grito",
                "consequencia": "Manejo fisíco" if index % 5 == 0 else "Pausa",
                "classificacao": None if index % 11 == 0 else "C2" if index % 7 == 0 else "C1",
                "funcao": "Fuga ou esquiva" if primary else "Não identificada",
                "observer_id": "synthetic-observer-01",
                "source_system": "synthetic-fixture",
                "taxonomy_version": "abc-taxonomy-v1",
                "taxonomy_valid": True,
            }
        )
    return records


def build_payload() -> dict:
    records = synthetic_records()
    analysis = build_abc_analysis(records)
    prediction = predict_abc_behavior(
        records,
        behavior="Choro",
        antecedent="Demanda apresentada",
        environment="Sala de terapia",
    )
    intervals = [
        {
            "intervalo_id": row["intervalo_id"],
            "session_id": row["session_id"],
            "status_observacao": "observed",
            "duracao_planejada_minutos": 5,
            "instrumento_versao": "2",
            "start_ts": row["data_hora"],
            "end_ts": (datetime.fromisoformat(row["data_hora"]) + timedelta(minutes=5)).isoformat(),
            "observer_id": "synthetic-observer-01",
            "source_system": "synthetic-fixture",
            "taxonomy_version": "abc-taxonomy-v1",
            "eventos_nao_informados": 0,
        }
        for row in records
    ]
    summary = build_abc_printable_summary(
        patient_name="Paciente Sintético",
        records=records,
        intervals=intervals,
        temporal_candidates=[],
        logic_version="abc-methodology-v3",
    )
    selected_chain = (analysis.get("cadeias_completas") or [{}])[0]
    return {
        "patient": "Paciente Sintético",
        "environment": "Todos os ambientes",
        "risk_environment": "Sala de terapia",
        "analysis": analysis,
        "analysis_all": analysis,
        "chain_scope": "all",
        "selected_chain": selected_chain,
        "prediction": prediction,
        "temporal_data": {"candidates": [], "stats": []},
        "report_summary": summary,
        "include_charts": True,
        "include_abc_explanation": True,
        "include_limitations": True,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    (OUTPUT_DIR / "relatorio_abc_v3_sintetico.docx").write_bytes(build_abc_report_docx(payload))
    (OUTPUT_DIR / "relatorio_abc_v3_sintetico.pdf").write_bytes(build_abc_report_pdf(payload))
    (OUTPUT_DIR / "payload_abc_v3_sintetico.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(OUTPUT_DIR)


if __name__ == "__main__":
    main()

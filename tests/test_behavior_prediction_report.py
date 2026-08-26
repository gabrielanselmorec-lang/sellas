"""Testes do relatório imprimível da previsão comportamental."""
import io
import os
import sys
from datetime import date

import pandas as pd
from docx import Document
from PyPDF2 import PdfReader

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.behavior_prediction_report import (
    behavior_report_filename,
    build_behavior_report_docx,
    build_behavior_report_pdf,
    build_plain_language_explanation,
)


def _payload() -> dict:
    return {
        "patient": "Paciente Teste",
        "behavior": "Agressão física",
        "period_start": date(2026, 1, 1),
        "period_end": date(2026, 6, 30),
        "y_title": "Taxa média mensal",
        "observed": pd.DataFrame(
            {
                "month": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
                "valor": [3.0, 2.2, 1.8],
            }
        ),
        "forecast": pd.DataFrame(
            {
                "month": pd.to_datetime(["2026-04-01", "2026-05-01"]),
                "prediction": [1.5, 1.3],
                "lower": [0.7, 0.5],
                "upper": [2.6, 2.4],
            }
        ),
        "metrics": {
            "occurrence_probability": 0.42,
            "occurrence_risk": "moderado",
            "baseline": 0.55,
            "recent_risk": 0.39,
            "current": 1.8,
            "next_prediction": 1.5,
            "next_lower": 0.7,
            "next_upper": 2.6,
            "probability_increase": 31.0,
            "probability_reduction": 48.0,
            "reduction_target": 20,
            "metric_label": "Taxa média",
            "data_points": 6,
            "alpha_ema": 0.30,
            "beta": -0.12,
            "r2": 0.72,
            "sigma": 0.18,
        },
    }


def test_explicacao_leiga_traduz_probabilidade_sem_nan():
    explicacao = build_plain_language_explanation(_payload()["metrics"])
    texto = " ".join([explicacao["headline"], *explicacao["bullets"]]).lower()

    assert "100 sessões" in texto
    assert "não uma garantia" in texto
    assert "nan" not in texto


def test_docx_contem_resumo_grafico_e_aviso_clinico():
    conteudo = build_behavior_report_docx(_payload())
    doc = Document(io.BytesIO(conteudo))
    texto = "\n".join(paragrafo.text for paragrafo in doc.paragraphs)

    assert "Resumo em linguagem simples" in texto
    assert "Agressão física" in texto
    assert "não substitui avaliação funcional" in texto
    assert len(doc.inline_shapes) >= 1


def test_pdf_contem_resumo_e_comportamento():
    conteudo = build_behavior_report_pdf(_payload())
    leitor = PdfReader(io.BytesIO(conteudo))
    texto = "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)

    assert conteudo.startswith(b"%PDF")
    assert len(leitor.pages) >= 2
    assert "Resumo em linguagem simples" in texto
    assert "Agressão física" in texto


def test_nome_do_relatorio_e_compativel_com_windows():
    nome = behavior_report_filename("João da Silva", "Agressão / fuga", "docx")

    assert nome.endswith(".docx")
    assert "Joao_da_Silva" in nome
    assert "/" not in nome

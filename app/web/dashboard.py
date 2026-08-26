import html
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from app.services.abc_closed import parse_hour_minute_text
from app.services.abc_prediction_report import (
    abc_report_filename,
    build_abc_report_docx,
    build_abc_report_pdf_from_docx,
)
from app.services.behavior_prediction_report import (
    behavior_report_filename,
    build_behavior_report_docx,
    build_behavior_report_pdf,
    build_plain_language_explanation,
)
from main import sincronizar_bhave_api

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from api_client import load_data_from_api, load_library_from_api


BASE_DIR = Path(__file__).resolve().parent
STYLE_PATH = BASE_DIR / "assets" / "style.css"
API_URL = os.getenv("SELLAS_API_URL", os.getenv("SKINNER_API_URL", "http://127.0.0.1:8010")).rstrip("/")
API_TIMEOUT_SECONDS = 90

PASTEL_SKILL = "#7d9b76"
PASTEL_BEHAVIOR = "#c17c74"
PASTEL_FORECAST = "#6f86a4"
PASTEL_BAND = "rgba(216, 189, 114, 0.24)"
NEW_ABC_OPTION = "Digitar nova opção..."
NEW_FUNCTION_OPTION = "Digitar nova função..."
ABC_FUNCTIONS = [
    "Atenção social",
    "Acesso a item ou atividade",
    "Fuga ou esquiva",
    "Reforçamento automático",
    "Indeterminada / em avaliação",
]


def inject_css(css_path: Path = STYLE_PATH) -> None:
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def normal_cdf(value: float, mean: float, std: float) -> float:
    std = max(float(std), 1e-6)
    z = (value - mean) / (std * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def probability_at_least(threshold: float, mean: float, std: float) -> float:
    return clamp((1 - normal_cdf(threshold, mean, std)) * 100)


def probability_below(threshold: float, mean: float, std: float) -> float:
    return clamp(normal_cdf(threshold, mean, std) * 100)


def logistic(value: float) -> float:
    return 1 / (1 + math.exp(-max(-50, min(50, value))))


def logit(probability: float) -> float:
    probability = max(1e-4, min(0.9999, probability))
    return math.log(probability / (1 - probability))


def risk_label(probability: float) -> str:
    if probability < 0.30:
        return "baixo"
    if probability < 0.70:
        return "moderado"
    return "alto"


def ema(values: list[int], alpha: float) -> float:
    alpha = max(0.0, min(1.0, float(alpha)))
    current = 0.0
    for value in values:
        current = alpha * int(value) + (1 - alpha) * current
    return current


def month_start(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.to_period("M").dt.to_timestamp()


def fit_linear_forecast(monthly: pd.DataFrame, value_col: str, horizon: int, bounds=(0.0, 100.0)) -> dict:
    points = monthly.dropna(subset=[value_col]).copy().sort_values("month")
    if points.empty:
        return {"forecast": pd.DataFrame(), "beta0": 0.0, "beta1": 0.0, "r2": 0.0, "sigma": 12.0}

    points["t"] = range(len(points))
    x = points["t"].astype(float)
    y = points[value_col].astype(float)

    if len(points) >= 2 and float(x.var()) > 0:
        beta1 = float(((x - x.mean()) * (y - y.mean())).sum() / ((x - x.mean()) ** 2).sum())
        beta0 = float(y.mean() - beta1 * x.mean())
    else:
        beta1 = 0.0
        beta0 = float(y.iloc[-1])

    fitted = beta0 + beta1 * x
    residuals = y - fitted
    sigma = float(residuals.std(ddof=1)) if len(points) >= 3 else 10.0
    sigma = max(sigma, 8.0)
    ss_res = float((residuals**2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 0.0 if ss_tot == 0 else clamp(1 - ss_res / ss_tot, 0.0, 1.0)

    last_month = points["month"].max()
    future_rows = []
    for step in range(1, horizon + 1):
        t_future = len(points) - 1 + step
        raw = beta0 + beta1 * t_future
        predicted = clamp(raw, bounds[0], bounds[1])
        future_rows.append(
            {
                "month": last_month + pd.DateOffset(months=step),
                "prediction": predicted,
                "lower": clamp(predicted - 1.28 * sigma, bounds[0], bounds[1]),
                "upper": clamp(predicted + 1.28 * sigma, bounds[0], bounds[1]),
                "t": t_future,
            }
        )

    return {
        "forecast": pd.DataFrame(future_rows),
        "beta0": beta0,
        "beta1": beta1,
        "r2": r2,
        "sigma": sigma,
        "observed": points,
    }


def fit_log_forecast(monthly: pd.DataFrame, value_col: str, horizon: int) -> dict:
    points = monthly.dropna(subset=[value_col]).copy().sort_values("month")
    if points.empty:
        return {"forecast": pd.DataFrame(), "alpha": 0.0, "beta": 0.0, "sigma": 1.0, "r2": 0.0}

    points["t"] = range(len(points))
    points["log_value"] = (points[value_col].clip(lower=0) + 1).map(math.log)
    model_points = points[["month", "log_value"]].rename(columns={"log_value": "value"})
    linear = fit_linear_forecast(model_points, "value", horizon, bounds=(-20.0, 20.0))
    forecast = linear["forecast"].copy()
    if not forecast.empty:
        for col in ["prediction", "lower", "upper"]:
            forecast[col] = forecast[col].map(lambda v: max(math.exp(v) - 1, 0.0))

    observed = points.copy()
    observed["fitted"] = (linear["beta0"] + linear["beta1"] * observed["t"]).map(lambda v: max(math.exp(v) - 1, 0.0))
    residual_sigma = max(float((observed[value_col] - observed["fitted"]).std(ddof=1)) if len(observed) >= 3 else 1.0, 1.0)

    return {
        "forecast": forecast,
        "alpha": linear["beta0"],
        "beta": linear["beta1"],
        "sigma": residual_sigma,
        "r2": linear["r2"],
        "observed": observed,
    }


def month_label(value) -> str:
    return pd.to_datetime(value).strftime("%m/%Y")


def pct(value: float) -> str:
    return f"{float(value):.1f}%"


def signed_pct(value: float) -> str:
    return f"{float(value):+.1f} p.p./mês"


def number(value: float) -> str:
    return f"{float(value):.2f}"


def render_formula(title: str, lines: list[str]) -> None:
    content = "".join(f"<div>{html.escape(line)}</div>" for line in lines)
    st.markdown(
        f"""
        <div class="formula-box">
            <strong>{html.escape(title)}</strong>
            {content}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_formula_blocks(title: str, blocks: list[tuple[str, str, str]]) -> None:
    st.markdown(f"#### {title}")
    for start in range(0, len(blocks), 2):
        columns = st.columns(2)
        for column, (block_title, equation, meaning) in zip(columns, blocks[start : start + 2], strict=False):
            with column:
                render_formula(block_title, [equation, meaning])


def render_html_table(df: pd.DataFrame, columns: list[str], formatters: dict | None = None, max_rows: int = 12) -> None:
    if df.empty:
        st.info("Sem dados suficientes para montar a tabela.")
        return

    formatters = formatters or {}
    view = df.head(max_rows).copy()
    header = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    rows = []
    for _, row in view.iterrows():
        cells = []
        for col in columns:
            value = row.get(col, "")
            if col in formatters:
                value = formatters[col](value)
            elif isinstance(value, float):
                value = f"{value:.2f}"
            cells.append(f"<td>{html.escape(str(value))}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    st.markdown(f"<table class='analysis-table'><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>", unsafe_allow_html=True)


def render_metric_card(label: str, value: str, note: str) -> None:
    st.markdown(
        f"""
        <div class="analysis-card">
            <span>{html.escape(label)}</span>
            <strong>{html.escape(value)}</strong>
            <small>{html.escape(note)}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )


def apply_chart_theme(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#fffdf8",
        plot_bgcolor="#f3eadb",
        font=dict(color="#33291f", family="Inter, Segoe UI, sans-serif", size=12),
        legend=dict(bgcolor="rgba(255,253,248,0.94)", bordercolor="#ded0b8", borderwidth=1),
        margin=dict(l=42, r=24, t=58, b=44),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#e5d9c7", linecolor="#c9b897")
    fig.update_yaxes(gridcolor="#e5d9c7", linecolor="#c9b897")
    return fig


def _api_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        return str(payload.get("detail") or payload)
    except Exception:
        return f"Erro HTTP {response.status_code}"


def load_abc_categories() -> list[dict]:
    response = requests.get(f"{API_URL}/api/abc/categorias", timeout=API_TIMEOUT_SECONDS)
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def load_abc_analysis(patient: str, environment: str | None = None) -> dict:
    params = {"paciente": patient}
    if environment:
        params["ambiente"] = environment
    response = requests.get(
        f"{API_URL}/api/abc/analise",
        params=params,
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def load_abc_report_summary(
    patient: str,
    *,
    start_date: date,
    end_date: date,
    environment: str | None,
    include_weekends: bool,
    include_candidate_chains: bool,
    reviewed_chains_only: bool,
    include_charts: bool,
    anonymize_patient: bool,
    generated_by: str,
    output_format: str,
) -> dict:
    params = {
        "paciente": patient,
        "data_inicio": start_date.isoformat(),
        "data_fim": end_date.isoformat(),
        "incluir_finais_semana": include_weekends,
        "incluir_cadeias_candidatas": include_candidate_chains,
        "apenas_cadeias_revisadas": reviewed_chains_only,
        "incluir_graficos": include_charts,
        "anonimizar_paciente": anonymize_patient,
        "gerado_por": generated_by,
        "formato": output_format,
    }
    if environment:
        params["ambiente"] = environment
    response = requests.get(
        f"{API_URL}/api/abc/reports/summary",
        params=params,
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def load_abc_prediction(
    patient: str,
    behavior: str,
    antecedent: str | None = None,
    environment: str | None = None,
    classification: str | None = None,
    function: str | None = None,
) -> dict:
    params = {"paciente": patient, "comportamento": behavior}
    if antecedent:
        params["antecedente"] = antecedent
    if environment:
        params["ambiente"] = environment
    if classification:
        params["classificacao"] = classification
    if function:
        params["funcao"] = function
    response = requests.get(
        f"{API_URL}/api/abc/estimativa-descritiva",
        params=params,
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def load_abc_functional_ai(
    patient: str,
    *,
    chain: str | None,
    environment: str | None,
    question: str,
) -> dict:
    response = requests.post(
        f"{API_URL}/api/abc/analise-funcional-ia",
        json={
            "paciente": patient,
            "cadeia": chain,
            "ambiente": environment,
            "pergunta": question or None,
            "limite_fontes": 5,
        },
        timeout=max(API_TIMEOUT_SECONDS, 180),
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def detect_abc_temporal_chains(patient: str, config: dict) -> dict:
    response = requests.post(
        f"{API_URL}/api/abc/chains/detect",
        json={"paciente": patient, **config},
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def load_abc_temporal_chains(patient: str) -> dict:
    params = {"paciente": patient}
    responses = {
        "candidates": requests.get(f"{API_URL}/api/abc/chains/candidates", params=params, timeout=API_TIMEOUT_SECONDS),
        "stats": requests.get(f"{API_URL}/api/abc/chains/stats", params=params, timeout=API_TIMEOUT_SECONDS),
        "matrix": requests.get(f"{API_URL}/api/abc/chains/transition-matrix", params=params, timeout=API_TIMEOUT_SECONDS),
        "timeline": requests.get(f"{API_URL}/api/abc/chains/timeline", params=params, timeout=API_TIMEOUT_SECONDS),
    }
    for response in responses.values():
        if not response.ok:
            raise RuntimeError(_api_error_message(response))
    return {
        "candidates": responses["candidates"].json(),
        "stats": responses["stats"].json().get("stats", []),
        "matrix": responses["matrix"].json().get("matrix", []),
        "timeline": responses["timeline"].json().get("timeline", []),
    }


def review_abc_temporal_chain(candidate_id: str, status: str, reviewer: str, note: str) -> dict:
    response = requests.post(
        f"{API_URL}/api/abc/chains/{candidate_id}/review",
        json={"status": status, "revisado_por": reviewer, "observacao": note or None},
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def load_abc_chain_rules() -> dict:
    response = requests.get(f"{API_URL}/api/abc/config/chain-rules", timeout=API_TIMEOUT_SECONDS)
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def create_abc_chain_rule(from_code: str, to_code: str, rationale: str) -> dict:
    response = requests.post(
        f"{API_URL}/api/abc/config/chain-rules",
        json={
            "from_consequence_code": from_code,
            "to_antecedent_code": to_code,
            "relation_type": "clinical_review",
            "active": True,
            "rule_version": "1",
            "rationale": rationale,
        },
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def save_abc_record(patient: str, payload: dict) -> dict:
    response = requests.post(
        f"{API_URL}/api/abc/registros",
        json={"paciente": patient, **payload},
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def update_abc_record(patient: str, interval_id: str, payload: dict) -> dict:
    response = requests.patch(
        f"{API_URL}/api/abc/registros/{interval_id}",
        json={"paciente": patient, **payload},
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def create_abc_category(patient: str, name: str, category_type: str) -> dict:
    response = requests.post(
        f"{API_URL}/api/abc/categorias",
        json={"paciente": patient, "nome": name, "tipo": category_type},
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def delete_abc_record(patient: str, interval_id: str) -> dict:
    response = requests.delete(
        f"{API_URL}/api/abc/registros/{interval_id}",
        params={"paciente": patient},
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    return response.json()


def load_abc_excel(patient: str) -> tuple[bytes, str]:
    response = requests.get(
        f"{API_URL}/api/abc/excel",
        params={"paciente": patient},
        timeout=API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(_api_error_message(response))
    disposition = response.headers.get("content-disposition", "")
    filename = "registro_abc.xlsx"
    if "filename=" in disposition:
        filename = disposition.split("filename=", 1)[1].strip().strip('"')
    return response.content, filename


def _abc_options(categories: list[dict], category_type: str) -> tuple[list[str], dict[str, str]]:
    rows = [item for item in categories if item.get("tipo") == category_type]
    labels = [str(item.get("nome") or item.get("codigo")) for item in rows]
    codes = {str(item.get("nome") or item.get("codigo")): str(item.get("codigo")) for item in rows}
    return labels, codes


def _resolve_abc_code(patient: str, selected_label: str, typed_name: str, category_type: str, codes: dict[str, str]) -> str:
    if selected_label != NEW_ABC_OPTION:
        return codes[selected_label]
    clean_name = typed_name.strip()
    if not clean_name:
        raise ValueError(f"Digite a nova opção de {category_type}.")
    category = create_abc_category(patient, clean_name, category_type)
    return str(category["codigo"])


def _abc_record_month_key(record: dict) -> str:
    return str(record.get("data_hora") or record.get("data") or "")[:7]


def _abc_month_choices(records: list[dict]) -> dict[str, str | None]:
    months = sorted(
        {key for record in records if len(key := _abc_record_month_key(record)) == 7},
        reverse=True,
    )
    return {
        "Todos os meses": None,
        **{f"{key[5:7]}/{key[:4]}": key for key in months},
    }


def _abc_records_in_month(records: list[dict], month_key: str | None) -> list[dict]:
    selected = records if month_key is None else [
        record for record in records if _abc_record_month_key(record) == month_key
    ]
    return sorted(selected, key=lambda record: str(record.get("data_hora") or ""), reverse=True)


def _abc_record_option_label(record: dict) -> str:
    interval_id = str(record.get("intervalo_id") or "")
    return (
        f"{record.get('data', '-')} {record.get('hora', '-')} | "
        f"{record.get('antecedente', '-')} -> {record.get('comportamento', '-')} -> "
        f"{record.get('consequencia', '-')} | ID {interval_id[-8:]}"
    )


def _abc_added_at_label(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return "-"


def render_forecast_chart(
    observed: pd.DataFrame,
    forecast: pd.DataFrame,
    value_col: str,
    title: str,
    y_title: str,
    observed_name: str,
    forecast_name: str,
    color: str,
    target: float | None = None,
) -> None:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=observed["month"],
            y=observed[value_col],
            mode="lines+markers",
            name=observed_name,
            line=dict(color=color, width=3),
            marker=dict(size=8),
        )
    )
    if not forecast.empty:
        fig.add_trace(
            go.Scatter(
                x=forecast["month"],
                y=forecast["upper"],
                mode="lines",
                line=dict(width=0),
                hoverinfo="skip",
                showlegend=False,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=forecast["month"],
                y=forecast["lower"],
                mode="lines",
                fill="tonexty",
                fillcolor=PASTEL_BAND,
                line=dict(width=0),
                name="Faixa provável (80%)",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=forecast["month"],
                y=forecast["prediction"],
                mode="lines+markers",
                name=forecast_name,
                line=dict(color=PASTEL_FORECAST, dash="dash", width=3),
                marker=dict(size=8),
            )
        )
    if target is not None:
        fig.add_hline(y=target, line_dash="dot", line_color="#9f6727", annotation_text=f"Critério {target:.0f}%")

    fig.update_layout(title=title, xaxis_title="Mês", yaxis_title=y_title)
    st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")


def prepare_skill_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["month"] = month_start(out["date"])
    out["independent_rate"] = pd.to_numeric(out.get("independent_rate"), errors="coerce").clip(0, 100)
    out["prompt_rate"] = pd.to_numeric(out.get("prompt_rate"), errors="coerce").clip(0, 100)
    out["programa"] = out.get("programa", "").astype(str).str.strip()
    return out.dropna(subset=["date", "month", "independent_rate"])


def prepare_behavior_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["month"] = month_start(out["date"])
    out["count"] = pd.to_numeric(out.get("count"), errors="coerce").fillna(0).clip(lower=0)
    out["rate"] = pd.to_numeric(out.get("rate"), errors="coerce").fillna(0).clip(lower=0)
    out["comportamento"] = out.get("comportamento", "").astype(str).str.strip()
    return out.dropna(subset=["date", "month"])


def filter_by_period(df: pd.DataFrame, period: tuple[date, date]) -> pd.DataFrame:
    if df.empty or not period or len(period) != 2:
        return df
    start, end = pd.to_datetime(period[0]), pd.to_datetime(period[1])
    return df[(df["date"] >= start) & (df["date"] <= end)].copy()


def monthly_skill(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby("month", as_index=False)
        .agg(independencia=("independent_rate", "mean"), ajuda=("prompt_rate", "mean"), sessoes=("independent_rate", "count"))
        .sort_values("month")
    )


def monthly_behavior(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    agg = {"count": "sum", "rate": "mean"}[metric]
    return df.groupby("month", as_index=False).agg(valor=(metric, agg), registros=(metric, "count")).sort_values("month")


def date_period_default(df_a: pd.DataFrame, df_b: pd.DataFrame) -> tuple[date, date]:
    dates = []
    for df in [df_a, df_b]:
        if not df.empty and "date" in df:
            dates.extend(pd.to_datetime(df["date"], errors="coerce").dropna().tolist())
    if not dates:
        today = date.today()
        return today, today
    return min(dates).date(), max(dates).date()


def target_month(forecast: pd.DataFrame, target: float) -> str:
    if forecast.empty:
        return "sem projeção"
    hit = forecast[forecast["prediction"] >= target]
    if hit.empty:
        return "não estimado no horizonte"
    return month_label(hit.iloc[0]["month"])


def render_skill_prediction(df_skills: pd.DataFrame, df_lib: pd.DataFrame, period: tuple[date, date]) -> None:
    st.subheader("Previsão de habilidades e objetivos")
    st.caption("Modelo matemático: média mensal de independência, regressão linear por mês e probabilidade normal calibrada pelo erro histórico.")

    df_period = filter_by_period(df_skills, period)
    if df_period.empty:
        st.info("Sem dados de habilidades/objetivos para o período selecionado.")
        return

    programs = sorted(df_period["programa"].dropna().unique().tolist())
    selected = st.selectbox("Objetivo/programa para previsão detalhada", ["Média geral dos objetivos"] + programs)
    horizon = st.slider("Meses projetados", 1, 12, 6, key="skill_horizon")
    criterion = st.slider("Critério de independência esperado (%)", 50, 100, 80, 5, key="skill_criterion")

    df_selected = df_period if selected == "Média geral dos objetivos" else df_period[df_period["programa"] == selected]
    monthly = monthly_skill(df_selected)
    model = fit_linear_forecast(monthly.rename(columns={"independencia": "valor"}), "valor", horizon, bounds=(0, 100))
    observed = model["observed"].rename(columns={"valor": "independencia"})
    forecast = model["forecast"]

    current = float(monthly["independencia"].iloc[-1])
    next_prediction = float(forecast["prediction"].iloc[0]) if not forecast.empty else current
    probability_goal = probability_at_least(criterion, next_prediction, model["sigma"])
    probability_maintenance = probability_at_least(current, next_prediction, model["sigma"])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_metric_card("Independência atual", pct(current), "média do último mês observado")
    with c2:
        render_metric_card("Tendência mensal", signed_pct(model["beta1"]), "coeficiente β1 da regressão")
    with c3:
        render_metric_card("P(atingir critério)", pct(probability_goal), f"próximo mês >= {criterion}%")
    with c4:
        render_metric_card("Mês provável do critério", target_month(forecast, criterion), "com base na projeção linear")

    render_forecast_chart(
        observed,
        forecast,
        "independencia",
        "Trajetória prevista de independência",
        "Independência média (%)",
        "Observado",
        "Previsto",
        PASTEL_SKILL,
        target=criterion,
    )

    render_formula(
        "Equações usadas",
        [
            "1) y_t = β0 + β1.t, onde y_t é a independência média mensal.",
            f"2) β0 = {model['beta0']:.2f}; β1 = {model['beta1']:.2f} ponto percentual por mês; R² = {model['r2']:.2f}.",
            f"3) P(y >= critério) = 1 - Φ((critério - previsão) / σ), com σ = {model['sigma']:.2f}.",
            f"4) Exemplo deste objetivo: próximo mês = {next_prediction:.1f}% e P(manter/ultrapassar mês atual) = {probability_maintenance:.1f}%.",
        ],
    )

    table = forecast.copy()
    if not table.empty:
        table["Mês"] = table["month"].map(month_label)
        table["Previsão"] = table["prediction"]
        table["Faixa 80%"] = table.apply(lambda r: f"{r['lower']:.1f}% - {r['upper']:.1f}%", axis=1)
        table["P >= critério"] = table["prediction"].map(lambda v: probability_at_least(criterion, v, model["sigma"]))
        render_html_table(table, ["Mês", "Previsão", "Faixa 80%", "P >= critério"], {"Previsão": pct, "P >= critério": pct})

    st.markdown("### Priorização analítica dos objetivos")
    ranking_rows = []
    for program in programs:
        df_program = df_period[df_period["programa"] == program]
        monthly_program = monthly_skill(df_program)
        if monthly_program.empty:
            continue
        program_model = fit_linear_forecast(monthly_program.rename(columns={"independencia": "valor"}), "valor", 1, bounds=(0, 100))
        program_forecast = program_model["forecast"]
        last_value = float(monthly_program["independencia"].iloc[-1])
        next_value = float(program_forecast["prediction"].iloc[0]) if not program_forecast.empty else last_value
        objective = ""
        if not df_lib.empty and "name" in df_lib.columns and "objective" in df_lib.columns:
            matches = df_lib[df_lib["name"].astype(str) == program]
            if not matches.empty:
                objective = str(matches.iloc[0].get("objective") or "")[:180]
        ranking_rows.append(
            {
                "Objetivo": program,
                "Atual": last_value,
                "Próximo mês": next_value,
                "Tendência": program_model["beta1"],
                "P >= critério": probability_at_least(criterion, next_value, program_model["sigma"]),
                "Resumo clínico": objective or "Objetivo não informado na biblioteca.",
            }
        )

    ranking = pd.DataFrame(ranking_rows).sort_values(["P >= critério", "Tendência"], ascending=[False, False])
    render_html_table(
        ranking,
        ["Objetivo", "Atual", "Próximo mês", "Tendência", "P >= critério", "Resumo clínico"],
        {"Atual": pct, "Próximo mês": pct, "Tendência": signed_pct, "P >= critério": pct},
        max_rows=10,
    )


def render_abc_temporal_chains(patient: str, categories: list[dict]) -> None:
    st.markdown("### Cadeias temporais entre episódios")
    st.warning(
        "As cadeias apresentadas são padrões descritivos temporais e não confirmam causa ou função comportamental."
    )
    st.caption(
        "Semântica: Bₙ → Cₙ ⇒ Aₙ₊₁ → Bₙ₊₁. A consequência do mesmo episódio nunca é usada "
        "para explicar o comportamento que já ocorreu."
    )

    with st.expander("Regras consequência → antecedente"):
        st.caption(
            "Códigos idênticos são aceitos automaticamente. Para códigos diferentes, registre uma equivalência "
            "clínica explícita e justificada antes da detecção."
        )
        consequences = [item for item in categories if item.get("tipo") == "consequencia"]
        antecedents = [item for item in categories if item.get("tipo") == "antecedente"]
        consequence_labels = {f"{item.get('nome')} [{item.get('codigo')}]": str(item.get("codigo")) for item in consequences}
        antecedent_labels = {f"{item.get('nome')} [{item.get('codigo')}]": str(item.get("codigo")) for item in antecedents}
        if consequence_labels and antecedent_labels:
            rule_col_1, rule_col_2 = st.columns(2)
            with rule_col_1:
                from_label = st.selectbox("Consequência de origem", list(consequence_labels), key="abc_rule_from")
            with rule_col_2:
                to_label = st.selectbox("Antecedente seguinte", list(antecedent_labels), key="abc_rule_to")
            rationale = st.text_input(
                "Justificativa da equivalência",
                placeholder="Ex.: a retirada da demanda passa a compor o contexto antecedente do episódio seguinte.",
                max_chars=2000,
                key="abc_rule_rationale",
            )
            if st.button("Adicionar regra mapeada", use_container_width=True, key="abc_add_chain_rule"):
                if len(rationale.strip()) < 5:
                    st.error("Descreva brevemente por que a transição deve ser considerada.")
                else:
                    try:
                        create_abc_chain_rule(
                            consequence_labels[from_label],
                            antecedent_labels[to_label],
                            rationale.strip(),
                        )
                        st.success("Regra versionada registrada. Recalcule as cadeias para aplicá-la.")
                    except Exception as exc:
                        st.error(f"Não foi possível salvar a regra: {exc}")
        try:
            rule_data = load_abc_chain_rules()
            rule_rows = pd.DataFrame(rule_data.get("rules", []))
            if not rule_rows.empty:
                rule_rows = rule_rows.rename(
                    columns={
                        "from_consequence_code": "Consequência",
                        "to_antecedent_code": "Antecedente",
                        "relation_type": "Relação",
                        "rule_version": "Versão",
                        "active": "Ativa",
                        "rationale": "Justificativa",
                    }
                )
                render_html_table(rule_rows, ["Consequência", "Antecedente", "Relação", "Versão", "Ativa", "Justificativa"])
        except Exception as exc:
            st.caption(f"As regras existentes não puderam ser consultadas agora: {exc}")

    control_1, control_2, control_3 = st.columns(3)
    with control_1:
        max_lag = st.slider(
            "Defasagem máxima (segundos)",
            min_value=1,
            max_value=1800,
            value=300,
            step=1,
            key="abc_chain_max_lag",
        )
    with control_2:
        min_confidence = st.slider(
            "Confiança mínima",
            min_value=0.0,
            max_value=1.0,
            value=0.90,
            step=0.05,
            key="abc_chain_min_confidence",
        )
    with control_3:
        min_repetitions = st.number_input(
            "Repetições para estabilidade",
            min_value=1,
            max_value=100,
            value=3,
            step=1,
            key="abc_chain_min_repetitions",
        )
    allow_cross_session = st.toggle(
        "Permitir transição entre sessões clínicas diferentes",
        value=False,
        key="abc_chain_allow_cross_session",
        help="Permanece desligado por padrão. Registros legados contínuos do mesmo dia e ambiente são identificados separadamente.",
    )
    if st.button(
        "Detectar e recalcular cadeias temporais",
        type="primary",
        use_container_width=True,
        key="abc_detect_temporal_chains",
    ):
        try:
            with st.spinner("Ordenando eventos e validando elos temporais..."):
                result = detect_abc_temporal_chains(
                    patient,
                    {
                        "max_lag_seconds": max_lag,
                        "min_confidence": min_confidence,
                        "allow_cross_session_chain": allow_cross_session,
                        "break_on_not_observed": True,
                        "chain_min_repetitions": int(min_repetitions),
                    },
                )
            st.session_state[f"abc_chain_detection::{patient}"] = result
            st.success(
                f"{result.get('episodes_examined', 0)} episódios examinados. "
                f"Configuração versionada: {result.get('config_version', '-')}"
            )
        except Exception as exc:
            st.error(f"Não foi possível detectar as cadeias temporais: {exc}")

    try:
        data = load_abc_temporal_chains(patient)
    except Exception as exc:
        st.info(f"A análise temporal será exibida após a primeira detecção: {exc}")
        return

    candidates = pd.DataFrame(data["candidates"])
    stats = pd.DataFrame(data["stats"])
    if candidates.empty:
        current_candidates = candidates
        superseded_candidates = candidates
    else:
        superseded_mask = candidates["rejection_reason"].eq("superseded_detection")
        current_candidates = candidates[~superseded_mask].copy()
        superseded_candidates = candidates[superseded_mask].copy()
    valid_count = 0 if current_candidates.empty else int(current_candidates["validation_status"].isin(["candidate", "accepted"]).sum())
    accepted_count = 0 if current_candidates.empty else int((current_candidates["validation_status"] == "accepted").sum())
    censored_count = 0 if current_candidates.empty else int((current_candidates["validation_status"] == "censored").sum())
    stable_count = 0 if stats.empty else int((~stats["insufficient_sample"].astype(bool)).sum())
    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    with metric_1:
        render_metric_card("Elos válidos", str(valid_count), "candidatos + aceitos")
    with metric_2:
        render_metric_card("Revisados e aceitos", str(accepted_count), "revisão humana")
    with metric_3:
        render_metric_card("Censurados", str(censored_count), "lacunas ou tempo desconhecido")
    with metric_4:
        render_metric_card("Evidência estável", str(stable_count), "amostra, sessões e períodos")

    if current_candidates.empty:
        st.info("Ainda não há candidatos temporais persistidos para este paciente.")
        return

    visible = current_candidates.copy()
    visible["cadeia_temporal"] = (
        visible["origin_behavior_code"].fillna("?")
        + " → "
        + visible["from_consequence_code"].fillna("?")
        + " ⇒ "
        + visible["to_antecedent_code"].fillna("?")
        + " → "
        + visible["next_behavior_code"].fillna("?")
    )
    visible["validation_status"] = visible["validation_status"].replace(
        {
            "candidate": "Pendente de revisão",
            "accepted": "Aceita",
            "rejected": "Rejeitada",
            "censored": "Censurada",
        }
    )
    visible["session_relation"] = visible["session_relation"].replace(
        {
            "same_session": "Mesma sessão",
            "legacy_contiguous": "Continuidade legada",
            "cross_session_allowed": "Entre sessões permitido",
        }
    )
    visible["rejection_reason"] = visible["rejection_reason"].replace(
        {
            "same_interval": "Mesmo intervalo bloqueado",
            "cross_session_blocked": "Sessões diferentes bloqueadas",
            "transition_rule_missing": "Sem regra de transição",
            "low_confidence": "Confiança insuficiente",
            "antecedent_unknown": "Antecedente desconhecido",
            "next_behavior_unknown": "Comportamento seguinte desconhecido",
            "after_landmark": "Elo posterior ao landmark",
            "human_review": "Rejeitada em revisão humana",
            "superseded_detection": "Detecção histórica substituída pelo recálculo atual",
        }
    )
    visible["rejection_reason"] = visible["rejection_reason"].fillna("")
    visible.loc[
        visible["validation_status"] == "Pendente de revisão",
        "rejection_reason",
    ] = "Regra de transição atendida; aguardando revisão"
    visible.loc[
        visible["validation_status"] == "Aceita",
        "rejection_reason",
    ] = "Regra de transição atendida e revisada"
    visible.loc[visible["rejection_reason"] == "", "rejection_reason"] = "Sem motivo adicional"
    visible = visible.rename(
        columns={
            "cadeia_temporal": "Cadeia temporal",
            "delta_seconds": "Δ segundos",
            "chain_confidence": "Confiança",
            "validation_status": "Status",
            "session_relation": "Relação de sessão",
            "rejection_reason": "Motivo",
            "environment": "Ambiente",
        }
    )
    render_html_table(
        visible,
        ["Cadeia temporal", "Ambiente", "Δ segundos", "Confiança", "Status", "Relação de sessão", "Motivo"],
        {"Confiança": lambda value: "-" if pd.isna(value) else f"{float(value) * 100:.1f}%"},
        max_rows=20,
    )
    if not superseded_candidates.empty:
        with st.expander(f"Consultar detecções históricas substituídas ({len(superseded_candidates)})"):
            st.caption(
                "Estes elos pertencem a recálculos anteriores. Foram preservados para auditoria e não entram nas métricas atuais."
            )

    matrix = pd.DataFrame(data["matrix"])
    if not matrix.empty:
        matrix_table = matrix.pivot_table(
            index="from_consequence_code",
            columns="to_antecedent_code",
            values="probability",
            fill_value=0,
        )
        fig_matrix = go.Figure(
            data=go.Heatmap(
                z=matrix_table.values * 100,
                x=matrix_table.columns.tolist(),
                y=matrix_table.index.tolist(),
                colorscale=[[0, "#f7f1e6"], [0.5, "#d9c58e"], [1, "#bd7d75"]],
                colorbar=dict(title="P(A seguinte | C anterior) %"),
                hovertemplate="C: %{y}<br>A seguinte: %{x}<br>Probabilidade: %{z:.1f}%<extra></extra>",
            )
        )
        fig_matrix.update_layout(
            title="Matriz de transição consequência → antecedente",
            xaxis_title="Antecedente do episódio seguinte",
            yaxis_title="Consequência do episódio anterior",
            height=max(360, 56 * len(matrix_table.index)),
        )
        st.plotly_chart(apply_chart_theme(fig_matrix), theme=None, width="stretch")

    timeline = pd.DataFrame(data["timeline"])
    if not timeline.empty:
        timeline["start_ts"] = pd.to_datetime(timeline["start_ts"], errors="coerce")
        timeline["episodio"] = (
            timeline["antecedent_code"].fillna("?")
            + " → "
            + timeline["behavior_code"].fillna("?")
            + " → "
            + timeline["consequence_code"].fillna("?")
        )
        timeline["ordem"] = range(1, len(timeline) + 1)
        fig_timeline = go.Figure(
            go.Scatter(
                x=timeline["start_ts"],
                y=timeline["ordem"],
                mode="lines+markers",
                marker=dict(size=12, color="#6f86a4", line=dict(color="#fffdf8", width=2)),
                customdata=timeline[["episodio", "environment", "status"]],
                hovertemplate="%{customdata[0]}<br>Ambiente: %{customdata[1]}<br>Status: %{customdata[2]}<extra></extra>",
            )
        )
        fig_timeline.update_layout(
            title="Linha do tempo ABC sequencial",
            xaxis_title="Data e hora completas",
            yaxis_title="Ordem dos episódios",
            height=420,
        )
        st.plotly_chart(apply_chart_theme(fig_timeline), theme=None, width="stretch")

    if not stats.empty:
        stats_visible = stats.rename(
            columns={
                "from_consequence_code": "C anterior",
                "to_antecedent_code": "A seguinte",
                "next_behavior_code": "B seguinte",
                "n_exposures": "Exposições",
                "n_transitions": "Transições",
                "p_transition": "P(transição)",
                "p_behavior_given_chain": "P(B|cadeia)",
                "lift": "Lift",
                "odds_ratio": "OR",
                "risk_ratio": "RR",
                "phi": "Phi",
                "fisher_exact_pvalue": "Fisher p",
                "stability_score": "Estabilidade",
                "evidence_quality": "Qualidade",
            }
        )
        render_html_table(
            stats_visible,
            ["C anterior", "A seguinte", "B seguinte", "Exposições", "Transições", "P(transição)", "P(B|cadeia)", "Lift", "OR", "RR", "Phi", "Fisher p", "Estabilidade", "Qualidade"],
            {
                "P(transição)": lambda value: "-" if pd.isna(value) else f"{float(value) * 100:.1f}%",
                "P(B|cadeia)": lambda value: "-" if pd.isna(value) else f"{float(value) * 100:.1f}%",
                "Estabilidade": lambda value: "-" if pd.isna(value) else f"{float(value) * 100:.1f}%",
            },
            max_rows=20,
        )

    reviewable = current_candidates[current_candidates["validation_status"] == "candidate"]
    with st.expander("Revisão humana dos candidatos"):
        if reviewable.empty:
            st.caption("Não há candidatos pendentes de revisão.")
        else:
            labels = {
                (
                    f"{row.get('completed_at', '-')} | {row.get('from_consequence_code', '?')} → "
                    f"{row.get('to_antecedent_code', '?')} → {row.get('next_behavior_code', '?')}"
                ): str(row["id"])
                for _, row in reviewable.iterrows()
            }
            selected = st.selectbox("Candidato", list(labels), key="abc_chain_review_candidate")
            decision = st.radio(
                "Decisão",
                ["Aceitar hipótese descritiva", "Rejeitar candidato"],
                horizontal=True,
                key="abc_chain_review_decision",
            )
            reviewer = st.text_input("Responsável pela revisão", key="abc_chain_reviewer", max_chars=160)
            note = st.text_area("Justificativa da revisão", key="abc_chain_review_note", max_chars=2000)
            if st.button("Registrar revisão", use_container_width=True, key="abc_chain_review_submit"):
                if len(reviewer.strip()) < 2:
                    st.error("Informe o responsável pela revisão.")
                else:
                    try:
                        review_abc_temporal_chain(
                            labels[selected],
                            "accepted" if decision.startswith("Aceitar") else "rejected",
                            reviewer.strip(),
                            note.strip(),
                        )
                        st.success("Revisão registrada com data, responsável e histórico de status.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Não foi possível registrar a revisão: {exc}")

    with st.expander("Quais fórmulas temporais estamos usando"):
        render_formula_blocks(
            "Cadeias entre episódios",
            [
                ("Defasagem", "Δ = início(Aₙ₊₁) − fim(Cₙ); 0 ≤ Δ ≤ Δmáx.", "Usa onset/offset real; quando ausente, usa os limites do intervalo."),
                ("Transição", "P(Aₙ₊₁|Cₙ) = n(Cₙ→Aₙ₊₁) / n(Cₙ).", "Mede quantas exposições à consequência foram seguidas pelo antecedente elegível."),
                ("Comportamento seguinte", "P(Bₙ₊₁|Cₙ,Aₙ₊₁) = n(C→A→B) / n(C→A).", "Não usa a consequência do mesmo episódio para prever Bₙ."),
                ("Diferença e lift", "DR = P(B|cadeia) − P(B); Lift = P(B|cadeia) / P(B).", "Compara o elo temporal ao baseline pessoal."),
                ("Tabela 2×2", "OR=(ad)/(bc); RR=[a/(a+b)]/[c/(c+d)]; φ=(ad−bc)/√((a+b)(c+d)(a+c)(b+d)).", "Células zero recebem correção de continuidade 0,5; Fisher é calculado para amostras pequenas."),
                ("Estabilidade", "S = ∛(repetição × cobertura de sessões × cobertura temporal).", "Amostras pequenas permanecem marcadas como insuficientes."),
            ],
        )


def render_abc_registration_and_analysis(patient: str) -> None:
    st.subheader("Análise ABC e cadeias fechadas")
    st.caption(
        "Registre a cadeia A-B-C com precisão de segundos. O Supabase, os gráficos, as probabilidades e o Excel do paciente são atualizados a cada ação."
    )

    saved_message = st.session_state.pop("abc_saved_message", None)
    if saved_message:
        st.success(saved_message)
    saved_warning = st.session_state.pop("abc_saved_warning", None)
    if saved_warning:
        st.warning(saved_warning)

    try:
        categories = load_abc_categories()
        analysis_all = load_abc_analysis(patient)
    except Exception as exc:
        st.error(f"Não foi possível carregar o módulo ABC: {exc}")
        return

    antecedent_labels, antecedent_codes = _abc_options(categories, "antecedente")
    behavior_labels, behavior_codes = _abc_options(categories, "comportamento")
    consequence_labels, consequence_codes = _abc_options(categories, "consequencia")
    if not antecedent_labels or not behavior_labels or not consequence_labels:
        st.warning("As categorias fechadas de antecedente, comportamento e consequência ainda não estão completas.")
        return

    antecedent_labels = antecedent_labels + [NEW_ABC_OPTION]
    behavior_labels = behavior_labels + [NEW_ABC_OPTION]
    consequence_labels = consequence_labels + [NEW_ABC_OPTION]

    st.markdown("### Registrar acontecimento")
    c1, c2, c3 = st.columns(3)
    with c1:
        antecedent = st.selectbox("Antecedente", antecedent_labels, key="abc_antecedent")
        new_antecedent = ""
        if antecedent == NEW_ABC_OPTION:
            new_antecedent = st.text_input(
                "Digite o novo antecedente",
                max_chars=200,
                placeholder="Ex.: mudança inesperada",
                key="abc_new_antecedent",
            )
    with c2:
        behavior = st.selectbox("Comportamento", behavior_labels, key="abc_behavior")
        new_behavior = ""
        if behavior == NEW_ABC_OPTION:
            new_behavior = st.text_input(
                "Digite o novo comportamento",
                max_chars=200,
                placeholder="Ex.: jogar material no chão",
                key="abc_new_behavior",
            )
    with c3:
        consequence = st.selectbox("Consequência", consequence_labels, key="abc_consequence")
        new_consequence = ""
        if consequence == NEW_ABC_OPTION:
            new_consequence = st.text_input(
                "Digite a nova consequência",
                max_chars=200,
                placeholder="Ex.: retirada da atividade",
                key="abc_new_consequence",
            )

    default_environments = ["Sala de terapia", "Sala de aula", "Casa", "Refeitório", "Área externa"]
    observed_environments = analysis_all.get("ambientes_observados", [])
    environment_options = list(dict.fromkeys([*default_environments, *observed_environments]))
    new_environment_option = "Digitar novo ambiente..."
    environment_options.append(new_environment_option)

    c4, c5, c6, c7 = st.columns([1, 1, 0.65, 1.2])
    with c4:
        record_date = st.date_input(
            "Data do registro",
            value=date.today(),
            format="DD/MM/YYYY",
            key="abc_record_date",
        )
    with c5:
        record_time_text = st.text_input(
            "Hora e minuto",
            value=datetime.now().strftime("%H:%M"),
            placeholder="Ex.: 9:30, 0930 ou 9h30",
            help="Ao digitar, o horário é interpretado automaticamente.",
            key="abc_record_time",
        )
    with c6:
        record_second = st.number_input(
            "Segundo",
            min_value=0,
            max_value=59,
            value=datetime.now().second,
            step=1,
            key="abc_record_second",
        )
    with c7:
        environment_option = st.selectbox("Ambiente", environment_options, key="abc_environment")
        custom_environment = ""
        if environment_option == new_environment_option:
            custom_environment = st.text_input(
                "Digite o novo ambiente",
                max_chars=160,
                placeholder="Ex.: pátio coberto",
                key="abc_new_environment",
            )

    st.markdown("#### Classificação do comportamento interferente")
    severity_col, function_col = st.columns([1, 1.4])
    with severity_col:
        classification_label = st.radio(
            "Nível observado",
            ["C1 - leve", "C2 - intenso"],
            horizontal=True,
            key="abc_classification",
            help="C1: sem lesão, sangramento ou direção a ponto vital. C2: ao menos um desses critérios ocorreu.",
        )
    function_options = list(
        dict.fromkeys(
            [
                *ABC_FUNCTIONS,
                *[
                    item
                    for item in analysis_all.get("funcoes_observadas", [])
                    if item and item != "Não informada"
                ],
                NEW_FUNCTION_OPTION,
            ]
        )
    )
    with function_col:
        function_option = st.selectbox(
            "Função informada (hipótese funcional)",
            function_options,
            key="abc_function",
        )
        custom_function = ""
        if function_option == NEW_FUNCTION_OPTION:
            custom_function = st.text_input(
                "Digite a função ou hipótese",
                max_chars=120,
                placeholder="Ex.: acesso a atenção de pares",
                key="abc_new_function",
            )

    caused_injury = False
    bleeding = False
    targeted_vital_point = False
    if classification_label.startswith("C2"):
        st.caption("Marque ao menos um critério observado para classificar como C2 - intenso.")
        criterion_1, criterion_2, criterion_3 = st.columns(3)
        with criterion_1:
            caused_injury = st.checkbox("Machucou ou feriu", key="abc_caused_injury")
        with criterion_2:
            bleeding = st.checkbox("Houve sangramento", key="abc_bleeding")
        with criterion_3:
            targeted_vital_point = st.checkbox(
                "Direcionado a ponto vital",
                key="abc_targeted_vital_point",
            )
    else:
        st.caption("C1 - leve: não machucou, não feriu, não sangrou e não foi direcionado a ponto vital.")

    submitted = st.button(
        "Adicionar acontecimento ABC",
        type="primary",
        use_container_width=True,
        key="abc_add_record",
    )

    if submitted:
        environment = custom_environment.strip() if environment_option == new_environment_option else environment_option
        selected_function = custom_function.strip() if function_option == NEW_FUNCTION_OPTION else function_option
        if not environment:
            st.error("Informe o ambiente antes de adicionar.")
        elif not selected_function:
            st.error("Informe a função ou hipótese funcional.")
        elif classification_label.startswith("C2") and not (
            caused_injury or bleeding or targeted_vital_point
        ):
            st.error("C2 exige ao menos um critério objetivo de intensidade.")
        else:
            try:
                antecedent_code = _resolve_abc_code(
                    patient, antecedent, new_antecedent, "antecedente", antecedent_codes
                )
                behavior_code = _resolve_abc_code(
                    patient, behavior, new_behavior, "comportamento", behavior_codes
                )
                consequence_code = _resolve_abc_code(
                    patient, consequence, new_consequence, "consequencia", consequence_codes
                )
                parsed_record_time = parse_hour_minute_text(record_time_text)
                save_result = save_abc_record(
                    patient,
                    {
                        "antecedente_codigo": antecedent_code,
                        "comportamento_codigo": behavior_code,
                        "consequencia_codigo": consequence_code,
                        "data": record_date.isoformat(),
                        "hora": parsed_record_time.replace(second=int(record_second)).strftime("%H:%M:%S"),
                        "ambiente": environment,
                        "classificacao": classification_label[:2],
                        "causou_lesao": caused_injury,
                        "houve_sangramento": bleeding,
                        "direcionado_ponto_vital": targeted_vital_point,
                        "funcao": selected_function,
                    },
                )
                if save_result.get("excel_warning"):
                    st.session_state["abc_saved_warning"] = save_result["excel_warning"]
                    st.session_state["abc_saved_message"] = "Registro ABC adicionado e gráficos atualizados."
                elif save_result.get("excel_status") == "queued":
                    st.session_state["abc_saved_message"] = (
                        "Registro ABC adicionado. Os gráficos foram atualizados e o Excel está sendo gerado em segundo plano."
                    )
                else:
                    st.session_state["abc_saved_message"] = "Registro ABC adicionado. Os gráficos e o Excel foram atualizados."
                st.rerun()
            except Exception as exc:
                st.error(f"Não foi possível adicionar o registro: {exc}")

    total_all = int(analysis_all.get("total_registros", 0))

    excel_col, path_col = st.columns([1, 2])
    try:
        excel_bytes, excel_name = load_abc_excel(patient)
        with excel_col:
            st.download_button(
                "Baixar Excel do paciente",
                data=excel_bytes,
                file_name=excel_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with path_col:
            st.caption(f"Cópia automática no projeto: {analysis_all.get('excel_path', '-')}")
            st.caption("Após incluir, editar ou remover, a cópia é atualizada em segundo plano.")
            st.caption("Arquivo de saída: alterações feitas no Excel não são importadas pela aplicação.")
    except Exception as exc:
        st.warning(f"O Excel ainda não pôde ser disponibilizado: {exc}")

    if total_all == 0:
        st.markdown("### Imprimir análise ABC")
        st.button(
            "Preparar relatório ABC em Word e PDF",
            type="primary",
            use_container_width=True,
            disabled=True,
            key="abc_prepare_empty_prediction_report",
        )
        st.info(
            "Ainda não há registros ABC para este paciente. Adicione o primeiro acontecimento para habilitar "
            "os gráficos, as previsões e o relatório para impressão."
        )
        return

    st.markdown("### Explorar os registros")
    analysis_environment = st.selectbox(
        "Ambiente da análise",
        ["Todos os ambientes", *analysis_all.get("ambientes_observados", [])],
        key="abc_analysis_environment",
    )
    if analysis_environment == "Todos os ambientes":
        analysis = analysis_all
    else:
        try:
            analysis = load_abc_analysis(patient, analysis_environment)
        except Exception as exc:
            st.error(f"Não foi possível aplicar o filtro de ambiente: {exc}")
            return

    total = int(analysis.get("total_registros", 0))
    if total == 0:
        st.info("Não há registros para o ambiente selecionado.")
        return

    latest = (analysis.get("registros_recentes") or [{}])[0]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_metric_card("Registros ABC", str(total), "intervalos observados")
    with c2:
        render_metric_card("Comportamentos", str(analysis.get("comportamentos_distintos", 0)), "categorias observadas")
    with c3:
        render_metric_card("Ambientes", str(analysis.get("ambientes_distintos", 0)), "contextos registrados")
    with c4:
        render_metric_card("Último registro", str(latest.get("data", "-")), str(latest.get("hora", "-")))

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        render_metric_card("C1 - leve", str(analysis.get("c1_leve", 0)), "sem critério de dano")
    with s2:
        render_metric_card("C2 - intenso", str(analysis.get("c2_intenso", 0)), "classificação de gravidade registrada")
    with s3:
        render_metric_card("Sem classificação", str(analysis.get("nao_classificados", 0)), "edite os registros históricos")
    with s4:
        render_metric_card("Funções", str(len(analysis.get("funcoes_observadas", []))), "hipóteses informadas")

    report_chains = list(analysis.get("cadeias_completas") or [])
    report_chain_names = [str(item.get("cadeia")) for item in report_chains if item.get("cadeia")]
    report_chain_name = str(st.session_state.get("abc_selected_chain") or "")
    if report_chain_name not in report_chain_names:
        report_chain_name = report_chain_names[0] if report_chain_names else ""
    report_chain = next(
        (item for item in report_chains if str(item.get("cadeia")) == report_chain_name),
        {},
    )

    report_behaviors = list(analysis_all.get("comportamentos_observados") or [])
    report_behavior = str(st.session_state.get("abc_prediction_behavior") or "")
    if report_behavior not in report_behaviors:
        report_behavior = report_behaviors[0] if report_behaviors else ""
    report_antecedent = str(st.session_state.get("abc_prediction_antecedent") or "Qualquer antecedente")
    if report_antecedent not in ["Qualquer antecedente", *analysis_all.get("antecedentes_observados", [])]:
        report_antecedent = "Qualquer antecedente"
    report_prediction_environment = str(st.session_state.get("abc_prediction_environment") or "Qualquer ambiente")
    if report_prediction_environment not in ["Qualquer ambiente", *analysis_all.get("ambientes_observados", [])]:
        report_prediction_environment = "Qualquer ambiente"
    report_classification = str(st.session_state.get("abc_prediction_classification") or "Qualquer classificação")
    if report_classification not in {"Qualquer classificação", "C1", "C2"}:
        report_classification = "Qualquer classificação"
    report_function = str(st.session_state.get("abc_prediction_function") or "Qualquer função")
    if report_function not in ["Qualquer função", *analysis_all.get("funcoes_observadas", [])]:
        report_function = "Qualquer função"

    report_prediction = {}
    if report_behavior:
        try:
            report_prediction = load_abc_prediction(
                patient,
                report_behavior,
                None if report_antecedent == "Qualquer antecedente" else report_antecedent,
                None if report_prediction_environment == "Qualquer ambiente" else report_prediction_environment,
                None if report_classification == "Qualquer classificação" else report_classification,
                None if report_function == "Qualquer função" else report_function,
            )
        except Exception:
            report_prediction = {}

    report_environments = list(analysis_all.get("ambientes_observados") or [])
    report_risk_environment = str(st.session_state.get("abc_heatmap_environment") or "")
    if report_risk_environment not in report_environments:
        if analysis_environment != "Todos os ambientes" and analysis_environment in report_environments:
            report_risk_environment = analysis_environment
        else:
            report_risk_environment = report_environments[0] if report_environments else ""

    report_ai_environment = None if analysis_environment == "Todos os ambientes" else analysis_environment
    report_ai_key = f"abc_functional_ai::{patient}::{report_chain_name}::{report_ai_environment or ''}"
    report_ai_result = st.session_state.get(report_ai_key)

    st.markdown("### Resumo ABC para impressão")
    st.write(
        "Gere o relatório descritivo completo para analistas do comportamento. O corpo apresenta a formulação clínica; "
        "todas as cadeias e os indicadores técnicos permanecem nos apêndices."
    )
    report_date_values = pd.to_datetime(
        [item.get("data") for item in analysis_all.get("serie_temporal", [])], errors="coerce"
    )
    report_date_values = report_date_values[~pd.isna(report_date_values)]
    default_report_start = report_date_values.min().date() if len(report_date_values) else date.today()
    default_report_end = report_date_values.max().date() if len(report_date_values) else date.today()

    with st.container(border=True):
        report_period = st.date_input(
            "Selecionar período",
            value=(default_report_start, default_report_end),
            format="DD/MM/YYYY",
            key="abc_print_report_period",
        )
        if isinstance(report_period, (tuple, list)) and len(report_period) == 2:
            report_start_date, report_end_date = report_period
        else:
            report_start_date = report_end_date = report_period

        report_chain_scope_label = st.radio(
            "Cadeias A-B-C do mesmo registro",
            ["Uma cadeia específica", "Todas as cadeias comportamentais"],
            index=1,
            horizontal=True,
            key="abc_report_chain_scope",
        )
        report_chain_scope = "selected" if report_chain_scope_label == "Uma cadeia específica" else "all"
        if report_chain_scope == "selected" and report_chain_names:
            report_chain_name = st.selectbox(
                "Cadeia para o relatório",
                report_chain_names,
                index=report_chain_names.index(report_chain_name) if report_chain_name in report_chain_names else 0,
                key="abc_report_selected_chain",
            )
        elif report_chain_names:
            reference_chain = max(report_chains, key=lambda item: int(item.get("suporte") or 0))
            report_chain_name = str(reference_chain.get("cadeia") or "")
            st.caption(
                f"As {len(report_chain_names)} cadeias do recorte serão listadas. A mais frequente será apenas a referência visual."
            )
        else:
            report_chain_name = ""
            st.info("Ainda não há uma cadeia completa disponível neste recorte.")

        chain_filter_col, identity_col = st.columns(2)
        with chain_filter_col:
            temporal_chain_filter = st.radio(
                "Cadeias temporais",
                ["Revisadas e candidatas", "Apenas revisadas e aceitas"],
                key="abc_print_temporal_chain_filter",
            )
        with identity_col:
            report_generated_by = st.text_input(
                "Responsável pela geração",
                value="Usuário local",
                max_chars=160,
                key="abc_print_generated_by",
            )

        option_col1, option_col2, option_col3 = st.columns(3)
        with option_col1:
            report_include_charts = st.toggle("Incluir gráficos", value=True, key="abc_print_include_charts")
            report_include_weekends = st.toggle("Incluir finais de semana", value=True, key="abc_print_weekends")
        with option_col2:
            report_include_explanation = st.toggle(
                "Incluir explicação do ABC fechado", value=True, key="abc_print_explanation"
            )
            report_include_limitations = st.toggle(
                "Incluir limitações clínicas", value=True, key="abc_print_limitations"
            )
        with option_col3:
            report_anonymize = st.toggle(
                "Ocultar identificação do paciente", value=False, key="abc_print_anonymize"
            )

    report_chain = next(
        (item for item in report_chains if str(item.get("cadeia")) == report_chain_name),
        {},
    )
    report_ai_key = f"abc_functional_ai::{patient}::{report_chain_name}::{report_ai_environment or ''}"
    report_ai_result = st.session_state.get(report_ai_key)
    report_reviewed_only = temporal_chain_filter == "Apenas revisadas e aceitas"
    report_include_candidates = not report_reviewed_only
    report_environment_param = None if analysis_environment == "Todos os ambientes" else analysis_environment
    report_state_key = "abc_prediction_report_" + str(
        hash(
            (
                patient,
                report_start_date,
                report_end_date,
                analysis_environment,
                report_chain_scope,
                report_chain_name,
                report_reviewed_only,
                report_include_charts,
                report_include_weekends,
                report_include_explanation,
                report_include_limitations,
                report_anonymize,
                report_generated_by,
                total,
            )
        )
    )
    preview_state_key = f"{report_state_key}_preview"
    preview_col, print_col = st.columns(2)
    with preview_col:
        preview_clicked = st.button(
            "Visualizar resumo",
            use_container_width=True,
            key="abc_preview_printable_summary",
        )
    with print_col:
        print_clicked = st.button(
            "Imprimir resumo ABC",
            type="primary",
            use_container_width=True,
            key="abc_prepare_prediction_report",
        )

    if preview_clicked or print_clicked:
        try:
            with st.spinner("Preparando o resumo clínico ABC..."):
                report_summary = load_abc_report_summary(
                    patient,
                    start_date=report_start_date,
                    end_date=report_end_date,
                    environment=report_environment_param,
                    include_weekends=report_include_weekends,
                    include_candidate_chains=report_include_candidates,
                    reviewed_chains_only=report_reviewed_only,
                    include_charts=report_include_charts,
                    anonymize_patient=report_anonymize,
                    generated_by=report_generated_by or "Usuário local",
                    output_format="pdf" if print_clicked else "preview",
                )
                st.session_state[preview_state_key] = report_summary
                if print_clicked:
                    try:
                        report_temporal_data = load_abc_temporal_chains(patient)
                    except Exception:
                        report_temporal_data = {"candidates": [], "stats": [], "matrix": [], "timeline": []}
                    filtered_analysis = report_summary.get("analysis") or analysis
                    filtered_chains = list(filtered_analysis.get("cadeias_completas") or [])
                    filtered_chain = next(
                        (item for item in filtered_chains if str(item.get("cadeia")) == report_chain_name),
                        max(filtered_chains, key=lambda item: int(item.get("suporte") or 0)) if filtered_chains else {},
                    )
                    filtered_observation = report_summary.get("observation_summary") or {}
                    filtered_behavior = (report_summary.get("most_frequent") or {}).get("behavior") or {}
                    filtered_total = int(filtered_analysis.get("total_registros") or filtered_observation.get("total_records") or 0)
                    filtered_behavior_count = int(filtered_behavior.get("quantidade") or 0)
                    report_prediction_for_period = {
                        **report_prediction,
                        "analysis_mode": "descriptive",
                        "comportamento": filtered_behavior.get("nome") or report_prediction.get("comportamento") or "-",
                        "numerador": filtered_behavior_count,
                        "denominador": filtered_total,
                        "estimativa_descritiva": (filtered_behavior_count / filtered_total) if filtered_total else None,
                        "metodo": "proporção descritiva do recorte filtrado",
                        "intervalo_agrupado_sessao": report_summary.get("top_behavior_session_cluster_interval") or {},
                    }
                    report_payload = {
                        "patient": report_summary.get("patient", {}).get("display_name") or patient,
                        "environment": analysis_environment,
                        "risk_environment": report_risk_environment,
                        "analysis": filtered_analysis,
                        "analysis_all": filtered_analysis,
                        "chain_scope": report_chain_scope,
                        "selected_chain": filtered_chain,
                        "prediction": report_prediction_for_period,
                        "temporal_data": report_temporal_data,
                        "report_summary": report_summary,
                        "include_charts": report_include_charts,
                        "include_abc_explanation": report_include_explanation,
                        "include_limitations": report_include_limitations,
                        "ai_result": report_ai_result,
                    }
                    report_docx_bytes = build_abc_report_docx(report_payload)
                    st.session_state[report_state_key] = {
                        "docx": report_docx_bytes,
                        "pdf": build_abc_report_pdf_from_docx(report_docx_bytes),
                    }
        except Exception as exc:
            st.error(f"Não foi possível preparar o resumo ABC: {exc}")

    report_preview = st.session_state.get(preview_state_key)
    if report_preview:
        st.markdown("#### Pré-visualização do resumo")
        st.write(report_preview.get("descriptive_summary") or "Sem resumo disponível.")
        preview_observation = report_preview.get("observation_summary") or {}
        preview_quality = report_preview.get("data_quality") or {}
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Sessões", int(preview_observation.get("sessions") or 0))
        p2.metric("Intervalos observados", int(preview_observation.get("observed_intervals") or 0))
        p3.metric("Cobertura", f"{float(preview_observation.get('coverage') or 0) * 100:.1f}%")
        p4.metric("Qualidade", str(preview_quality.get("status") or "insuficiente").capitalize())
        for warning in preview_quality.get("warnings") or []:
            st.warning(warning)
        st.caption(report_preview.get("clinical_disclaimer") or "")

    generated_abc_report = st.session_state.get(report_state_key)
    if generated_abc_report:
        st.success("Resumo ABC pronto para salvar ou imprimir.")
        report_filename_patient = "Paciente_anonimizado" if report_anonymize else patient
        report_word_col, report_pdf_col = st.columns(2)
        with report_word_col:
            st.download_button(
                "Baixar Word ABC",
                data=generated_abc_report["docx"],
                file_name=abc_report_filename(report_filename_patient, "docx"),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with report_pdf_col:
            st.download_button(
                "Salvar como PDF",
                data=generated_abc_report["pdf"],
                file_name=abc_report_filename(report_filename_patient, "pdf"),
                mime="application/pdf",
                use_container_width=True,
            )
    st.caption(
        "Use “Imprimir resumo ABC” para gerar o Word e o PDF completos. A prévia é apenas uma síntese na tela. "
        "Gere novamente após alterar período ou opções."
    )

    series = pd.DataFrame(analysis.get("serie_temporal", []))
    if not series.empty:
        series["data_exibicao"] = pd.to_datetime(series["data"], errors="coerce").dt.strftime("%d/%m/%Y")
        date_order = series.drop_duplicates("data").sort_values("data")["data_exibicao"].tolist()
        fig = go.Figure()
        colors = ["#c17c74", "#7d9b76", "#6f86a4", "#b58a55", "#8a7a9d", "#6f9b96"]
        for index, behavior_name in enumerate(series["comportamento"].drop_duplicates()):
            item = series[series["comportamento"] == behavior_name]
            fig.add_trace(
                go.Bar(
                    x=item["data_exibicao"],
                    y=item["quantidade"],
                    name=behavior_name,
                    marker_color=colors[index % len(colors)],
                )
            )
        fig.update_layout(
            title="Frequência de comportamentos por data",
            xaxis_title="Data",
            yaxis_title="Quantidade de registros",
            barmode="stack",
        )
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=date_order)
        st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")

    timeline = pd.DataFrame(analysis.get("linha_tempo", []))
    if not timeline.empty:
        timeline["data_hora"] = pd.to_datetime(timeline["data_hora"], errors="coerce")
        fig = go.Figure()
        timeline_colors = ["#c17c74", "#7d9b76", "#6f86a4", "#b58a55", "#8a7a9d", "#6f9b96"]
        for index, environment_name in enumerate(timeline["ambiente"].drop_duplicates()):
            item = timeline[timeline["ambiente"] == environment_name]
            fig.add_trace(
                go.Scatter(
                    x=item["data_hora"],
                    y=item["cadeia"],
                    mode="markers",
                    name=environment_name,
                    marker=dict(
                        size=13,
                        color=timeline_colors[index % len(timeline_colors)],
                        symbol=item["classificacao"].map(
                            {"C1 - leve": "circle", "C2 - intenso": "triangle-up", "Não classificado": "x"}
                        ).fillna("x"),
                        line=dict(width=1, color="#fffaf2"),
                    ),
                    customdata=item[["hora", "ambiente", "classificacao", "funcao"]],
                    hovertemplate=(
                        "%{y}<br>%{customdata[0]}<br>%{customdata[1]}<br>"
                        "%{customdata[2]}<br>Função: %{customdata[3]}<extra></extra>"
                    ),
                )
            )
        fig.update_layout(title="Linha do tempo das cadeias por segundo", xaxis_title="Data e hora", yaxis_title="Cadeia A-B-C")
        fig.update_xaxes(tickformat="%d/%m/%Y<br>%H:%M:%S")
        st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")

    antecedent_associations = pd.DataFrame(analysis.get("antecedente_comportamento", []))
    consequence_associations = pd.DataFrame(analysis.get("comportamento_consequencia", []))
    chart_left, chart_right = st.columns(2)
    with chart_left:
        if not antecedent_associations.empty:
            view = antecedent_associations.head(10).copy()
            view["associacao"] = view["antecedente"] + " -> " + view["comportamento"]
            view["probabilidade"] = view["probabilidade_condicional"] * 100
            fig = go.Figure(
                go.Bar(
                    x=view["probabilidade"],
                    y=view["associacao"],
                    orientation="h",
                    marker_color=PASTEL_SKILL,
                    customdata=view[["suporte", "lift"]],
                    hovertemplate="P(B|A)=%{x:.1f}%<br>n=%{customdata[0]}<br>lift=%{customdata[1]:.2f}<extra></extra>",
                )
            )
            fig.update_layout(title="P(comportamento | antecedente)", xaxis_title="Probabilidade condicional (%)", yaxis_title="")
            fig.update_xaxes(range=[0, 100])
            st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")
    with chart_right:
        if not consequence_associations.empty:
            view = consequence_associations.head(10).copy()
            view["associacao"] = view["comportamento"] + " -> " + view["consequencia"]
            view["probabilidade"] = view["probabilidade_condicional"] * 100
            fig = go.Figure(
                go.Bar(
                    x=view["probabilidade"],
                    y=view["associacao"],
                    orientation="h",
                    marker_color=PASTEL_BEHAVIOR,
                    customdata=view[["suporte", "lift"]],
                    hovertemplate="P(C|B)=%{x:.1f}%<br>n=%{customdata[0]}<br>lift=%{customdata[1]:.2f}<extra></extra>",
                )
            )
            fig.update_layout(title="P(consequencia | comportamento)", xaxis_title="Probabilidade condicional (%)", yaxis_title="")
            fig.update_xaxes(range=[0, 100])
            st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")

    environments = pd.DataFrame(analysis.get("por_ambiente", []))
    if not environments.empty:
        fig = go.Figure(
            go.Bar(
                x=environments["ambiente"],
                y=environments["quantidade"],
                marker_color=PASTEL_FORECAST,
            )
        )
        fig.update_layout(title="Distribuição dos registros por ambiente", xaxis_title="Ambiente", yaxis_title="Registros")
        st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")

    severity_data = pd.DataFrame(analysis.get("por_classificacao", []))
    function_data = pd.DataFrame(analysis.get("por_funcao", []))
    severity_chart, function_chart = st.columns(2)
    with severity_chart:
        if not severity_data.empty:
            severity_data["rotulo"] = severity_data["classificacao"].map(
                {"C1": "C1 - leve", "C2": "C2 - intenso", "NC": "Não classificado"}
            ).fillna(severity_data["classificacao"])
            severity_colors = severity_data["classificacao"].map(
                {"C1": "#7d9b76", "C2": "#c17c74", "NC": "#9b948b"}
            ).fillna("#9b948b")
            fig = go.Figure(
                go.Bar(
                    x=severity_data["rotulo"],
                    y=severity_data["quantidade"],
                    marker_color=severity_colors,
                )
            )
            fig.update_layout(title="Classificação dos interferentes", xaxis_title="Nível", yaxis_title="Registros")
            st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")
    with function_chart:
        if not function_data.empty:
            fig = go.Figure(
                go.Bar(
                    x=function_data["quantidade"],
                    y=function_data["funcao"],
                    orientation="h",
                    marker_color="#6f86a4",
                )
            )
            fig.update_layout(title="Funções informadas", xaxis_title="Registros", yaxis_title="")
            st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")

    st.markdown("### Mapa de frequência e gravidade configurada por local")
    st.caption(
        "O eixo X mostra a frequência observada da cadeia; o eixo Y mostra o peso médio configurado de gravidade. "
        "O tamanho do ponto representa suporte. C1/C2 são categorias registradas, não probabilidades clínicas."
    )
    map_environment = st.selectbox(
        "Local do mapa de calor",
        analysis_all.get("ambientes_observados", []),
        key="abc_heatmap_environment",
    )
    risk_map = pd.DataFrame(analysis_all.get("mapa_risco", []))
    if not risk_map.empty:
        risk_map = risk_map[risk_map["ambiente"] == map_environment].copy()
    if risk_map.empty:
        st.info("Ainda não há cadeias suficientes no local selecionado para montar o mapa de risco.")
    else:
        risk_map["ocorrencia_pct"] = risk_map["probabilidade_ocorrencia"] * 100
        severity_column = "peso_medio_gravidade" if "peso_medio_gravidade" in risk_map.columns else "indice_perigo"
        risk_map["gravidade_configurada"] = risk_map[severity_column].fillna(0)
        max_support = max(1, int(risk_map["suporte"].max()))
        risk_map["tamanho"] = 14 + (risk_map["suporte"] / max_support) * 18
        fig = go.Figure()
        quadrant_specs = [
            (0, 50, 0, 0.5, "rgba(188, 216, 190, 0.30)", "Menor frequência / menor peso"),
            (0, 50, 0.5, 1.0, "rgba(236, 197, 178, 0.28)", "Menor frequência / maior peso"),
            (50, 100, 0, 0.5, "rgba(235, 220, 164, 0.30)", "Maior frequência / menor peso"),
            (50, 100, 0.5, 1.0, "rgba(207, 137, 130, 0.28)", "Maior frequência / maior peso"),
        ]
        for x0, x1, y0, y1, color, label in quadrant_specs:
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=color, line_width=0, layer="below")
            fig.add_annotation(
                x=(x0 + x1) / 2,
                y=y1 - 0.05,
                text=label,
                showarrow=False,
                font=dict(size=11, color="#5d5145"),
                bgcolor="rgba(255,253,248,0.74)",
            )
        symbol_specs = {
            "C1": ("circle", "#587957", "C1 - leve"),
            "C2": ("triangle-up", "#a94f49", "C2 - intenso"),
            "NC": ("x", "#6f6b66", "Não classificado"),
        }
        for classification_code, (symbol, color, label) in symbol_specs.items():
            item = risk_map[risk_map["classificacao_predominante"] == classification_code]
            if item.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=item["ocorrencia_pct"],
                    y=item["gravidade_configurada"],
                    mode="markers+text",
                    name=label,
                    text=item["comportamento"],
                    textposition="top center",
                    textfont=dict(size=10, color="#33291f"),
                    marker=dict(
                        symbol=symbol,
                        size=item["tamanho"],
                        color=color,
                        line=dict(color="#fffdf8", width=2),
                        opacity=0.92,
                    ),
                    customdata=item[["cadeia", "funcao", "suporte", "c1_leve", "c2_intenso", "nao_classificado"]],
                    hovertemplate=(
                        "%{customdata[0]}<br>Função: %{customdata[1]}<br>"
                        "Frequência no local: %{x:.1f}%<br>Peso médio configurado: %{y:.2f}<br>"
                        "n=%{customdata[2]} | C1=%{customdata[3]} | C2=%{customdata[4]} | sem classe=%{customdata[5]}"
                        "<extra></extra>"
                    ),
                )
            )
        fig.add_vline(x=50, line_width=1, line_dash="dash", line_color="#8f8171")
        fig.add_hline(y=0.5, line_width=1, line_dash="dash", line_color="#8f8171")
        fig.update_layout(
            title=f"Frequência observada × peso de gravidade em {map_environment}",
            xaxis_title="Frequência observada da cadeia no local (%)",
            yaxis_title="Peso médio configurado de gravidade",
            hovermode="closest",
            legend_title="Classificação predominante",
            height=620,
        )
        fig.update_xaxes(range=[-2, 102], dtick=10)
        fig.update_yaxes(range=[-0.02, 1.02], dtick=0.1)
        st.plotly_chart(apply_chart_theme(fig), theme=None, width="stretch")
        if int(risk_map["nao_classificado"].sum()) > 0:
            st.info("Há registros oficiais sem C1/C2. Eles permanecem contabilizados separadamente e reduzem a qualidade da evidência.")

    chains = pd.DataFrame(analysis.get("cadeias_completas", []))
    selected_chain_row = None
    selected_chain = None
    if not chains.empty:
        selected_chain = st.selectbox(
            "Cadeia completa para análise detalhada",
            chains["cadeia"].tolist(),
            key="abc_selected_chain",
        )
        selected_chain_row = chains[chains["cadeia"] == selected_chain].iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            render_metric_card("P(A-B-C)", pct(float(selected_chain_row["probabilidade_conjunta"]) * 100), "frequência conjunta observada")
        with c2:
            render_metric_card("P(B | A)", pct(float(selected_chain_row["probabilidade_comportamento_dado_antecedente"]) * 100), "comportamento após antecedente")
        with c3:
            render_metric_card("P(C | A,B)", pct(float(selected_chain_row["probabilidade_consequencia_dada_cadeia_ab"]) * 100), "consequência após A-B")
        with c4:
            render_metric_card("Suporte", str(int(selected_chain_row["suporte"])), "cadeias completas observadas")

        risk_c1, risk_c2, risk_c3 = st.columns(3)
        with risk_c1:
            render_metric_card("C1 - leve", str(int(selected_chain_row.get("c1_leve", 0))), "na cadeia selecionada")
        with risk_c2:
            render_metric_card("C2 - intenso", str(int(selected_chain_row.get("c2_intenso", 0))), "na cadeia selecionada")
        with risk_c3:
            render_metric_card(
                "Hipótese mais registrada",
                str(selected_chain_row.get("funcao_predominante", "Não informada")),
                f"peso médio configurado = {float(selected_chain_row.get('peso_medio_gravidade') or selected_chain_row.get('indice_perigo') or 0):.2f}",
            )

        chain_table = chains.rename(
            columns={
                "cadeia": "Cadeia A-B-C",
                "suporte": "n",
                "probabilidade_conjunta": "P(A-B-C)",
                "probabilidade_comportamento_dado_antecedente": "P(B|A)",
                "probabilidade_consequencia_dada_cadeia_ab": "P(C|A,B)",
                "lift_conjunto": "Lift conjunto",
                "c1_leve": "C1",
                "c2_intenso": "C2",
                "indice_perigo": "Peso médio configurado",
                "funcao_predominante": "Função",
            }
        )
        render_html_table(
            chain_table,
            ["Cadeia A-B-C", "Função", "C1", "C2", "Peso médio configurado", "n", "P(A-B-C)", "P(B|A)", "P(C|A,B)", "Lift conjunto"],
            {
                "P(A-B-C)": lambda value: pct(float(value) * 100),
                "P(B|A)": lambda value: pct(float(value) * 100),
                "P(C|A,B)": lambda value: pct(float(value) * 100),
                "Peso médio configurado": lambda value: "-" if pd.isna(value) else f"{float(value):.2f}",
                "Lift conjunto": lambda value: "-" if pd.isna(value) else f"{float(value):.2f}",
            },
            max_rows=12,
        )

    st.markdown("### Estimativa descritiva em registros semelhantes")
    st.caption(
        "Os registros legados são oficiais e entram no cálculo após normalização auditável. Como a base contém episódios, "
        "mas não todas as oportunidades negativas observáveis, esta seção não estima o risco absoluto do próximo registro."
    )
    prediction_formula_blocks: list[tuple[str, str, str]] = []
    prediction_behaviors = analysis_all.get("comportamentos_observados", [])
    if prediction_behaviors:
        p1, p2, p3 = st.columns(3)
        with p1:
            prediction_behavior = st.selectbox("Comportamento a descrever", prediction_behaviors, key="abc_prediction_behavior")
        with p2:
            prediction_antecedent = st.selectbox(
                "Antecedente do recorte",
                ["Qualquer antecedente", *analysis_all.get("antecedentes_observados", [])],
                key="abc_prediction_antecedent",
            )
        with p3:
            prediction_environment = st.selectbox(
                "Ambiente do recorte",
                ["Qualquer ambiente", *analysis_all.get("ambientes_observados", [])],
                key="abc_prediction_environment",
            )
        p4, p5 = st.columns(2)
        with p4:
            prediction_classification = st.selectbox(
                "Classificação para estratificação",
                ["Qualquer classificação", "C1", "C2"],
                key="abc_prediction_classification",
            )
        with p5:
            prediction_function = st.selectbox(
                "Hipótese registrada para estratificação",
                ["Qualquer função", *analysis_all.get("funcoes_observadas", [])],
                key="abc_prediction_function",
            )
        try:
            prediction = load_abc_prediction(
                patient,
                prediction_behavior,
                None if prediction_antecedent == "Qualquer antecedente" else prediction_antecedent,
                None if prediction_environment == "Qualquer ambiente" else prediction_environment,
                None if prediction_classification == "Qualquer classificação" else prediction_classification,
                None if prediction_function == "Qualquer função" else prediction_function,
            )
            c1, c2, c3, c4 = st.columns(4)
            descriptive_value = prediction.get("estimativa_descritiva", prediction.get("probabilidade_prevista"))
            with c1:
                render_metric_card("Frequência observada", pct(float(descriptive_value) * 100) if descriptive_value is not None else "-", prediction_behavior)
            with c2:
                render_metric_card("k / n", f"{int(prediction.get('numerador') or 0)} / {int(prediction.get('denominador') or 0)}", "episódios registrados")
            with c3:
                render_metric_card(
                    "IC de Wilson",
                    f"{pct(float(prediction['intervalo_wilson_inferior']) * 100)}–{pct(float(prediction['intervalo_wilson_superior']) * 100)}" if prediction.get("intervalo_wilson_inferior") is not None else "-",
                    "proporção binomial bruta",
                )
            with c4:
                render_metric_card("Qualidade", str(prediction.get("qualidade_evidencia", "inicial")), "regras auditáveis")

            e1, e2, e3 = st.columns(3)
            with e1:
                render_metric_card("Histórico observado", pct(float(prediction["probabilidade_baseline"]) * 100), f"n total = {prediction.get('amostra_total', 0)}")
            with e2:
                context_value = prediction.get("probabilidade_contexto")
                render_metric_card("Contexto A + ambiente", pct(float(context_value) * 100) if context_value is not None else "-", f"n contexto = {prediction.get('amostra_contexto', 0)}")
            with e3:
                render_metric_card("Modo analítico", "Descritivo", "risco absoluto indisponível")

            prediction_formula_blocks = [
                (
                    "Estimativa frequencista",
                    "p = k / n; IC = intervalo de Wilson para a proporção binomial bruta.",
                    "Numerador, denominador, método e nível do intervalo são exibidos e reproduzíveis.",
                ),
                (
                    "Limite do estimando",
                    "Estimando = distribuição relativa entre episódios ABC registrados.",
                    "A ausência de registro não é convertida em ausência de comportamento nem em exemplo negativo.",
                ),
                (
                    "Uso clínico",
                    "Associação não demonstra causalidade nem confirma função comportamental.",
                    "O resultado não substitui avaliação funcional ou julgamento clínico individualizado.",
                ),
            ]
        except Exception as exc:
            st.warning(f"A estimativa descritiva ABC ainda não pôde ser calculada: {exc}")

    with st.expander("Expandir análise funcional exploratória"):
        st.markdown(
            "Esta leitura organiza padrões de antecedente, comportamento, consequência, ambiente e tempo. Ela pode orientar hipóteses para avaliação funcional, mas não determina a função do comportamento."
        )
        if selected_chain_row is not None:
            chain_environment = pd.DataFrame(analysis_all.get("cadeias_por_ambiente", []))
            chain_environment = chain_environment[chain_environment["cadeia"] == selected_chain_row["cadeia"]]
            if not chain_environment.empty:
                chain_environment = chain_environment.rename(
                    columns={
                        "ambiente": "Ambiente",
                        "suporte": "n",
                        "total_ambiente": "Registros no ambiente",
                        "probabilidade_no_ambiente": "P(cadeia|ambiente)",
                        "funcao_predominante": "Função",
                        "c1_leve": "C1",
                        "c2_intenso": "C2",
                        "indice_perigo": "Peso médio configurado",
                    }
                )
                render_html_table(
                    chain_environment,
                    ["Ambiente", "Função", "C1", "C2", "Peso médio configurado", "n", "Registros no ambiente", "P(cadeia|ambiente)"],
                    {
                        "P(cadeia|ambiente)": lambda value: pct(float(value) * 100),
                        "Peso médio configurado": lambda value: "-" if pd.isna(value) else f"{float(value):.2f}",
                    },
                )
        st.markdown("#### Leitura assistida por IA")
        st.caption(
            "A IA recebe um resumo desidentificado dos registros e compara hipóteses concorrentes. "
            "O nome do paciente não é enviado ao provedor externo."
        )
        ai_question = st.text_area(
            "Pergunta ou foco para a IA (opcional)",
            placeholder=(
                "Ex.: compare fuga/esquiva e atenção social e diga quais dados ainda faltam para diferenciá-las."
            ),
            height=90,
            key="abc_functional_ai_question",
        )
        ai_environment = None if analysis_environment == "Todos os ambientes" else analysis_environment
        ai_state_key = f"abc_functional_ai::{patient}::{selected_chain or ''}::{ai_environment or ''}"
        if st.button(
            "Gerar leitura funcional com IA",
            type="primary",
            use_container_width=True,
            key="abc_generate_functional_ai",
        ):
            try:
                with st.spinner("Comparando padrões, hipóteses e limites da amostra..."):
                    st.session_state[ai_state_key] = load_abc_functional_ai(
                        patient,
                        chain=selected_chain,
                        environment=ai_environment,
                        question=ai_question,
                    )
            except Exception as exc:
                st.error(f"A leitura por IA não pôde ser gerada: {exc}")

        ai_result = st.session_state.get(ai_state_key)
        if ai_result:
            mode = str(ai_result.get("modo") or "indisponível")
            model = str(ai_result.get("modelo") or "")
            if mode in {"limite_ia", "busca_local_fallback", "sem_provedor"}:
                st.warning(str(ai_result.get("resposta") or "A IA não respondeu neste momento."))
            else:
                st.markdown(str(ai_result.get("resposta") or ""))
            provider_label = f"Provedor: {mode}"
            if model:
                provider_label += f" | Modelo: {model}"
            st.caption(f"{provider_label} | Dados desidentificados: sim")
            sources = list(ai_result.get("fontes") or [])
            if sources and st.toggle("Mostrar fontes locais consultadas", key=f"abc_ai_sources_{ai_state_key}"):
                for index, source in enumerate(sources, start=1):
                    st.markdown(
                        f"**Fonte {index}:** {source.get('titulo') or 'Trecho sem título'}  \n"
                        f"Arquivo: `{source.get('fonte', '-')}`"
                    )
        st.info(
            "Para concluir uma avaliação funcional, combine estes padrões com definição operacional, medidas de oportunidade, observação direta, concordância entre observadores e, quando indicado, procedimentos funcionais conduzidos por profissional habilitado."
        )

    render_abc_temporal_chains(patient, categories)

    with st.expander("Quais fórmulas estamos usando"):
        formula_blocks = [
            (
                "Comportamento após antecedente",
                "P(B|A,E) = n(A,B,E) / n(A,E).",
                "Proporção de exposições ao antecedente A, no ambiente E, seguidas pelo comportamento B.",
            ),
            (
                "Consequência após a cadeia A-B",
                "P(C|A,B,E) = n(A,B,C,E) / n(A,B,E).",
                "Proporção de pares A-B seguidos pela consequência C no mesmo ambiente.",
            ),
            (
                "Probabilidade da cadeia completa",
                "P(A,B,C|E) = P(A|E) × P(B|A,E) × P(C|A,B,E).",
                "Fatora a cadeia completa dentro do ambiente selecionado.",
            ),
            (
                "Gravidade configurada e risco exploratório",
                "D(C1)=0,20; D(C2)=1,00; R = P(A,B,C|E) × D.",
                "Mantém frequência e peso configurado de gravidade separados e os combina apenas no índice exploratório interno.",
            ),
            (
                "Lift da cadeia",
                "Lift = P(A,B,C) / [P(A) × P(B) × P(C)].",
                "Compara a cadeia observada com a frequência esperada se A, B e C fossem independentes.",
            ),
            (
                "Suavização para pouca amostra",
                "p_suavizada = (k + 1) / (n + 2).",
                "Wilson acompanha somente a proporção bruta k/n; a opção bayesiana usa posterior Beta e intervalo de credibilidade próprios.",
            ),
            *prediction_formula_blocks,
        ]
        render_formula_blocks("Probabilidades, risco e previsão", formula_blocks)
        st.caption(
            "Cada clique marca as três categorias escolhidas e as demais como não ocorridas no intervalo. "
            + str(analysis.get("aviso", "Associações descritivas não confirmam causa nem função comportamental."))
        )

    management_records = list(
        analysis_all.get("registros_para_gestao")
        or analysis_all.get("registros_recentes")
        or []
    )
    recently_added_records = list(analysis_all.get("registros_recentes") or [])
    if management_records:
        def records_table(records: list[dict]) -> pd.DataFrame:
            table = pd.DataFrame(records)
            table["criterios_c2"] = table.apply(
                lambda row: ", ".join(
                    label
                    for enabled, label in [
                        (row.get("causou_lesao"), "lesão/ferimento"),
                        (row.get("houve_sangramento"), "sangramento"),
                        (row.get("direcionado_ponto_vital"), "ponto vital"),
                    ]
                    if enabled
                )
                or "nenhum",
                axis=1,
            )
            created_values = table.get("criado_em", pd.Series(index=table.index, dtype=object))
            table["adicionado_em"] = created_values.map(_abc_added_at_label)
            return table.rename(
                columns={
                    "data": "Data",
                    "hora": "Hora",
                    "ambiente": "Ambiente",
                    "antecedente": "Antecedente",
                    "comportamento": "Comportamento",
                    "consequencia": "Consequência",
                    "classificacao_rotulo": "Classificação",
                    "funcao": "Função",
                    "criterios_c2": "Critérios C2",
                    "adicionado_em": "Adicionado em",
                }
            )

        st.markdown("#### Últimos registros adicionados")
        st.caption("Ordenados pela data e hora em que foram incluídos no sistema, do mais novo para o mais antigo.")
        render_html_table(
            records_table(recently_added_records),
            [
                "Adicionado em",
                "Data",
                "Hora",
                "Ambiente",
                "Antecedente",
                "Comportamento",
                "Classificação",
                "Função",
                "Consequência",
            ],
            max_rows=12,
        )

        month_choices = _abc_month_choices(management_records)
        with st.expander("Consultar histórico completo de acontecimentos"):
            selected_history_month = st.selectbox(
                "Mês dos acontecimentos",
                list(month_choices),
                key="abc_history_month",
            )
            history_records = _abc_records_in_month(
                management_records,
                month_choices[selected_history_month],
            )
            st.caption(f"{len(history_records)} acontecimento(s) no período selecionado.")
            render_html_table(
                records_table(history_records),
                [
                    "Data",
                    "Hora",
                    "Adicionado em",
                    "Ambiente",
                    "Antecedente",
                    "Comportamento",
                    "Classificação",
                    "Função",
                    "Consequência",
                    "Critérios C2",
                ],
                max_rows=500,
            )

        removal_options = {
            _abc_record_option_label(item): str(item.get("intervalo_id"))
            for item in management_records
        }
        with st.expander("Editar um acontecimento registrado"):
            edit_month_choices = _abc_month_choices(management_records)
            selected_edit_month = st.selectbox(
                "Mês do acontecimento para editar",
                list(edit_month_choices),
                key="abc_edit_month",
            )
            editable_records = _abc_records_in_month(
                management_records,
                edit_month_choices[selected_edit_month],
            )
            editing_options = {
                _abc_record_option_label(item): item
                for item in editable_records
            }
            selected_edit_label = st.selectbox(
                "Acontecimento para editar",
                list(editing_options),
                key="abc_edit_selection",
            )
            edit_item = editing_options[selected_edit_label]
            edit_id = str(edit_item.get("intervalo_id"))
            edit_suffix = edit_id[-8:]
            edit_timestamp = datetime.fromisoformat(str(edit_item["data_hora"]))

            edit_a_col, edit_b_col, edit_c_col = st.columns(3)
            edit_antecedent_options = antecedent_labels
            edit_behavior_options = behavior_labels
            edit_consequence_options = consequence_labels
            with edit_a_col:
                edit_antecedent = st.selectbox(
                    "Antecedente",
                    edit_antecedent_options,
                    index=edit_antecedent_options.index(edit_item["antecedente"])
                    if edit_item.get("antecedente") in edit_antecedent_options
                    else 0,
                    key=f"abc_edit_antecedent_{edit_suffix}",
                )
                edit_new_antecedent = ""
                if edit_antecedent == NEW_ABC_OPTION:
                    edit_new_antecedent = st.text_input(
                        "Novo antecedente",
                        max_chars=200,
                        key=f"abc_edit_new_antecedent_{edit_suffix}",
                    )
            with edit_b_col:
                edit_behavior = st.selectbox(
                    "Comportamento",
                    edit_behavior_options,
                    index=edit_behavior_options.index(edit_item["comportamento"])
                    if edit_item.get("comportamento") in edit_behavior_options
                    else 0,
                    key=f"abc_edit_behavior_{edit_suffix}",
                )
                edit_new_behavior = ""
                if edit_behavior == NEW_ABC_OPTION:
                    edit_new_behavior = st.text_input(
                        "Novo comportamento",
                        max_chars=200,
                        key=f"abc_edit_new_behavior_{edit_suffix}",
                    )
            with edit_c_col:
                edit_consequence = st.selectbox(
                    "Consequência",
                    edit_consequence_options,
                    index=edit_consequence_options.index(edit_item["consequencia"])
                    if edit_item.get("consequencia") in edit_consequence_options
                    else 0,
                    key=f"abc_edit_consequence_{edit_suffix}",
                )
                edit_new_consequence = ""
                if edit_consequence == NEW_ABC_OPTION:
                    edit_new_consequence = st.text_input(
                        "Nova consequência",
                        max_chars=200,
                        key=f"abc_edit_new_consequence_{edit_suffix}",
                    )

            edit_d1, edit_d2, edit_d3, edit_d4 = st.columns([1, 1, 0.65, 1.2])
            with edit_d1:
                edit_date = st.date_input(
                    "Data",
                    value=edit_timestamp.date(),
                    format="DD/MM/YYYY",
                    key=f"abc_edit_date_{edit_suffix}",
                )
            with edit_d2:
                edit_time_text = st.text_input(
                    "Hora e minuto",
                    value=edit_timestamp.strftime("%H:%M"),
                    placeholder="Ex.: 9:30, 0930 ou 9h30",
                    help="Ao digitar, o horário é interpretado automaticamente.",
                    key=f"abc_edit_time_{edit_suffix}",
                )
            with edit_d3:
                edit_second = st.number_input(
                    "Segundo",
                    min_value=0,
                    max_value=59,
                    value=edit_timestamp.second,
                    step=1,
                    key=f"abc_edit_second_{edit_suffix}",
                )
            with edit_d4:
                edit_environment = st.text_input(
                    "Ambiente",
                    value=str(edit_item.get("ambiente") or ""),
                    max_chars=160,
                    key=f"abc_edit_environment_{edit_suffix}",
                )

            edit_s1, edit_s2 = st.columns([1, 1.4])
            current_classification = str(edit_item.get("classificacao") or "C1")
            with edit_s1:
                edit_classification_label = st.radio(
                    "Classificação",
                    ["C1 - leve", "C2 - intenso"],
                    index=1 if current_classification == "C2" else 0,
                    horizontal=True,
                    key=f"abc_edit_classification_{edit_suffix}",
                )
            current_function = str(edit_item.get("funcao") or "Indeterminada / em avaliação")
            if current_function == "Não informada":
                current_function = "Indeterminada / em avaliação"
            edit_function_options = list(dict.fromkeys([*function_options[:-1], current_function, NEW_FUNCTION_OPTION]))
            with edit_s2:
                edit_function_option = st.selectbox(
                    "Função informada (hipótese funcional)",
                    edit_function_options,
                    index=edit_function_options.index(current_function),
                    key=f"abc_edit_function_{edit_suffix}",
                )
                edit_custom_function = ""
                if edit_function_option == NEW_FUNCTION_OPTION:
                    edit_custom_function = st.text_input(
                        "Nova função ou hipótese",
                        max_chars=120,
                        key=f"abc_edit_new_function_{edit_suffix}",
                    )

            edit_injury = False
            edit_bleeding = False
            edit_vital = False
            if edit_classification_label.startswith("C2"):
                ec1, ec2, ec3 = st.columns(3)
                with ec1:
                    edit_injury = st.checkbox(
                        "Machucou ou feriu",
                        value=bool(edit_item.get("causou_lesao")),
                        key=f"abc_edit_injury_{edit_suffix}",
                    )
                with ec2:
                    edit_bleeding = st.checkbox(
                        "Houve sangramento",
                        value=bool(edit_item.get("houve_sangramento")),
                        key=f"abc_edit_bleeding_{edit_suffix}",
                    )
                with ec3:
                    edit_vital = st.checkbox(
                        "Direcionado a ponto vital",
                        value=bool(edit_item.get("direcionado_ponto_vital")),
                        key=f"abc_edit_vital_{edit_suffix}",
                    )

            if st.button(
                "Salvar alterações do acontecimento",
                type="primary",
                use_container_width=True,
                key=f"abc_edit_save_{edit_suffix}",
            ):
                edit_selected_function = (
                    edit_custom_function.strip()
                    if edit_function_option == NEW_FUNCTION_OPTION
                    else edit_function_option
                )
                if not edit_environment.strip():
                    st.error("Informe o ambiente.")
                elif not edit_selected_function:
                    st.error("Informe a função ou hipótese funcional.")
                elif edit_classification_label.startswith("C2") and not (
                    edit_injury or edit_bleeding or edit_vital
                ):
                    st.error("C2 exige ao menos um critério objetivo de intensidade.")
                else:
                    try:
                        edit_antecedent_code = _resolve_abc_code(
                            patient,
                            edit_antecedent,
                            edit_new_antecedent,
                            "antecedente",
                            antecedent_codes,
                        )
                        edit_behavior_code = _resolve_abc_code(
                            patient,
                            edit_behavior,
                            edit_new_behavior,
                            "comportamento",
                            behavior_codes,
                        )
                        edit_consequence_code = _resolve_abc_code(
                            patient,
                            edit_consequence,
                            edit_new_consequence,
                            "consequencia",
                            consequence_codes,
                        )
                        parsed_edit_time = parse_hour_minute_text(edit_time_text)
                        update_result = update_abc_record(
                            patient,
                            edit_id,
                            {
                                "antecedente_codigo": edit_antecedent_code,
                                "comportamento_codigo": edit_behavior_code,
                                "consequencia_codigo": edit_consequence_code,
                                "data": edit_date.isoformat(),
                                "hora": parsed_edit_time.replace(second=int(edit_second)).strftime("%H:%M:%S"),
                                "ambiente": edit_environment.strip(),
                                "classificacao": edit_classification_label[:2],
                                "causou_lesao": edit_injury,
                                "houve_sangramento": edit_bleeding,
                                "direcionado_ponto_vital": edit_vital,
                                "funcao": edit_selected_function,
                            },
                        )
                        if update_result.get("excel_warning"):
                            st.session_state["abc_saved_warning"] = update_result["excel_warning"]
                            st.session_state["abc_saved_message"] = (
                                "Acontecimento editado. Gráficos, previsões e auditoria foram atualizados."
                            )
                        elif update_result.get("excel_status") == "queued":
                            st.session_state["abc_saved_message"] = (
                                "Acontecimento editado. Gráficos e previsões foram atualizados; o Excel está sendo gerado em segundo plano."
                            )
                        else:
                            st.session_state["abc_saved_message"] = (
                                "Acontecimento editado. Gráficos, previsões, auditoria e Excel foram atualizados."
                            )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Não foi possível editar o acontecimento: {exc}")

        with st.expander("Remover um acontecimento registrado"):
            selected_removal = st.selectbox("Acontecimento", list(removal_options), key="abc_removal_selection")
            st.caption("A remoção tira o acontecimento dos gráficos, mas permanece registrada no log de auditoria e no Excel.")
            if st.button("Remover acontecimento", type="secondary", use_container_width=True):
                try:
                    delete_result = delete_abc_record(patient, removal_options[selected_removal])
                    if delete_result.get("excel_warning"):
                        st.session_state["abc_saved_warning"] = delete_result["excel_warning"]
                        st.session_state["abc_saved_message"] = "Acontecimento removido e gráficos atualizados."
                    elif delete_result.get("excel_status") == "queued":
                        st.session_state["abc_saved_message"] = (
                            "Acontecimento removido. Os gráficos foram atualizados e o Excel está sendo gerado em segundo plano."
                        )
                    else:
                        st.session_state["abc_saved_message"] = "Acontecimento removido. Os gráficos e o Excel foram atualizados."
                    st.rerun()
                except Exception as exc:
                    st.error(f"Não foi possível remover o acontecimento: {exc}")


def render_behavior_prediction(patient: str, df_behavior: pd.DataFrame, period: tuple[date, date]) -> None:
    st.subheader("Previsão de dados comportamentais e comportamento-problema")
    st.caption("Modelo matemático: agregação mensal, regressão log-linear para dados não negativos e probabilidade de aumento/redução.")

    df_period = filter_by_period(df_behavior, period)
    if df_period.empty:
        st.info("Sem dados comportamentais para o período selecionado.")
        return

    behaviors = sorted(df_period["comportamento"].dropna().unique().tolist())
    selected = st.selectbox("Comportamento-problema para previsão detalhada", ["Total geral"] + behaviors)
    metric_label = st.radio("Medida principal", ["Taxa média", "Contagem total"], horizontal=True)
    metric = "rate" if metric_label == "Taxa média" else "count"
    c_cfg1, c_cfg2, c_cfg3 = st.columns(3)
    with c_cfg1:
        horizon = st.slider("Meses projetados", 1, 12, 6, key="behavior_horizon")
    with c_cfg2:
        recent_n = st.slider("N sessões recentes", 2, 20, 5, key="behavior_recent_n")
    with c_cfg3:
        alpha = st.slider("Alpha da média exponencial", 0.05, 0.90, 0.30, 0.05, key="behavior_alpha")
    reduction_target = st.slider("Meta de redução no próximo mês (%)", 5, 80, 20, 5, key="behavior_reduction")

    df_selected = df_period if selected == "Total geral" else df_period[df_period["comportamento"] == selected]
    monthly = monthly_behavior(df_selected, metric)
    model = fit_log_forecast(monthly.rename(columns={"valor": "value"}), "value", horizon)
    observed = model["observed"].rename(columns={"value": "valor"})
    forecast = model["forecast"]

    current = float(monthly["valor"].iloc[-1])
    next_prediction = float(forecast["prediction"].iloc[0]) if not forecast.empty else current
    probability_increase = probability_at_least(current, next_prediction, model["sigma"])
    reduction_threshold = current * (1 - reduction_target / 100)
    probability_reduction = probability_below(reduction_threshold, next_prediction, model["sigma"])
    sessions = df_selected.sort_values(["date", "comportamento"]).copy()
    sessions["occurred"] = ((sessions["count"] > 0) | (sessions["rate"] > 0)).astype(int)
    baseline = float(sessions["occurred"].mean()) if len(sessions) else 0.0
    recent_occurrence = sessions["occurred"].tail(recent_n).to_list()
    recent_frequency = float(sum(recent_occurrence))
    recent_mean = recent_frequency / max(recent_n, 1)
    recent_risk = ema(recent_occurrence, alpha)
    if len(sessions) > recent_n:
        previous_window = sessions["occurred"].tail(max(recent_n * 2, recent_n + 1)).head(recent_n)
        previous_mean = float(previous_window.mean()) if len(previous_window) else baseline
    else:
        previous_mean = baseline
    trend = recent_mean - (0.0 if math.isnan(previous_mean) else previous_mean)
    z = logit(baseline) + 1.25 * (recent_mean - baseline) + 0.90 * (recent_risk - baseline) + 0.65 * trend
    occurrence_probability = logistic(z)
    occurrence_risk = risk_label(occurrence_probability)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_metric_card("P ocorrência", pct(occurrence_probability * 100), "próxima sessão")
    with c2:
        render_metric_card("Baseline histórico", pct(baseline * 100), "sessões com ocorrência")
    with c3:
        render_metric_card("Risco clínico", occurrence_risk, "baixo <30%, alto >=70%")
    with c4:
        render_metric_card("EMA recente", pct(recent_risk * 100), f"alpha = {alpha:.2f}")

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        render_metric_card("Nível atual", number(current), "último mês observado")
    with c6:
        render_metric_card("β log mensal", f"{model['beta']:+.3f}", "crescimento relativo esperado")
    with c7:
        render_metric_card("P(aumentar)", pct(probability_increase), "próximo mês maior que atual")
    with c8:
        render_metric_card("P(reduzir meta)", pct(probability_reduction), f"redução >= {reduction_target}%")

    y_title = "Taxa média mensal" if metric == "rate" else "Contagem total mensal"
    next_lower = float(forecast["lower"].iloc[0]) if not forecast.empty else None
    next_upper = float(forecast["upper"].iloc[0]) if not forecast.empty else None
    report_metrics = {
        "occurrence_probability": occurrence_probability,
        "occurrence_risk": occurrence_risk,
        "baseline": baseline,
        "recent_risk": recent_risk,
        "current": current,
        "next_prediction": next_prediction,
        "next_lower": next_lower,
        "next_upper": next_upper,
        "probability_increase": probability_increase,
        "probability_reduction": probability_reduction,
        "reduction_target": reduction_target,
        "metric_label": metric_label,
        "data_points": len(monthly),
        "alpha_ema": alpha,
        "beta": model["beta"],
        "r2": model["r2"],
        "sigma": model["sigma"],
    }
    plain_explanation = build_plain_language_explanation(report_metrics)

    st.markdown("### Entenda o resultado")
    st.info(plain_explanation["headline"])
    c_plain1, c_plain2 = st.columns(2)
    with c_plain1:
        st.markdown("**O que isso quer dizer na prática**")
        for item in plain_explanation["bullets"][:3]:
            st.markdown(f"- {item}")
    with c_plain2:
        st.markdown("**Probabilidade e confiança da projeção**")
        for item in plain_explanation["bullets"][3:]:
            st.markdown(f"- {item}")
    st.caption(
        "Probabilidade descreve o que é esperado em situações semelhantes; não garante que o comportamento "
        "ocorrerá em uma sessão específica e não determina sua função."
    )

    render_forecast_chart(
        observed,
        forecast,
        "valor",
        "Trajetória prevista do comportamento-problema",
        y_title,
        "Observado",
        "Previsto",
        PASTEL_BEHAVIOR,
        target=None,
    )

    render_formula(
        "Equações usadas",
        [
            "1) z_t = ln(y_t + 1), para manter previsões não negativas.",
            f"2) z_t = α + β.t; α = {model['alpha']:.3f}; β = {model['beta']:.3f}; R² = {model['r2']:.2f}.",
            "3) previsão comportamental = exp(α + β.t) - 1.",
            f"4) P(aumento) = P(y_previsto > y_atual), com σ = {model['sigma']:.2f}.",
            f"5) Y_t,h = 1 se ocorrer na próxima sessão; baseline = {baseline:.3f}; F_recente(t,N) = {recent_frequency:.0f}; F_media(t,N) = {recent_mean:.3f}.",
            f"6) R_t = alpha.y_t + (1-alpha).R_t-1; R_t atual = {recent_risk:.3f}; P(Y_t,h=1|X_t) = {occurrence_probability:.3f}.",
        ],
    )

    table = forecast.copy()
    if not table.empty:
        table["Mês"] = table["month"].map(month_label)
        table["Previsão"] = table["prediction"]
        table["Faixa 80%"] = table.apply(lambda r: f"{r['lower']:.2f} - {r['upper']:.2f}", axis=1)
        table["P de aumento"] = table["prediction"].map(lambda v: probability_at_least(current, v, model["sigma"]))
        render_html_table(table, ["Mês", "Previsão", "Faixa 80%", "P de aumento"], {"Previsão": number, "P de aumento": pct})

    st.markdown("### Relatório para impressão")
    st.write(
        "Gere um documento com a explicação em linguagem simples, os números principais, os gráficos, "
        "a projeção mensal e a descrição das fórmulas usadas."
    )
    report_payload = {
        "patient": patient,
        "behavior": selected,
        "period_start": period[0],
        "period_end": period[1],
        "y_title": y_title,
        "observed": observed,
        "forecast": forecast,
        "metrics": report_metrics,
    }
    report_state_key = "behavior_prediction_report_" + str(
        hash(
            (
                patient,
                selected,
                period[0].isoformat(),
                period[1].isoformat(),
                metric,
                horizon,
                recent_n,
                round(alpha, 2),
                reduction_target,
                len(monthly),
            )
        )
    )
    if st.button("Preparar Word e PDF", type="primary", use_container_width=True):
        try:
            with st.spinner("Montando o relatório e os gráficos..."):
                st.session_state[report_state_key] = {
                    "docx": build_behavior_report_docx(report_payload),
                    "pdf": build_behavior_report_pdf(report_payload),
                }
        except Exception as exc:
            st.error(f"Não foi possível gerar o relatório: {exc}")

    generated_report = st.session_state.get(report_state_key)
    if generated_report:
        st.success("Relatório pronto. O PDF pode ser aberto e impresso diretamente; o Word permanece editável.")
        c_word, c_pdf = st.columns(2)
        with c_word:
            st.download_button(
                "Baixar Word",
                data=generated_report["docx"],
                file_name=behavior_report_filename(patient, selected, "docx"),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with c_pdf:
            st.download_button(
                "Baixar PDF",
                data=generated_report["pdf"],
                file_name=behavior_report_filename(patient, selected, "pdf"),
                mime="application/pdf",
                use_container_width=True,
            )

    st.markdown("### Ranking de risco comportamental")
    ranking_rows = []
    for behavior in behaviors:
        df_item = df_period[df_period["comportamento"] == behavior]
        monthly_item = monthly_behavior(df_item, metric)
        if monthly_item.empty:
            continue
        item_model = fit_log_forecast(monthly_item.rename(columns={"valor": "value"}), "value", 1)
        item_forecast = item_model["forecast"]
        last_value = float(monthly_item["valor"].iloc[-1])
        next_value = float(item_forecast["prediction"].iloc[0]) if not item_forecast.empty else last_value
        ranking_rows.append(
            {
                "Comportamento": behavior,
                "Atual": last_value,
                "Próximo mês": next_value,
                "β log": item_model["beta"],
                "P de aumento": probability_at_least(last_value, next_value, item_model["sigma"]),
            }
        )

    ranking = pd.DataFrame(ranking_rows).sort_values(["P de aumento", "Próximo mês"], ascending=[False, False])
    render_html_table(
        ranking,
        ["Comportamento", "Atual", "Próximo mês", "β log", "P de aumento"],
        {"Atual": number, "Próximo mês": number, "β log": lambda v: f"{float(v):+.3f}", "P de aumento": pct},
        max_rows=12,
    )


st.set_page_config(page_title="Sellas Predição Analítica", layout="wide")
inject_css()

st.markdown(
    """
    <section class="sellas-page-header prediction-header">
        <div>
            <span class="sellas-eyebrow">Sellas Project</span>
            <h1>Predição analítica comportamental</h1>
            <p>Modelos probabilísticos para habilidades, objetivos e comportamentos-problema.</p>
        </div>
        <span class="sellas-header-badge">Foco matemático</span>
    </section>
    """,
    unsafe_allow_html=True,
)

try:
    lista_pacientes = requests.get(f"{API_URL}/api/pacientes", timeout=API_TIMEOUT_SECONDS).json()
    lista_pacientes = sorted(lista_pacientes)
except requests.exceptions.ConnectionError:
    st.error("A API não está respondendo. Inicie o backend pelo iniciar_sellas.bat.")
    st.stop()
except requests.exceptions.Timeout:
    st.error("A API demorou demais para responder.")
    st.stop()
except Exception as exc:
    st.error(f"Erro ao conectar à API: {type(exc).__name__}.")
    st.stop()

if not lista_pacientes:
    st.warning("Banco de dados vazio.")
    if st.button("Sincronizar bHave agora"):
        sincronizar_bhave_api()
        st.cache_data.clear()
        st.rerun()
    st.stop()

paciente_sel = st.sidebar.selectbox("Paciente", lista_pacientes)
st.sidebar.markdown("---")
if st.sidebar.button("Sincronizar bHave"):
    with st.spinner("Buscando dados no bHave..."):
        sincronizar_bhave_api()
        st.cache_data.clear()
        st.rerun()

df_p_raw, df_b_raw = load_data_from_api(paciente_sel)
df_lib = load_library_from_api()
df_skills = prepare_skill_data(df_p_raw)
df_behavior = prepare_behavior_data(df_b_raw)
default_start, default_end = date_period_default(df_skills, df_behavior)

st.sidebar.markdown("### Janela de análise")
period = st.sidebar.date_input(
    "Período",
    value=(default_start, default_end),
    min_value=default_start,
    max_value=default_end,
    format="DD/MM/YYYY",
)
if not isinstance(period, tuple) or len(period) != 2:
    period = (default_start, default_end)

active_tab = st.radio(
    "Área de previsão",
    ["Previsão de habilidades/objetivos", "Previsão comportamental", "Análise ABC (fechado)"],
    horizontal=True,
    label_visibility="collapsed",
    key="prediction_area",
)

st.markdown(
    f"""
    <div class="patient-strip">
        <span>Paciente</span>
        <strong>{html.escape(paciente_sel)}</strong>
        <small>{period[0].strftime('%d/%m/%Y')} a {period[1].strftime('%d/%m/%Y')}</small>
    </div>
    """,
    unsafe_allow_html=True,
)

if active_tab == "Previsão de habilidades/objetivos":
    render_skill_prediction(df_skills, df_lib, period)
elif active_tab == "Previsão comportamental":
    render_behavior_prediction(paciente_sel, df_behavior, period)
else:
    render_abc_registration_and_analysis(paciente_sel)

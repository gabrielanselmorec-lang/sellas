"""Presentation helpers for the Sellas ABC charts; no patient data is changed."""

from collections import Counter

import plotly.graph_objects as go

from app.services.abc_integrity import local_timestamp


def hourly_rows(summary: dict, *, ranked: bool = False) -> list[dict]:
    """Include gaps between recorded times, without inventing observation time."""
    existing = {int(r["inicio"][:2]) * 60 + int(r["inicio"][3:]): r
                for r in summary.get("por_horario", [])}
    if not existing:
        return []
    step = int(summary.get("faixa_minutos") or 60)
    maximum = max(r["quantidade"] for r in existing.values())
    rows = []
    for minute in range(min(existing), max(existing) + step, step):
        end = minute + step - 1
        row = {"inicio": f"{minute // 60:02d}:{minute % 60:02d}",
               "fim": f"{end // 60:02d}:{end % 60:02d}",
               "quantidade": 0, "dias": 0, "respostas": 0, "percentual_total": 0,
               **existing.get(minute, {})}
        rows.append({**row, "faixa": f"{row['inicio']} às {row['fim']}",
                     "destaque": maximum > 0 and row["quantidade"] == maximum})
    return sorted(rows, key=lambda r: (-r["quantidade"], r["inicio"])) if ranked else rows


def build_hourly_figure(rows: list[dict]) -> go.Figure:
    # More granular views can contain 96 bins. Keep those readable inside a
    # bounded scroll container in the dashboard instead of shrinking their text.
    fig = go.Figure(go.Bar(
        x=[r["quantidade"] for r in rows], y=[r["faixa"] for r in rows], orientation="h",
        marker_color=["#a64d27" if r["destaque"] else "#397b83" for r in rows],
        text=[f"{r['quantidade']}  ·  {r['percentual_total']:.1%}" if r["quantidade"] else "Sem registros" for r in rows],
        textposition="outside", cliponaxis=False,
        customdata=[[r["dias"], r["respostas"], r["percentual_total"]] for r in rows],
        hovertemplate=("<b>%{y}</b><br>%{x} episódios · %{customdata[2]:.1%} do total"
                       "<br>%{customdata[0]} dias com registros<br>%{customdata[1]} respostas registradas<extra></extra>"),
    ))
    maximum = max((r["quantidade"] for r in rows), default=1)
    fig.update_layout(height=max(300, 90 + 32 * len(rows)), showlegend=False,
                      xaxis_title="Episódios registrados", yaxis_title=None, bargap=0.28,
                      hovermode="closest", margin=dict(l=130, r=80, t=12, b=45))
    fig.update_xaxes(range=[0, max(1, maximum) * 1.35], rangemode="tozero", dtick=1 if maximum < 10 else None)
    fig.update_yaxes(autorange="reversed", type="category", fixedrange=True,
                     categoryorder="array", categoryarray=[r["faixa"] for r in rows])
    return fig


def records_in_hour_bin(records: list[dict], start: str, minutes: int) -> list[dict]:
    beginning = int(start[:2]) * 60 + int(start[3:])
    result = []
    for row in records:
        if row.get("precisao_horario") == "unknown":
            continue
        try:
            stamp = local_timestamp(row["data_hora"])
        except (KeyError, ValueError, TypeError):
            continue
        minute = stamp.hour * 60 + stamp.minute
        if beginning <= minute < beginning + minutes:
            result.append(row)
    return result


def category_counts(records: list[dict], field: str) -> list[dict]:
    counts = Counter(str(row.get(field) or "Não informado") for row in records)
    return [{"Categoria": name, "Episódios": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]

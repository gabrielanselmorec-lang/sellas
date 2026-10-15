from copy import deepcopy

import pytest

from app.services.abc_integrity import build_time_summary
from app.web.abc_charts import build_hourly_figure, category_counts, hourly_rows, records_in_hour_bin


def record(stamp, precision="minute", frequency=1, **extra):
    return {"data_hora": stamp, "precisao_horario": precision, "frequencia": frequency,
            "comportamento": "Choro", "ambiente": "Sala", **extra}


def test_hourly_peak_uses_episodes_not_responses_and_preserves_unknown_denominator():
    records = [record("2026-07-15T08:00:00-03:00", frequency=20),
               record("2026-07-15T08:59:59-03:00"),
               record("2026-07-16T10:20:00-03:00"),
               record("2026-07-16T00:00:00-03:00", precision="unknown")]
    summary = build_time_summary(records)
    before = deepcopy(summary)
    rows = hourly_rows(summary)
    assert [r["faixa"] for r in rows] == ["08:00 às 08:59", "09:00 às 09:59", "10:00 às 10:59"]
    assert [r["quantidade"] for r in rows] == [2, 0, 1]
    assert rows[0]["respostas"] == 21
    assert rows[0]["dias"] == 1
    assert rows[0]["percentual_total"] == .5
    assert rows[0]["destaque"]
    assert not rows[1]["destaque"]
    assert summary == before
    chart = build_hourly_figure(rows)
    assert list(chart.data[0].x) == [2, 0, 1]
    assert chart.data[0].text[1] == "Sem registros"
    assert chart.data[0].marker.color[0] != chart.data[0].marker.color[2]


def test_ranked_chart_marks_every_tie_and_matches_sorted_labels():
    rows = hourly_rows(build_time_summary([
        record("2026-07-15T08:00:00-03:00"),
        record("2026-07-15T09:00:00-03:00"),
        record("2026-07-16T09:00:00-03:00"),
        record("2026-07-15T10:00:00-03:00"),
        record("2026-07-16T10:00:00-03:00"),
    ]), ranked=True)
    assert [r["inicio"] for r in rows] == ["09:00", "10:00", "08:00"]
    assert [r["destaque"] for r in rows] == [True, True, False]
    assert list(build_hourly_figure(rows).data[0].y) == [r["faixa"] for r in rows]


@pytest.mark.parametrize("minutes,start,end", [(60, "23:00", "23:59"), (30, "23:30", "23:59"), (15, "23:45", "23:59")])
def test_last_bin_and_timezone_are_correct(minutes, start, end):
    record_utc = record("2026-07-16T02:59:59+00:00")
    rows = hourly_rows(build_time_summary([record_utc], minutes))
    assert rows[0]["inicio"] == start
    assert rows[0]["fim"] == end
    assert records_in_hour_bin([record_utc], start, minutes) == [record_utc]


def test_drilldown_obeys_boundaries_and_excludes_unknown_invalid_times():
    records = [record("2026-07-15T08:59:59-03:00"), record("2026-07-15T09:00:00-03:00"),
               record("2026-07-15T09:59:59-03:00", precision="approximate"),
               record("2026-07-15T10:00:00-03:00"),
               record("2026-07-15T09:00:00-03:00", precision="unknown"), record("invalid")]
    selected = records_in_hour_bin(records, "09:00", 60)
    assert selected == records[1:3]
    assert category_counts(selected, "comportamento") == [{"Categoria": "Choro", "Episódios": 2}]
    assert hourly_rows(build_time_summary([])) == []
    assert category_counts([], "ambiente") == []

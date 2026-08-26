from pathlib import Path

from fastapi import BackgroundTasks

import api


def _clear_excel_refresh_state() -> None:
    with api._ABC_EXCEL_REFRESH_LOCK:
        api._ABC_EXCEL_REFRESH_STATE.clear()


def test_excel_refresh_is_queued_without_running_generator_in_http_path(monkeypatch):
    _clear_excel_refresh_state()
    calls = []
    monkeypatch.setattr(api, "latest_patient_excel_path", lambda patient: Path("C:/tmp/current.xlsx"))
    monkeypatch.setattr(api, "_refresh_abc_excel", lambda **kwargs: calls.append(kwargs))
    tasks = BackgroundTasks()

    result = api._queue_abc_excel_refresh(
        tasks,
        patient_token="patient-token",
        patient_name="Paciente",
    )

    assert result["excel_status"] == "queued"
    assert result["excel_path"].endswith("current.xlsx")
    assert calls == []
    assert len(tasks.tasks) == 1
    _clear_excel_refresh_state()


def test_excel_refresh_consolidates_write_that_arrives_during_generation(monkeypatch):
    _clear_excel_refresh_state()
    calls = []

    def fake_refresh(*, patient_token, patient_name):
        calls.append((patient_token, patient_name))
        if len(calls) == 1:
            with api._ABC_EXCEL_REFRESH_LOCK:
                api._ABC_EXCEL_REFRESH_STATE[patient_token]["dirty"] = True
        return {"excel_path": "ok.xlsx", "excel_warning": None}

    monkeypatch.setattr(api, "_refresh_abc_excel", fake_refresh)
    with api._ABC_EXCEL_REFRESH_LOCK:
        api._ABC_EXCEL_REFRESH_STATE["patient-token"] = {
            "patient_name": "Paciente",
            "dirty": False,
        }

    api._run_queued_abc_excel_refresh("patient-token")

    assert calls == [
        ("patient-token", "Paciente"),
        ("patient-token", "Paciente"),
    ]
    assert "patient-token" not in api._ABC_EXCEL_REFRESH_STATE

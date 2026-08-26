from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class BHaveConfigRequest(BaseModel):
    base_url: str | None = Field(None, max_length=500)
    api_token: str | None = Field(None, max_length=2000)
    use_mock: bool = True
    field_map: dict[str, str] = Field(default_factory=dict)
    records_path: str = Field("/behavior-records", max_length=300)
    auth_scheme: str = Field("bearer", max_length=40)
    auth_header: str = Field("Authorization", max_length=120)
    records_key: str = Field("data,records,items,documents", max_length=200)
    page_param: str = Field("page", max_length=80)
    page_size_param: str = Field("pageSize", max_length=80)
    next_page_key: str = Field("nextPageToken,next,next_cursor", max_length=200)


class SyncRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    use_mock: bool | None = None
    field_map: dict[str, str] = Field(default_factory=dict)


class TrainRequest(BaseModel):
    patient_id: str | None = Field(None, max_length=80)
    behavior_name: str = Field(..., min_length=1, max_length=120)
    prediction_window: str = Field("next_session", max_length=80)
    horizon_sessions: int = Field(1, ge=1, le=20)
    recent_sessions: int = Field(5, ge=1, le=50)
    recent_days: int = Field(14, ge=1, le=365)
    alpha: float = Field(0.3, ge=0, le=1)


class PredictRequest(BaseModel):
    patient_id: str | None = Field(None, max_length=80)
    behavior_name: str = Field(..., min_length=1, max_length=120)
    prediction_window: str = Field("next_session", max_length=80)
    horizon_sessions: int = Field(1, ge=1, le=20)
    recent_sessions: int = Field(5, ge=1, le=50)
    recent_days: int = Field(14, ge=1, le=365)
    alpha: float = Field(0.3, ge=0, le=1)


class FeatureRequest(BaseModel):
    patient_id: str | None = Field(None, max_length=80)
    behavior_name: str = Field(..., min_length=1, max_length=120)
    prediction_window: str = Field("next_session", max_length=80)
    horizon_sessions: int = Field(1, ge=1, le=20)
    recent_sessions: int = Field(5, ge=1, le=50)
    recent_days: int = Field(14, ge=1, le=365)
    alpha: float = Field(0.3, ge=0, le=1)


class BehaviorRecord(BaseModel):
    patient_id: str
    session_id: str
    date: str | None
    behavior_id: str | None = None
    behavior_name: str
    frequency: float = 0
    duration: float = 0
    intensity: float = 0
    antecedent: str | None = None
    consequence: str | None = None
    hypothesized_function: str | None = None
    environment: str | None = None
    therapist_id: str | None = None
    strategies_used: list[str] = Field(default_factory=list)
    prompt_level: float = 0
    independence_score: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class NoteCorrectionRequest(BaseModel):
    human_confirmed: bool = True
    human_corrected: bool = True
    corrected_by: str | None = Field(None, max_length=120)
    corrected_extraction: dict[str, Any] = Field(default_factory=dict)

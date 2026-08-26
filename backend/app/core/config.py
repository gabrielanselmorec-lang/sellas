from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = PROJECT_DIR / "storage"
MODEL_DIR = DATA_DIR / "models"


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "bHave Behavioral Prediction MVP")
    app_env: str = os.getenv("APP_ENV", "local")
    database_url: str = os.getenv("SELLAS_BHAVE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'bhave_mvp.db'}")
    bhave_base_url: str = os.getenv("SELLAS_BHAVE_BASE_URL", os.getenv("BHAVE_BASE_URL", ""))
    bhave_api_token: str = os.getenv("SELLAS_BHAVE_API_TOKEN", os.getenv("BHAVE_API_TOKEN", ""))
    anonymization_salt: str = os.getenv("SELLAS_ANONYMIZATION_SALT", os.getenv("ANONYMIZATION_SALT", "change-me-in-production"))
    risk_low_max: float = float(os.getenv("SELLAS_RISK_LOW_MAX", os.getenv("RISK_LOW_MAX", "0.30")))
    risk_moderate_max: float = float(os.getenv("SELLAS_RISK_MODERATE_MAX", os.getenv("RISK_MODERATE_MAX", "0.70")))
    prediction_horizon: str = os.getenv("SELLAS_PREDICTION_HORIZON", os.getenv("PREDICTION_HORIZON", "next_session"))
    use_mock_data: bool = os.getenv("USE_MOCK_DATA", "true").lower() in {"1", "true", "yes", "sim"}
    abc_analysis_start_date: str | None = os.getenv("ABC_ANALYSIS_START_DATE")
    abc_analysis_end_date: str | None = os.getenv("ABC_ANALYSIS_END_DATE")
    abc_include_weekends: bool = os.getenv("ABC_INCLUDE_WEEKENDS", "true").lower() in {"1", "true", "yes", "sim"}
    abc_interval_minutes: int = int(os.getenv("ABC_INTERVAL_MINUTES", "5"))
    abc_minimum_valid_intervals: int = int(os.getenv("ABC_MINIMUM_VALID_INTERVALS", "10"))


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

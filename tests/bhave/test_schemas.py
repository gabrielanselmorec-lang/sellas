import pytest
from pydantic import ValidationError

from backend.app.schemas.behavior import PredictRequest, TrainRequest


def test_train_request_accepts_behavior_name():
    payload = TrainRequest(patient_id="pac_1", behavior_name="agressao")
    assert payload.behavior_name == "agressao"


def test_predict_request_rejects_empty_behavior_name():
    with pytest.raises(ValidationError):
        PredictRequest(patient_id="pac_1", behavior_name="")

from backend.app.utils.risk import classify_risk


def test_classify_risk_default_thresholds():
    assert classify_risk(0.10) == "baixo"
    assert classify_risk(0.30) == "moderado"
    assert classify_risk(0.50) == "moderado"
    assert classify_risk(0.70) == "alto"
    assert classify_risk(0.90) == "alto"


def test_classify_risk_custom_thresholds():
    assert classify_risk(0.39, low_max=0.40, moderate_max=0.80) == "baixo"
    assert classify_risk(0.40, low_max=0.40, moderate_max=0.80) == "moderado"
    assert classify_risk(0.81, low_max=0.40, moderate_max=0.80) == "alto"

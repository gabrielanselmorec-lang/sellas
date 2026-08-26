from __future__ import annotations

import random
from datetime import date, datetime, timedelta, time


PATIENTS = [
    ("P001", "Paciente A"),
    ("P002", "Paciente B"),
    ("P003", "Paciente C"),
    ("P004", "Paciente D"),
    ("P005", "Paciente E"),
]

BEHAVIORS = [
    ("B001", "agressao", "externalizante"),
    ("B002", "autolesao", "risco"),
    ("B003", "fuga", "esquiva"),
    ("B004", "choro", "desregulacao"),
    ("B005", "grito", "disruptivo"),
]

ENVIRONMENTS = ["sala de terapia", "sala de grupo", "casa", "escola", "transicao"]
ANTECEDENTS = ["demanda", "negacao de acesso", "espera", "transicao", "brincadeira livre"]
CONSEQUENCES = ["redirecionamento", "pausa", "acesso a item", "atencao", "comunicacao funcional"]
FUNCTIONS = ["fuga/esquiva", "acesso a tangivel", "atencao", "automatica", "indeterminada"]
STRATEGIES = [
    ["FCT", "DRA"],
    ["antecedent modification", "visual schedule"],
    ["NCR", "choice making"],
    ["differential reinforcement"],
    ["prompt fading", "planned ignoring"],
]


def generate_mock_records(days: int = 75, seed: int = 42) -> list[dict]:
    """Generate realistic session-level bHave-like records for local development."""
    rng = random.Random(seed)
    start = date.today() - timedelta(days=days)
    records: list[dict] = []
    session_counter = 0

    for patient_idx, (patient_id, patient_name) in enumerate(PATIENTS):
        vulnerability = 0.12 + patient_idx * 0.03
        for day in range(days):
            current = start + timedelta(days=day)
            if current.weekday() >= 5 or rng.random() < 0.18:
                continue
            session_counter += 1
            session_id = f"S{session_counter:05d}"
            env = rng.choice(ENVIRONMENTS)
            antecedent = rng.choice(ANTECEDENTS)
            consequence = rng.choice(CONSEQUENCES)
            hypothesized_function = rng.choice(FUNCTIONS)
            session_hour = rng.choice([8, 9, 10, 13, 14, 15, 16])
            recent_pressure = 0.08 if day % 9 in {0, 1, 2} else 0.0
            transition_pressure = 0.10 if env == "transicao" or antecedent == "transicao" else 0.0
            demand_pressure = 0.12 if antecedent == "demanda" else 0.0

            for behavior_idx, (behavior_id, behavior_name, category) in enumerate(BEHAVIORS):
                base = vulnerability + behavior_idx * 0.015 + recent_pressure
                behavior_pressure = demand_pressure if behavior_name in {"agressao", "fuga"} else 0.0
                probability = min(0.82, base + transition_pressure + behavior_pressure)
                occurred = rng.random() < probability
                frequency = rng.randint(1, 5) if occurred else 0
                intensity = rng.choice([1, 2, 3, 4, 5]) if occurred else 0
                duration = rng.randint(15, 600) if occurred else 0
                records.append(
                    {
                        "patient_id": patient_id,
                        "patient_name": patient_name,
                        "session_id": session_id,
                        "date": current.isoformat(),
                        "start_time": time(session_hour, 0).isoformat(timespec="minutes"),
                        "end_time": time(min(session_hour + 1, 23), 0).isoformat(timespec="minutes"),
                        "behavior_id": behavior_id,
                        "behavior_name": behavior_name,
                        "behavior_category": category,
                        "frequency": frequency,
                        "duration": duration,
                        "intensity": intensity,
                        "antecedent": antecedent,
                        "consequence": consequence,
                        "hypothesized_function": hypothesized_function,
                        "environment": env,
                        "therapist_id": f"T{rng.randint(1, 7):03d}",
                        "therapist_name": f"Terapeuta {rng.randint(1, 7)}",
                        "intervention_plan_id": f"IP-{patient_id}",
                        "strategies_used": rng.choice(STRATEGIES),
                        "prompt_level": rng.choice([0, 1, 2, 3]),
                        "independence_score": max(0, min(100, rng.gauss(68 - intensity * 6, 12))),
                        "notes": "Registro simulado para desenvolvimento local.",
                        "created_at": datetime.combine(current, time(session_hour, 0)).isoformat(),
                        "updated_at": datetime.combine(current, time(session_hour, 45)).isoformat(),
                    }
                )

    return records

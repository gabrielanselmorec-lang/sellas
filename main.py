from __future__ import annotations

import base64
import binascii
import datetime as dt
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import text

from app.data.models import (
    InterferingBehavior,
    InterferingRecord,
    Patient,
    Program,
    ProgramRecord,
    ProgramTargetRecord,
    SessionLocal,
    init_db,
)
from app.security import (
    SecurityValidationError,
    assert_response_size,
    build_retry_session,
    ensure_child_path,
    ensure_https_url,
    get_env,
    pseudonymize_identifier,
    require_env,
    safe_filename,
    sanitize_text,
)
from app.services.docx_parser import BhaveParserService


load_dotenv()

API_KEY = require_env("BHAVE_API_KEY")
EMAIL = require_env("BHAVE_EMAIL")
PASSWORD = require_env("BHAVE_PASSWORD")
ACCOUNT_ID = require_env("BHAVE_ACCOUNT_ID")

REPORT_START_DATE = os.getenv("BHAVE_REPORT_START_DATE", "2026-01-01")
RAW_DATA_DIR = Path("raw_data")
MAX_JSON_BYTES = int(get_env("SELLAS_MAX_JSON_BYTES", "SKINNER_MAX_JSON_BYTES", default="5000000"))
MAX_REPORT_BYTES = int(get_env("SELLAS_MAX_REPORT_BYTES", "SKINNER_MAX_REPORT_BYTES", default="25000000"))
API_PAUSE_SECONDS = float(get_env("SELLAS_API_PAUSE_SECONDS", "SKINNER_API_PAUSE_SECONDS", default="0.5"))
REQUEST_TIMEOUT = (5, 30)
REPORT_TIMEOUT = (10, 180)

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
TAG_CARUARU = "caruaru pe"

FIREBASE_HOSTS = {
    "identitytoolkit.googleapis.com",
    "firestore.googleapis.com",
    "us-east1-aplicatudo-prod.cloudfunctions.net",
}

HTTP = build_retry_session(total_retries=3, backoff_factor=1.0)


def _validate_external_id(value: str, field_name: str) -> str:
    candidate = sanitize_text(value, max_length=160, allow_newlines=False)
    if not IDENTIFIER_RE.fullmatch(candidate):
        raise SecurityValidationError(f"{field_name} invalido.")
    return candidate


ACCOUNT_ID = _validate_external_id(ACCOUNT_ID, "BHAVE_ACCOUNT_ID")


def _validate_iso_date(value: str, field_name: str) -> str:
    candidate = sanitize_text(value, max_length=10, allow_newlines=False)
    try:
        dt.datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError as exc:
        raise SecurityValidationError(f"{field_name} deve usar YYYY-MM-DD.") from exc
    return candidate


REPORT_START_DATE = _validate_iso_date(REPORT_START_DATE, "BHAVE_REPORT_START_DATE")


def _safe_error(error: Exception) -> str:
    return sanitize_text(error, max_length=200, allow_newlines=False)


def _fold_text(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    ).lower()


def _response_json(response: requests.Response, *, max_bytes: int = MAX_JSON_BYTES) -> dict | list:
    assert_response_size(response, max_bytes)
    try:
        return response.json()
    except ValueError as exc:
        raise SecurityValidationError("Resposta remota nao contem JSON valido.") from exc


def _auth_headers(token: str) -> dict[str, str]:
    token = sanitize_text(token, max_length=4096, allow_newlines=False)
    if not token:
        raise SecurityValidationError("Token de autenticacao vazio.")
    return {"Authorization": f"Bearer {token}"}


def _field_string(fields: dict, name: str, *, max_length: int = 5000) -> str:
    return sanitize_text(
        fields.get(name, {}).get("stringValue", ""),
        max_length=max_length,
        allow_newlines=True,
    )


def _field_bool(fields: dict, name: str, *, default: bool = False) -> bool:
    value = fields.get(name, {}).get("booleanValue", default)
    return bool(value)


def _bounded_percent(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(number, 100.0))


def _bounded_int(value: object, *, max_value: int = 10000) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, max_value))


def _clinical_date(value: object) -> str | None:
    candidate = sanitize_text(value, max_length=20, allow_newlines=False)
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            dt.datetime.strptime(candidate, pattern)
            return candidate
        except ValueError:
            continue
    return None


def obter_token() -> str | None:
    url = ensure_https_url(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
        allowed_hosts=FIREBASE_HOSTS,
    )
    payload = {"email": EMAIL, "password": PASSWORD, "returnSecureToken": True}

    try:
        response = HTTP.post(
            url,
            params={"key": API_KEY},
            json=payload,
            timeout=REQUEST_TIMEOUT,
            verify=True,
        )
        if response.status_code != 200:
            print(f"  Falha ao obter token Firebase (HTTP {response.status_code}).")
            return None

        data = _response_json(response)
        token = data.get("idToken") if isinstance(data, dict) else None
        return sanitize_text(token, max_length=4096, allow_newlines=False) or None
    except (requests.RequestException, SecurityValidationError) as exc:
        print(f"  Falha segura ao obter token: {_safe_error(exc)}")
        return None


def atualizar_biblioteca_local(token: str, session) -> None:
    print("Baixando gabaritos da biblioteca do bHave...")

    url = ensure_https_url(
        f"https://firestore.googleapis.com/v1/projects/aplicatudo-prod/databases/(default)/documents/accounts/{ACCOUNT_ID}/programs",
        allowed_hosts=FIREBASE_HOSTS,
    )
    params = {"pageSize": "300"}

    try:
        response = HTTP.get(
            url,
            params=params,
            headers=_auth_headers(token),
            timeout=REQUEST_TIMEOUT,
            verify=True,
        )
        if response.status_code != 200:
            print(f"  Nao foi possivel acessar a biblioteca do bHave (HTTP {response.status_code}).")
            return
        data = _response_json(response)
    except (requests.RequestException, SecurityValidationError) as exc:
        print(f"  Falha ao acessar biblioteca externa: {_safe_error(exc)}")
        return

    documentos = data.get("documents", []) if isinstance(data, dict) else []
    programas_salvos = 0

    for doc in documentos:
        fields = doc.get("fields", {}) if isinstance(doc, dict) else {}
        nome = _field_string(fields, "name", max_length=200)
        goal = _field_string(fields, "goal", max_length=4000)
        is_archived = _field_bool(fields, "isArchived")

        if not nome or is_archived:
            continue

        threshold = 90
        days = 3
        goal_search = _fold_text(goal)

        match_perc = re.search(r"(\d{1,3})\s*%", goal_search)
        if match_perc:
            threshold = _bounded_int(match_perc.group(1), max_value=100)

        match_tempo = re.search(r"(\d{1,3})\s*(sessoes|dias|semanas|meses)", goal_search)
        if match_tempo:
            qtd = _bounded_int(match_tempo.group(1), max_value=365)
            tipo = match_tempo.group(2).lower()
            if "semana" in tipo:
                days = qtd * 5
            elif "mes" in tipo:
                days = qtd * 20
            else:
                days = qtd

        query = text(
            """
            INSERT INTO program_library (name, objective_template, mastery_threshold_percent, mastery_days)
            VALUES (:name, :goal, :threshold, :days)
            ON CONFLICT (name) DO UPDATE
            SET objective_template = EXCLUDED.objective_template,
                mastery_threshold_percent = EXCLUDED.mastery_threshold_percent,
                mastery_days = EXCLUDED.mastery_days
            """
        )
        session.execute(query, {"name": nome, "goal": goal, "threshold": threshold, "days": days})
        programas_salvos += 1

    session.commit()
    print(f"  {programas_salvos} programas/criterios atualizados no gabarito do Sellas.")


def buscar_alunos(token: str) -> list[dict[str, object]]:
    url = ensure_https_url(
        "https://firestore.googleapis.com/v1/projects/aplicatudo-prod/databases/(default)/documents:runQuery",
        allowed_hosts=FIREBASE_HOSTS,
    )
    query_payload = {
        "structuredQuery": {
            "from": [{"collectionId": "students"}],
            "where": {
                "compositeFilter": {
                    "op": "AND",
                    "filters": [
                        {
                            "fieldFilter": {
                                "field": {"fieldPath": "accountId"},
                                "op": "EQUAL",
                                "value": {"stringValue": ACCOUNT_ID},
                            }
                        },
                        {
                            "fieldFilter": {
                                "field": {"fieldPath": "isArchived"},
                                "op": "EQUAL",
                                "value": {"booleanValue": False},
                            }
                        },
                    ],
                }
            },
        }
    }

    try:
        response = HTTP.post(
            url,
            json=query_payload,
            headers=_auth_headers(token),
            timeout=REQUEST_TIMEOUT,
            verify=True,
        )
        if response.status_code != 200:
            print(f"  Falha ao buscar alunos (HTTP {response.status_code}).")
            return []
        data = _response_json(response)
    except (requests.RequestException, SecurityValidationError) as exc:
        print(f"  Falha ao buscar alunos: {_safe_error(exc)}")
        return []

    alunos_processados: list[dict[str, object]] = []
    for doc in data if isinstance(data, list) else []:
        document = doc.get("document") if isinstance(doc, dict) else None
        if not document:
            continue

        try:
            student_id = _validate_external_id(document["name"].split("/")[-1], "student_id")
        except (KeyError, SecurityValidationError):
            continue

        fields = document.get("fields", {})
        nome = _field_string(fields, "name", max_length=200) or "Sem Nome"
        tags: list[str] = []

        for value in fields.get("customTags", {}).get("arrayValue", {}).get("values", []):
            tag = sanitize_text(value.get("stringValue", ""), max_length=80, allow_newlines=False).lower()
            if tag:
                tags.append(tag)

        tag_fields = fields.get("tags", {}).get("mapValue", {}).get("fields", {})
        for tag_data in tag_fields.values():
            texto = (
                tag_data.get("mapValue", {})
                .get("fields", {})
                .get("text", {})
                .get("stringValue", "")
            )
            tag = sanitize_text(texto, max_length=80, allow_newlines=False).lower()
            if tag:
                tags.append(tag)

        alunos_processados.append({"id": student_id, "nome": nome, "tags": tags})

    return alunos_processados


def get_bhave_report(token: str, student_id: str, tentativas: int = 3) -> bytes | None:
    url = ensure_https_url(
        "https://us-east1-aplicatudo-prod.cloudfunctions.net/generateStudentReportOnCall",
        allowed_hosts=FIREBASE_HOSTS,
    )
    student_id = _validate_external_id(student_id, "student_id")
    amanha = (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"    Pedindo relatorio a partir de {REPORT_START_DATE}...")
    payload = {
        "data": {
            "studentId": student_id,
            "startDate": REPORT_START_DATE,
            "endDate": amanha,
            "hideIncompleteSessions": False,
        }
    }

    for tentativa in range(1, tentativas + 1):
        try:
            response = HTTP.post(
                url,
                json=payload,
                headers=_auth_headers(token),
                timeout=REPORT_TIMEOUT,
                verify=True,
            )
            if response.status_code != 200:
                print(f"    O bHave falhou (HTTP {response.status_code}).")
                return None

            assert_response_size(response, max(MAX_JSON_BYTES, MAX_REPORT_BYTES * 2))
            data = response.json()
            encoded_report = data.get("result") if isinstance(data, dict) else None
            if not isinstance(encoded_report, str):
                print("    Resposta do relatorio veio sem payload esperado.")
                return None
            if len(encoded_report) > ((MAX_REPORT_BYTES + 2) // 3) * 4 + 4:
                raise SecurityValidationError("Relatorio codificado excede o limite permitido.")

            report_bytes = base64.b64decode(encoded_report, validate=True)
            if len(report_bytes) > MAX_REPORT_BYTES:
                raise SecurityValidationError("Relatorio DOCX excede o limite permitido.")
            if not report_bytes.startswith(b"PK"):
                raise SecurityValidationError("Relatorio remoto nao parece ser um DOCX valido.")
            return report_bytes
        except (requests.RequestException, ValueError, binascii.Error, SecurityValidationError) as exc:
            print(f"    Falha de rede/dados ({tentativa}/{tentativas}): {_safe_error(exc)}")
            if tentativa < tentativas:
                time.sleep(2 * tentativa)

    print("    Desistindo deste paciente apos falhas repetidas.")
    return None


def limpar_pasta_temporaria() -> None:
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    base = RAW_DATA_DIR.resolve()
    for file_path in RAW_DATA_DIR.glob("*.docx"):
        safe_path = ensure_child_path(base, file_path)
        if safe_path.is_file():
            safe_path.unlink()

def sincronizar_objetivos_individuais(token: str, student_id: str, db_patient_id: int, session) -> None:
    """Busca o texto exato do objetivo na biblioteca individual do paciente no bHave."""
    url = ensure_https_url(
        f"https://firestore.googleapis.com/v1/projects/aplicatudo-prod/databases/(default)/documents/students/{student_id}/programs",
        allowed_hosts=FIREBASE_HOSTS,
    )
    try:
        resp = HTTP.get(
            url,
            headers=_auth_headers(token),
            params={"pageSize": "300"},
            timeout=REQUEST_TIMEOUT,
            verify=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            for doc in data.get("documents", []):
                fields = doc.get("fields", {})
                nome_prog = _field_string(fields, "name", max_length=200)
                objetivo = _field_string(fields, "goal", max_length=4000)

                if nome_prog and objetivo:
                    # Sobrescreve o objetivo no banco com o texto da nuvem
                    session.execute(
                        text("UPDATE programs SET objective = :obj WHERE patient_id = :pid AND name = :nome"),
                        {"obj": objetivo, "pid": db_patient_id, "nome": nome_prog}
                    )
            session.commit()
    except Exception:
        pass

def sincronizar_bhave_api() -> None:
    print("Iniciando sincronizacao direta da API do bHave...")
    init_db()

    # --- NOVO: GARANTE QUE A COLUNA NOME EXISTE NO POSTGRES ---
    session_setup = SessionLocal()
    try:
        alteracoes = [
            "ALTER TABLE patients ADD COLUMN name VARCHAR;",
            "ALTER TABLE program_records ADD COLUMN evolution TEXT;",
            "ALTER TABLE program_target_records ADD COLUMN evolution TEXT;",
            "ALTER TABLE interfering_records ADD COLUMN evolution TEXT;",
        ]
        for ddl in alteracoes:
            try:
                session_setup.execute(text(ddl))
                session_setup.commit()
            except Exception:
                session_setup.rollback()
    finally:
        session_setup.close()
    # -----------------------------------------------------------

    limpar_pasta_temporaria()

    token_geral = obter_token()
    if not token_geral:
        return

    session_geral = SessionLocal()
    try:
        atualizar_biblioteca_local(token_geral, session_geral)
    finally:
        session_geral.close()

    alunos = buscar_alunos(token_geral)

    for aluno in alunos:
        tags = aluno.get("tags", [])

        # 🔥 NOVA LÓGICA: Verifica se a palavra "caruaru" existe dentro de qualquer uma das tags (ignorando maiúsculas/minúsculas)
        tem_caruaru = any("caruaru" in tag for tag in tags)

        if not tem_caruaru:
            continue

        nome_paciente = sanitize_text(aluno.get("nome", ""), max_length=200, allow_newlines=False)
        try:
            patient_hash = pseudonymize_identifier(nome_paciente)
        except SecurityValidationError as exc:
            print(f"  Paciente ignorado: {_safe_error(exc)}")
            continue

        print(f"\nProcessando paciente pseudonimizado: {patient_hash[:12]}")
        safe_stem = safe_filename(f"{patient_hash[:16]}-{uuid.uuid4().hex}", default="relatorio")
        path_local = ensure_child_path(RAW_DATA_DIR, RAW_DATA_DIR / f"{safe_stem}.docx")
        token_paciente = obter_token()
        if not token_paciente:
            continue

        try:
            relatorio_bytes = get_bhave_report(token_paciente, str(aluno["id"]))
            if not relatorio_bytes:
                continue

            path_local.write_bytes(relatorio_bytes)
            session_paciente = SessionLocal()
            try:
                # 1. Processa o DOCX como antes
                processar_e_salvar(path_local, nome_paciente, session_paciente)

                # 2. NOVO: Puxa o texto do objetivo individual atualizado da nuvem
                db_patient = session_paciente.query(Patient).filter_by(name_hash=patient_hash).first()
                if db_patient:
                    sincronizar_objetivos_individuais(token_paciente, str(aluno["id"]), db_patient.id, session_paciente)

            except Exception as exc:
                session_paciente.rollback()
                print(f"  Erro ao salvar no banco: {_safe_error(exc)}")
            finally:
                session_paciente.close()
        except Exception as exc:
            print(f"  Erro no processamento seguro: {_safe_error(exc)}")
        finally:
            if path_local.exists():
                path_local.unlink()
            time.sleep(API_PAUSE_SECONDS)

    print("\nSincronizacao finalizada.")


def processar_e_salvar(caminho_docx: str | Path, nome_paciente: str, session) -> None:
    patient_hash = pseudonymize_identifier(nome_paciente)
    parser = BhaveParserService(str(caminho_docx))
    dados = parser.extract_clinical_data()

    if not dados.get("programas") and not dados.get("interferentes"):
        return

    legacy_name = sanitize_text(nome_paciente, max_length=200, allow_newlines=False)
    patient = (
        session.query(Patient)
        .filter(Patient.name_hash.in_([patient_hash, legacy_name]))
        .first()
    )
    if not patient:
        patient = Patient(
            name_hash=patient_hash,
            name=nome_paciente, # Salva o nome real
            created_at=str(dt.date.today())
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
    else:
        # Atualiza o nome real se estiver faltando ou diferente
        patient.name_hash = patient_hash
        patient.name = nome_paciente
        session.commit()

    ultima_data_programa: dict[str, str] = {}

    for prog_data in dados.get("programas", []):
        nome_prog = sanitize_text(prog_data.get("programa", ""), max_length=200, allow_newlines=False)
        data_sessao = _clinical_date(prog_data.get("data"))
        if not nome_prog or not data_sessao:
            continue
        ultima_data_programa[nome_prog] = data_sessao

        db_prog = session.query(Program).filter_by(patient_id=patient.id, name=nome_prog).first()
        objetivo = sanitize_text(prog_data.get("objetivo", ""), max_length=4000, allow_newlines=True)
        if not db_prog:
            db_prog = Program(patient_id=patient.id, name=nome_prog, objective=objetivo)
            session.add(db_prog)
            session.commit()
            session.refresh(db_prog)
        elif objetivo and db_prog.objective != objetivo:
            db_prog.objective = objetivo
            session.commit()

        success_rate = _bounded_percent(prog_data.get("acertos_percentual"))
        existe = (
            session.query(ProgramRecord)
            .filter_by(program_id=db_prog.id, date=data_sessao, success_rate=success_rate)
            .first()
        )

        evolucao_prog = sanitize_text(prog_data.get("evolucao", ""), max_length=4000, allow_newlines=True)

        if not existe:
            session.add(
                ProgramRecord(
                    program_id=db_prog.id,
                    date=data_sessao,
                    therapist=sanitize_text(prog_data.get("terapeuta", ""), max_length=120),
                    phase=sanitize_text(prog_data.get("fase", ""), max_length=120),
                    evolution=evolucao_prog,
                    success_rate=success_rate,
                    independent_rate=_bounded_percent(prog_data.get("indep_percent")),
                    prompt_rate=_bounded_percent(prog_data.get("dicas_percent")),
                )
            )
        elif evolucao_prog and not getattr(existe, "evolution", None):
            existe.evolution = evolucao_prog

    for alvo_data in dados.get("alvos", []):
        nome_prog_alvo = sanitize_text(alvo_data.get("programa", ""), max_length=200, allow_newlines=False)
        if not nome_prog_alvo:
            continue
        db_prog = session.query(Program).filter_by(patient_id=patient.id, name=nome_prog_alvo).first()
        if not db_prog:
            continue

        data_alvo = _clinical_date(alvo_data.get("data")) or ultima_data_programa.get(
            nome_prog_alvo,
            str(dt.date.today()),
        )
        target_name = sanitize_text(alvo_data.get("alvo", ""), max_length=200, allow_newlines=False)
        if not target_name:
            continue

        existe_alvo = (
            session.query(ProgramTargetRecord)
            .filter_by(program_id=db_prog.id, target_name=target_name, date=data_alvo)
            .first()
        )

        evolucao_alvo = sanitize_text(alvo_data.get("evolucao", ""), max_length=4000, allow_newlines=True)

        if not existe_alvo:
            session.add(
                ProgramTargetRecord(
                    program_id=db_prog.id,
                    target_name=target_name,
                    attempts=_bounded_int(alvo_data.get("tentativas")),
                    independent_rate=_bounded_percent(alvo_data.get("indep_percent")),
                    prompt_rate=_bounded_percent(alvo_data.get("dicas_percent")),
                    success_rate=_bounded_percent(alvo_data.get("acertos_percentual")),
                    date=data_alvo,
                    prompt_type=sanitize_text(alvo_data.get("prompt_type", "Nao especificada"), max_length=120),
                    evolution=evolucao_alvo,
                )
            )
        elif evolucao_alvo and not getattr(existe_alvo, "evolution", None):
            existe_alvo.evolution = evolucao_alvo

    for int_data in dados.get("interferentes", []):
        nome_int = sanitize_text(int_data.get("nome", ""), max_length=200, allow_newlines=False)
        data_int = _clinical_date(int_data.get("data"))
        if not nome_int or not data_int:
            continue

        db_beh = session.query(InterferingBehavior).filter_by(patient_id=patient.id, name=nome_int).first()
        if not db_beh:
            db_beh = InterferingBehavior(patient_id=patient.id, name=nome_int)
            session.add(db_beh)
            session.commit()
            session.refresh(db_beh)

        existe_int = session.query(InterferingRecord).filter_by(behavior_id=db_beh.id, date=data_int).first()
        evolucao_int = sanitize_text(int_data.get("evolucao", ""), max_length=4000, allow_newlines=True)

        if not existe_int:
            session.add(
                InterferingRecord(
                    behavior_id=db_beh.id,
                    date=data_int,
                    therapist=sanitize_text(int_data.get("terapeuta", ""), max_length=120),
                    count=_bounded_int(int_data.get("contagem")),
                    rate=_bounded_percent(int_data.get("taxa")),
                    evolution=evolucao_int,
                )
            )
        elif evolucao_int and not getattr(existe_int, "evolution", None):
            existe_int.evolution = evolucao_int

    session.commit()
    print(f"  Paciente {patient_hash[:12]} sincronizado.")


if __name__ == "__main__":
    sincronizar_bhave_api()

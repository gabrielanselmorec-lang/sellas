"""Auditoria somente leitura de todos os pacientes com acontecimentos ABC fechado."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text

from app.services import abc_closed
from app.services.abc_integrity import build_time_summary, has_behavior
from app.services.abc_methodology import audit_abc_data_quality, compute_exposure_summary


def verify_all_patients(engine):
    # Não inicializa nem migra tabelas durante uma auditoria.
    abc_closed._ABC_TABLES_READY = True
    with engine.connect() as conn:
        names = {abc_closed.patient_token_uuid(row.id): row.name or "Paciente sem nome"
                 for row in conn.execute(text("SELECT id,name FROM patients"))}
        targets = conn.execute(text("""
            SELECT s.patient_token::text AS token,COUNT(DISTINCT i.id) AS total
            FROM abc_sessions s JOIN abc_intervals i ON i.sessao_id=s.id
            JOIN abc_interval_events e ON e.intervalo_id=i.id
            JOIN abc_categories c ON c.id=e.categoria_id
            WHERE i.status_observacao='observed' AND e.ocorreu IS TRUE AND c.tipo='comportamento'
            GROUP BY s.patient_token ORDER BY s.patient_token
        """)).mappings().all()
        original = _fingerprints(conn)
    results = []
    for target in targets:
        token = target["token"]
        patient = names.get(token, f"Vínculo de paciente ausente ({token[-8:]})")
        try:
            records = abc_closed.load_patient_records(engine, token)
            intervals = abc_closed.load_observation_metadata(engine, patient_token=token)
            distribution = build_time_summary(records)
            exposure = compute_exposure_summary(records, intervals)
            audit = audit_abc_data_quality(records, intervals)
            report = abc_closed.build_abc_printable_summary(patient_name=patient,records=records,intervals=intervals)
            counts = Counter(row.get("classificacao") for row in records)
            duplicate_groups = {}
            for record in records:
                key = tuple(str(record.get(field) or "") for field in ("data_hora","session_id","ambiente","antecedente","comportamento","consequencia"))
                duplicate_groups.setdefault(key, []).append(record)
            duplicates = [{"data_hora":key[0],"ambiente":key[2],"antecedente":key[3],"comportamento":key[4],"consequencia":key[5],
                           "record_ids":[r["intervalo_id"] for r in group],"copies":len(group)}
                          for key,group in duplicate_groups.items() if len(group)>1]
            unclassified = [{"record_id":r["intervalo_id"],"data_hora":r["data_hora"],"comportamento":r["comportamento"]}
                            for r in records if r.get("classificacao")=="NC"]
            ids = [str(row["intervalo_id"]) for row in records]
            invariants = {
                "patient_binding_exists": token in names,
                "database_count_matches_loader": len(records) == target["total"],
                "unique_record_identifiers": len(ids) == len(set(ids)),
                "all_rows_have_positive_behavior": all(has_behavior(row) for row in records),
                "classification_totals_match": sum(counts.values()) == len(records),
                "time_partition_matches": distribution["horarios_validos"] + distribution["sem_horario"] + distribution["horarios_invalidos"] == len(records),
                "report_count_matches": report["observation_summary"]["total_records"] == len(records),
                "report_rate_matches": report["observation_summary"]["occurrences_per_observed_hour"] == exposure["occurrences_per_hour"],
                "no_rate_without_real_observation": exposure["observed_intervals_with_duration"] > 0 or exposure["occurrences_per_hour"] is None,
                "no_artificial_observation_duration": all(row.get("duracao_planejada_minutos") is None for row in intervals if row.get("record_kind") == "episode"),
            }
            with engine.connect() as conn:
                multiple = conn.execute(text("""SELECT COUNT(*) FROM (
                    SELECT i.id FROM abc_sessions s JOIN abc_intervals i ON i.sessao_id=s.id
                    JOIN abc_interval_events e ON e.intervalo_id=i.id JOIN abc_categories c ON c.id=e.categoria_id
                    WHERE s.patient_token=CAST(:p AS UUID) AND i.status_observacao='observed'
                      AND e.ocorreu IS TRUE AND c.tipo='comportamento'
                    GROUP BY i.id HAVING COUNT(*)>1) affected"""),{"p":token}).scalar_one()
            invariants["one_behavior_per_closed_record"] = multiple == 0
            peaks = distribution["por_horario"]
            peak_count = max((row["quantidade"] for row in peaks),default=0)
            results.append({"patient":patient,"status":"ok" if all(invariants.values()) else "requires_review",
                "records":len(records),"days":distribution["dias"],"start":distribution["inicio"],"end":distribution["fim"],
                "classification":dict(counts),"c1_by_responsible_rule":sum(bool(r.get("classificacao_assumida")) for r in records),"peak_hours":[row for row in peaks if row["quantidade"]==peak_count],
                "unknown_times":distribution["sem_horario"],"invalid_times":distribution["horarios_invalidos"],
                "legacy_precision":distribution["precisao_legada"],"approximate_times":distribution["horarios_aproximados"],
                "possible_duplicate_rows":audit["duplicate_exact_rows"],"duplicate_ids":audit["duplicate_identifiers"],
                "real_observation_windows":exposure["observed_intervals_with_duration"],
                "hourly_rate":exposure["occurrences_per_hour"],"invariants":invariants,
                "missing_episode_fields":report["data_quality"].get("missing_episode_fields",0),
                "source_review_required":bool(duplicates or unclassified),
                "possible_duplicate_groups":duplicates,"unclassified_records":unclassified})
        except Exception as exc:
            results.append({"patient":patient,"status":"error","error_type":type(exc).__name__})
    with engine.connect() as conn:
        preserved = original == _fingerprints(conn)
    return {"generated_at":datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
            "read_only":True,"original_data_preserved":preserved,
            "patients":len(results),"total_records":sum(row.get("records",0) for row in results),
            "results":results}


def _fingerprints(conn):
    result = {}
    for table in ("abc_sessions","abc_intervals","abc_interval_events","abc_categories"):
        row=conn.execute(text(f"SELECT COUNT(*) AS count,md5(COALESCE(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY id::text),'')) AS fingerprint FROM {table} t")).mappings().one()
        result[table]=dict(row)
    return result


def configure_read_only(engine):
    def enforce_read_only(connection):
        # Só a transação atual; parâmetros de sessão podem vazar no pooler.
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    event.listen(engine,"begin",enforce_read_only)
    return enforce_read_only


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root/".env")
    engine = create_engine(os.getenv("SELLAS_DATABASE_URL") or os.getenv("DATABASE_URL"),connect_args={"connect_timeout":15})
    configure_read_only(engine)
    result=verify_all_patients(engine)
    folder=root/"output"/f"abc_all_patients_{result['generated_at'][:10]}"
    folder.mkdir(exist_ok=True,parents=True)
    (folder/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# Verificação dos ABCs fechados de todos os pacientes", "",f"Gerado em {result['generated_at']}.",
           "",f"Pacientes: **{result['patients']}**. Registros: **{result['total_records']}**.",
           f"Somente leitura: **sim**. Dados originais preservados: **{'sim' if result['original_data_preserved'] else 'divergência detectada'}**.",
           "", "| Paciente | Registros | Dias | Faixa com mais registros | Verificação |",
           "|---|---:|---:|---|---|"]
    for row in result["results"]:
        peak=", ".join(f"{v['inicio']}–{v['fim']} ({v['quantidade']})" for v in row.get("peak_hours",[])) or "—"
        status = row["status"] + ("; conferir folhas" if row.get("source_review_required") else "")
        lines.append(f"| {row['patient'].replace('|','/')} | {row.get('records','—')} | {row.get('days','—')} | {peak} | {status} |")
    lines.extend(["", "As faixas mostram contagens dos horários salvos, no fuso America/Sao_Paulo. Precisão legada não confirma segundos. Taxas por hora ficam indisponíveis sem janelas reais e completas de observação.","",
                  "A verificação cobre a consistência do banco, carregamento, classificação, distribuição temporal e resumo do relatório. Ela não confirma a exatidão clínica das anotações nem substitui a conferência das folhas."])
    for row in result["results"]:
        lines.extend(["",f"## {row['patient']}",""])
        if row["status"]=="error":
            lines.append(f"Erro: {row['error_type']}.")
            continue
        lines.extend([f"Período: {row['start']} a {row['end']}. C1: {row['classification'].get('C1',0)}; C2: {row['classification'].get('C2',0)}; sem classificação: {row['classification'].get('NC',0)}.",
            f"C1 atribuídos pela regra do responsável: {row.get('c1_by_responsible_rule',0)}. Horários ausentes: {row['unknown_times']}; inválidos: {row['invalid_times']}; precisão legada: {row['legacy_precision']}; aproximados: {row['approximate_times']}.",
            f"Possíveis duplicações clínicas: {row['possible_duplicate_rows']}; identificadores duplicados: {row['duplicate_ids']}; campos A/C não informados: {row['missing_episode_fields']}.",
            f"Janelas reais de observação: {row['real_observation_windows']}; taxa por hora: {row['hourly_rate'] if row['hourly_rate'] is not None else 'não calculável'}.",
            "Verificações: "+", ".join(f"{name}={'ok' if passed else 'FALHOU'}" for name,passed in row['invariants'].items())+"."])
        for group in row.get("possible_duplicate_groups",[]):
            lines.append(f"Possível repetição: {group['data_hora']} | {group['ambiente']} | {group['antecedente']} → {group['comportamento']} → {group['consequencia']} | {group['copies']} registros. IDs: {', '.join(group['record_ids'])}.")
        for item in row.get("unclassified_records",[]):
            lines.append(f"Sem C1/C2: {item['data_hora']} | {item['comportamento']} | ID {item['record_id']}.")
    (folder/"verification.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if result["original_data_preserved"] and all(row["status"]=="ok" for row in result["results"]) else 1


if __name__=="__main__":
    raise SystemExit(main())

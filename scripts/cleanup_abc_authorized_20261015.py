"""Limpeza autorizada pelo responsável: duplicações apontadas e NC como C1."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
from app.services import abc_closed

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/"storage"/"backups"/"abc_cleanup_2026-10-15"


def main():
    if "--apply" not in sys.argv:
        raise SystemExit("Use --apply somente para executar a limpeza autorizada.")
    FOLDER.mkdir(parents=True,exist_ok=True)
    receipt=FOLDER/"applied.json"
    if receipt.exists():
        print(receipt.read_text(encoding="utf-8"))
        return
    load_dotenv(ROOT/".env")
    engine=create_engine(os.getenv("SELLAS_DATABASE_URL") or os.getenv("DATABASE_URL"),connect_args={"connect_timeout":15})
    abc_closed._ABC_TABLES_READY=True
    source_audit=json.loads((ROOT/"output"/"abc_all_patients_2026-10-15"/"verification.json").read_text(encoding="utf-8"))
    groups=[group for row in source_audit["results"] for group in row.get("possible_duplicate_groups",[])]
    duplicate_ids={identifier for group in groups for identifier in group["record_ids"]}
    removed=[]
    reclassified=[]
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout='5s'"))
        tokens=conn.execute(text("SELECT DISTINCT s.patient_token::text FROM abc_sessions s JOIN abc_intervals i ON i.sessao_id=s.id WHERE i.id::text=ANY(:ids)"),{"ids":sorted(duplicate_ids)}).scalars().all()
        for token in sorted(tokens):
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),{"key":f"sellas:abc:save:{token}"})
        unclassified=conn.execute(text("""SELECT DISTINCT i.id::text FROM abc_sessions s JOIN abc_intervals i ON i.sessao_id=s.id
            JOIN abc_interval_events e ON e.intervalo_id=i.id JOIN abc_categories c ON c.id=e.categoria_id
            WHERE i.status_observacao='observed' AND e.ocorreu IS TRUE AND c.tipo='comportamento'
              AND (e.intensidade IS NULL OR e.intensidade NOT IN (1,2))""")).scalars().all()
        affected=sorted(duplicate_ids|set(unclassified))
        locked=conn.execute(text("SELECT i.id FROM abc_intervals i WHERE i.id::text=ANY(:ids) FOR UPDATE"),{"ids":affected}).all()
        if len(locked)!=len(affected):
            raise ValueError("Os registros mudaram desde a verificação; gere outra auditoria antes de executar.")
        intervals=[dict(r) for r in conn.execute(text("SELECT * FROM abc_intervals WHERE id::text=ANY(:ids)"),{"ids":affected}).mappings()]
        session_ids=[str(r["sessao_id"]) for r in intervals]
        sessions=[dict(r) for r in conn.execute(text("SELECT * FROM abc_sessions WHERE id::text=ANY(:ids)"),{"ids":session_ids}).mappings()]
        events=[dict(r) for r in conn.execute(text("SELECT * FROM abc_interval_events WHERE intervalo_id::text=ANY(:ids)"),{"ids":affected}).mappings()]
        chains=[dict(r) for r in conn.execute(text("SELECT * FROM abc_chain_candidates WHERE from_interval_id::text=ANY(:ids) OR to_interval_id::text=ANY(:ids)"),{"ids":affected}).mappings()]
        chain_ids=[str(r["id"]) for r in chains]
        reviews=[dict(r) for r in conn.execute(text("SELECT * FROM abc_chain_review_logs WHERE chain_candidate_id::text=ANY(:ids)"),{"ids":chain_ids}).mappings()]
        backup={"created_at":datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
            "authorization":"Responsável solicitou remover duplicações e considerar registros sem C1/C2 como C1.",
            "source_audit":source_audit,"abc_sessions":sessions,"abc_intervals":intervals,
            "abc_interval_events":events,"abc_chain_candidates":chains,"abc_chain_review_logs":reviews}
        (FOLDER/"before.json").write_text(json.dumps(backup,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        interval_by_id={str(row["id"]):row for row in intervals}
        token_by_session={str(row["id"]):str(row["patient_token"]) for row in sessions}
        records={identifier:abc_closed._load_interval_record(conn,token_by_session[str(interval_by_id[identifier]["sessao_id"])],identifier) for identifier in affected}
        for group in groups:
            entries=[records[identifier] for identifier in group["record_ids"]]
            signatures={tuple(str(row.get(key) or "") for key in ("data_hora","ambiente","antecedente_codigo","comportamento_codigo","consequencia_codigo")) for row in entries}
            if len(signatures)!=1:
                raise ValueError("O grupo não corresponde ao mesmo acontecimento ABC; limpeza revertida.")
            def priority(row):
                assumed=bool(row.get("classificacao_assumida"))
                return (-int(row.get("classificacao")=="C2"),int(assumed),interval_by_id[row["intervalo_id"]]["criado_em"],row["intervalo_id"])
            entries.sort(key=priority)
            kept=entries[0]["intervalo_id"]
            for row in entries[1:]:
                identifier=row["intervalo_id"]
                token=token_by_session[str(interval_by_id[identifier]["sessao_id"])]
                abc_closed._write_action_log(conn,patient_token=token,action="registro_removido",interval_id=identifier,
                    snapshot={"motivo":"duplicacao_confirmada_pelo_responsavel","registro_preservado":kept,"registro_original":row})
                conn.execute(text("DELETE FROM abc_intervals WHERE id=CAST(:i AS UUID)"),{"i":identifier})
                conn.execute(text("DELETE FROM abc_sessions s WHERE s.id=CAST(:s AS UUID) AND NOT EXISTS(SELECT 1 FROM abc_intervals i WHERE i.sessao_id=s.id)"),{"s":interval_by_id[identifier]["sessao_id"]})
                removed.append({"removed":identifier,"kept":kept})
        removed_ids={row["removed"] for row in removed}
        for identifier in unclassified:
            if identifier in removed_ids:
                continue
            conflicting=conn.execute(text("""SELECT COUNT(*) FROM abc_interval_events e JOIN abc_categories c ON c.id=e.categoria_id
                WHERE e.intervalo_id=CAST(:i AS UUID) AND c.tipo='comportamento' AND e.ocorreu IS TRUE
                  AND (e.causou_lesao IS TRUE OR e.houve_sangramento IS TRUE OR e.direcionado_ponto_vital IS TRUE)"""),{"i":identifier}).scalar_one()
            if conflicting:
                raise ValueError("Há critério de C2 em um registro sem classificação; limpeza revertida para preservar o dado.")
            conn.execute(text("""UPDATE abc_interval_events e SET intensidade=1 FROM abc_categories c
                WHERE e.categoria_id=c.id AND e.intervalo_id=CAST(:i AS UUID) AND c.tipo='comportamento' AND e.ocorreu IS TRUE"""),{"i":identifier})
            conn.execute(text("""UPDATE abc_intervals SET metadados=metadados || CAST(:metadata AS JSONB) WHERE id=CAST(:i AS UUID)"""),
                {"i":identifier,"metadata":json.dumps({"classificacao_original":"NC","classificacao_assumida":True,"classificacao_origem":"regra_responsavel_sem_classificacao_C1"})})
            token=token_by_session[str(interval_by_id[identifier]["sessao_id"])]
            abc_closed._write_action_log(conn,patient_token=token,action="registro_editado",interval_id=identifier,
                snapshot={"motivo":"C1_por_regra_explicita_do_responsavel","classificacao_original":"NC","classificacao":"C1","criterios_de_lesao_nao_inferidos":True})
            reclassified.append(identifier)
        remaining=conn.execute(text("""SELECT COUNT(*) FROM abc_intervals i JOIN abc_interval_events e ON e.intervalo_id=i.id
            JOIN abc_categories c ON c.id=e.categoria_id WHERE i.status_observacao='observed' AND c.tipo='comportamento'
              AND e.ocorreu IS TRUE AND (e.intensidade IS NULL OR e.intensidade NOT IN(1,2))""")).scalar_one()
        assert remaining==0,"Restaram registros sem classificação."
        assert len(removed)==sum(group["copies"]-1 for group in groups)
    result={"applied":True,"removed_duplicates":len(removed),"reclassified_remaining_records":len(reclassified),
            "unclassified_duplicates_removed":len(set(unclassified)&{row['removed'] for row in removed}),
            "removed":removed,"reclassified":reclassified,"backup":str(FOLDER/"before.json")}
    receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=="__main__":
    main()

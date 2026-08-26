"""Regras clínicas puras do PEI.

Todas as funções aqui recebem DataFrames e valores simples,
não dependem de estado Streamlit nem de chamadas HTTP.
"""
import re

import pandas as pd


def limpar_texto_pei(texto):
    texto = str(texto)
    substituicoes = {
        "‘": "",
        "’": "",
        "“": "",
        "”": "",
        "–": "",
        "—": "",
        "­": "",
        "​": "",
        "﻿": "",
        "•": "-",
    }
    for antigo, novo in substituicoes.items():
        texto = texto.replace(antigo, novo)
    return re.sub(r"[\U0001F300-\U0001FAFF☀-➿]️?", "", texto).strip()


def limpar_nome_objetivo(texto):
    """Remove código interno entre parênteses vindo da base de dados."""
    texto = limpar_texto_pei(texto)
    # A regex \s*\([^)]*\) remove tudo que estiver entre parênteses, incluindo espaços ao redor
    return re.sub(r'\s*\([^)]*\)', '', texto).strip()


def verificar_alvos_clean(objetivo_texto):
    if not objetivo_texto or str(objetivo_texto).strip() == "" or str(objetivo_texto).lower() in ["nan", "none"]:
        return "Descrição não informada."
    return str(objetivo_texto)


def calcular_evolucao_trimestral(desempenho_anterior, desempenho_atual, criterio=90):
    if pd.isna(desempenho_atual):
        return "-"
    if desempenho_atual >= criterio:
        return "Atingiu (ATG)"
    if pd.isna(desempenho_anterior):
        return "Em Avaliação"
    if desempenho_atual > desempenho_anterior + 5:
        return "Avançou (AVA)"
    if desempenho_atual < desempenho_anterior - 5:
        return "Agravou (AGR)"
    return "Estabilizou (EST)"


def resposta_ia_invalida(texto):
    texto_limpo = str(texto or "").strip()
    texto_cf = texto_limpo.casefold()
    marcadores_bloqueados = [
        "```", "{", "}", "[", "]",
        "json", "python", "summary", "behavior",
        "patient_context", "fontes_recuperadas",
        "dados_do_paciente", "response",
    ]
    return not texto_limpo or any(marcador in texto_cf for marcador in marcadores_bloqueados)


def filtrar_periodo_pei(df, inicio, fim_exclusivo):
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame() if df is None else df.copy()
    df_periodo = df.copy()
    df_periodo["date_pd"] = pd.to_datetime(df_periodo["date"], errors="coerce")
    df_periodo = df_periodo.dropna(subset=["date_pd"])
    return df_periodo[
        (df_periodo["date_pd"] >= pd.Timestamp(inicio))
        & (df_periodo["date_pd"] < pd.Timestamp(fim_exclusivo))
    ].copy()


def limiar_programa_pei(programa, df_lib, padrao=90):
    if df_lib.empty or "name" not in df_lib.columns or not programa:
        return padrao
    registros = df_lib[df_lib["name"] == programa]
    if registros.empty:
        return padrao
    valor = registros.iloc[0].get("mastery_threshold_percent", padrao)
    return float(valor) if pd.notna(valor) else padrao


def status_por_independencia(inicial, atual, limiar=90):
    if pd.isna(atual):
        return "Sem registro"
    if atual >= limiar:
        return "Atingiu"
    if pd.isna(inicial):
        return "Em avaliação"
    if atual > inicial + 5:
        return "Avançou"
    if atual < inicial - 5:
        return "Agravou"
    return "Estabilizou"


def preparar_alvos_programa(df_alvos, programa):
    if (
        df_alvos is None
        or df_alvos.empty
        or "programa" not in df_alvos.columns
        or "target_name" not in df_alvos.columns
    ):
        return pd.DataFrame()
    df_prog_alvos = df_alvos[df_alvos["programa"] == programa].copy()
    if df_prog_alvos.empty:
        return pd.DataFrame()
    if "date_pd" not in df_prog_alvos.columns and "date" in df_prog_alvos.columns:
        df_prog_alvos["date_pd"] = pd.to_datetime(df_prog_alvos["date"], errors="coerce")
    if "independent_rate" in df_prog_alvos.columns:
        df_prog_alvos["independent_rate"] = pd.to_numeric(
            df_prog_alvos["independent_rate"], errors="coerce"
        )
    if "phase" not in df_prog_alvos.columns:
        df_prog_alvos["phase"] = ""
    return df_prog_alvos


def resumo_alvos_programa(df_alvos, programa, df_lib, df_alvos_periodo=None):
    colunas = ["Alvo", "LinhaBase", "Periodo", "Resumo", "Status"]
    df_prog_alvos = preparar_alvos_programa(df_alvos, programa)
    df_prog_periodo = preparar_alvos_programa(
        df_alvos_periodo if df_alvos_periodo is not None else df_alvos, programa
    )
    if df_prog_periodo.empty:
        return pd.DataFrame(columns=colunas)

    limiar = limiar_programa_pei(programa, df_lib)
    rows = []
    for alvo, grupo_periodo in df_prog_periodo.groupby("target_name"):
        grupo_todos = df_prog_alvos[df_prog_alvos["target_name"] == alvo].copy()
        if grupo_todos.empty:
            grupo_todos = grupo_periodo.copy()
        grupo_todos = grupo_todos.sort_values("date_pd") if "date_pd" in grupo_todos.columns else grupo_todos.copy()
        grupo_periodo = grupo_periodo.sort_values("date_pd") if "date_pd" in grupo_periodo.columns else grupo_periodo.copy()

        serie_todos = grupo_todos["independent_rate"].dropna() if "independent_rate" in grupo_todos.columns else pd.Series(dtype=float)
        serie_periodo = grupo_periodo["independent_rate"].dropna() if "independent_rate" in grupo_periodo.columns else pd.Series(dtype=float)
        mask_lb = grupo_todos["phase"].astype(str).str.contains(
            r"linha de base|\blb\b|baseline|sondagem", case=False, na=False
        )
        serie_lb = grupo_todos.loc[mask_lb, "independent_rate"].dropna() if "independent_rate" in grupo_todos.columns else pd.Series(dtype=float)
        inicial = serie_lb.mean() if not serie_lb.empty else (serie_todos.iloc[0] if not serie_todos.empty else pd.NA)
        atual = serie_periodo.iloc[-1] if not serie_periodo.empty else pd.NA
        media_ind = serie_periodo.mean() if not serie_periodo.empty else pd.NA
        linha_base_txt = (
            f"{inicial:.1f}% de independência na linha de base"
            if pd.notna(inicial) else "linha de base sem registro percentual"
        )
        periodo_txt = (
            f"{media_ind:.1f}% de independência no período observado"
            if pd.notna(media_ind) else "período observado sem registro percentual"
        )
        rows.append({
            "Alvo": limpar_texto_pei(alvo),
            "LinhaBase": linha_base_txt,
            "Periodo": periodo_txt,
            "Resumo": f"{linha_base_txt}; {periodo_txt}",
            "Status": status_por_independencia(inicial, atual, limiar),
        })
    return pd.DataFrame(rows, columns=colunas).sort_values(["Status", "Alvo"]).reset_index(drop=True)


def resumo_objetivos_por_area(areas):
    rows = []
    for area_num, itens in areas.items():
        for item_index, item in enumerate(itens):
            rows.append({
                "Objetivo": f"Objetivo {area_num}.{item_index + 1}",
                "Descrição": limpar_texto_pei(item.get("texto", "")),
            })
        if not itens:
            rows.append({
                "Objetivo": "-",
                "Descrição": "Sem objetivos cadastrados para esta área.",
            })
    return pd.DataFrame(rows, columns=["Objetivo", "Descrição"])


def formatar_lista_alvos_corrida(alvos):
    if alvos.empty:
        return "Sem alvos trabalhados no período"
    partes = []
    for _, alvo_row in alvos.iterrows():
        alvo = limpar_texto_pei(alvo_row.get("Alvo", ""))
        resumo = limpar_texto_pei(alvo_row.get("Resumo", ""))
        partes.append(f"{alvo} ({resumo})")
    return "; ".join(partes)


def status_geral_alvos(alvos):
    if alvos.empty:
        return "Sem registro"
    prioridades = {"Agravou": 0, "Estabilizou": 1, "Em avaliação": 2, "Avançou": 3, "Atingiu": 4}
    return sorted(
        alvos["Status"].tolist(),
        key=lambda status: prioridades.get(str(status), 2),
        reverse=True,
    )[0]


def periodo_aplicacao_programa(df_hist, programa, periodo_inicio, periodo_fim):
    fallback = f"{periodo_inicio.strftime('%d/%m/%Y')} a {periodo_fim.strftime('%d/%m/%Y')}"
    if df_hist is None or df_hist.empty or not programa or "programa" not in df_hist.columns or "date" not in df_hist.columns:
        return fallback
    hist = df_hist[df_hist["programa"] == programa].copy()
    if hist.empty:
        return fallback
    hist["date_pd"] = pd.to_datetime(hist["date"], errors="coerce")
    hist = hist.dropna(subset=["date_pd"])
    if hist.empty:
        return fallback

    inicio_ts = pd.Timestamp(periodo_inicio)
    fim_ts = pd.Timestamp(periodo_fim)
    hist_periodo = hist[(hist["date_pd"] >= inicio_ts) & (hist["date_pd"] <= fim_ts)]
    if hist_periodo.empty:
        return fallback

    primeira = hist_periodo["date_pd"].min()
    ultima = hist_periodo["date_pd"].max()
    inicio_real = max(inicio_ts, primeira)
    fim_real = ultima
    return f"{inicio_real.strftime('%d/%m/%Y')} a {fim_real.strftime('%d/%m/%Y')}"


def texto_desempenho_inicial(valor):
    return (
        f"{valor:.1f}% de independência na linha de base"
        if pd.notna(valor) else "Linha de base sem registro percentual"
    )


def classificar_area_pei(programa, objetivo):
    texto = f"{programa} {objetivo}".lower()
    if any(chave in texto for chave in ["avd", "vida diária", "vida diaria", "higiene", "banheiro", "vestir", "aliment", "escovar", "lavar"]):
        return 5
    if any(chave in texto for chave in ["social", "turno", "grupo", "espera", "compartilhar", "interferente", "comportamento", "crise", "fuga"]):
        return 4
    if any(chave in texto for chave in ["brinc", "jogo", "brinquedo", "faz de conta", "lúdico", "ludico"]):
        return 3
    if any(chave in texto for chave in ["recept", "ouvinte", "instru", "discrimina", "identificar", "apontar", "parear"]):
        return 2
    return 1


def texto_objetivo_programa(row, verificar_fn=None):
    objetivo = (verificar_fn or verificar_alvos_clean)(row.get("objective", ""))
    programa = str(row.get("programa", "")).strip()
    if objetivo and objetivo != "Descrição não informada.":
        return limpar_nome_objetivo(objetivo)
    return limpar_nome_objetivo(programa) or "Objetivo não informado."


def distribuir_objetivos_por_area(df_prog, df_beh):
    areas = {1: [], 2: [], 3: [], 4: [], 5: []}
    df_prog_unicos = df_prog.drop_duplicates(subset=["programa"]) if not df_prog.empty else pd.DataFrame()
    for _, row in df_prog_unicos.iterrows():
        programa = str(row.get("programa", "")).strip()
        objetivo = texto_objetivo_programa(row)
        area = classificar_area_pei(programa, objetivo)
        areas[area].append({"programa": programa, "texto": objetivo})

    if not df_beh.empty and "comportamento" in df_beh.columns:
        comportamentos = sorted([str(v) for v in df_beh["comportamento"].dropna().unique() if str(v).strip()])
        if comportamentos:
            texto = (
                "Reduzir a taxa dos comportamentos interferentes monitorados "
                f"({', '.join(comportamentos[:8])}) com base nos registros clínicos do período."
            )
            areas[4].insert(0, {"programa": "Comportamentos interferentes", "texto": texto})
    return areas

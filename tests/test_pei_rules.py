"""Testes unitários para app/services/pei_rules.py

Cobrem os casos clínicos críticos listados no RELATORIO_MELHORIAS_CODEX:
- objetivo novo dentro do período deve iniciar na primeira aplicação real
- objetivo encerrado antes do fim deve terminar na última aplicação real
- objetivo sem aplicação no período não deve aparecer
- status_por_independencia: transições corretas
- filtrar_periodo_pei: corte de datas correto
"""
import sys
import os

# Adiciona a raiz do projeto ao path para que os imports funcionem sem instalar o pacote
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest

from app.services.pei_rules import (
    calcular_evolucao_trimestral,
    filtrar_periodo_pei,
    formatar_lista_alvos_corrida,
    limiar_programa_pei,
    limpar_texto_pei,
    periodo_aplicacao_programa,
    preparar_alvos_programa,
    resumo_alvos_programa,
    status_geral_alvos,
    status_por_independencia,
    verificar_alvos_clean,
)
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Helpers de fixture
# ---------------------------------------------------------------------------

def _hist(programa, datas, ind_rates, phases=None):
    """Cria DataFrame de histórico para um programa."""
    n = len(datas)
    return pd.DataFrame({
        "programa": [programa] * n,
        "date": [str(d) for d in datas],
        "independent_rate": ind_rates,
        "phase": phases if phases else [""] * n,
    })


def _alvos(programa, alvos_nomes, datas, ind_rates, phases=None):
    """Cria DataFrame de alvos para um programa."""
    rows = []
    for alvo, data, ind, phase in zip(
        alvos_nomes, datas, ind_rates, phases if phases else [""] * len(datas)
    ):
        rows.append({
            "programa": programa,
            "target_name": alvo,
            "date": str(data),
            "independent_rate": ind,
            "phase": phase,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# filtrar_periodo_pei
# ---------------------------------------------------------------------------

class TestFiltrarPeriodoPei:
    def test_retorna_apenas_datas_no_intervalo(self):
        df = pd.DataFrame({
            "date": ["2026-07-01", "2026-07-15", "2026-10-01"],
            "programa": ["A", "A", "A"],
        })
        resultado = filtrar_periodo_pei(df, date(2026, 7, 1), date(2026, 10, 1))
        assert len(resultado) == 2
        assert "2026-07-01" in resultado["date"].values
        assert "2026-07-15" in resultado["date"].values

    def test_fim_exclusivo_nao_incluido(self):
        df = pd.DataFrame({
            "date": ["2026-09-30", "2026-10-01"],
            "programa": ["A", "A"],
        })
        resultado = filtrar_periodo_pei(df, date(2026, 7, 1), date(2026, 10, 1))
        assert len(resultado) == 1
        assert resultado["date"].values[0] == "2026-09-30"

    def test_df_vazio_retorna_vazio(self):
        resultado = filtrar_periodo_pei(pd.DataFrame(), date(2026, 7, 1), date(2026, 10, 1))
        assert resultado.empty

    def test_sem_coluna_date_retorna_copia(self):
        df = pd.DataFrame({"programa": ["A", "B"]})
        resultado = filtrar_periodo_pei(df, date(2026, 7, 1), date(2026, 10, 1))
        assert len(resultado) == 2

    def test_none_retorna_df_vazio(self):
        resultado = filtrar_periodo_pei(None, date(2026, 7, 1), date(2026, 10, 1))
        assert resultado.empty


# ---------------------------------------------------------------------------
# periodo_aplicacao_programa
# ---------------------------------------------------------------------------

class TestPeriodoAplicacaoPrograma:
    def test_objetivo_novo_inicia_na_primeira_aplicacao_real(self):
        """Objetivo com aplicações em 10/07 e 05/08 num período 01/07–30/09
        deve retornar 10/07 como início."""
        df_hist = _hist("Prog A", ["2026-07-10", "2026-08-05"], [60, 70])
        resultado = periodo_aplicacao_programa(
            df_hist, "Prog A", date(2026, 7, 1), date(2026, 9, 30)
        )
        assert "10/07/2026" in resultado
        assert "05/08/2026" in resultado

    def test_objetivo_encerrado_antes_do_fim_termina_na_ultima_aplicacao(self):
        """Objetivo com aplicações em 15/08, 01/09 e 30/09 num período 01/07–30/09
        deve terminar em 30/09."""
        df_hist = _hist("Prog B", ["2026-08-15", "2026-09-01", "2026-09-30"], [50, 60, 75])
        resultado = periodo_aplicacao_programa(
            df_hist, "Prog B", date(2026, 7, 1), date(2026, 9, 30)
        )
        assert "15/08/2026" in resultado
        assert "30/09/2026" in resultado

    def test_objetivo_sem_aplicacao_no_periodo_retorna_fallback(self):
        """Objetivo com aplicações apenas fora do período não deve retornar dados reais."""
        df_hist = _hist("Prog C", ["2026-01-10", "2026-02-05"], [40, 50])
        resultado = periodo_aplicacao_programa(
            df_hist, "Prog C", date(2026, 7, 1), date(2026, 9, 30)
        )
        # Deve usar o fallback (datas do período, não as datas reais fora do período)
        assert "01/07/2026" in resultado
        assert "30/09/2026" in resultado

    def test_df_vazio_retorna_fallback(self):
        resultado = periodo_aplicacao_programa(
            pd.DataFrame(), "Prog X", date(2026, 7, 1), date(2026, 9, 30)
        )
        assert "01/07/2026" in resultado

    def test_programa_inexistente_retorna_fallback(self):
        df_hist = _hist("Prog A", ["2026-07-10"], [60])
        resultado = periodo_aplicacao_programa(
            df_hist, "Prog Z", date(2026, 7, 1), date(2026, 9, 30)
        )
        assert "01/07/2026" in resultado


# ---------------------------------------------------------------------------
# status_por_independencia
# ---------------------------------------------------------------------------

class TestStatusPorIndependencia:
    def test_sem_registro_quando_atual_nulo(self):
        assert status_por_independencia(50, pd.NA) == "Sem registro"

    def test_atingiu_quando_acima_do_limiar(self):
        assert status_por_independencia(80, 92) == "Atingiu"
        assert status_por_independencia(None, 90) == "Atingiu"

    def test_em_avaliacao_sem_inicial(self):
        assert status_por_independencia(pd.NA, 50) == "Em avaliação"

    def test_avancou_quando_subiu_mais_de_5(self):
        assert status_por_independencia(40, 50) == "Avançou"

    def test_agravou_quando_caiu_mais_de_5(self):
        assert status_por_independencia(60, 50) == "Agravou"

    def test_estabilizou_quando_variacao_pequena(self):
        assert status_por_independencia(50, 53) == "Estabilizou"
        assert status_por_independencia(50, 47) == "Estabilizou"

    def test_limiar_customizado(self):
        assert status_por_independencia(80, 85, limiar=85) == "Atingiu"
        assert status_por_independencia(80, 84, limiar=85) == "Estabilizou"


# ---------------------------------------------------------------------------
# resumo_alvos_programa
# ---------------------------------------------------------------------------

class TestResumoAlvosPrograma:
    def test_objetivo_sem_aplicacao_no_periodo_nao_aparece(self):
        """Alvos sem registro no período não devem aparecer no resumo."""
        df_alvos_todos = _alvos(
            "Prog A",
            ["Alvo 1", "Alvo 2"],
            ["2026-01-10", "2026-01-15"],
            [60, 70],
        )
        # Período vazio — nenhum alvo dentro
        df_alvos_periodo = pd.DataFrame()
        df_lib = pd.DataFrame()

        resultado = resumo_alvos_programa(df_alvos_todos, "Prog A", df_lib, df_alvos_periodo)
        assert resultado.empty

    def test_alvo_com_aplicacao_no_periodo_aparece(self):
        df_alvos = _alvos(
            "Prog A",
            ["Alvo 1"],
            ["2026-07-10"],
            [65],
        )
        df_lib = pd.DataFrame()
        resultado = resumo_alvos_programa(df_alvos, "Prog A", df_lib, df_alvos)
        assert len(resultado) == 1
        assert resultado.iloc[0]["Alvo"] == "Alvo 1"

    def test_status_atingiu_quando_acima_do_limiar(self):
        df_alvos = _alvos("Prog A", ["Alvo 1"], ["2026-07-10"], [95])
        df_lib = pd.DataFrame()
        resultado = resumo_alvos_programa(df_alvos, "Prog A", df_lib, df_alvos)
        assert resultado.iloc[0]["Status"] == "Atingiu"

    def test_programa_sem_alvos_retorna_vazio(self):
        df_alvos = _alvos("Prog B", ["Alvo 1"], ["2026-07-10"], [65])
        df_lib = pd.DataFrame()
        resultado = resumo_alvos_programa(df_alvos, "Prog A", df_lib, df_alvos)
        assert resultado.empty


# ---------------------------------------------------------------------------
# limpar_texto_pei
# ---------------------------------------------------------------------------

class TestLimparTextoPei:
    def test_remove_caracteres_especiais(self):
        resultado = limpar_texto_pei("Texto’ com aspas‘ e – travessao")
        assert "’" not in resultado
        assert "‘" not in resultado

    def test_remove_bullet_substitui_por_hifen(self):
        resultado = limpar_texto_pei("• item")
        assert resultado == "- item"

    def test_texto_normal_mantido(self):
        resultado = limpar_texto_pei("Objetivo terapêutico 1")
        assert resultado == "Objetivo terapêutico 1"


# ---------------------------------------------------------------------------
# verificar_alvos_clean
# ---------------------------------------------------------------------------

class TestVerificarAlvosClean:
    def test_texto_vazio_retorna_descricao_nao_informada(self):
        assert verificar_alvos_clean("") == "Descrição não informada."
        assert verificar_alvos_clean(None) == "Descrição não informada."
        assert verificar_alvos_clean("nan") == "Descrição não informada."

    def test_texto_valido_retorna_ele_mesmo(self):
        assert verificar_alvos_clean("Ensinar contato visual") == "Ensinar contato visual"


# ---------------------------------------------------------------------------
# calcular_evolucao_trimestral
# ---------------------------------------------------------------------------

class TestCalcularEvolucaoTrimestral:
    def test_atingiu(self):
        assert calcular_evolucao_trimestral(70, 92) == "Atingiu (ATG)"

    def test_avancou(self):
        assert calcular_evolucao_trimestral(40, 50) == "Avançou (AVA)"

    def test_agravou(self):
        assert calcular_evolucao_trimestral(60, 50) == "Agravou (AGR)"

    def test_estabilizou(self):
        assert calcular_evolucao_trimestral(50, 53) == "Estabilizou (EST)"

    def test_sem_registro_quando_atual_nulo(self):
        assert calcular_evolucao_trimestral(50, float("nan")) == "-"

    def test_em_avaliacao_sem_anterior(self):
        assert calcular_evolucao_trimestral(float("nan"), 50) == "Em Avaliação"


# ---------------------------------------------------------------------------
# status_geral_alvos
# ---------------------------------------------------------------------------

class TestStatusGeralAlvos:
    def test_retorna_mais_alto_em_prioridade(self):
        df = pd.DataFrame({"Status": ["Estabilizou", "Avançou", "Atingiu"]})
        assert status_geral_alvos(df) == "Atingiu"

    def test_df_vazio_retorna_sem_registro(self):
        assert status_geral_alvos(pd.DataFrame()) == "Sem registro"


# ---------------------------------------------------------------------------
# limiar_programa_pei
# ---------------------------------------------------------------------------

class TestLimiarProgramaPei:
    def test_retorna_limiar_do_programa(self):
        df_lib = pd.DataFrame({"name": ["Prog A"], "mastery_threshold_percent": [85.0]})
        assert limiar_programa_pei("Prog A", df_lib) == 85.0

    def test_retorna_padrao_quando_programa_nao_encontrado(self):
        df_lib = pd.DataFrame({"name": ["Prog B"], "mastery_threshold_percent": [85.0]})
        assert limiar_programa_pei("Prog A", df_lib) == 90

    def test_retorna_padrao_com_df_vazio(self):
        assert limiar_programa_pei("Prog A", pd.DataFrame()) == 90

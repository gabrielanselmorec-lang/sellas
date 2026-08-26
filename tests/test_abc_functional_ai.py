import app.services.knowledge_agent as knowledge_agent


def _analysis_fixture():
    chain = "Demanda -> Choro -> Pausa"
    return {
        "total_registros": 12,
        "comportamentos_distintos": 2,
        "ambientes_distintos": 2,
        "c1_leve": 8,
        "c2_intenso": 1,
        "nao_classificados": 3,
        "cadeias_completas": [
            {
                "cadeia": chain,
                "suporte": 5,
                "probabilidade_conjunta": 5 / 12,
                "probabilidade_comportamento_dado_antecedente": 0.71,
                "probabilidade_consequencia_dada_cadeia_ab": 0.83,
                "funcao_predominante": "Fuga ou esquiva",
                "c1_leve": 4,
                "c2_intenso": 1,
                "indice_perigo": 0.36,
            }
        ],
        "cadeias_por_ambiente": [
            {
                "ambiente": "Sala de aula",
                "cadeia": chain,
                "suporte": 4,
                "total_ambiente": 7,
                "probabilidade_no_ambiente": 4 / 7,
                "funcao_predominante": "Fuga ou esquiva",
            }
        ],
        "antecedente_comportamento": [
            {
                "antecedente": "Demanda",
                "comportamento": "Choro",
                "suporte": 6,
                "probabilidade_condicional": 0.75,
            }
        ],
        "comportamento_consequencia": [
            {
                "comportamento": "Choro",
                "consequencia": "Pausa",
                "suporte": 5,
                "probabilidade_condicional": 0.83,
            }
        ],
        "por_ambiente": [{"ambiente": "Sala de aula", "quantidade": 7}],
        "por_funcao": [{"funcao": "Fuga ou esquiva", "quantidade": 6}],
        "registros_recentes": [
            {
                "data": "2026-07-14",
                "hora": "09:30:10",
                "ambiente": "Sala de aula",
                "antecedente": "Demanda",
                "comportamento": "Choro",
                "consequencia": "Pausa",
                "classificacao": "C1",
                "funcao": "Fuga ou esquiva",
            }
        ],
        "aviso": "Associações descritivas não confirmam causa nem função comportamental.",
    }


def test_functional_ai_uses_deidentified_abc_context_and_provider_fallback(monkeypatch):
    captured = {}
    references = [
        {
            "fonte": "analise_funcional.md",
            "titulo": "Avaliação funcional",
            "trecho": "Hipóteses funcionais exigem verificação com observação sistemática.",
            "indice": 1,
        }
    ]
    monkeypatch.setattr(knowledge_agent, "search", lambda query, limit: references)
    monkeypatch.setattr(
        knowledge_agent,
        "configured_ai_providers",
        lambda: [{"nome": "openrouter", "configurado": True}],
    )

    def fake_generate(prompt):
        captured["prompt"] = prompt
        return "### Leitura dos padrões\nLeitura cautelosa.", "openrouter", "openrouter/free", []

    monkeypatch.setattr(knowledge_agent, "_generate_with_fallbacks", fake_generate)
    result = knowledge_agent.answer_abc_functional_analysis(
        analysis=_analysis_fixture(),
        selected_chain="Demanda -> Choro -> Pausa",
        selected_environment="Sala de aula",
        question="Compare fuga e atenção.",
    )

    assert result["modo"] == "openrouter"
    assert result["modelo"] == "openrouter/free"
    assert result["dados_desidentificados"] is True
    assert "total_registros" in captured["prompt"]
    assert "Demanda -> Choro -> Pausa" in captured["prompt"]
    assert "Não conclua a função" in captured["prompt"]
    assert "C1 e C2 classificam somente" in captured["prompt"]
    assert "estados privados" in captured["prompt"]
    assert "HIPÓTESE PRÉVIA" in captured["prompt"]
    assert "PACIENTE TESTE" not in captured["prompt"]
    assert "Referencias utilizadas" in result["resposta"]


def test_functional_ai_reports_when_no_provider_is_configured(monkeypatch):
    monkeypatch.setattr(knowledge_agent, "search", lambda query, limit: [])
    monkeypatch.setattr(
        knowledge_agent,
        "configured_ai_providers",
        lambda: [{"nome": "gemini", "configurado": False}],
    )

    result = knowledge_agent.answer_abc_functional_analysis(analysis=_analysis_fixture())

    assert result["modo"] == "sem_provedor"
    assert "nenhum provedor" in result["resposta"].casefold()

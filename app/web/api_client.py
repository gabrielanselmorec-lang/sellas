import os

import streamlit as st
import pandas as pd
import requests

API_URL = os.getenv("SELLAS_API_URL", os.getenv("SKINNER_API_URL", "http://127.0.0.1:8010")).rstrip("/")
API_TIMEOUT_SECONDS = 90
AGENT_TIMEOUT_SECONDS = int(os.getenv("SELLAS_AGENT_TIMEOUT_SECONDS", os.getenv("SKINNER_AGENT_TIMEOUT_SECONDS", "300")))


@st.cache_data(ttl=600, show_spinner=False)
def load_data_from_api(paciente):
    try:
        resposta = requests.get(
            f"{API_URL}/api/dados_paciente",
            params={"paciente": paciente},
            timeout=API_TIMEOUT_SECONDS,
        )
        if resposta.status_code != 200:
            return pd.DataFrame(), pd.DataFrame()
        dados = resposta.json()
        df_p = pd.DataFrame(dados.get("programas", []))
        df_b = pd.DataFrame(dados.get("interferentes", []))

        if not df_p.empty:
            if "date" in df_p.columns:
                df_p["date"] = pd.to_datetime(df_p["date"], errors="coerce", dayfirst=True).dt.date
            if "independent_rate" in df_p.columns:
                df_p["independent_rate"] = pd.to_numeric(df_p["independent_rate"], errors="coerce")
            if "prompt_rate" in df_p.columns:
                df_p["prompt_rate"] = pd.to_numeric(df_p["prompt_rate"], errors="coerce")

        if not df_b.empty:
            if "date" in df_b.columns:
                df_b["date"] = pd.to_datetime(df_b["date"], errors="coerce", dayfirst=True).dt.date
            if "rate" in df_b.columns:
                df_b["rate"] = pd.to_numeric(df_b["rate"], errors="coerce")
            if "count" in df_b.columns:
                df_b["count"] = pd.to_numeric(df_b["count"], errors="coerce")

        return df_p, df_b
    except requests.exceptions.ConnectionError:
        st.warning("Não foi possível conectar à API. Verifique se o servidor está rodando.")
        return pd.DataFrame(), pd.DataFrame()
    except requests.exceptions.Timeout:
        st.warning("A API demorou demais para responder. Tente novamente.")
        return pd.DataFrame(), pd.DataFrame()
    except Exception as e:
        st.warning(f"Erro ao carregar dados do paciente: {type(e).__name__}")
        return pd.DataFrame(), pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_targets_from_api(paciente, programa):
    try:
        resposta = requests.get(
            f"{API_URL}/api/alvos",
            params={"paciente": paciente, "programa": programa},
            timeout=API_TIMEOUT_SECONDS,
        )
        if resposta.status_code != 200:
            return pd.DataFrame()
        df = pd.DataFrame(resposta.json())

        if not df.empty:
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True).dt.date
            if "independent_rate" in df.columns:
                df["independent_rate"] = pd.to_numeric(df["independent_rate"], errors="coerce")
            if "prompt_rate" in df.columns:
                df["prompt_rate"] = pd.to_numeric(df["prompt_rate"], errors="coerce")

        return df
    except requests.exceptions.ConnectionError:
        return pd.DataFrame()
    except requests.exceptions.Timeout:
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def load_library_from_api():
    try:
        resposta = requests.get(f"{API_URL}/api/biblioteca", timeout=API_TIMEOUT_SECONDS)
        if resposta.status_code == 200:
            return pd.DataFrame(resposta.json())
    except requests.exceptions.ConnectionError:
        pass
    except requests.exceptions.Timeout:
        pass
    except Exception:
        pass
    return pd.DataFrame()


def ask_clinical_agent(paciente, pergunta, start_date=None, end_date=None):
    payload = {"paciente": paciente, "pergunta": pergunta}
    if start_date:
        payload["data_inicio"] = start_date.isoformat()
    if end_date:
        payload["data_fim"] = end_date.isoformat()
    resposta = requests.post(
        f"{API_URL}/api/agente/clinico",
        json=payload,
        timeout=AGENT_TIMEOUT_SECONDS,
    )
    resposta.raise_for_status()
    return resposta.json()

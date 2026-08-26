from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ABCType = Literal["antecedente", "comportamento", "consequencia"]
ObservationStatus = Literal["observed", "partial", "not_observed", "not_applicable", "invalid"]


class ABCAnalysisConfig(BaseModel):
    periodo_inicio: datetime | None = None
    periodo_fim: datetime | None = None
    include_weekends: bool = True
    service_calendar_id: str | None = Field(None, max_length=120)
    interval_minutes: int = Field(5, ge=1, le=240)
    minimum_valid_intervals: int = Field(10, ge=1, le=10000)


class ABCInstrumentVersionRequest(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=80)
    nome: str = Field(..., min_length=1, max_length=200)
    versao: str = Field("1", max_length=30)
    ativo: bool = True
    metadados: dict[str, Any] = Field(default_factory=dict)


class ABCCategoryRequest(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=80)
    nome: str = Field(..., min_length=1, max_length=200)
    tipo: ABCType
    definicao_operacional: str | None = None
    versao: int = Field(1, ge=1)
    ativa: bool = True
    service_id: str | None = Field(None, max_length=120)
    organization_id: str | None = Field(None, max_length=120)


class ABCSessionRequest(BaseModel):
    patient_token: str = Field(..., min_length=1, max_length=120)
    service_id: str | None = Field(None, max_length=120)
    data_inicio: datetime
    data_fim: datetime
    timezone: str = Field("America/Sao_Paulo", max_length=80)
    observacao_completa: bool = False
    instrumento_versao: str = Field("1", max_length=30)


class ABCIntervalRequest(BaseModel):
    inicio: datetime
    fim: datetime | None = None
    timezone: str = Field("America/Sao_Paulo", max_length=80)
    interval_minutes: int = Field(5, ge=1, le=240)
    status_observacao: ObservationStatus = "observed"
    atraso_registro_segundos: int | None = Field(None, ge=0)
    observador_token: str | None = Field(None, max_length=120)
    instrumento_versao: str = Field("1", max_length=30)


class ABCEventRequest(BaseModel):
    categoria_id: str | None = Field(None, max_length=120)
    categoria_codigo: str | None = Field(None, max_length=80)
    ocorreu: bool | None = None
    frequencia: int | None = Field(None, ge=0)
    duracao_segundos: int | None = Field(None, ge=0)
    intensidade: int | None = Field(None, ge=0, le=5)
    confianca_registro: float | None = Field(None, ge=0, le=1)
    fonte: str = Field("registro_fechado", max_length=40)
    revisado_humano: bool = False


class ABCAnalysisQuery(BaseModel):
    patient_token: str | None = Field(None, max_length=120)
    antecedente_codigo: str | None = Field(None, max_length=80)
    comportamento_codigo: str | None = Field(None, max_length=80)
    periodo_inicio: datetime | None = None
    periodo_fim: datetime | None = None
    include_weekends: bool = True
    minimum_valid_intervals: int = Field(10, ge=1, le=10000)

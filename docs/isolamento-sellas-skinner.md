# Isolamento entre Sellas Project e Skinner Project

Este repositório (`C:\sellas_project`) foi configurado para rodar ao lado do `C:\skinner_project_2.0` sem depender dos mesmos scripts, portas ou variáveis com prefixo de projeto.

## Portas reservadas

| Projeto | API | Streamlit | MVP bHave frontend | MVP bHave API |
| --- | ---: | ---: | ---: | ---: |
| Skinner Project | 8000 | 8501 | - | - |
| Sellas Project | 8010 | 8510 | 5174 | 8020 |

## Ambientes Python

- Sellas: `C:\sellas_project\.venv`
- Skinner: `C:\skinner_project_2.0\.venv`

Não use executáveis de um projeto para iniciar o outro. O Sellas foi reparado para que `.\.venv\Scripts\python.exe` aponte para `C:\sellas_project`.

## Variáveis de ambiente

No Sellas, prefira variáveis `SELLAS_*`:

- `SELLAS_API_URL=http://127.0.0.1:8010`
- `SELLAS_STREAMLIT_PORT=8510`
- `SELLAS_DATABASE_URL`
- `SELLAS_PATIENT_HASH_SALT`
- `SELLAS_BHAVE_DATABASE_URL=sqlite:///storage/bhave_mvp.db`
- `SELLAS_BHAVE_BASE_URL`
- `SELLAS_BHAVE_API_TOKEN`

O código ainda aceita alguns `SKINNER_*` como fallback para compatibilidade temporária, mas o ideal é manter o `.env` do Sellas com prefixo `SELLAS_*` e o `.env` do Skinner com prefixo `SKINNER_*`.

## Como iniciar

Sellas clínico:

```bat
iniciar_sellas.bat
```

Sellas MVP bHave:

```bash
docker compose up --build
```

Skinner:

Use o inicializador do próprio diretório `C:\skinner_project_2.0`, sem reaproveitar scripts do Sellas.

## Regra de segurança operacional

Antes de usar dados reais no Sellas, configure `SELLAS_DATABASE_URL` para um banco separado do Skinner. O MVP bHave usa SQLite local por padrão para evitar mistura acidental de dados.

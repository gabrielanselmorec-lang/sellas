# Sellas Project

Plataforma clínica para registro, análise e documentação de dados em Análise do Comportamento Aplicada (ABA). O Sellas reúne prontuário comportamental, registros ABC, análise de cadeias, relatórios clínicos e módulos experimentais de previsão em uma interface única.

> **Aviso clínico:** as análises são ferramentas de apoio ao analista do comportamento. Elas não substituem avaliação funcional, julgamento clínico, supervisão profissional ou decisão médica. Associações temporais não demonstram causalidade.

## Principais recursos

- registro ABC com data, horário, ambiente, antecedente, comportamento, consequência, intensidade e hipótese funcional;
- classificação de comportamentos interferentes em C1 e C2, com critérios observáveis de risco;
- histórico completo, edição auditável e ordenação pelos registros adicionados mais recentemente;
- análise descritiva de frequências, ambientes, funções e cadeias A-B-C;
- detecção e revisão humana de cadeias temporais entre episódios;
- relatórios clínicos em PDF e Word com a mesma estrutura visual;
- exportação do histórico ABC por paciente para Excel;
- avaliações e acompanhamento de habilidades;
- base para integração com bHave e experimentos de previsão comportamental;
- controles de segurança, anonimização e governança alinhados à LGPD.

## Tecnologias

- **Aplicação clínica:** Python, FastAPI, Streamlit, SQLAlchemy e PostgreSQL/Supabase.
- **Análise e relatórios:** pandas, Plotly, Matplotlib, scikit-learn, ReportLab, python-docx e openpyxl.
- **Módulo bHave:** FastAPI, React, TypeScript, Vite e Recharts.
- **Infraestrutura:** Alembic, Docker e Docker Compose.

## Estrutura do projeto

```text
app/                 serviços clínicos, modelos e dashboard Streamlit
backend/             API do módulo experimental bHave
frontend/            interface React do módulo bHave
alembic/             migrações do banco principal
supabase/            scripts SQL do módulo ABC
tests/               testes automatizados
tools/               geradores e utilitários de documentos
docs/                metodologia e documentação técnica
api.py               API clínica FastAPI
main.py              rotinas principais da aplicação
iniciar_sellas.bat   inicializador local para Windows
```

## Pré-requisitos

- Python 3.11 ou superior;
- PostgreSQL ou projeto Supabase;
- Node.js 20 ou superior para o gerador de Excel e o frontend React;
- Git;
- opcionalmente, Docker Desktop.

## Instalação local

Clone o repositório e entre na pasta:

```bash
git clone https://github.com/gabrielanselmorec-lang/sellas.git
cd sellas
```

Crie e ative um ambiente virtual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copie o arquivo de exemplo e configure somente valores locais:

```powershell
Copy-Item .env.example .env
```

Variáveis essenciais:

- `SELLAS_DATABASE_URL`: conexão PostgreSQL/Supabase;
- `SELLAS_PATIENT_HASH_SALT`: segredo exclusivo para pseudonimização;
- `SELLAS_API_URL`: endereço da API clínica;
- `GEMINI_API_KEY`: opcional, utilizada apenas nos recursos assistidos configurados.

Nunca publique o arquivo `.env`, credenciais, bancos locais ou exportações de pacientes.

## Executando no Windows

Depois de instalar as dependências e configurar o `.env`, execute:

```powershell
.\iniciar_sellas.bat
```

Serviços locais:

- dashboard Streamlit: `http://127.0.0.1:8510`;
- API FastAPI: `http://127.0.0.1:8010`;
- documentação da API: `http://127.0.0.1:8010/docs`.

Também é possível iniciar os serviços separadamente:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api:app --host 0.0.0.0 --port 8010
.\.venv\Scripts\python.exe -m streamlit run app\web\dashboard.py --server.port 8510
```

## Módulo bHave com Docker

O protótipo independente pode ser iniciado com:

```bash
docker compose up --build
```

- frontend React: `http://localhost:5174`;
- API do módulo: `http://localhost:8020`;
- Swagger: `http://localhost:8020/docs`.

O modo mock usa pacientes fictícios e pode ser habilitado com `USE_MOCK_DATA=true`.

## Banco de dados

As migrações Alembic ficam em `alembic/versions`. A estrutura complementar do módulo ABC para Supabase está em `supabase/abc_fechado`.

Antes de aplicar migrações em produção:

1. faça backup do banco;
2. valide as variáveis do ambiente;
3. aplique primeiro em homologação;
4. confira políticas de acesso e Row Level Security.

## Testes

Execute a suíte completa:

```powershell
pytest
```

Ou somente o módulo bHave:

```powershell
pytest tests\bhave
```

## Segurança e privacidade

Este repositório não inclui dados clínicos, exportações, credenciais ou arquivos `.env`. Os diretórios locais `exports/`, `output/`, `storage/`, `raw_data/` e `tmp/` são ignorados pelo Git.

Para uso real:

- aplique o princípio do menor privilégio;
- mantenha segredos fora do código-fonte;
- utilize conexões criptografadas;
- registre acessos e alterações relevantes;
- estabeleça retenção e descarte seguro dos dados;
- submeta o fluxo a revisão clínica, jurídica e de segurança.

## Metodologia ABC

O Sellas distingue:

- a cadeia A-B-C observada no mesmo registro;
- cadeias temporais entre episódios distintos;
- associações descritivas;
- previsões probabilísticas experimentais.

Consequências posteriores ao comportamento não são utilizadas como se fossem variáveis antecedentes do mesmo episódio. Registros ausentes, inválidos ou não observados não são convertidos automaticamente em ausência de comportamento.

Detalhes adicionais estão em:

- `docs/abc_closed_interval_analysis.md`;
- `docs/abc_temporal_chains.md`;
- `docs/abc_printable_summary.md`;
- `docs/RELATORIO_PREVISAO_ABC_FORMULAS.md`.

## Estado do projeto

O Sellas está em desenvolvimento ativo. Antes de uso clínico ou institucional, valide localmente a instalação, as migrações, os relatórios e as regras de acesso no ambiente de destino.

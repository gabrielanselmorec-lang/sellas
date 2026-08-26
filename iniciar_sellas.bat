@echo off
title Sellas Project - Inicializador

echo ===================================================
echo   Iniciando o Sellas Project (API + Dashboard)
echo ===================================================
echo.

:: Vai para a pasta raiz do projeto.
cd /d "%~dp0"

set "SELLAS_API_URL=http://127.0.0.1:8010"
set "SELLAS_STREAMLIT_PORT=8510"

echo 1. Ligando a API FastAPI...
start "Sellas - API (Backend)" cmd /k "set ""SELLAS_API_URL=%SELLAS_API_URL%"" && .\.venv\Scripts\python.exe -m uvicorn api:app --host 0.0.0.0 --port 8010"

echo Aguardando a API iniciar...
timeout /t 3 /nobreak > NUL

echo 2. Ligando o dashboard Streamlit...
cd app\web
start "Sellas - Painel (Frontend)" cmd /k "set ""SELLAS_API_URL=%SELLAS_API_URL%"" && ..\..\.venv\Scripts\python.exe -m streamlit run dashboard.py --server.port 8510"

echo.
echo Tudo pronto!
pause

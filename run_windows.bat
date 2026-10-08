@echo off
rem Starts the panel on Windows without WSL: http://127.0.0.1:8000 (stop with Ctrl+C).
rem Uses its own virtual environment (.venv-win) so it never clashes with the Linux .venv of WSL.
setlocal
cd /d "%~dp0"
set "UV_PROJECT_ENVIRONMENT=.venv-win"
set "PYTHONUTF8=1"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv nao encontrado. Instale com: winget install --id astral-sh.uv
  exit /b 1
)
uv sync
if errorlevel 1 exit /b 1
echo Painel em http://127.0.0.1:8000 (Ctrl+C para parar)
uv run financas serve --host 127.0.0.1 --port 8000

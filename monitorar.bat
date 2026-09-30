@echo off
rem Monitoramento periodico: coleta os produtos ativos, executa o matching e gera o relatorio.
rem Pode ser chamado pelo Agendador de Tarefas. Argumentos sao repassados (ex.: --sem-coleta).
rem Requer a instalacao feita por iniciar.bat e o servidor fechado (o banco aceita um processo).
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
if not exist "data\logs" mkdir "data\logs"
echo ==== %date% %time% ====>> "data\logs\monitor.log"
".venv\Scripts\python.exe" -m app.monitor %* >> "data\logs\monitor.log" 2>&1
exit /b %errorlevel%

@echo off
rem Instala as dependencias na primeira execucao e sobe o RA Product Monitor localmente.
setlocal
cd /d "%~dp0"
title RA Product Monitor

set "VENV_PY=.venv\Scripts\python.exe"
set "MARCA=.venv\instalacao_ok"

if exist "%MARCA%" goto iniciar

set "PY="
call :testar_python py -3
if not defined PY call :testar_python python
if not defined PY call :testar_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto instalar

echo Python 3.12 ou superior nao foi encontrado nesta maquina.
choice /c SN /m "Deseja instalar o Python 3.12 agora"
if errorlevel 2 goto sem_python
where winget >nul 2>&1
if errorlevel 1 goto sem_winget
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto sem_winget
call :testar_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
    echo Python instalado. Feche esta janela e execute iniciar.bat novamente.
    pause
    exit /b 0
)

:instalar
echo.
echo Primeira execucao: instalando dependencias. Isso pode levar alguns minutos.
%PY% -m venv --clear .venv || goto falha
"%VENV_PY%" -m pip install -e ".[collect]" || goto falha
"%VENV_PY%" -m playwright install chromium || goto falha
echo ok> "%MARCA%"

:iniciar
echo.
echo RA Product Monitor em http://127.0.0.1:8000/
echo Para encerrar, feche esta janela.
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 4; Start-Process 'http://127.0.0.1:8000/'"
"%VENV_PY%" -m uvicorn app.main:app
if errorlevel 1 pause
exit /b

:testar_python
%* -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1 && set "PY=%*"
exit /b 0

:sem_python
echo Instale o Python 3.12 ou superior em https://www.python.org/downloads/
echo e execute iniciar.bat novamente.
pause
exit /b 1

:sem_winget
echo Nao foi possivel instalar automaticamente. Abrindo a pagina de download do Python.
echo Durante a instalacao, marque "Add python.exe to PATH". Depois execute iniciar.bat novamente.
start "" https://www.python.org/downloads/
pause
exit /b 1

:falha
echo.
echo A instalacao falhou. Veja a mensagem acima.
echo Se o erro citar caminho longo, mova a pasta para um caminho curto, como C:\RAPM.
pause
exit /b 1

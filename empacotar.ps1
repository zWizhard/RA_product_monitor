# Gera dist\RA_Product_Monitor.zip para distribuicao: o necessario para rodar, o iniciar.bat
# e uma copia do banco atual. Nao inclui .venv, .env, testes nem amostras.
# Uso: powershell -ExecutionPolicy Bypass -File empacotar.ps1
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
$banco = Join-Path $raiz 'data\ra_product_monitor.duckdb'

# Copiar o banco com o servidor aberto pode gerar uma copia inconsistente.
try { [IO.File]::Open($banco, 'Open', 'Read', 'None').Close() }
catch { throw "Banco em uso ou ausente ($banco). Pare o servidor e tente novamente." }
if (Test-Path "$banco.wal") { throw "Existe $banco.wal. Inicie e pare o servidor uma vez e tente novamente." }

$pasta = Join-Path ([IO.Path]::GetTempPath()) 'RA_Product_Monitor'
if (Test-Path $pasta) { Remove-Item $pasta -Recurse -Force }
New-Item -ItemType Directory (Join-Path $pasta 'data') | Out-Null
Copy-Item (Join-Path $raiz 'app'), (Join-Path $raiz 'frontend') $pasta -Recurse
Get-ChildItem $pasta -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Copy-Item (Join-Path $raiz 'pyproject.toml'), (Join-Path $raiz 'README.md'), (Join-Path $raiz 'iniciar.bat') $pasta
Copy-Item $banco (Join-Path $pasta 'data')

$zip = Join-Path $raiz 'dist\RA_Product_Monitor.zip'
New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
Compress-Archive $pasta $zip -Force
Remove-Item $pasta -Recurse -Force
Write-Host "Pacote gerado: $zip"

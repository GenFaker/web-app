param([int]$Port = 8767)
$ErrorActionPreference = 'Stop'
$webPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $webPython)) {
    $webPython = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $webPython)) {
    throw 'Create a Python environment and install web-app/requirements.txt as described in README.md.'
}
& $webPython (Join-Path $PSScriptRoot 'app.py') --port $Port

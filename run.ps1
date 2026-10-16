$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$labPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $labPython)) {
    $labPython = 'python'
}
& $labPython (Join-Path $PSScriptRoot 'desktop.py')
exit $LASTEXITCODE

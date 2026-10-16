$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$labBuildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $labBuildPython)) {
    throw 'Create .venv and install requirements-build.txt first. See README.md.'
}
& $labBuildPython -m PyInstaller --noconfirm SignalLab.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Native build: dist\SignalLab\SignalLab.exe'

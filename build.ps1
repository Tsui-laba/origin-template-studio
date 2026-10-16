$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $buildPython = Get-Command python -ErrorAction Stop
    & $buildPython.Source .\tools\package.py
    if ($LASTEXITCODE -ne 0) { throw 'Application packaging failed.' }
}
finally { Pop-Location }

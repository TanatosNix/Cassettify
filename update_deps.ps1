param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bootstrap = Join-Path $Here "bootstrap.ps1"

if (-not (Test-Path $Bootstrap)) {
    throw "No se encontró bootstrap.ps1 en: $Bootstrap"
}

$shell = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $shell) { $shell = Get-Command powershell -ErrorAction SilentlyContinue }
if (-not $shell) { throw "No se encontró PowerShell (pwsh/powershell)." }

& $shell.Source -ExecutionPolicy Bypass -File $Bootstrap -UpdateOnly -Quiet:$Quiet

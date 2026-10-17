param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bootstrap = Join-Path $Here "bootstrap.ps1"
$LogHint = Join-Path $env:LOCALAPPDATA "Cassettify\deps\bootstrap.log"

Write-Host ""
Write-Host "Cassettify - actualizador de dependencias" -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor DarkCyan

if (-not (Test-Path $Bootstrap)) {
    throw "No se encontro bootstrap.ps1 en: $Bootstrap"
}

$shell = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $shell) { $shell = Get-Command powershell -ErrorAction SilentlyContinue }
if (-not $shell) { throw "No se encontro PowerShell (pwsh/powershell)." }

Write-Host "Usando bootstrap centralizado:" -ForegroundColor Gray
Write-Host "  $Bootstrap" -ForegroundColor DarkGray
Write-Host ""

& $shell.Source -ExecutionPolicy Bypass -File $Bootstrap -UpdateOnly -Quiet

Write-Host ""
Write-Host "Dependencias actualizadas. Log:" -ForegroundColor Green
Write-Host "  $LogHint" -ForegroundColor DarkGray

if (-not $Quiet) {
    Write-Host ""
    Write-Host "Pulsa Enter para cerrar..."
    Read-Host | Out-Null
}

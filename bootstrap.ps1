param(
    [switch]$UpdateOnly,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$AppDataRoot = Join-Path $env:LOCALAPPDATA "Cassettify"
$DepsLogDir = Join-Path $AppDataRoot "deps"
$null = New-Item -ItemType Directory -Path $DepsLogDir -Force
$DepsLog = Join-Path $DepsLogDir "bootstrap.log"
$Requirements = Join-Path $PSScriptRoot "requirements.txt"
$BuildRequirements = Join-Path $PSScriptRoot "requirements-build.txt"
$BgutilProviderVersion = "1.3.1"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $DepsLog -Append
}

function Invoke-WingetInstall {
    param([string]$Id)
    Write-Log "Instalando $Id con winget..."
    winget install -e --id $Id --accept-package-agreements --accept-source-agreements
}

function Invoke-WingetUpgrade {
    param([string]$Id)
    Write-Log "Actualizando $Id con winget..."
    winget upgrade -e --id $Id --accept-package-agreements --accept-source-agreements
}

function Resolve-PythonExe {
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    return $null
}

function Resolve-NodeExe {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Resolve-GitExe {
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Resolve-DenoPath {
    $cmd = Get-Command deno -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\deno.exe"),
        (Join-Path $env:USERPROFILE ".deno\bin\deno.exe")
    )

    $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $wingetRoot) {
        $pkg = Get-ChildItem $wingetRoot -Directory -Filter "DenoLand.Deno*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($pkg) {
            $candidates += (Join-Path $pkg.FullName "deno.exe")
        }
    }

    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Resolve-FfmpegPath {
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $PSScriptRoot "ffmpeg\bin\ffmpeg.exe"),
        (Join-Path $PSScriptRoot "ffmpeg\ffmpeg.exe"),
        (Join-Path $PSScriptRoot "ffmpeg.exe")
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Setup-BgutilProvider {
    param(
        [bool]$UpdateOnlyMode
    )

    $nodeExe = Resolve-NodeExe
    if (-not $nodeExe) {
        if ($UpdateOnlyMode) {
            Write-Log "Node.js no detectado y UpdateOnly activo: no se prepara bgutil provider."
            return
        }
        try {
            Invoke-WingetInstall -Id "OpenJS.NodeJS.LTS"
            $nodeExe = Resolve-NodeExe
        } catch {
            Write-Log "Aviso: no pude instalar Node.js: $($_.Exception.Message)"
            return
        }
    }

    $gitExe = Resolve-GitExe
    if (-not $gitExe) {
        if ($UpdateOnlyMode) {
            Write-Log "Git no detectado y UpdateOnly activo: no se prepara bgutil provider."
            return
        }
        try {
            Invoke-WingetInstall -Id "Git.Git"
            $gitExe = Resolve-GitExe
        } catch {
            Write-Log "Aviso: no pude instalar Git: $($_.Exception.Message)"
            return
        }
    }

    $potRoot = Join-Path $env:USERPROFILE "bgutil-ytdlp-pot-provider"
    $potServer = Join-Path $potRoot "server"
    $potScript = Join-Path $potServer "build\generate_once.js"

    try {
        if (-not (Test-Path $potRoot)) {
            if ($UpdateOnlyMode) {
                Write-Log "bgutil provider no encontrado y UpdateOnly activo: no se clona."
                return
            }
            Write-Log "Clonando bgutil-ytdlp-pot-provider $BgutilProviderVersion en $potRoot"
            & $gitExe clone --single-branch --branch $BgutilProviderVersion https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git $potRoot
        } else {
            Push-Location $potRoot
            try {
                Write-Log "Actualizando bgutil-ytdlp-pot-provider a $BgutilProviderVersion"
                & $gitExe fetch --tags
                & $gitExe checkout $BgutilProviderVersion
            } finally {
                Pop-Location
            }
        }

        if (-not (Test-Path $potServer)) {
            Write-Log "Aviso: no existe carpeta server en bgutil provider."
            return
        }

        $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
        $npxCmd = Get-Command npx -ErrorAction SilentlyContinue
        if (-not $npmCmd -or -not $npxCmd) {
            Write-Log "Aviso: npm/npx no disponibles en PATH; reinicia terminal y ejecuta update_deps.ps1."
            return
        }

        Push-Location $potServer
        try {
            Write-Log "bgutil provider: npm install"
            & $npmCmd.Source install
            Write-Log "bgutil provider: npx tsc"
            & $npxCmd.Source tsc
        } finally {
            Pop-Location
        }

        if (Test-Path $potScript) {
            Write-Log "bgutil provider listo: $potScript"
        } else {
            Write-Log "Aviso: no se generó script bgutil ($potScript)."
        }
    } catch {
        Write-Log "Aviso: error preparando bgutil provider: $($_.Exception.Message)"
    }
}

Write-Log "=== Bootstrap iniciado (UpdateOnly=$UpdateOnly) ==="

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "No se encontró winget. Instala App Installer de Microsoft Store e inténtalo de nuevo."
}

$pyExe = Resolve-PythonExe
if (-not $pyExe) {
    if ($UpdateOnly) {
        Write-Log "Python no detectado y se pidió UpdateOnly. Saltando instalación de paquetes pip."
    } else {
        Invoke-WingetInstall -Id "Python.Python.3.12"
        $env:PATH += ";$env:LocalAppData\Programs\Python\Python312;$env:LocalAppData\Programs\Python\Python312\Scripts"
        $pyExe = Resolve-PythonExe
    }
}

if ($pyExe) {
    Write-Log "Python detectado: $pyExe"
    & $pyExe -m pip install -U pip
    if (Test-Path $Requirements) {
        Write-Log "Instalando dependencias desde $Requirements"
        & $pyExe -m pip install -U -r $Requirements
    } else {
        Write-Log "requirements.txt no encontrado; usando lista minima integrada."
        & $pyExe -m pip install -U yt-dlp yt-dlp-ejs Pillow pywebview bgutil-ytdlp-pot-provider
    }

    if (Test-Path $BuildRequirements) {
        Write-Log "Verificando dependencias de build desde $BuildRequirements"
        & $pyExe -m pip install -U -r $BuildRequirements
    }
} else {
    Write-Log "Python no disponible tras bootstrap."
}

$denoPath = Resolve-DenoPath
if ($denoPath) {
    Write-Log "Deno detectado: $denoPath"
    try {
        Invoke-WingetUpgrade -Id "DenoLand.Deno"
    } catch {
        Write-Log "Aviso: no pude actualizar Deno con winget: $($_.Exception.Message)"
    }
} elseif (-not $UpdateOnly) {
    Invoke-WingetInstall -Id "DenoLand.Deno"
} else {
    Write-Log "Deno no detectado y UpdateOnly activo: no se instala."
}

$ffmpegPath = Resolve-FfmpegPath
if ($ffmpegPath) {
    Write-Log "FFmpeg detectado: $ffmpegPath"
    $isLocalBundledFfmpeg = $ffmpegPath.StartsWith((Join-Path $PSScriptRoot "ffmpeg"), [System.StringComparison]::OrdinalIgnoreCase)
    if ($isLocalBundledFfmpeg) {
        Write-Log "FFmpeg local del proyecto detectado; no se fuerza upgrade por winget."
    } else {
        try {
            Invoke-WingetUpgrade -Id "Gyan.FFmpeg"
        } catch {
            Write-Log "Aviso: no pude actualizar FFmpeg con winget: $($_.Exception.Message)"
        }
    }
} elseif (-not $UpdateOnly) {
    Invoke-WingetInstall -Id "Gyan.FFmpeg"
} else {
    Write-Log "FFmpeg no detectado y UpdateOnly activo: no se instala."
}

Setup-BgutilProvider -UpdateOnlyMode:$UpdateOnly

Write-Log "Bootstrap finalizado."
if (-not $Quiet) {
    Write-Host ""
    Write-Host "Proceso completado. Log: $DepsLog"
}

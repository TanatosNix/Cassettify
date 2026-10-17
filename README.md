<div align="center">

# Cassettify

### Conversión multimedia local, rápida y con una interfaz cuidada

App de escritorio en **Python + CustomTkinter + FFmpeg + yt-dlp** para descargar, convertir y organizar audio/video con cola visual y login integrado.

<p>
  <img src="https://img.shields.io/badge/Python-3.13+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/yt--dlp-2026.8.19-2DD4BF?style=for-the-badge" alt="yt-dlp"/>
  <img src="https://img.shields.io/badge/FFmpeg-Integrated-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg"/>
  <img src="https://img.shields.io/badge/License-MIT-F59E0B?style=for-the-badge" alt="MIT"/>
</p>

</div>

---

## Preview

- App desktop principal: `diseño.py`
- Landing visual del proyecto: `index.html`
- Asset hero: `assets/cassettify-console.png`

## Features

- Descarga y conversión de audio/video con FFmpeg.
- Login WebView para guardar cookies en AppData.
- Cola de trabajos con progreso, apertura de archivo y carpeta.
- Interfaz moderna en CustomTkinter con fallback Tkinter si no está disponible.
- Detección de FFmpeg, runtime JS, yt-dlp-ejs y bgutil POT provider.
- Scripts reproducibles para instalar o actualizar dependencias.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe diseño.py
```

Abrir landing web local:

```powershell
.\.venv\Scripts\python.exe -m http.server 5173
```

Luego abre `http://localhost:5173`.

## Dependencias

- `requirements.txt`: dependencias de ejecución.
- `requirements-build.txt`: dependencias de ejecución + PyInstaller.
- `requirements.lock.txt`: snapshot exacto del entorno actualizado.
- `bootstrap.ps1`: instalación/reparación de entorno y herramientas externas.
- `update_deps.ps1`: actualización centralizada usando `bootstrap.ps1 -UpdateOnly`.

## Project Structure

```text
cassettify/
|- diseño.py                 # App principal CustomTkinter con fallback Tkinter
|- assets/                   # Recursos visuales de la landing
|- ffmpeg/                   # Binarios y docs de FFmpeg
|- bootstrap.ps1             # Setup/arranque de dependencias
|- update_deps.ps1           # Actualizador centralizado
|- requirements*.txt         # Dependencias Python
|- Cassettify.spec           # Build con PyInstaller
|- try.iss                   # Instalador con Inno Setup
|- index.html                # Landing
|- styles.css                # Estilos de landing
|- app.js                    # Animaciones de landing
```

## Build

```powershell
.\.venv\Scripts\pyinstaller.exe Cassettify.spec
```

## License

MIT

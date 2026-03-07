<div align="center">

# Cassettify

### Conversion multimedia local, rapida y con estilo

App de escritorio en **Python + FFmpeg** para convertir audio/video, procesar por lotes y exportar en segundos.

<p>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/FFmpeg-Integrated-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg"/>
  <img src="https://img.shields.io/badge/Desktop-App-1F6FEB?style=for-the-badge" alt="Desktop App"/>
  <img src="https://img.shields.io/badge/License-MIT-A3BE8C?style=for-the-badge" alt="MIT"/>
</p>

</div>

---

## Preview

- Interfaz desktop principal: `diseño.py`
- Landing visual del proyecto: `index.html`

## Features

- Conversion de audio y video con perfiles utiles.
- Procesamiento por lotes (batch) para grandes listas.
- Ejecucion local para mayor privacidad.
- Motor FFmpeg integrado en el repositorio.
- Flujo rapido: cargar, configurar, convertir.

## Quick Start

```powershell
git clone https://github.com/TanatosNix/Cassettify.git
cd Cassettify
python diseño.py
```

Abrir landing web local:

```powershell
start index.html
```

## Project Structure

```text
cassettify/
|- diseño.py         # App principal
|- ffmpeg/           # Binarios y docs de FFmpeg
|- bootstrap.ps1     # Setup/arranque
|- Cassettify.spec   # Build con PyInstaller
|- try.iss           # Instalador con Inno Setup
|- index.html        # Landing
|- styles.css        # Estilos de landing
|- app.js            # Animaciones de landing
```

## Build

- `Cassettify.spec`: empaquetado con PyInstaller
- `try.iss`: generacion de instalador (Inno Setup)

## Roadmap

- Mejorar presets de conversion.
- Afinar UX de cola de trabajos.
- Expandir branding visual de la app y la landing.

## License

MIT

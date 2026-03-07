# Cassettify

Cassettify es una app de escritorio para convertir y gestionar audio/video de forma rapida, local y sencilla, usando Python + FFmpeg.

## Que incluye

- Conversion de audio y video con perfiles practicos
- Procesamiento por lotes (batch)
- Integracion local con FFmpeg
- Interfaz con estilo retro-moderno
- Landing web llamativa en:
  - `index.html`
  - `styles.css`
  - `app.js`

## Estructura principal

- `diseño.py`: aplicacion principal
- `ffmpeg/`: binarios y docs de FFmpeg usados por el proyecto
- `bootstrap.ps1`: script de arranque/configuracion
- `try.iss`: script de Inno Setup para instalador
- `index.html`: landing page visual del proyecto

## Requisitos

- Windows
- Python 3.10+ recomendado
- FFmpeg (incluido en este repo)

## Ejecucion local

1. Clona el repositorio.
2. Entra en la carpeta del proyecto.
3. Ejecuta la app principal:

```powershell
python diseño.py
```

Para abrir la landing web local:

```powershell
start index.html
```

## Build de instalador (referencia)

El repo incluye configuracion para empaquetado con PyInstaller y generacion de instalador con Inno Setup.

## Licencia

MIT


# Cassettify

<p align="center">
  Aplicacion de escritorio para convertir y gestionar audio/video con <b>Python + FFmpeg</b>.<br/>
  Flujo rapido, procesamiento por lotes y una identidad visual con estilo propio.
</p>

---

## Vision

Cassettify busca una experiencia directa: cargar archivos, elegir perfil y exportar sin friccion.
Todo el procesamiento se ejecuta en local.

## Lo mas potente

- Conversion de audio y video con perfiles utiles para uso real.
- Procesamiento por lotes para colecciones grandes.
- Motor FFmpeg integrado en el proyecto.
- Interfaz desktop personalizada (estilo retro-moderno).
- Landing visual incluida para presentacion del proyecto.

## Demo Web (Landing)

La landing se encuentra en la raiz:

- `index.html`
- `styles.css`
- `app.js`

Abrir localmente:

```powershell
start index.html
```

## Estructura del proyecto

```text
cassettify/
|- diseño.py            # Aplicacion principal
|- ffmpeg/              # Binarios y documentacion de FFmpeg
|- bootstrap.ps1        # Script de arranque/configuracion
|- Cassettify.spec      # Configuracion de PyInstaller
|- try.iss              # Script de Inno Setup (instalador)
|- index.html           # Landing page
|- styles.css           # Estilos de la landing
|- app.js               # Interacciones de la landing
```

## Requisitos

- Windows
- Python 3.10 o superior
- FFmpeg (incluido en este repositorio)

## Inicio rapido

1. Clonar el repositorio.
2. Entrar al directorio del proyecto.
3. Ejecutar la aplicacion:

```powershell
python diseño.py
```

## Build y empaquetado

El repositorio ya incluye archivos para empaquetar la app:

- `Cassettify.spec` para PyInstaller
- `try.iss` para generar instalador con Inno Setup

## Estado del proyecto

Desarrollo activo. El enfoque actual esta en mejorar experiencia de uso, presentacion y empaquetado.

## Licencia

MIT License

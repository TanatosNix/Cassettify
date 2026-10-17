# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['diseño.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('done.wav', '.'),
        ('error.wav', '.'),
        ('bootstrap.ps1', '.'),
        ('update_deps.ps1', '.'),
        ('requirements.txt', '.'),
        ('requirements-build.txt', '.'),
        ('ffmpeg/*', 'ffmpeg'),
        ('ffmpeg/bin/*', 'ffmpeg/bin'),
    ] + collect_data_files('customtkinter'),
    hiddenimports=[
        'customtkinter',
        'darkdetect',
        'webview',
        'yt_dlp_ejs',
        'yt_dlp_plugins.extractor.getpot_bgutil',
        'yt_dlp_plugins.extractor.getpot_bgutil_http',
        'yt_dlp_plugins.extractor.getpot_bgutil_script',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cassettify',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icono.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Cassettify',
)

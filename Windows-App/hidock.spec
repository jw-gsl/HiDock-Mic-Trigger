# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building HiDock.exe.

Build with: pyinstaller hidock.spec --noconfirm
"""

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
    ],
    hiddenimports=[
        'PyQt6.sip',
        'pycaw',
        'pycaw.pycaw',
        'comtypes',
        'comtypes.stream',
        'pywhispercpp',
        'pywhispercpp.model',
        # Lazily imported modules (PyInstaller static analysis may miss these)
        'core.models',
        'core.config',
        'core.state',
        'core.usb_sync',
        'core.mic_trigger',
        'core.transcription',
        'core.model_download',
        'core.update_checker',
        'ui.main_window',
        'ui.recording_model',
        'ui.model_manager_dialog',
        'ui.device_manager_dialog',
        'ui.voice_library_dialog',
        'ui.onboarding_dialog',
        'ui.transcript_viewer',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HiDock',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app, no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icon.ico',
)

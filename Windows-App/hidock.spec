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
        # Bundle the cross-platform packages the app imports lazily at runtime.
        # Without these the frozen exe launches but every `shared.*` feature
        # (Models/get_model_status, Summarise, etc.) fails with an import error.
        # The runtime hook (pyi_rth_hidock.py) puts sys._MEIPASS on sys.path so
        # `import shared.*` resolves inside the frozen exe as it does from source.
        ('../shared', 'shared'),
        ('../transcription-pipeline', 'transcription-pipeline'),
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
        'core.summarize',
        'core.plaud',
        'core.imports',
        'ui.main_window',
        'ui.recording_model',
        'ui.model_manager_dialog',
        'ui.device_manager_dialog',
        'ui.voice_library_dialog',
        'ui.voice_training_dialog',
        'ui.onboarding_dialog',
        'ui.transcript_viewer',
        'ui.transcription_queue_dialog',
        'ui.summary_viewer',
        'ui.templates_manager_dialog',
        'ui.terminal_pane',
        'ui.device_strip',
        'ui.plaud_signin_dialog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_hidock.py'],
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

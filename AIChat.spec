# -*- mode: python ; coding: utf-8 -*-
block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

a = Analysis(
    ['main.py'], pathex=[PROJECT_ROOT], binaries=[], datas=[],
    hiddenimports=[
        'chromadb','chromadb.api','chromadb.config','openai',
        'PyQt6.QtCore','PyQt6.QtGui','PyQt6.QtWidgets',
        'PyQt6.QtNetwork','PyQt6.QtPrintSupport','PyQt6.QtSql','PyQt6.QtXml',
        'numpy','numpy.core',
    ],
    hookspath=[], runtime_hooks=[],
    excludes=['tkinter','pytest','IPython','jupyter','matplotlib','PIL'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='AIChat', debug=False, strip=False, upx=True, console=False,
)

app = BUNDLE(
    exe, name='AIChat.app', bundle_identifier='com.aichat.app',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'LSUIElement': False,
        'CFBundleShortVersionString': '0.1.0',
        'CFBundleVersion': '1',
        'LSMinimumSystemVersion': '11.0',
        'NSAppTransportSecurity': {'NSAllowsArbitraryLoads': True},
    },
)

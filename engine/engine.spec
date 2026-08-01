# PyInstaller spec for the NYAM Terminal engine.
#
# Produces a self-contained engine the Tauri shell can spawn on a machine with
# no Python installed. Built as ONEDIR, not onefile: onefile unpacks the whole
# tree to a temp directory on every launch, which adds seconds to startup and
# leaves the app looking hung while it does. This starts immediately.
#
#   cd engine && python -m PyInstaller engine.spec --noconfirm
#
# Output: engine/dist/nyam-engine/nyam-engine.exe

import os

block_cipher = None

# uvicorn and yfinance resolve a lot of their machinery at runtime, so static
# analysis misses it. Each of these is an import PyInstaller cannot see.
hidden = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "apscheduler.schedulers.background",
    "apscheduler.triggers.cron",
    "apscheduler.executors.pool",
    "encodings.idna",          # urllib needs this for international hostnames
    # chat.py does `from anthropic import Anthropic` INSIDE the function, so
    # static analysis never sees it and the packaged build shipped without the
    # SDK — chat failed with "anthropic SDK not installed" only once packaged.
    "anthropic",
]

# certifi's CA bundle is a data file, not a module — without it the ECB feed
# (and anything else chaining through an intermediate) fails verification.
datas = []
try:
    import certifi
    datas.append((certifi.where(), "certifi"))
except ImportError:
    pass

a = Analysis(
    ["server.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nyam-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console stays ON: the shell reads the engine's stdout, and its startup
    # line is how a launch failure gets diagnosed. Tauri spawns it without a
    # visible window anyway.
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="nyam-engine",
)

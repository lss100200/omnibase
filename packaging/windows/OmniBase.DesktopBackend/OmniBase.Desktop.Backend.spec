from pathlib import Path

project_dir = Path(SPECPATH).resolve()
repo_root = project_dir.parents[2]
backend_source = repo_root / "backend" / "src"
entrypoint = backend_source / "omnibase" / "desktop_local" / "app.py"

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(backend_source)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "uvicorn.lifespan.on",
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "alembic",
        "celery",
        "cryptography",
        "minio",
        "numpy",
        "openai",
        "pgvector",
        "psycopg",
        "redis",
        "sentence_transformers",
        "sqlalchemy",
        "torch",
        "transformers",
    ],
    noarchive=False,
    optimize=2,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="OmniBase.Desktop.Backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OmniBase.Desktop.Backend",
)

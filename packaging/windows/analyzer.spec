# Build on Windows from the repository root:
# python -m PyInstaller --noconfirm packaging/windows/analyzer.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent.parent
analysis = Analysis(
    [str(root / 'desktop.py')],
    pathex=[str(root)],
    hookspath=[str(root / 'packaging' / 'windows' / 'hooks')],
    datas=[(str(root / 'app.py'), '.'),
           (str(root / 'assets' / 'collagen.png'), 'assets'),
           (str(root / 'assets' / 'fiber_editor'), 'assets/fiber_editor'),
           (str(root / 'assets' / 'collagen.ico'), 'assets')]
          + collect_data_files('streamlit') + copy_metadata('streamlit'),
    hiddenimports=collect_submodules('streamlit') + collect_submodules('collagen')
                  + collect_submodules('scipy._external'),
    excludes=['pytest'],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
          name='CollagenFiberAnalyzer', console=False,
          icon=str(root / 'assets' / 'collagen.ico'))
collection = COLLECT(exe, analysis.binaries, analysis.datas,
                     name='CollagenFiberAnalyzer')

"""Bundle the lazy implementation used by scikit-image 0.27.

The .pyi files are read at runtime by lazy_loader.attach_stub. This hook is
activated only when _skimage2 is imported; stable scikit-image 0.26 has no such
package and continues to use PyInstaller's existing skimage hooks.
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('_skimage2', includes=['**/*.pyi'])
hiddenimports = collect_submodules(
    '_skimage2', filter=lambda name: '.tests' not in name and not name.endswith('.conftest')
)

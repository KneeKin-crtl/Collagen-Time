# Experimental Python 3.15.2 setup

Python 3.15 uses a separate package list, `requirements-python315.lock.txt`.
The stable `requirements.lock.txt` and Windows build workflow remain on their
tested Python 3.12 configuration. Installing a new Python version does not
upgrade an existing virtual environment.

This configuration includes **scikit-image 0.27.0rc1**, a release candidate.
Stable scikit-image 0.26.0 does not provide a Windows CPython 3.15 wheel.
It also updates Streamlit to 1.67.0, pandas to 3.1.1, PyArrow to 26.0.1,
httptools to 0.9.0, and rpds-py to 2026.9.1 for compatible dependencies.
The application permits pandas 3; the stable lock continues to pin pandas 2.
Do not mix the stable and experimental lock files in one environment.
The packaging hook retains the new scikit-image implementation's lazy imports
and runtime `.pyi` files. It does not switch the app to the new skimage2 API.

## Run from source on Windows

Download this branch's source ZIP from GitHub and extract it. Open the extracted
folder containing `app.py`, `pyproject.toml`, and `requirements-python315.lock.txt`.
Right-click empty space and select **Open in Terminal**. Use a user-owned folder,
such as Downloads or Documents. Administrator access is unnecessary.

Paste these commands **one at a time**, stopping if any command fails:

```powershell
py -3.15 --version
py -3.15 -m venv .venv315
.\.venv315\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-python315.lock.txt
.\.venv315\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv315\Scripts\python.exe -m pip check
.\.venv315\Scripts\python.exe -m pytest -q
.\.venv315\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

If Streamlit asks for an email, leave it blank and press Enter. Leave the terminal
open while using the app; press Ctrl+C to stop it. For later launches, open a
terminal in the same folder and run only the final command. Activation scripts
and PowerShell execution-policy changes are unnecessary. Installation needs an
internet connection; analysis runs locally afterward.

## Compatibility checks and Windows packaging

**Actions > Experimental Python 3.15** installs exact CPython 3.15.2 on Ubuntu
and Windows. It checks dependencies, runs the existing geometry, QC, export,
comparison and Streamlit tests, and checks the source launcher. On Windows it
also builds and smoke-tests the executable using PyInstaller 6.22.3. Download
the artifact labeled `experimental-python315` only after the Windows job passes.
This separate workflow does not upgrade the stable packaged build.

To build locally on Windows, use the environment above, then:

```powershell
.\.venv315\Scripts\python.exe -m pip install -r packaging/windows/build-requirements-python315.txt
.\.venv315\Scripts\python.exe desktop.py --smoke-test
.\.venv315\Scripts\python.exe -m PyInstaller --noconfirm packaging/windows/analyzer.spec
```

CI results must be checked for the exact commit being installed. A successful
dependency resolution alone is not a runtime test. Passing software tests does
not establish real-image scientific accuracy; representative ImageJ validation
and reviewed annotations remain necessary.

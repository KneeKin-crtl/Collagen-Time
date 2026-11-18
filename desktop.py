"""Local desktop launcher. The Windows bundle includes Python and the web UI."""
import argparse
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from collagen.resources import asset_path, resource_root


def app_path():
    return resource_root() / 'app.py'


def set_desktop_identity():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('KneeKin.CollagenFiberAnalyzer')


def set_window_icon(window):
    if sys.platform == 'win32':
        window.iconbitmap(default=str(asset_path('collagen.ico')))
    else:
        import tkinter as tk
        icon = tk.PhotoImage(file=str(asset_path('collagen.png')))
        window.iconphoto(True, icon)
        window._collagen_icon = icon  # Keep the Tcl image alive.


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class LocalServer:
    def __init__(self):
        self.port = free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.process = None
        log_dir = Path(tempfile.gettempdir()) / 'CollagenFiberAnalyzer'
        log_dir.mkdir(exist_ok=True)
        self.log_path = log_dir / f'server-{self.port}.log'

    def start(self):
        command = [sys.executable]
        if not getattr(sys, 'frozen', False):
            command.append(str(Path(__file__).resolve()))
        command += ['--serve', str(self.port), '--log-file', str(self.log_path)]
        with self.log_path.open('w', encoding='utf-8') as log:
            self.process = subprocess.Popen(command, stdout=log, stderr=log)

    def wait_ready(self, timeout=90):
        # Loopback requests stay local even when system proxy variables are set.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f'The analyzer stopped during startup. Log: {self.log_path}')
            try:
                with opener.open(self.url + '/_stcore/health', timeout=1) as response:
                    healthy = response.read().strip() == b'ok'
                with opener.open(self.url, timeout=1) as response:
                    html = response.read().lower()
                if healthy and b'streamlit' in html:
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.2)
        raise RuntimeError(f'Startup timed out. Log: {self.log_path}')

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def serve(port):
    from streamlit.web import cli
    sys.argv = ['streamlit', 'run', str(app_path()), '--server.address=127.0.0.1',
                f'--server.port={port}', '--server.headless=true',
                '--browser.gatherUsageStats=false', '--server.fileWatcherType=none',
                '--global.developmentMode=false', '--theme.base=light',
                '--theme.primaryColor=#168566', '--theme.backgroundColor=#F7F8F6',
                '--theme.secondaryBackgroundColor=#FFFFFF', '--theme.textColor=#334155']
    cli.main()


def self_test():
    from PIL import Image
    with Image.open(asset_path('collagen.ico')) as icon:
        if icon.ico.sizes() != {(n, n) for n in (16, 24, 32, 48, 64, 128, 256)}:
            raise RuntimeError('Bundled Windows icon is missing required sizes.')
    with Image.open(asset_path('collagen.png')) as icon:
        if icon.size != (256, 256):
            raise RuntimeError('Bundled application PNG icon is invalid.')
    # These explicit imports also let PyInstaller discover the analysis modules.
    from io import BytesIO
    import numpy as np
    from openpyxl import Workbook
    from collagen.pipeline import analyze
    from collagen.export import result_archive
    from collagen.validation import comparison_report
    from collagen.workbook import import_workbook
    from collagen.segmentation import ThresholdSegmenter
    image = np.zeros((80, 120), dtype=np.uint8)
    image[30:39, 15:100] = 200
    result = analyze(image, .5, 'desktop-smoke')
    if result.summary['accepted_isolated_count'] != 1 or not result_archive(result):
        raise RuntimeError('Synthetic analysis/export failed.')
    # Optional settings must also work with the bundled lazy image modules.
    for method in ('manual', 'local'):
        configured = analyze(image, .5, 'desktop-options', segmenter=ThresholdSegmenter(
            method=method, opening_radius=2, closing_radius=2))
        if configured.summary['accepted_isolated_count'] != 1:
            raise RuntimeError(f'Bundled {method} threshold/morphology failed.')
    manual = result.table[['image_id', 'fiber_id', 'length_um', 'mean_diameter_um']].copy()
    if comparison_report(result.table, manual).counts['matched_count'] != 1:
        raise RuntimeError('Comparison failed.')
    book = Workbook()
    sheet = book.active
    sheet.title = 'RT30min Length'
    sheet.append(['Image 1', 'Length'])
    sheet.append([1, 12.])
    buffer = BytesIO()
    book.save(buffer)
    if len(import_workbook(buffer.getvalue())) != 1:
        raise RuntimeError('Workbook import failed.')
    # Exercise the bundled app script, not just the web server's health route.
    from streamlit.testing.v1 import AppTest
    prefix = '''
from unittest.mock import patch
from io import BytesIO
import numpy as np
import tifffile
image = np.zeros((80, 120), np.uint16)
image[30:39, 15:100] = 45000
buffer = BytesIO()
tifffile.imwrite(buffer, image)
buffer.name = "desktop-smoke.tiff"
def uploaded(label, **kwargs):
    return buffer if label.startswith("Microscopy") else None
patcher = patch("streamlit.file_uploader", side_effect=uploaded)
patcher.start()
'''
    app = AppTest.from_string(prefix + app_path().read_text(encoding='utf-8') + '\npatcher.stop()').run(timeout=60)
    if app.dataframe:
        raise RuntimeError('Analysis ran without calibration/submission.')
    next(w for w in app.number_input if w.label == 'Calibration (µm/pixel)').set_value(.5)
    next(w for w in app.checkbox if w.label == 'Bright fibers on dark background').check()
    next(w for w in app.button if w.label == 'Analyze image').click().run(timeout=60)
    if app.exception or app.error or len(app.dataframe) != 1:
        raise RuntimeError(f'Bundled upload UI failed: {app.exception} {app.error}')
    if app.dataframe[0].value.review_status.tolist() != ['accepted']:
        raise RuntimeError('Bundled UI measurement failed.')
    if len(app.main.get('component_instance')) != 1:
        raise RuntimeError('Bundled UI must provide one interactive primary counting overlay.')
    import json
    label_args=json.loads(app.main.get('component_instance')[0].proto.json_args)
    from collagen.editor import original_preview
    uploaded_pixels = np.zeros((80, 120), np.uint16)
    uploaded_pixels[30:39, 15:100] = 45000
    if label_args['original_image'] != original_preview(uploaded_pixels)[0]:
        raise RuntimeError('Bundled comparison must retain the exact original source pixels.')
    if [item['fiber_id'] for item in label_args['fiber_id_labels']] != [1]:
        raise RuntimeError('Bundled ID label must match the measured fiber ID.')
    editor = resource_root() / 'assets' / 'fiber_editor' / 'index.html'
    script = editor.with_name('editor.js')
    if not editor.is_file() or not script.is_file() or 'streamlit:setComponentValue' not in script.read_text(encoding='utf-8'):
        raise RuntimeError('Bundled interactive review component is missing.')
    next(w for w in app.button if w.label == 'Mark rejected').click().run(timeout=60)
    if app.exception or app.dataframe[0].value.classification.tolist() != ['rejected']:
        raise RuntimeError('Bundled correction UI failed.')
    if json.loads(app.main.get('component_instance')[0].proto.json_args)['fiber_id_labels']:
        raise RuntimeError('Rejected fibers must not retain ID callouts.')
    next(w for w in app.button if w.label == 'Undo last correction').click().run(timeout=60)
    if app.exception or app.dataframe[0].value.classification.tolist() != ['accepted']:
        raise RuntimeError('Bundled correction Undo failed.')
    app.session_state['fiber_details'] = True
    app.run(timeout=60)
    details = next(e for e in app.expander if e.label == 'View Detailed Fiber Analysis')
    if len(details.get('image')) != 4 or len(app.main.get('component_instance')) != 1:
        raise RuntimeError('Bundled detailed analysis must retain all four stages.')
    from collagen.learning import FEATURE_NAMES, fit_classifier, predict
    samples = [dict(image_key=str(i), label='accepted' if label else 'rejected',
                    features=[float(label)] * len(FEATURE_NAMES))
               for i in range(2) for label in (0,1) for _ in range(5)]
    fitted = fit_classifier(samples)
    if predict(fitted, [[0.] * len(FEATURE_NAMES), [1.] * len(FEATURE_NAMES)]).tolist() != [False,True]:
        raise RuntimeError('Bundled feedback classifier fitting/inference failed.')
    if sys.platform == 'win32':
        import tkinter as tk
        set_desktop_identity()
        window = tk.Tk()
        window.withdraw()
        set_window_icon(window)
        window.update()
        window.destroy()


def smoke_test():
    # Never register synthetic smoke-test reviews in a user's persistent data.
    previous = os.environ.get('COLLAGEN_FEEDBACK_DIR')
    with tempfile.TemporaryDirectory(prefix='collagen-review-smoke-') as folder:
        os.environ['COLLAGEN_FEEDBACK_DIR'] = folder
        try:
            self_test()
        finally:
            if previous is None:
                os.environ.pop('COLLAGEN_FEEDBACK_DIR', None)
            else:
                os.environ['COLLAGEN_FEEDBACK_DIR'] = previous
    server = LocalServer()
    try:
        server.start()
        server.wait_ready()
    finally:
        server.stop()
    print('Desktop smoke test passed: analysis, export, comparison, workbook, upload UI and server.')


def launch():
    import tkinter as tk
    from tkinter import messagebox
    set_desktop_identity()
    root = tk.Tk()
    set_window_icon(root)
    root.title('Collagen Fiber Analyzer')
    root.geometry('540x220')
    status = tk.StringVar(value='Starting the local analyzer…')
    tk.Label(root, text='Collagen Fiber Analyzer', font=('Segoe UI', 16)).pack(pady=12)
    tk.Label(root, text='Baseline research application. No trained AI model is included.').pack()
    tk.Label(root, textvariable=status, wraplength=510).pack(pady=12)
    server = LocalServer()
    results = queue.Queue()
    opened = False
    open_button = tk.Button(root, text='Open analyzer', state='disabled', command=lambda: webbrowser.open(server.url))
    open_button.pack()

    def close():
        server.stop()
        root.destroy()

    def start():
        try:
            server.wait_ready()
            results.put(None)
        except Exception as error:
            results.put(str(error))

    def poll():
        nonlocal opened
        if not opened:
            try:
                error = results.get_nowait()
            except queue.Empty:
                root.after(200, poll)
                return
            if error:
                status.set('Startup failed. Close the launcher and review the log.')
                server.stop()
                messagebox.showerror('Analyzer startup failed', error)
                return
            opened = True
            status.set('Running locally. Close this launcher to stop the analyzer.')
            open_button.configure(state='normal')
            webbrowser.open(server.url)
        if server.process.poll() is not None:
            open_button.configure(state='disabled')
            status.set(f'The analyzer stopped. Log: {server.log_path}')
            return
        root.after(1000, poll)

    root.protocol('WM_DELETE_WINDOW', close)
    try:
        server.start()
        threading.Thread(target=start, daemon=True).start()
        root.after(200, poll)
        root.mainloop()
    finally:
        server.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--log-file', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    # Windowed executables have no console streams. Always retain diagnostics.
    if args.log_file or sys.stdout is None or sys.stderr is None:
        log_path = args.log_file or Path(tempfile.gettempdir()) / f'collagen-runtime-{os.getpid()}.log'
        log = log_path.open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = log
    if args.serve is not None:
        serve(args.serve)
    elif args.smoke_test:
        smoke_test()
    else:
        launch()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    try:
        main()
    except Exception:
        # Avoid a blocking bootloader error dialog in Windows CI smoke tests.
        traceback.print_exc()
        if '--smoke-test' not in sys.argv and '--serve' not in sys.argv and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, 'The analyzer could not start. Check the temporary startup logs.', 'Collagen Fiber Analyzer', 0)
        raise SystemExit(1)

"""End-to-end checks of source or frozen UI, using only generated synthetic data.

Install the optional .[browser] extra. Use --executable for a packaged analyzer.
Chromium is discovered locally on Linux; Windows uses installed Google Chrome.
"""
import argparse
from contextlib import closing, suppress
import csv
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import zlib
import zipfile

from playwright.sync_api import sync_playwright


def synthetic_png(path):
    # Mid-gray microscopy-like background makes plain black ID glyphs visible.
    # Larger than either result column: exercise fit-to-width and source mapping.
    rows = [bytearray([80]*1280) for _ in range(800)]
    for y0,y1,x0,x1 in ((30,41,30,140),(70,81,30,140),(110,121,0,140)):
        for y in range(y0,y1):
            rows[y][x0:x1] = bytes([200]) * (x1-x0)
    def chunk(kind, data):
        return struct.pack('!I',len(data)) + kind + data + struct.pack('!I',zlib.crc32(kind+data))
    raw = b''.join(b'\x00' + row for row in rows)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'
                     + chunk(b'IHDR',struct.pack('!IIBBBBB',1280,800,8,0,0,0,0))
                     + chunk(b'IDAT',zlib.compress(raw)) + chunk(b'IEND',b''))


def interactions(browser, url, root, output):
    page = browser.new_page(viewport={'width':1500,'height':1100})
    errors = []
    page.on('pageerror',lambda error: errors.append(str(error)))
    def editor():
        return page.frame_locator('iframe[title*="fiber_editor"]')
    def open_analysis():
        page.goto(url)
        page.get_by_role('heading',name='Collagen Fiber Analyzer',exact=True).wait_for()
        page.locator('input[type=file]').first.set_input_files(str(root/'synthetic.png'))
        # Wait for the image-specific form, rather than typing into the initial
        # empty-upload calibration widget while upload processing is in flight.
        page.get_by_text('Confirm exclusion rectangle',exact=True).wait_for(timeout=30000)
        calibration = page.get_by_role('spinbutton',name='Calibration (µm/pixel)',exact=True)
        calibration.fill('0.5')
        calibration.press('Tab')
        page.get_by_text('Bright fibers on dark background',exact=True).click()
        page.get_by_role('button',name='Analyze image',exact=True).click()
        editor().locator('#image').wait_for(state='visible',timeout=30000)
        editor().locator('#original-image').wait_for(state='visible',timeout=30000)
    def color(x,y):
        return editor().locator('#image').evaluate(
            '(c,p)=>Array.from(c.getContext("2d").getImageData(p[0],p[1],1,1).data)',[x,y])[:3]
    def original_pixels():
        return editor().locator('#original-image').evaluate('(c)=>c.toDataURL()')
    def assert_comparison(stacked=False):
        editor().get_by_role('heading',name='Original Microscope Image',exact=True).wait_for(state='visible')
        editor().get_by_role('heading',name='AI Fiber Analysis',exact=True).wait_for(state='visible')
        original = editor().locator('#original-image')
        analyzed = editor().locator('#image')
        # Wait for iframe resizing (including Streamlit's sidebar animation),
        # sampling both rectangles atomically rather than across resize events.
        deadline=time.monotonic()+10
        while True:
            layout=editor().locator('.results-grid').evaluate('''async g=>{
                await new Promise(requestAnimationFrame);
                const rect=id=>document.getElementById(id).getBoundingClientRect().toJSON();
                return {left:rect('original-viewport'),right:rect('viewport'),
                        original:rect('original-image'),analysis:rect('image')};
            }''')
            left,right=layout['left'],layout['right']
            equal=all(abs(layout['original'][axis]-layout['analysis'][axis])<1 for axis in ('width','height'))
            positioned=(left['y']+left['height']<right['y'] and abs(left['x']-right['x'])<1 if stacked
                        else left['x']+left['width']<right['x'] and abs(left['y']-right['y'])<1)
            if equal and positioned:
                break
            if time.monotonic()>deadline:
                raise AssertionError(layout)
            time.sleep(.1)
        assert original.evaluate('(c)=>[c.width,c.height]') == analyzed.evaluate('(c)=>[c.width,c.height]') == [1280,800]
        assert original.evaluate('(c)=>Array.from(c.getContext("2d").getImageData(50,35,1,1).data)') == [200,200,200,255]
        assert original.evaluate('(c)=>Array.from(c.getContext("2d").getImageData(500,500,1,1).data)') == [80,80,80,255]
    def id_labels():
        return json.loads(editor().locator('#image').get_attribute('data-id-labels'))
    def assert_ids(expected):
        actual=id_labels()
        assert [item['fiber_id'] for item in actual]==expected, actual
        assert all(item['text']==str(item['fiber_id']) for item in actual)
    def wait_color(x,y,green):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with suppress(Exception):
                value = color(x,y)
                if (value[1] > value[0] if green else value[0] > value[1]):
                    return
            time.sleep(.1)
        raise AssertionError(('Color did not update',x,y,green,color(x,y)))
    def click(x,y):
        canvas = editor().locator('#image')
        canvas.scroll_into_view_if_needed()
        canvas.evaluate('''(c,p)=>{
            const v=c.parentElement,s=c.getBoundingClientRect().width/c.width;
            v.scrollLeft=Math.max(0,p[0]*s-v.clientWidth/2);
            v.scrollTop=Math.max(0,p[1]*s-v.clientHeight/2);
        }''',[x,y])
        box = canvas.bounding_box()
        width,height = canvas.evaluate('(c)=>[c.width,c.height]')
        page.mouse.click(box['x']+x*box['width']/width,box['y']+y*box['height']/height)
    def states():
        with closing(sqlite3.connect(root/'data'/'feedback.sqlite3')) as con:
            return con.execute('SELECT fiber_id,manual_classification,active FROM objects ORDER BY fiber_id').fetchall()

    open_analysis()
    wait_color(50,35,True)
    wait_color(50,75,True)
    assert_ids([1,2])  # Excluded border object 3 never gets an ID.
    assert_comparison()
    original_before = original_pixels()
    editor().locator('.results-grid').screenshot(path=str(output/'comparison-wide.png'))
    # Original-side clicks must not create corrections or change the overlay.
    before_states=states()
    editor().locator('#original-image').click(position={'x':20,'y':20})
    assert states()==before_states
    page.set_viewport_size({'width':600,'height':1000})
    # The component's media query responds to available frontend width.
    editor().locator('.results-grid').evaluate('(g)=>getComputedStyle(g).gridTemplateColumns')
    editor().locator('#image').scroll_into_view_if_needed()
    assert_comparison(stacked=True)
    editor().locator('.results-grid').screenshot(path=str(output/'comparison-narrow.png'))
    page.set_viewport_size({'width':1500,'height':1100})
    editor().locator('#image').scroll_into_view_if_needed()
    assert_comparison()
    print('Original and analysis visible at matching scale; responsive stacking passed',flush=True)
    before_states=states()
    with_ids=editor().locator('#image').evaluate('(c)=>c.toDataURL()')
    editor().get_by_role('checkbox',name='Show accepted fiber IDs',exact=True).uncheck()
    without_ids=editor().locator('#image').evaluate('(c)=>c.toDataURL()')
    assert with_ids!=without_ids
    assert states()==before_states
    editor().get_by_role('checkbox',name='Show accepted fiber IDs',exact=True).check()
    assert editor().locator('#image').evaluate('(c)=>c.toDataURL()')==with_ids
    click(50,35)
    wait_color(50,35,False)
    assert original_pixels()==original_before, 'Review changed the original image.'
    assert_ids([2])
    assert color(50,75)[1] > color(50,75)[0], 'A neighboring object changed.'
    print('Exact click changed one object only',flush=True)
    editor().locator('#zoom').fill('400')
    assert_comparison()
    for source,target in (('#viewport','#original-viewport'),('#original-viewport','#viewport')):
        editor().locator(source).evaluate('(v)=>{v.scrollLeft=100;v.scrollTop=80;}')
        page.wait_for_function('''()=>{
            const f=document.querySelector('iframe[title*="fiber_editor"]');
            const d=f.contentDocument,a=d.getElementById('viewport'),b=d.getElementById('original-viewport');
            return a.scrollLeft===b.scrollLeft && a.scrollTop===b.scrollTop;
        }''')
    click(50,35)
    wait_color(50,35,True)
    assert_ids([1,2])
    click(50,35)
    wait_color(50,35,False)
    assert original_pixels()==original_before
    print('Zoomed click mapping passed',flush=True)
    editor().locator('#zoom').fill('100')
    editor().get_by_role('button',name='Outline missed / merged fiber',exact=True).click()
    for point in ((220,120),(280,120),(280,135),(220,135)):
        click(*point)
    editor().get_by_role('button',name='Save outline',exact=True).click()
    wait_color(240,125,True)
    assert_ids([2,4])
    assert (4,'accepted',1) in states()
    print('Manual outline saved and measured',flush=True)
    with page.expect_download() as downloaded:
        page.get_by_role('button',name='Download per-fiber CSV',exact=True).click()
    downloaded.value.save_as(output/'fibers.csv')
    with (output/'fibers.csv').open(encoding='utf-8',newline='') as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert len(rows)==4 and rows[0]['classification']=='rejected'
    assert rows[-1]['object_source']=='manual' and float(rows[-1]['length_um'])>0
    assert rows[-1]['included_in_measurement_statistics']=='True'
    before_labels=id_labels()
    expected_ids=[int(row['fiber_id']) for row in rows if row['included_in_measurement_statistics']=='True']
    assert [item['fiber_id'] for item in before_labels]==expected_ids
    page.get_by_role('button',name='Prepare QC archive',exact=True).click()
    with page.expect_download() as downloaded:
        page.get_by_role('button',name='Download all QC outputs',exact=True).click()
    downloaded.value.save_as(output/'analysis.zip')
    with zipfile.ZipFile(output/'analysis.zip') as archive:
        assert json.loads(archive.read('fiber_id_labels.json'))==before_labels
    page.screenshot(path=str(output/'review.png'),full_page=True)
    page.close()
    page = browser.new_page(viewport={'width':1500,'height':1100})
    page.on('pageerror',lambda error: errors.append(str(error)))
    open_analysis()
    wait_color(50,35,False)
    wait_color(240,125,True)
    assert id_labels()==before_labels
    assert_comparison()
    assert original_pixels()==original_before
    editor().get_by_role('button',name='Undo',exact=True).click()
    deadline = time.monotonic() + 20
    while time.monotonic()<deadline and (4,'accepted',1) in states():
        time.sleep(.1)
    assert (4,None,0) in states()
    # Wait for the same update to reach the displayed image, not just SQLite.
    deadline=time.monotonic()+20
    while time.monotonic()<deadline and [i['fiber_id'] for i in id_labels()]!=[2]:
        time.sleep(.1)
    assert_ids([2])
    print('Corrections and Undo survived reopening',flush=True)
    page.get_by_text('Feedback learning, model evaluation and backups',exact=True).click()
    page.get_by_role('button',name='Retrain Model Using Corrections',exact=True).click()
    page.get_by_text('Review at least two training images and two separate evaluation images before retraining.',
                     exact=True).wait_for(timeout=20000)
    assert not errors, errors
    report = dict(exact_object_click=True,zoom_mapping=True,manual_outline=True,csv_updated=True,
                  restored_after_reopen=True,persistent_undo=True,insufficient_data_notice=True,
                  measured_id_labels=True,label_visibility_updates=True,label_toggle_is_display_only=True,
                  label_manifest_matches_csv=True,
                  original_and_analysis_side_by_side=True,matching_scale_and_aspect_ratio=True,
                  responsive_stacking=True,original_unchanged_by_review=True,synchronized_zoom_and_scroll=True,
                  browser_errors=errors)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Browser interaction checks passed',flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable',type=Path)
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--browser-executable')
    parser.add_argument('--output-dir',type=Path,default=Path('feedback-browser-results'))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='collagen-browser-') as folder:
        root = Path(folder)
        synthetic_png(root/'synthetic.png')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}/'
        environment = os.environ.copy()
        environment['COLLAGEN_FEEDBACK_DIR'] = str(root/'data')
        command = ([str(args.executable.resolve()),'--serve',str(port),'--log-file',str(output/'runtime.log')]
                   if args.executable else [args.python,'-m','streamlit','run',str(project/'app.py'),
                    '--server.headless','true','--server.address','127.0.0.1','--server.port',str(port),
                    '--browser.gatherUsageStats','false','--server.fileWatcherType','none'])
        with (output/'server.log').open('w',encoding='utf-8') as log:
            process = subprocess.Popen(command,env=environment,cwd=project,stdout=log,stderr=log)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic()+60
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Server exited before readiness. Inspect server.log.')
                    try:
                        with opener.open(url+'_stcore/health',timeout=1) as response:
                            if response.read().strip()==b'ok':
                                break
                    except OSError:
                        pass
                    if time.monotonic()>deadline:
                        raise TimeoutError('Server readiness timed out.')
                    time.sleep(.2)
                with sync_playwright() as playwright:
                    chromium = args.browser_executable or shutil.which('chromium') or shutil.which('chromium-browser')
                    options = dict(headless=True)
                    if chromium:
                        options['executable_path'] = chromium
                    else:
                        options['channel'] = 'chrome'
                    if sys.platform.startswith('linux'):
                        options['args'] = ['--no-sandbox']
                    browser = playwright.chromium.launch(**options)
                    try:
                        interactions(browser,url,root,output)
                    except Exception:
                        with suppress(Exception):
                            failed_page = browser.contexts[0].pages[-1]
                            failed_page.screenshot(path=str(output/'failure.png'),full_page=True)
                            (output/'failure-page.txt').write_text(failed_page.locator('body').inner_text(),encoding='utf-8')
                        raise
                    finally:
                        browser.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__=='__main__':
    try:
        main()
    except Exception:
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a',encoding='utf-8') as summary:
                summary.write('### Packaged browser check failed\n\n```text\n' + traceback.format_exc()[-12000:] + '\n```\n')
        raise

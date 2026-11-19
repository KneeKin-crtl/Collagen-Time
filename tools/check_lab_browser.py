"""Check the lab presentation in source or packaged Windows UI with synthetic data.

Uses the same isolated server/database harness as check_feedback_browser.py.
"""
import base64
import csv
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse

from playwright.sync_api import expect

import check_feedback_browser as checks


def interactions(browser,url,root,output,network_latency_ms=0):
    page=checks.new_analysis_page(browser,network_latency_ms)
    page.set_viewport_size(dict(width=1920,height=1080))
    errors=[]
    external=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('request',lambda request:external.append(request.url)
            if urlparse(request.url).scheme in ('http','https') and urlparse(request.url).hostname not in ('127.0.0.1','localhost') else None)
    page.goto(url)
    heading=page.get_by_role('heading',name='Collagen Fiber Analyzer',exact=True)
    heading.wait_for()
    app=page.locator('[data-testid="stApp"]')
    def idle():
        expect(app).to_have_attribute('data-test-connection-state','CONNECTED',timeout=30000)
        expect(app).to_have_attribute('data-test-script-state','notRunning',timeout=30000)
    idle()
    page.get_by_role('heading',name='Ready for your next microscope image',exact=True).wait_for()
    logo=page.get_by_role('img',name='Collagen Fiber Analyzer custom logo',exact=True)
    expect(logo).to_be_visible()
    logo.evaluate('async img=>{if(!img.complete)await img.decode();}')
    expected_logo=Path(__file__).resolve().parents[1]/'assets'/'collagen.png'
    assert base64.b64decode(logo.get_attribute('src').split(',',1)[1])==expected_logo.read_bytes()
    def brand():
        a,b=logo.bounding_box(),heading.bounding_box()
        assert 36<=a['height']<=44 and abs(a['width']-a['height'])<1
        assert a['x']+a['width']<b['x'] and abs((a['y']+a['height']/2)-(b['y']+b['height']/2))<20,(a,b)
        assert a['y']>=0
        caption=page.locator('.lab-header p').bounding_box()
        assert b['y']+b['height']<=caption['y'], 'Header title overlaps its subtitle.'
    brand()
    assert app.evaluate('e=>getComputedStyle(e).backgroundColor')=='rgb(247, 248, 246)'
    page.screenshot(path=str(output/'empty-workspace.png'),full_page=True)
    # Every existing setting remains available; opening sections must not submit.
    page.get_by_text('Morphological Processing',exact=True).click()
    page.get_by_text('Fiber selection and measurement eligibility',exact=True).click()
    expected_controls={
        'spinbutton':['Calibration (µm/pixel)','Local threshold block size (odd)','Gaussian sigma (pixels)',
            'Opening radius (pixels)','Closing radius (pixels)','Minimum accepted area (pixels)',
            'Endpoint/junction exclusion (pixels)','Minimum fiber diameter (nm)','Maximum fiber diameter (nm)',
            'Minimum fiber length (µm)','Minimum component area (µm²)','Minimum reliable mean width (pixels)'],
        'combobox':['Threshold','Crossing / merged candidates'],
        'checkbox':['Bright fibers on dark background','Enable research-interest diameter range',
                    'Exclude border-truncated fibers from measurements'],
    }
    for role,labels in expected_controls.items():
        for label in labels:
            expect(page.get_by_role(role,name=label,exact=True)).to_be_visible()
    expect(page.get_by_role('group',name='Manual threshold (normalized intensity)',exact=True).get_by_role('slider')).to_be_visible()
    sidebar=page.locator('[data-testid="stSidebarContent"]')
    main=page.locator('[data-testid="stMain"]')
    previous_scroll=main.evaluate('e=>e.scrollTop')
    sidebar.evaluate('e=>{e.scrollTop=300;}')
    assert sidebar.evaluate('e=>e.scrollTop')>0
    assert main.evaluate('e=>e.scrollTop')==previous_scroll
    sidebar.evaluate('e=>{e.scrollTop=0;}')
    assert not page.locator('iframe[title*="fiber_editor"]').count()
    # Source hashing, image-specific calibration and original-pixel form values
    # are retained even with a long filename in the new upload panel.
    long_image=root/(('microscopy-sample-'*7)+'.png')
    checks.synthetic_png(long_image)
    sha=hashlib.sha256(long_image.read_bytes()).hexdigest()
    page.locator('input[type=file]').first.set_input_files(str(long_image))
    page.locator('.st-key-loaded-image-'+sha).wait_for()
    idle()
    for label in ('Exclusion left','Exclusion top','Exclusion right','Exclusion bottom'):
        expect(page.get_by_role('spinbutton',name=label,exact=True)).to_be_visible()
    calibration=page.get_by_role('spinbutton',name='Calibration (µm/pixel)',exact=True)
    calibration.fill('.5');calibration.press('Tab')
    page.get_by_text('Bright fibers on dark background',exact=True).click()
    page.get_by_role('button',name='Analyze image',exact=True).click()
    frame=page.frame_locator('iframe[title*="fiber_editor"]')
    canvas=frame.locator('#image')
    def saved():
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try:
                value=canvas.evaluate('c=>({pending:c.dataset.pendingCount,ready:c.dataset.editorReady,source:JSON.parse(c.dataset.sourceImage||"{}")})',timeout=500)
                if value['pending']=='0' and value['ready']=='true' and value['source'].get('sha256')==sha:
                    return
            except Exception:
                pass
            time.sleep(.1)
        raise AssertionError('Lab workspace did not finish loading: '+json.dumps(checks.editor_diagnostics(page)))
    canvas.wait_for(state='visible',timeout=30000);saved()
    def cards(values):
        for key,value in values.items():
            expect(page.locator(f'[data-summary-field="{key}"] dd')).to_have_text(str(value))
    cards(dict(candidate_count=3,manual_reviewed_count=0,accepted_measurement_count=2,review_required_count=1))
    for name in ('Review controls','Classification controls','History controls','Display controls'):
        expect(frame.get_by_role('group',name=name,exact=True)).to_be_visible()
    assert frame.locator('body').evaluate('e=>getComputedStyle(e).backgroundColor')=='rgb(247, 248, 246)'
    source_pixels=frame.locator('#original-image').evaluate('c=>c.toDataURL()')
    layouts=[]
    for width,height in ((1920,1080),(1440,1000),(1280,900),(600,1000)):
        page.set_viewport_size(dict(width=width,height=height))
        if width==600:
            # Streamlit automatically collapses the sidebar at this breakpoint.
            # Wait for that animation before deciding if a manual close is needed.
            collapse=page.locator('[data-testid="stSidebarCollapseButton"] button')
            page.wait_for_timeout(350)
            if collapse.is_visible():
                collapse.click()
        frame.locator('.results-grid').scroll_into_view_if_needed()
        deadline=time.monotonic()+10
        while True:
            layout=frame.locator('.results-grid').evaluate('''async g=>{
                await new Promise(requestAnimationFrame);
                const box=id=>document.getElementById(id).getBoundingClientRect().toJSON();
                return {left:box('original-viewport'),right:box('viewport'),
                        original:box('original-image'),analysis:box('image'),
                        overflow:document.documentElement.scrollWidth>innerWidth+1};
            }''')
            same=all(abs(layout['original'][k]-layout['analysis'][k])<1 for k in ('width','height'))
            a,b=layout['left'],layout['right']
            positioned=(a['y']+a['height']<b['y'] if width==600 else a['x']+a['width']<b['x'] and abs(a['y']-b['y'])<1)
            if same and positioned and not layout['overflow']:
                break
            if time.monotonic()>deadline:raise AssertionError((width,layout))
            time.sleep(.1)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert frame.locator('.tools').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        assert frame.locator('.display-tools').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        page.screenshot(path=str(output/f'research-lab-{width}.png'),full_page=True)
        layouts.append(dict(width=width,stacked=width==600,matching_scale=True,no_horizontal_overflow=True))
    # The new live cards must reflect confirmed existing decisions, not a
    # different visual-only count. Exercise an actual right click after resizing.
    frame.locator('#viewport').scroll_into_view_if_needed()
    canvas.evaluate('''c=>{const v=c.closest('.viewport');v.scrollLeft=c.offsetLeft;v.scrollTop=c.offsetTop;}''')
    scale=canvas.evaluate('c=>c.getBoundingClientRect().width/c.width')
    canvas.click(position=dict(x=50*scale,y=35*scale),button='right')
    saved()
    cards(dict(candidate_count=3,manual_reviewed_count=1,accepted_measurement_count=1,review_required_count=2))
    expect(page.get_by_role('combobox',name='Dataset role for this image',exact=True)).to_be_disabled()
    assert frame.locator('#original-image').evaluate('c=>c.toDataURL()')==source_pixels
    with page.expect_download() as downloaded:
        page.get_by_role('button',name='Download per-fiber CSV',exact=True).click()
    downloaded.value.save_as(output/'ui-measurements.csv')
    with (output/'ui-measurements.csv').open(encoding='utf-8',newline='') as file:
        rows=list(csv.DictReader(file))
    assert [int(r['fiber_id']) for r in rows if r['included_in_measurement_statistics']=='True']==[2]
    assert all(float(r['calibration_um_per_px'])==.5 for r in rows)
    assert not errors,errors
    assert not external,external
    (output/'ui-report.json').write_text(json.dumps(dict(custom_logo_unchanged=True,all_settings_available=True,
        existing_counts_match_decisions=True,role_lock_preserved=True,original_pixels_unchanged=True,
        exports_match_inclusion=True,independent_sidebar_scroll=True,layouts=layouts,external_requests=external,browser_errors=errors),indent=2),encoding='utf-8')
    print('Research Lab UI passed: original logo, settings, live saved counts, locked role, exports and four responsive layouts',flush=True)


if __name__=='__main__':
    checks.interactions=interactions
    checks.main()

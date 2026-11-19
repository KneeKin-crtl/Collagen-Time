"""Real GUI reference annotations, using an isolated generated image/database."""
from contextlib import closing,suppress
import csv
import hashlib
import json
import os
import re
import sqlite3
import time
import traceback

from playwright.sync_api import expect

import check_feedback_browser as checks


def interactions(browser,url,root,output,network_latency_ms=0):
    def new_page():
        page=checks.new_analysis_page(browser,network_latency_ms)
        # HTTP latency does not delay WebSocket form submissions. Exercise the
        # save/download ordering explicitly rather than relying on CI speed.
        page.add_init_script('''(()=>{
            const send=WebSocket.prototype.send;
            WebSocket.prototype.send=function(data){setTimeout(()=>{
                if(this.readyState===WebSocket.OPEN)send.call(this,data);
            },200);};
        })();''')
        return page
    page=new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    source=root/'synthetic.png';sha=hashlib.sha256(source.read_bytes()).hexdigest()
    def frame():return page.frame_locator('iframe[title*="fiber_editor"]')
    def idle():
        expect(page.locator('[data-testid="stApp"]')).to_have_attribute('data-test-script-state','notRunning',timeout=30000)
    def saved():
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            with suppress(Exception):
                state=frame().locator('#image').evaluate('c=>({ready:c.dataset.editorReady,pending:c.dataset.pendingCount,revision:args?.review_revision,source:JSON.parse(c.dataset.sourceImage||"{}")})',timeout=500)
                with closing(sqlite3.connect(root/'data/feedback.sqlite3')) as con:
                    revision=con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
                if (state['ready']=='true' and state['pending']=='0' and state['source'].get('sha256')==sha
                        and state['revision']==revision):return
            time.sleep(.1)
        raise AssertionError(checks.editor_diagnostics(page))
    def open_image():
        page.goto(url);page.get_by_text('Review ImageJ Excel workbook',exact=True).wait_for();idle()
        page.locator('input[type=file]').first.set_input_files(str(source))
        page.locator('.st-key-loaded-image-'+sha).wait_for(timeout=30000);idle()
        calibration=page.get_by_role('spinbutton',name='Calibration (µm/pixel)',exact=True)
        expect(calibration).to_have_value('');calibration.fill('.5');calibration.press('Tab')
        page.get_by_text('Bright fibers on dark background',exact=True).click()
        page.get_by_role('button',name='Analyze image',exact=True).click();saved();idle()
    def choose(label, option):
        control=page.get_by_role('combobox',name=label,exact=True)
        control.scroll_into_view_if_needed()
        control.evaluate('''async e=>{
            const d=e.closest('details');
            if(d)await Promise.all(d.getAnimations({subtree:true}).filter(a=>a.effect.getComputedTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));
            await new Promise(resolve=>{let old='',stable=0;const tick=()=>{
              const r=e.getBoundingClientRect(),now=[r.x,r.y,r.width,r.height].join(',');
              stable=now===old?stable+1:0;old=now;if(stable>=4)resolve();else requestAnimationFrame(tick);
            };requestAnimationFrame(tick);});
        }''')
        control.locator('..').get_by_role('button',name='Open',exact=True).click()
        expect(control).to_have_attribute('aria-expanded','true')
        page.get_by_role('option',name=option,exact=True).click();idle()
    def reference(name):
        captions=page.get_by_text(re.compile(r'^Saved reference revision:'))
        expect(captions).to_have_count(1,timeout=30000)
        caption=captions.inner_text()
        expected_revision=int(re.search(r'Saved reference revision: (\d+)',caption)[1])
        with page.expect_download() as result:
            page.get_by_role('button',name='Download versioned reference JSON',exact=True).click()
        result.value.save_as(output/name)
        document=json.loads((output/name).read_text())
        assert document['annotation_revision']==expected_revision, ('Stale reference download',expected_revision,document['annotation_revision'])
        return document
    def submit_reference(button, object_count):
        caption=page.get_by_text(re.compile(r'^Saved reference revision:'))
        previous=caption.inner_text()
        previous_revision=int(re.search(r'Saved reference revision: (\d+)',previous)[1])
        button.click()
        # The image editor can already be idle while a native Streamlit form
        # submission has not started. Wait for its actual saved revision.
        updated=page.get_by_text(re.compile(
            rf'^Saved reference revision: (?!{previous_revision}\b)\d+ · {object_count} reference objects\.')).last
        expect(updated).to_be_visible(timeout=30000)
        # Streamlit can briefly retain both stale and current panel trees.
        expect(caption).to_have_count(1,timeout=30000)
        expect(caption).not_to_have_text(previous,timeout=30000)
        idle();saved()
    def csv_rows(name):
        with page.expect_download() as result:
            page.get_by_role('button',name='Download per-fiber CSV',exact=True).click()
        result.value.save_as(output/name)
        with (output/name).open(newline='',encoding='utf-8') as file:return list(csv.DictReader(file))
    def click(x,y):
        canvas=frame().locator('#image');frame().locator('#viewport').scroll_into_view_if_needed()
        canvas.evaluate('''(c,p)=>{const v=c.closest('.viewport'),s=c.getBoundingClientRect().width/c.width;
            v.scrollLeft=Math.max(0,c.offsetLeft+p[0]*s-v.clientWidth/2);v.scrollTop=Math.max(0,c.offsetTop+p[1]*s-v.clientHeight/2);}''',[x,y])
        box=canvas.bounding_box()
        page.mouse.click(box['x']+x*box['width']/1280,box['y']+y*box['height']/800)
    def events():
        with closing(sqlite3.connect(root/'data/feedback.sqlite3')) as con:
            return con.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    open_image()
    before=csv_rows('before.csv');before_events=events()
    original=frame().locator('#original-image').evaluate('c=>c.toDataURL()')
    decision_pixels=frame().locator('#image').evaluate('c=>c.toDataURL()')
    panel='Scientific validation and independent reference annotations'
    page.get_by_text(panel,exact=True).click();idle()
    page.get_by_role('textbox',name='Reference reviewer',exact=True).fill('Synthetic browser reviewer')
    page.get_by_role('textbox',name='Biological specimen ID (leave blank if unknown)',exact=True).fill('synthetic-specimen-browser')
    page.get_by_role('textbox',name='Acquisition group (leave blank if unknown)',exact=True).fill('synthetic-session-browser')
    page.get_by_role('textbox',name='Magnification metadata',exact=True).fill('40x (synthetic metadata only)')
    choose('Independent reference category','Valid measurable fiber')
    submit_reference(page.get_by_role('button',name='Save selected detection category (not boundary truth)',exact=True),1)
    copied=reference('classification-only.json')
    assert len(copied['objects'])==1 and copied['objects'][0]['boundary_source']=='prediction_reference'
    prediction=copied['objects'][0]['original_prediction']
    assert prediction['fiber_id']==1 and prediction['original_row']['review_status']=='accepted'
    choose('Independent reference category','Non-fiber object')
    submit_reference(page.get_by_role('button',name='Save selected detection category (not boundary truth)',exact=True),1)
    relabeled=reference('reclassified.json')
    assert relabeled['objects'][0]['category']=='non_fiber'
    assert relabeled['objects'][0]['original_prediction']==prediction
    assert relabeled['objects'][0]['object_id']==copied['objects'][0]['object_id']
    assert events()==before_events and csv_rows('after-classification.csv')==before
    choose('Independent reference category','Valid measurable fiber')
    page.get_by_role('spinbutton',name='Independent manual reference length (µm; optional)',exact=True).fill('30')
    page.get_by_role('spinbutton',name='Independent manual reference mean diameter (µm; optional)',exact=True).fill('5')
    page.get_by_role('textbox',name='Independent measurement source / ROI identifier',exact=True).fill('Analytical synthetic dimensions')
    submit_reference(page.get_by_role('button',name='Configure independent reference outline',exact=True),1)
    expect(frame().locator('#reference')).to_be_enabled()
    frame().locator('#reference').click()
    expect(frame().locator('#mode-label')).to_contain_text('Independent reference mode')
    assert frame().locator('#image').evaluate('c=>c.toDataURL()')==original, 'Reference tracing must show unannotated source pixels.'
    expect(frame().locator('#accept')).to_be_disabled();expect(frame().locator('#reject')).to_be_disabled()
    frame().locator('#zoom').fill('400')
    for p in ((700,350),(760,350),(760,360),(700,360)):click(*p)
    frame().locator('#image').focus();page.keyboard.press('Control+z')
    assert frame().locator('#image').evaluate('()=>state.points.length')==3
    page.keyboard.press('a');page.keyboard.press('r')
    assert events()==before_events, 'Reference mode changed an operational decision.'
    click(700,360)
    submit_reference(frame().get_by_role('button',name='Save reference boundary',exact=True),2)
    expect(frame().locator('#mode-label')).to_contain_text('Review mode')
    drawn=reference('independent-reference.json')
    assert len(drawn['objects'])==2
    obj=drawn['objects'][1]
    assert obj['boundary_source']=='independent_manual' and obj['bbox_yxyx']==[350,700,361,761], (
        'Independent boundary coordinates differ',obj.get('bbox_yxyx'),obj.get('human_polygon_xy'))
    assert obj['length_um']==30 and obj['mean_diameter_um']==5
    assert drawn['specimen_id']=='synthetic-specimen-browser' and drawn['calibration_um_per_px']==.5
    assert events()==before_events and csv_rows('after-outline.csv')==before
    assert frame().locator('#image').evaluate('c=>c.toDataURL()')==decision_pixels
    assert frame().locator('#original-image').evaluate('c=>c.toDataURL()')==original
    frame().locator('#zoom').fill('100')
    page.screenshot(path=str(output/'scientific-reference-workflow.png'),full_page=True)
    bad={**drawn,'image_key':'0'*64};bad_path=root/'wrong-reference.json';bad_path.write_text(json.dumps(bad))
    uploader=page.get_by_test_id('stFileUploader').filter(has_text='Import independently reviewed reference JSON')
    uploader.locator('input[type=file]').set_input_files(str(bad_path));idle()
    page.get_by_role('button',name='Validate and import reference version',exact=True).click();idle()
    page.get_by_text('Reference pixels, shape or dtype do not match this original image.',exact=True).wait_for()
    assert reference('after-rejected-import.json')['version_id']==drawn['version_id']
    page.close();page=new_page()
    page.on('pageerror',lambda error:errors.append(str(error)))
    open_image();page.get_by_text(panel,exact=True).click();idle()
    restored=reference('reopened.json')
    assert restored==drawn and csv_rows('reopened-operational.csv')==before
    assert not errors,errors
    (output/'scientific-report.json').write_text(json.dumps(dict(
        reference_categories_saved=True,prediction_masks_not_ground_truth=True,original_prediction_preserved=True,
        independent_missed_reference_drawn=True,reference_mode_shows_original=True,zoom_coordinates_correct=True,
        manual_measurements_not_derived=True,operational_predictions_unchanged=True,reference_versions_persist=True,
        wrong_source_import_rejected=True,network_latency_ms=network_latency_ms,
        websocket_send_latency_ms=200,saved_revision_matches_download=True,browser_errors=errors),indent=2))
    print('Scientific reference browser passed: four-way labels, immutable predictions, independent outline, original-pixel tracing, no operational changes, import identity and reopening',flush=True)


if __name__=='__main__':
    try:
        checks.interactions=interactions
        checks.main()
    except Exception:
        if os.environ.get('GITHUB_ACTIONS')=='true':
            detail=traceback.format_exc()[-5000:].replace('%','%25').replace('\r','%0D').replace('\n','%0A')
            print('::error title=Reference browser failure::'+detail,flush=True)
        raise

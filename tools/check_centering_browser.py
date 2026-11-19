"""Verify active-fiber navigation in the real viewer on isolated synthetic data."""
from contextlib import closing,suppress
import csv
import json
import os
import sqlite3
import time
import traceback

from PIL import Image,ImageDraw
from playwright.sync_api import expect

import check_feedback_browser as checks


def synthetic(path,**kwargs):
    image=Image.new('L',(1280,800),80);draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,140,10),fill=200)
    draw.rectangle((1100,80,1279,90),fill=200)
    draw.line([(100,250),(400,250),(400,450)],fill=200,width=9)
    draw.line([(800,480),(800,650)],fill=200,width=7)
    draw.line([(750,550),(850,550)],fill=200,width=7)
    draw.rectangle((1050,760,1279,770),fill=200)
    draw.rectangle((30,789,160,799),fill=200)
    image.save(path)


def interactions(browser,url,root,output,network_latency_ms=0):
    page=checks.new_analysis_page(browser,network_latency_ms);errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    def editor():return page.frame_locator('iframe[title*="fiber_editor"]')
    def idle():
        expect(page.locator('[data-testid="stApp"]')).to_have_attribute('data-test-script-state','notRunning',timeout=30000)
    def saved():
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            with suppress(Exception):
                value=editor().locator('#image').evaluate('c=>[c.dataset.editorReady,c.dataset.pendingCount]',timeout=500)
                if value==['true','0']:idle();return
            time.sleep(.1)
        raise AssertionError(checks.editor_diagnostics(page))
    def open_image():
        page.goto(url);page.get_by_text('Review ImageJ Excel workbook',exact=True).wait_for();idle()
        page.locator('input[type=file]').first.set_input_files(str(root/'synthetic.png'))
        page.get_by_text('Loaded image: synthetic.png · 1280 × 800 pixels',exact=True).wait_for();idle()
        value=page.get_by_role('spinbutton',name='Calibration (µm/pixel)',exact=True)
        value.fill('.5');value.press('Tab')
        page.get_by_text('Bright fibers on dark background',exact=True).click()
        scale_review=page.get_by_role('combobox',name='Scale-bar review',exact=True)
        if scale_review.is_visible():
            scale_review.scroll_into_view_if_needed()
            scale_review.locator('..').get_by_role('button',name='Open',exact=True).click()
            page.get_by_role('option',name='No exclusion needed (reviewed)',exact=True).click()
        page.get_by_role('button',name='Analyze image',exact=True).click();saved()
    def active():return editor().locator('#image').get_attribute('data-active-fiber-id')
    def centered(expected=None):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            value=editor().locator('#image').evaluate('''c=>{
                const v=c.closest('.viewport'),p=JSON.parse(c.dataset.centerPoint||'null'),r=c.getBoundingClientRect(),b=v.getBoundingClientRect();
                const o=document.getElementById('original-viewport'),s=r.width/c.width;
                return {active:c.dataset.activeFiberId,centered:c.dataset.centeredFiberId,p,
                    dx:p?r.x+p.x*s-(b.x+v.clientLeft+v.clientWidth/2):null,
                    dy:p?r.y+p.y*s-(b.y+v.clientTop+v.clientHeight/2):null,
                    aligned:v.scrollLeft===o.scrollLeft&&v.scrollTop===o.scrollTop,
                    zoom:document.getElementById('zoom').value,
                    mask:p?assets.labels[Math.floor(p.y)*c.width+Math.floor(p.x)]:null};
            }''')
            if (value['active'] and value['active']==value['centered']
                    and (expected is None or value['active']==str(expected))
                    and abs(value['dx'])<2 and abs(value['dy'])<2 and value['aligned']):return value
            time.sleep(.1)
        raise AssertionError(('Fiber not centered',expected,value))
    def export(name):
        with page.expect_download() as result:
            page.get_by_role('button',name='Download per-fiber CSV',exact=True).click()
        result.value.save_as(output/name)
        with (output/name).open(newline='',encoding='utf-8') as file:return list(csv.DictReader(file))
    def states():
        with closing(sqlite3.connect(root/'data/feedback.sqlite3')) as con:
            return con.execute('SELECT fiber_id,manual_classification FROM objects ORDER BY fiber_id').fetchall()
    def select(fid):
        panel=page.get_by_text('Review by object ID and save confirmations',exact=True)
        if not page.get_by_role('combobox',name='Object ID',exact=True).is_visible():panel.click();idle()
        control=page.get_by_role('combobox',name='Object ID',exact=True);control.scroll_into_view_if_needed()
        control.evaluate('''async e=>{
            await Promise.all(e.closest('details').getAnimations({subtree:true})
                .filter(a=>a.effect.getComputedTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));
            await new Promise(resolve=>{let last='',n=0;function tick(){const r=e.getBoundingClientRect(),p=[r.x,r.y].join(',');
                n=p===last?n+1:0;last=p;if(n>=4)resolve();else requestAnimationFrame(tick);}requestAnimationFrame(tick);});
        }''')
        control.locator('..').get_by_role('button',name='Open',exact=True).click()
        page.get_by_role('option',name=str(fid),exact=True).click();saved();centered(fid)
    def shortcut(key):
        editor().locator('#image').evaluate('c=>c.focus({preventScroll:true})');page.keyboard.press(key);saved()
    def scroll():return editor().locator('#viewport').evaluate('v=>[v.scrollLeft,v.scrollTop]')
    def reload_editor(orphaned_load=False):
        # Recreate the viewer document and replay the host's last render. This
        # deterministically exercises the refresh seen on the Windows runner.
        captured=editor().locator('#image').evaluate('()=>args')
        if orphaned_load:
            editor().locator('#image').evaluate('''()=>{
                const key=(args.client_key||'baseline')+':'+args.asset_key;
                shared.assets.set(key,{promise:new Promise(()=>{}),owner:document});
            }''')
        epoch=editor().locator('#image').evaluate('()=>performance.timeOrigin')
        page.locator('iframe[title*="fiber_editor"]').evaluate('f=>f.contentWindow.location.reload()')
        page.wait_for_function('''old=>{
            const w=document.querySelector('iframe[title*="fiber_editor"]')?.contentWindow;
            return w&&w.performance.timeOrigin!==old&&w.document.getElementById('image')?.dataset.editorReady!==undefined;
        }''',arg=epoch)
        editor().locator('#image').evaluate('''(c,data)=>window.dispatchEvent(new MessageEvent('message',{
            data:{type:'streamlit:render',args:data},source:window.parent,origin:window.parent.location.origin}))''',captured)
        saved()
    open_image();centered(1)
    before=export('before.csv');source=editor().locator('#original-image').evaluate('c=>c.toDataURL()')
    assert len(before)==6 and states()==[(i,None) for i in range(1,7)]
    measurements=[]
    for zoom in ('50','100','150','400','800'):
        editor().locator('#zoom').fill(zoom)
        editor().locator('#center').click()
        for _ in range(6):
            value=centered();assert value['zoom']==zoom
            assert value['mask']==int(value['active'])
            measurements.append(value)
            editor().locator('#next').click();saved()
            if zoom=='50':
                reload_editor();centered()
    print('All six targets centered at 50/100/150/400/800%, including all image edges',flush=True)
    select(3);curved=centered(3)
    assert curved['p']['source']=='centerline_midpoint' and curved['p']['x']>325 and curved['p']['y']<260
    select(4);assert centered(4)['p']['source']=='mask_representative'
    shortcut('n');centered(5);shortcut('p');centered(4)
    select(3);editor().locator('#zoom').fill('150');shortcut('c');centered(3)
    editor().locator('#viewport').evaluate('v=>{v.scrollLeft=0;v.scrollTop=0;}')
    editor().locator('#viewport').evaluate('async()=>{await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));}')
    manual=scroll()
    reload_editor()
    assert scroll()==manual,'Iframe restoration recentered a manually panned view'
    editor().locator('#showIds').uncheck();editor().locator('#showIds').check()
    export('navigation-only.csv');saved()
    assert scroll()==manual,'Unrelated rerender recentered a manually panned view'
    shortcut('c');centered(3)
    reload_editor(orphaned_load=True);centered(3)
    for width in (800,1500):
        page.set_viewport_size(dict(width=width,height=1100));editor().locator('#center').click();centered(3)
    select(1);editor().locator('#viewMode').select_option('hide_auto');saved()
    filtered=centered();assert filtered['active']!=str(1)
    editor().locator('#viewMode').select_option('all');saved();centered()
    assert states()==[(i,None) for i in range(1,7)] and export('selection-only.csv')==before
    select(3)
    for button in ('#accept','#reject'):
        old=active();editor().locator(button).click();saved();assert active()!=old;centered()
    # Buttons below the main viewer share the same selected object and centering.
    page.get_by_role('button',name='Mark accepted',exact=True).click();saved();centered()
    # Exact mouse clicks at the now-centered object retain original-coordinate hit testing.
    for button in ('left','right'):
        value=centered();old=value['active']
        canvas=editor().locator('#image');editor().locator('#viewport').scroll_into_view_if_needed()
        box=canvas.bounding_box()
        page.mouse.click(box['x']+value['p']['x']*box['width']/1280,
                         box['y']+value['p']['y']*box['height']/800,button=button);saved()
        if active():assert active()!=old;centered()
    editor().locator('#undo').click();saved();centered()
    previous=states()
    editor().locator('#image').evaluate("()=>{args.context='deliberately-stale-centering-test';}")
    failed=active();editor().locator('#accept').click();saved();centered(failed)
    assert states()==previous
    page.get_by_text('This click belongs to an older image or review. Click again on the current image.',exact=True).wait_for()
    # Finish every remaining target, including immediately queued decisions.
    while active():
        centered();shortcut('a')
    expect(editor().locator('#center')).to_be_disabled()
    expect(editor().locator('#outline-preview')).to_have_attribute('data-active-fiber-id','')
    final=export('after.csv')
    for old,new in zip(before,final):
        for field in ('fiber_id','length_um','mean_diameter_um','diameter_sd_um','calibration_um_per_px'):
            assert new[field]==old[field],(field,old,new)
    assert editor().locator('#original-image').evaluate('c=>c.toDataURL()')==source
    completed_render=editor().locator('#image').evaluate('()=>args')
    page.evaluate('''()=>{
        window.__historyCursorActions=[];
        window.addEventListener('message',e=>{
            if(e.data?.type==='streamlit:setComponentValue' && ['undo','select'].includes(e.data.value?.action))
                window.__historyCursorActions.push(e.data.value.action);
        });
    }''')
    editor().locator('#undo').click();saved();expected=active();centered(expected)
    assert page.evaluate('window.__historyCursorActions')==['undo'], 'History acknowledgement echoed a stale selection'
    confirmed=editor().locator('#image').evaluate('c=>c.toDataURL()')
    confirmed_history=states()
    # A delayed completed-queue render must not erase Undo's active target or
    # put older green/red decisions back on screen, including across iframes.
    editor().locator('#image').evaluate('''(c,data)=>window.dispatchEvent(new MessageEvent('message',{
        data:{type:'streamlit:render',args:data},source:window.parent,origin:window.parent.location.origin}))''',completed_render)
    centered(expected)
    assert editor().locator('#image').evaluate('c=>c.toDataURL()')==confirmed
    assert states()==confirmed_history
    page.screenshot(path=str(output/'centered-active-fiber.png'),full_page=True)
    page.close();page=checks.new_analysis_page(browser,network_latency_ms)
    page.on('pageerror',lambda error:errors.append(str(error)))
    open_image();centered(expected)
    assert not errors,errors
    (output/'centering-report.json').write_text(json.dumps(dict(
        all_edges_and_zoom_levels=True,curved_centerline_midpoint=True,ambiguous_mask_fallback=True,
        next_previous_keyboard=True,object_id_jump=True,accept_reject_and_mouse=True,
        manual_navigation_preserved=True,explicit_center_button_and_shortcut=True,
        responsive_layout=True,filter_changes=True,failed_save_restores_center=True,
        measurements_unchanged=True,original_unchanged=True,reopened_review=True,
        iframe_refresh_restores_center_trace=True,iframe_refresh_preserves_manual_pan=True,
        orphaned_image_load_recovers=True,
        stale_render_cannot_overwrite_undo_or_colors=True,
        network_latency_ms=network_latency_ms,positions=measurements,browser_errors=errors),indent=2))
    print('Centering browser passed: decisions, mouse/keyboard, dropdown, filters, manual pan, failures, exports and reopening',flush=True)


if __name__=='__main__':
    try:
        checks.synthetic_png=synthetic;checks.interactions=interactions;checks.main()
    except Exception:
        if os.environ.get('GITHUB_ACTIONS')=='true':
            detail=traceback.format_exc()[-5000:].replace('%','%25').replace('\r','%0D').replace('\n','%0A')
            print('::error title=Centering browser failure::'+detail,flush=True)
        raise

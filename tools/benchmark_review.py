"""Controlled browser benchmark; generated data only, no timing assertions."""
import json
import statistics
import struct
import platform
import zlib

import check_feedback_browser as module

def fixture(path):
    width,height=4096,3072
    rows=[bytearray([80]*width) for _ in range(height)]
    for row in range(20):
        for column in range(20):
            x,y=40+column*200,40+row*145
            for yy in range(y,y+10):rows[yy][x:x+140]=bytes([200])*140
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',width,height,8,0,0,0,0))
                     +chunk(b'IDAT',zlib.compress(b''.join(b'\0'+r for r in rows)))+chunk(b'IEND',b''))

def interactions(browser,url,root,output):
    page=browser.new_page(viewport={'width':1500,'height':1100})
    page.goto(url)
    page.get_by_text('Review ImageJ Excel workbook',exact=True).wait_for()
    page.locator('input[type=file]').first.set_input_files(str(root/'synthetic.png'))
    page.get_by_text('Confirm exclusion rectangle',exact=True).wait_for(timeout=30000)
    calibration=page.get_by_role('spinbutton',name='Calibration (µm/pixel)',exact=True)
    calibration.fill('.02');calibration.press('Tab')
    page.get_by_text('Bright fibers on dark background',exact=True).click()
    page.get_by_role('button',name='Analyze image',exact=True).click()
    frame=page.frame_locator('iframe[title*="fiber_editor"]')
    canvas=frame.locator('#image');canvas.wait_for(state='visible',timeout=120000)
    frame.locator('#toggle').wait_for(state='visible',timeout=120000)
    if frame.locator('#clickMode').count():frame.locator('#clickMode').select_option('toggle')
    # Wait for actual image commit, rather than the empty canvas element.
    canvas.evaluate('''async c=>{while(c.width!==4096 || document.getElementById('toggle').disabled)
      await new Promise(r=>setTimeout(r,50));}''')
    assert len(json.loads(canvas.get_attribute('data-fiber-ids')))==400
    durations=[];acknowledgments=[]
    for index in range(8):
        canvas.scroll_into_view_if_needed()
        canvas.evaluate('''c=>{const v=c.parentElement,s=c.getBoundingClientRect().width/c.width;
          v.scrollLeft=0;v.scrollTop=0;}''')
        # Instrument input-to-next-paint where the requested pixel actually changes.
        page.evaluate('''()=>{
          const d=document.querySelector('iframe[title*="fiber_editor"]').contentDocument;
          const c=d.getElementById('image'),before=c.getContext('2d').getImageData(80,45,1,1).data;
          const green=before[1]>before[0];window.benchResult={visual:null,ack:null};
          c.addEventListener('pointerup',()=>{
            const start=performance.now();
            function check(){
              const d=document.querySelector('iframe[title*="fiber_editor"]')?.contentDocument;
              const c=d?.getElementById('image');
              if(c?.width===4096){const p=c.getContext('2d').getImageData(80,45,1,1).data;
                if((p[1]>p[0])!==green && window.benchResult.visual===null)
                  requestAnimationFrame(()=>{if(window.benchResult.visual===null)window.benchResult.visual=performance.now()-start;});
                if(window.benchResult.visual!==null && !d.getElementById('toggle').disabled && Number(c.dataset.pendingCount||0)===0)
                  {window.benchResult.ack=performance.now()-start;return;}}
              requestAnimationFrame(check);}
            requestAnimationFrame(check);
          },{once:true,capture:true});
        }''')
        box=canvas.bounding_box()
        page.mouse.click(box['x']+80*box['width']/4096,box['y']+45*box['height']/3072)
        page.wait_for_function('window.benchResult.ack!==null',timeout=90000)
        result=page.evaluate('window.benchResult')
        assert result['visual'] is not None and result['ack'] is not None,result
        durations.append(result['visual']);acknowledgments.append(result['ack'])
        print(index,result,flush=True)
    def stats(values):
        ordered=sorted(values)
        return {'median_ms':statistics.median(values),'p95_ms':ordered[-1], 'samples_ms':values}
    report={'resolution':[4096,3072],'objects':400,'samples':len(durations),
            'metric':'pointerup to next animation frame after changed object pixel',
            'visual':stats(durations),'server_ack':stats(acknowledgments),
            'browser':browser.version,'platform':platform.platform(),
            'note':'Controlled local benchmark; no latency guarantee for other hardware or scientific accuracy claim.'}
    (output/'latency.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':
    module.synthetic_png=fixture
    module.interactions=interactions
    module.main()

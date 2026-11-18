'use strict';
// Geometry layers are immutable. Review messages contain decisions and small ID
// sprites; the clicked mask is recolored locally before waiting for persistence.
const $=id=>document.getElementById(id);
const canvas=$('image'),ctx=canvas.getContext('2d'),viewport=$('viewport');
const originalCanvas=$('original-image'),originalCtx=originalCanvas.getContext('2d');
const originalViewport=$('original-viewport'),preview=$('outline-preview'),previewCtx=preview.getContext('2d');
const zoom=$('zoom');
let args=null,state=null,assets=null,committedKey=null,objects=new Map(),origin='*',generation=0,ready=false,fit=1,down=null,lastHeight=0;
// Streamlit may replace an iframe during reruns. Keep the queue and decoded
// layers in this browser tab so replacement cannot lose queued review events.
const shared=window.parent.__collagenReview || (window.parent.__collagenReview={states:new Map(),assets:new Map()});
function post(type,extra){window.parent.postMessage(Object.assign({isStreamlitMessage:true,type},extra||{}),origin);}
function height(){const h=document.body.scrollHeight+6;if(h!==lastHeight){lastHeight=h;post('streamlit:setFrameHeight',{height:h});}}
function pending(){return state ? (state.inflight?[state.inflight]:[]).concat(state.queue) : [];}
function barrier(){return pending().some(e=>!['accept','reject'].includes(e.action));}
function editable(){return ready && args && args.review_enabled!==false && !barrier();}
function status(text){$('status').textContent=text;height();}
function controls(){
  const disabled=!ready || args?.review_enabled===false;
  for(const id of ['toggle','outline','clear','accept','reject'])$(id).disabled=disabled || barrier();
  for(const id of ['accept','reject'])$(id).disabled=disabled || barrier() || !visible(objects.get(state?.selected));
  const target=visible(objects.get(state?.selected))?'fiber '+state.selected:'selected';
  $('accept').textContent=`Accept ${target} (A)`;$('reject').textContent=`Reject ${target} (R)`;
  const selected=objects.get(state?.selected);
  $('resolve').hidden=!(visible(selected) && selected.manual==='accepted' && !selected.accepted && selected.can_outline);
  $('resolve').disabled=disabled || barrier() || state?.mode!=='review';
  for(const id of ['undo','redo'])$(id).disabled=disabled || barrier();
  $('next').disabled=disabled;
  for(const id of ['clickMode','viewMode','shortcuts','showIds','zoom'])$(id).disabled=!ready;
  $('clickMode').disabled=disabled;
  $('save').disabled=disabled || pending().length>0 || state?.mode!=='outline' || state.points.length<3;
  canvas.dataset.pendingCount=String(pending().length);
  canvas.dataset.editorReady=String(ready);
  $('toggle').setAttribute('aria-pressed',String(state?.mode==='review'));
  $('outline').setAttribute('aria-pressed',String(state?.mode==='outline'));
}
function visible(obj){return obj && (state.viewMode==='all' ||
  (state.viewMode==='hide_auto' ? obj.accepted || obj.manual!==null : obj.needs_review));}
function projected(){
  const result=new Map((args.objects||[]).map(o=>[o.fiber_id,Object.assign({},o)]));
  for(const event of pending()){
    const obj=result.get(event.fiber_id);
    if(obj && ['accept','reject'].includes(event.action)){
      obj.manual=event.action==='accept'?'accepted':'rejected';
      obj.accepted=event.action==='accept' && obj.can_accept;
      obj.category=obj.accepted?'accepted':event.action==='reject'?'rejected':obj.automatic_category;
      obj.reason=obj.accepted?'':event.action==='reject'?'manual_rejection':obj.automatic_reason;
      obj.needs_review=event.action==='accept' && !obj.can_accept;
    }
  }
  return result;
}
function activeLayouts(){return Array.from(state.layouts.values()).filter(item=>{
  const obj=objects.get(item.fiber_id);return obj?.accepted && obj.measurable && visible(obj);
}).sort((a,b)=>a.fiber_id-b.fiber_id);}
function updateMetadata(){
  const labels=activeLayouts();
  canvas.dataset.fiberIds=JSON.stringify(labels.map(x=>x.fiber_id));
  canvas.dataset.idLabels=JSON.stringify(labels);
  canvas.dataset.idsVisible=String(state.showIds);
  canvas.dataset.reviewContext=args.context||'';
  canvas.dataset.sourceImage=JSON.stringify(args.source_image||{});
  const rows=Array.from(objects.values());
  const confirmed=new Map((args.objects||[]).map(o=>[o.fiber_id,o]));
  const reviewed=(args.review_progress?.reviewed||0)+rows.filter(o=>o.manual!==null && confirmed.get(o.fiber_id)?.manual===null).length;
  const count=rows.filter(o=>o.accepted && o.measurable).length;
  const saved=pending().length===0;
  $('review-progress').textContent=`Reviewed ${reviewed} / ${args.review_progress?.total||rows.length} detections (${rows.filter(visible).length} shown) · Selected measurable fibers: ${count} · ${saved?'All corrections saved':`${pending().length} saving — colors/counts are provisional`}`;
  canvas.dataset.reviewedCount=String(reviewed);
  const selected=objects.get(state.selected);
  const blocked=selected?.manual==='accepted' && !selected.accepted;
  const decision=blocked?'human label accepted; NOT counted':selected?.category.replaceAll('_',' ');
  const reason=blocked?selected.accept_block_message||selected.reason:selected?.reason;
  $('selected').textContent=selected?`Fiber ${selected.fiber_id}: ${decision}${reason?' · '+reason:''}`:'Select a colored object. A: accept · R: reject · N: next unreviewed · Ctrl+Z / Ctrl+Y: undo / redo.';
  points();controls();height();
}
function intersects(a,b){return a[0]<b[2] && b[0]<a[2] && a[1]<b[3] && b[1]<a[3];}
function paintRect(rect){
  if(!assets)return;
  const x=Math.max(0,Math.floor(rect[0])),y=Math.max(0,Math.floor(rect[1]));
  const width=Math.min(assets.width,Math.ceil(rect[2]))-x,height=Math.min(assets.height,Math.ceil(rect[3]))-y;
  if(width<=0 || height<=0)return;
  const data=assets.baseCtx.getImageData(x,y,width,height),pixels=data.data;
  for(let row=0;row<height;row++)for(let col=0;col<width;col++){
    const id=assets.labels[(row+y)*assets.width+x+col];
    if(!id)continue;
    const obj=objects.get(id);if(!visible(obj))continue;
    const p=(row*width+col)*4;
    pixels[p]=Math.round(.4*pixels[p]+(obj.accepted?0:153));
    pixels[p+1]=Math.round(.4*pixels[p+1]+(obj.accepted?153:0));
    pixels[p+2]=Math.round(.4*pixels[p+2]);
  }
  ctx.putImageData(data,x,y);
  if(state.showIds){
    ctx.save();ctx.beginPath();ctx.rect(x,y,width,height);ctx.clip();
    const items=activeLayouts().map(item=>assets.sprites.get(item.fiber_id)).filter(Boolean)
      .filter(s=>intersects([x,y,x+width,y+height],[s.x,s.y,s.x+s.leader.width,s.y+s.leader.height]));
    // Same order as the exported OpenCV layer: all leaders, then all glyphs.
    for(const s of items)ctx.drawImage(s.leader,s.x,s.y);
    for(const s of items)ctx.drawImage(s.text,s.x,s.y);
    ctx.restore();
  }
}
function paintObjects(ids){
  for(const id of ids){
    const bounds=assets.bounds.get(id);if(bounds)paintRect(bounds);
    const s=assets.sprites.get(id);if(s)paintRect([s.x,s.y,s.x+s.leader.width,s.y+s.leader.height]);
  }
}
function reconcile(previous){
  objects=projected();
  const changed=new Set();
  for(const [id,obj] of objects){const old=previous.get(id);
    if(!old || old.accepted!==obj.accepted || old.measurable!==obj.measurable ||
       old.manual!==obj.manual || old.needs_review!==obj.needs_review ||
       // Confirmation must repaint even if the optimistic decision already
       // matches: a missed provisional draw must never become the saved view.
       old.saved_event!==obj.saved_event)changed.add(id);}
  paintObjects(changed);updateMetadata();
}
function points(){
  if(!assets)return;
  previewCtx.clearRect(0,0,preview.width,preview.height);
  const scale=fit*Number(zoom.value)/100;
  if(state.mode==='review' && visible(objects.get(state.selected))){
    const b=assets.bounds.get(state.selected),pad=3/scale;
    if(b){
      previewCtx.save();previewCtx.lineWidth=3/scale;previewCtx.strokeStyle='#000';
      previewCtx.strokeRect(b[0]-pad,b[1]-pad,b[2]-b[0]+2*pad,b[3]-b[1]+2*pad);
      previewCtx.lineWidth=1/scale;previewCtx.strokeStyle='#fff';previewCtx.setLineDash([4/scale,4/scale]);
      previewCtx.strokeRect(b[0]-pad,b[1]-pad,b[2]-b[0]+2*pad,b[3]-b[1]+2*pad);previewCtx.restore();
    }
  }
  if(state.mode!=='outline' || !state.points.length)return;
  previewCtx.lineWidth=2/scale;previewCtx.strokeStyle='#ffd43b';previewCtx.fillStyle='#ffd43b';previewCtx.beginPath();
  state.points.forEach((p,i)=>i?previewCtx.lineTo(...p):previewCtx.moveTo(...p));previewCtx.stroke();
  for(const p of state.points){previewCtx.beginPath();previewCtx.arc(...p,3/scale,0,2*Math.PI);previewCtx.fill();}
}
function syncScroll(source,target){
  if(Math.abs(source.scrollLeft-target.scrollLeft)>.5)target.scrollLeft=source.scrollLeft;
  if(Math.abs(source.scrollTop-target.scrollTop)>.5)target.scrollTop=source.scrollTop;
}
function resize(){
  if(!assets)return;
  const oldScale=parseFloat(canvas.style.width)/canvas.width || fit;
  const cx=(viewport.scrollLeft+viewport.clientWidth/2)/oldScale,cy=(viewport.scrollTop+viewport.clientHeight/2)/oldScale;
  fit=Math.min(1,Math.max(1,Math.min(viewport.clientWidth,originalViewport.clientWidth)-2)/assets.width);
  const scale=fit*Number(zoom.value)/100;
  for(const c of [canvas,originalCanvas,preview]){c.style.width=assets.width*scale+'px';c.style.height=assets.height*scale+'px';}
  for(const v of [viewport,originalViewport])v.style.height=Math.min(600,assets.height*fit+2)+'px';
  viewport.scrollLeft=Math.max(0,cx*scale-viewport.clientWidth/2);viewport.scrollTop=Math.max(0,cy*scale-viewport.clientHeight/2);
  syncScroll(viewport,originalViewport);$('zoomLabel').textContent=zoom.value+'%';points();height();
}
viewport.onscroll=()=>{syncScroll(viewport,originalViewport);if(state){state.scroll=[viewport.scrollLeft,viewport.scrollTop];}};
originalViewport.onscroll=()=>syncScroll(originalViewport,viewport);
function pump(){
  if(!ready || state.inflight || !state.queue.length)return;
  state.inflight=Object.assign(state.queue.shift(),{context:args.context,asset_key:args.asset_key,
    known_sprites:Array.from(assets.sprites.values()).map(s=>s.key)});
  updateMetadata();
  post('streamlit:setComponentValue',{value:state.inflight,dataType:'json'});
}
function enqueue(action,extra={}){
  if(!ready || args.review_enabled===false || barrier())return;
  if(pending().length>=128){status('Wait for queued corrections to finish saving before adding more.');return;}
  const previous=objects;
  const token=window.crypto.randomUUID?window.crypto.randomUUID():Date.now()+'-'+Math.random();
  state.queue.push(Object.assign({action,token},extra));
  reconcile(previous);status('Saving corrections… Keep this tab open until all corrections are saved.');pump();
}
function classify(id,action,extra={}){
  if(!editable() || !visible(objects.get(id)))return;
  state.selected=id;enqueue(action,Object.assign({fiber_id:id},extra));
}
function focusSelected(){
  const bounds=assets.bounds.get(state.selected),scale=fit*Number(zoom.value)/100;
  if(!bounds)return;
  viewport.scrollLeft=Math.max(0,(bounds[0]+bounds[2])/2*scale-viewport.clientWidth/2);
  viewport.scrollTop=Math.max(0,(bounds[1]+bounds[3])/2*scale-viewport.clientHeight/2);
  syncScroll(viewport,originalViewport);
}
function selectNext(direction=1){
  const candidates=Array.from(objects.values()).filter(o=>visible(o) && o.manual===null).map(o=>o.fiber_id).sort((a,b)=>a-b);
  const ids=candidates.length?candidates:Array.from(objects.values()).filter(visible).map(o=>o.fiber_id).sort((a,b)=>a-b);
  if(!ids.length){status('No objects in the current view.');return;}
  const index=ids.indexOf(state.selected);
  state.selected=ids[index<0?(direction>0?0:ids.length-1):(index+direction+ids.length)%ids.length];
  focusSelected();updateMetadata();canvas.focus({preventScroll:true});
}
function setMode(mode){if(!editable())return;state.mode=mode;state.resolving=false;points();controls();status(mode==='outline'?'Trace one separated boundary, then Save outline. Backspace removes a vertex.':'Left accept / right reject, or choose Toggle mode.');}
$('toggle').onclick=()=>setMode('review');$('outline').onclick=()=>setMode('outline');
$('clear').onclick=()=>{state.points=[];points();controls();};
$('save').onclick=()=>enqueue('outline',{points:state.points.map(p=>p.slice())});
$('accept').onclick=()=>classify(state.selected,'accept');$('reject').onclick=()=>classify(state.selected,'reject');
$('resolve').onclick=()=>{
  if(!editable() || !objects.get(state.selected)?.can_outline)return;
  focusSelected();setMode('outline');state.resolving=true;canvas.focus({preventScroll:true});
  status('Trace the actual boundary of one separated fiber, then Save outline. Its measurements will use the existing method; the original detection is retained.');
};
$('next').onclick=()=>selectNext();$('undo').onclick=()=>enqueue('undo');$('redo').onclick=()=>enqueue('redo');
$('clickMode').onchange=e=>{state.clickMode=e.target.value;};
$('shortcuts').onchange=e=>{state.shortcuts=e.target.checked;};
$('viewMode').onchange=e=>{state.viewMode=e.target.value;paintRect([0,0,canvas.width,canvas.height]);updateMetadata();};
$('showIds').onchange=e=>{state.showIds=e.target.checked;paintRect([0,0,canvas.width,canvas.height]);updateMetadata();};
zoom.oninput=()=>{state.zoom=zoom.value;resize();};
canvas.oncontextmenu=e=>{if(state?.mode==='review')e.preventDefault();};
canvas.onpointerdown=e=>{down=editable()?{x:e.clientX,y:e.clientY,button:e.button,assetKey:committedKey}:null;};
canvas.onpointerup=e=>{
  if(!editable() || !down || down.assetKey!==committedKey || Math.hypot(e.clientX-down.x,e.clientY-down.y)>5)return;
  const button=down.button;down=null;
  const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)*canvas.width/r.width,y=(e.clientY-r.top)*canvas.height/r.height;
  if(x<0 || y<0 || x>=canvas.width || y>=canvas.height)return;
  canvas.focus({preventScroll:true});
  if(state.mode==='outline'){
    if(button!==0)return;
    if(state.points.length>=512){status('Maximum 512 vertices; save or clear this outline.');return;}
    state.points.push([x,y]);points();controls();return;
  }
  const id=assets.labels[Math.floor(y)*assets.width+Math.floor(x)],obj=objects.get(id);
  if(!visible(obj)){
    state.selected=null;updateMetadata();
    status('No visible detected object at that pixel. Selection cleared; background never selects a neighbor.');return;
  }
  const action=button===2?'reject':state.clickMode==='toggle' && obj.accepted?'reject':'accept';
  if(button===0 || button===2)classify(id,action,{x,y});
};
canvas.onkeydown=e=>{
  if(!state?.shortcuts || !ready)return;
  const key=e.key.toLowerCase();
  if((e.ctrlKey||e.metaKey) && ['z','y'].includes(key)){
    e.preventDefault();enqueue(key==='y'||e.shiftKey?'redo':'undo');return;
  }
  if(e.ctrlKey||e.metaKey||e.altKey)return;
  if(key==='backspace' && state.mode==='outline' && editable()){e.preventDefault();state.points.pop();points();controls();}
  if(key==='a'){e.preventDefault();classify(state.selected,'accept');}
  if(key==='r'){e.preventDefault();classify(state.selected,'reject');}
  if(['n','arrowright','p','arrowleft'].includes(key)){e.preventDefault();selectNext(['p','arrowleft'].includes(key)?-1:1);}
};
function loadImage(url){return new Promise((resolve,reject)=>{const image=new window.parent.Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('Image layer could not load.'));image.src=url;});}
async function loadAssets(data){
  const key=(data.client_key||'baseline')+':'+data.asset_key;
  if(shared.assets.has(key))return shared.assets.get(key);
  const promise=(async()=>{
    const [original,base,labelMap]=await Promise.all([data.original_image,data.base_image,data.label_map].map(loadImage));
    if(original.width!==base.width || original.height!==base.height || labelMap.width!==base.width || labelMap.height!==base.height)
      throw new Error('Original and analysis geometry dimensions do not match.');
    const width=base.width,height=base.height;
    // Cached surfaces must belong to the parent document: Chromium can clear
    // canvases owned by an iframe when Streamlit replaces that iframe.
    const baseCanvas=window.parent.document.createElement('canvas');baseCanvas.width=width;baseCanvas.height=height;
    const baseCtx=baseCanvas.getContext('2d',{willReadFrequently:true});baseCtx.drawImage(base,0,0);
    const mapCanvas=window.parent.document.createElement('canvas');mapCanvas.width=width;mapCanvas.height=height;
    const mapCtx=mapCanvas.getContext('2d',{willReadFrequently:true});mapCtx.drawImage(labelMap,0,0);
    const rgb=mapCtx.getImageData(0,0,width,height).data,labels=new Uint32Array(width*height),bounds=new Map();
    for(let i=0;i<labels.length;i++){
      const id=rgb[i*4]+rgb[i*4+1]*256+rgb[i*4+2]*65536;labels[i]=id;
      if(id){const x=i%width,y=Math.floor(i/width),box=bounds.get(id);
        if(box){box[0]=Math.min(box[0],x);box[1]=Math.min(box[1],y);box[2]=Math.max(box[2],x+1);box[3]=Math.max(box[3],y+1);}
        else bounds.set(id,[x,y,x+1,y+1]);}
    }
    mapCanvas.width=0;mapCanvas.height=0;
    return {original,baseCtx,width,height,labels,bounds,sprites:new Map()};
  })();
  shared.assets.set(key,promise);
  // Bound decoded image memory, retaining only the two most recent geometries.
  while(shared.assets.size>2)shared.assets.delete(shared.assets.keys().next().value);
  try{return await promise;}catch(error){shared.assets.delete(key);throw error;}
}
window.addEventListener('message',async event=>{
  if(event.source!==window.parent || event.data?.type!=='streamlit:render')return;
  origin=event.origin;const data=event.data.args,id=++generation;
  if(event.data.theme){const t=event.data.theme;for(const [key,value] of Object.entries({text:t.textColor,surface:t.secondaryBackgroundColor,background:t.backgroundColor}))document.documentElement.style.setProperty('--'+key,value);}
  const client=data.client_key||'baseline';
  if(!shared.states.has(client))shared.states.set(client,{queue:[],inflight:null,selected:null,layouts:new Map(),mode:'review',points:[],clickMode:'accept_reject',viewMode:'all',shortcuts:true,showIds:true,zoom:'100',scroll:[0,0],policy:data.policy_key});
  state=shared.states.get(client);
  let notice=data.message||'';
  if(state.inflight && data.ack_token===state.inflight.token){
    if(state.inflight.action==='outline' && data.event_ok){
      state.points=[];
      if(state.resolving){
        state.mode='review';state.resolving=false;
        if(Number.isInteger(data.ack_fiber_id))state.selected=data.ack_fiber_id;
      }
    }
    if(!data.event_ok){state.queue=[];notice='Save failed; provisional changes were restored to the confirmed state. '+notice;}
    state.inflight=null;
  }
  if(state.policy!==data.policy_key){
    if(pending().length)notice=`Selection settings changed; ${pending().length} pending previews were canceled. Check confirmed decisions in the saved history.`;
    state.queue=[];state.inflight=null;state.policy=data.policy_key;
  }
  args=data;
  if(committedKey!==data.asset_key){ready=false;controls();}
  const cacheKey=client+':'+data.asset_key;
  if(!shared.assets.has(cacheKey) && !data.base_image){
    ready=false;controls();status('Restoring cached image layers…');
    if(!state.assetRequest){
      state.assetRequest=window.crypto.randomUUID?window.crypto.randomUUID():Date.now()+'-assets';
      post('streamlit:setComponentValue',{value:{action:'request_assets',token:state.assetRequest,context:data.context},dataType:'json'});
    }
    return;
  }
  state.assetRequest=null;
  try{
    const next=await loadAssets(data);
    if(id!==generation)return;
    const spriteChanges=new Set(),oldRects=[];
    await Promise.all((data.id_sprites||[]).map(async sprite=>{
      const old=next.sprites.get(sprite.fiber_id);
      if(old?.key===sprite.sprite_key)return;
      const [leader,text]=await Promise.all([sprite.leader_image,sprite.text_image].map(loadImage));
      if(id!==generation)return;
      if(old)oldRects.push([old.x,old.y,old.x+old.leader.width,old.y+old.leader.height]);
      spriteChanges.add(sprite.fiber_id);
      next.sprites.set(sprite.fiber_id,{x:sprite.x,y:sprite.y,leader,text,key:sprite.sprite_key});
    }));
    if(id!==generation)return;
    // Commit geometry only after every awaited layer is ready. Another render
    // can supersede this one while sprites decode, even at identical dimensions.
    const changed=assets!==next,previous=objects;
    assets=next;committedKey=data.asset_key;
    if(changed){
      for(const c of [canvas,originalCanvas,preview]){c.width=assets.width;c.height=assets.height;}
      originalCtx.drawImage(assets.original,0,0);state.layouts=new Map();
    }
    for(const item of data.fiber_id_labels||[])state.layouts.set(item.fiber_id,item);
    $('clickMode').value=state.clickMode;$('viewMode').value=state.viewMode;$('shortcuts').checked=state.shortcuts;
    $('showIds').checked=state.showIds;zoom.value=state.zoom;
    $('original-note').textContent=data.original_note||'';$('original-note').hidden=!data.original_note;
    ready=true;
    if(changed){objects=projected();paintRect([0,0,canvas.width,canvas.height]);updateMetadata();resize();
      viewport.scrollLeft=state.scroll[0];viewport.scrollTop=state.scroll[1];syncScroll(viewport,originalViewport);
    }else{
      reconcile(previous);
      for(const rect of oldRects)paintRect(rect);
      paintObjects(spriteChanges);
    }
    status(pending().length?'Saving corrections… Keep this tab open until all corrections are saved.':notice||'All corrections saved. Left accept / right reject. A/R/N and Undo/Redo work when the image has keyboard focus.');
    pump();
  }catch(error){if(id!==generation)return;ready=false;controls();status(error.message+' Review is disabled; refresh before editing.');}
});
if(!shared.unloadHandler){
  window.parent.addEventListener('beforeunload',event=>{
    if(Array.from(shared.states.values()).some(s=>s.inflight || s.queue.length)){event.preventDefault();event.returnValue='';}
  });shared.unloadHandler=true;
}
window.addEventListener('resize',resize);
controls();post('streamlit:componentReady',{apiVersion:1});height();

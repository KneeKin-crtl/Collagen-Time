"""Offline Streamlit component; browser sends original-pixel coordinates only."""
import base64
from io import BytesIO

import cv2
import numpy as np
from PIL import Image
import streamlit as st
import streamlit.components.v1 as components

from .resources import resource_root
from .quality import approval_block_details, review_approved_candidates

editor_path = resource_root() / 'assets' / 'fiber_editor'
fiber_editor = components.declare_component('fiber_editor', path=str(editor_path))


@st.cache_data(show_spinner=False, max_entries=2)
def image_url(rgb):
    output = BytesIO()
    Image.fromarray(rgb).save(output, format='PNG', compress_level=1)
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


@st.cache_data(show_spinner=False, max_entries=2)
def original_preview(image):
    """Encode decoded source pixels losslessly when PNG supports their dtype.

    Color-channel order changes only for OpenCV encoding. No normalization,
    annotations, resizing, or changes to the source array are applied to uint8
    or uint16 images. Other TIFF types require an explicitly labeled preview.
    """
    array = np.asarray(image)
    note = ''
    if array.dtype.kind == 'u' and array.dtype.itemsize in (1, 2):
        pixels = array.astype(np.uint8 if array.dtype.itemsize == 1 else np.uint16, copy=True)
    else:
        from .visualization import display_rgb
        pixels = display_rgb(array)
        note = ('Browser display conversion for this TIFF pixel type; original pixel '
                'values and resolution are preserved in analysis and downloads.')
    if pixels.ndim == 3:
        pixels = cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGRA if pixels.shape[2] == 4 else cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode('.png', pixels, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not success:
        raise ValueError('Could not encode the original image preview.')
    return 'data:image/png;base64,' + base64.b64encode(encoded).decode('ascii'), note


def editor_assets(result):
    """Immutable display/identity layers; encode once per geometry, not per click."""
    from .visualization import display_rgb
    labels = result.labels
    if int(labels.max(initial=0)) >= 2**24:
        raise ValueError('Interactive review supports fewer than 16,777,216 object IDs per image.')
    encoded = np.empty(labels.shape + (3,), np.uint8)
    for channel in range(3):
        encoded[...,channel] = (labels >> (channel * 8)) & 255
    original, note = original_preview(result.original)
    return dict(original_image=original, original_note=note,
                base_image=image_url(display_rgb(result.original)), label_map=image_url(encoded),
                focus_points=review_focus_points(result))


def centerline_midpoint(points):
    """Display-only weighted midpoint of an existing connected open skeleton.

    Use the measurement graph's corner rule without rerunning segmentation,
    skeletonization or measurements. Branched/cyclic/disconnected paths fall back
    to an actual mask pixel in review_focus_points.
    """
    pixels=set(map(tuple,points))
    if len(pixels)<2:return None
    graph={}
    for y,x in pixels:
        edges=[]
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                q=(y+dy,x+dx)
                if (dy==dx==0) or q not in pixels:continue
                if dy and dx and ((y,x+dx) in pixels or (y+dy,x) in pixels):continue
                edges.append(q)
        if len(edges)>2:return None
        graph[(y,x)]=edges
    ends=sorted(p for p,edges in graph.items() if len(edges)==1)
    if len(ends)!=2:return None
    path=[ends[0]];previous=None
    while True:
        choices=[q for q in graph[path[-1]] if q!=previous]
        if not choices:break
        previous=path[-1];path.append(choices[0])
        if len(path)>len(pixels):return None
    if len(path)!=len(pixels):return None
    path=np.asarray(path,float)
    steps=np.linalg.norm(np.diff(path,axis=0),axis=1)
    cumulative=np.r_[0.,np.cumsum(steps)];half=cumulative[-1]/2
    edge=min(int(np.searchsorted(cumulative,half,side='right'))-1,len(steps)-1)
    y,x=path[edge]+(path[edge+1]-path[edge])*(half-cumulative[edge])/steps[edge]
    return [float(x+.5),float(y+.5)]


def review_focus_points(result):
    """Immutable original-pixel navigation anchors, never ID-text positions."""
    from scipy.ndimage import find_objects
    anchors=[];lines=getattr(result,'centerlines',{})
    for fid,box in enumerate(find_objects(result.labels),1):
        if box is None:continue
        points=lines.get(fid)
        anchor=centerline_midpoint(points) if points is not None else None
        source='centerline_midpoint'
        if anchor is None:
            points=np.argwhere(result.labels[box]==fid)
            point=points[np.argmin(np.sum((points-points.mean(axis=0))**2,axis=1))]
            anchor=[float(point[1]+box[1].start+.5),float(point[0]+box[0].start+.5)]
            source='mask_representative'
        anchors.append(dict(fiber_id=fid,x=anchor[0],y=anchor[1],source=source))
    return anchors


def label_sprite(labels, item):
    """Small native-pixel ID layers, preserving the existing OpenCV glyphs."""
    from .id_labels import FONT
    x0,y0,x1,y1 = item['box_xyxy']
    ax,ay = item['anchor_xy']
    left,top = max(0,min(x0,ax)-2),max(0,min(y0,ay)-2)
    right,bottom = min(labels.shape[1],max(x1,ax+1)+2),min(labels.shape[0],max(y1,ay+1)+2)
    leaders = np.zeros((bottom-top,right-left,4), np.uint8)
    text = leaders.copy()
    shift = np.asarray((left,top))
    start,anchor = np.asarray(item['leader_start_xy'])-shift,np.asarray(item['anchor_xy'])-shift
    cv2.line(leaders,tuple(start),tuple(anchor),(0,0,0,255),1,cv2.LINE_AA)
    leaders[labels[top:bottom,left:right] != 0] = 0
    cv2.putText(text,item['text'],tuple(np.asarray(item['text_xy'])-shift),FONT,
                item['font_scale'],(0,0,0,255),item['font_thickness'],cv2.LINE_AA)
    def encode(pixels):
        output = BytesIO()
        Image.fromarray(pixels).save(output,format='PNG',compress_level=1)
        return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
    from .feedback import fingerprint
    return dict(fiber_id=item['fiber_id'], sprite_key=fingerprint(item), x=left, y=top,
                leader_image=encode(leaders), text_image=encode(text))


def editor_objects(table):
    """Small changing decision records, separate from immutable image payloads."""
    objects=[]
    for row,approved in zip(table.to_dict('records'),review_approved_candidates(table)):
        if not row.get('visible_in_overlay',True):
            continue
        category=row.get('selection_category','accepted' if row['review_status']=='accepted' else 'ambiguous')
        automatic=row.get('automatic_category',category)
        manual=row.get('manual_classification')
        hard = automatic in ('too_small','ambiguous')
        # A superseded object has no editable visible mask. Remaining hard gates
        # cannot be waived by a color change; the explicit human label is saved.
        finite=np.isfinite([row.get('length_um',np.nan),row.get('mean_diameter_um',np.nan)]).all()
        # Thousands of unreviewed candidates do not need repeated diagnostic
        # prose in every save response. Supply it when a saved approval is gated.
        details={}
        if manual=='accepted' and hard:
            blocked=approval_block_details(row)
            details=dict(accept_block_message=blocked['message'],can_outline=blocked['can_outline'])
        saved_event=row.get('last_correction_id')
        try:
            saved_event=int(saved_event) if saved_event is not None and np.isfinite(saved_event) else 0
        except (TypeError,ValueError):
            saved_event=0
        objects.append(dict(fiber_id=int(row['fiber_id']), accepted=row['review_status']=='accepted',
                            approved=bool(approved),
                            measurable=bool(finite and not hard),
                            category=category, automatic_category=automatic,
                            manual=manual if isinstance(manual,str) else None,
                            saved_event=saved_event,
                            can_accept=not hard,
                            automatic_reason=row.get('automatic_exclusion_reason',row.get('qc_flags','')),
                            reason=row.get('exclusion_reason',row.get('qc_flags','')),
                            needs_review=bool(row.get('requires_manual_review',row['review_status']!='accepted')),
                            **details))
    return objects

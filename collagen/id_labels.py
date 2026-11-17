"""Display-only fiber IDs, shared by the editor and exported images.

Placement consumes the existing decisions and instance IDs. It never assigns IDs,
changes decisions, recalculates measurements, or modifies an instance mask.
"""
import cv2
import numpy as np
from scipy.ndimage import find_objects

from .quality import counted_candidates

FONT = cv2.FONT_HERSHEY_SIMPLEX


def measured_label_rows(table):
    if not {'fiber_id', 'review_status'} <= set(table.columns):
        raise ValueError('Fiber labels require the exported fiber IDs and review statuses.')
    if table.fiber_id.duplicated().any() or any(not isinstance(i, (int, np.integer)) or i <= 0 for i in table.fiber_id):
        raise ValueError('Fiber labels require unique positive integer IDs.')
    selected = counted_candidates(table)
    if 'included_in_measurement_statistics' in table:
        values = table.included_in_measurement_statistics
        if not values.isin([True, False]).all():
            raise ValueError('Measurement inclusion must be explicitly true or false.')
        selected &= values.eq(True)
    return table.loc[selected].sort_values('fiber_id')


def _intersection(a, b):
    return max(0, min(a[2],b[2])-max(a[0],b[0])) * max(0, min(a[3],b[3])-max(a[1],b[1]))


def fiber_id_layout(labels, table):
    """Return deterministic callouts for measurable accepted rows only.

    Prefer background beside the fiber, penalize label overlap, and anchor each
    leader to an actual pixel belonging to that same exported fiber ID.
    """
    labels = np.asarray(labels)
    if labels.ndim != 2 or labels.dtype.kind not in 'iu' or np.any(labels < 0):
        raise ValueError('Fiber ID placement requires a nonnegative integer label mask.')
    rows = measured_label_rows(table)
    if rows.empty:
        return []
    height, width = labels.shape
    bounds = find_objects(labels)
    foreground = cv2.integral((labels != 0).astype(np.uint8))
    occupied, layout = {}, []
    cell = max(16, int(width / 20))

    def cells(rect):
        x0,y0,x1,y1 = rect
        return ((x,y) for x in range(x0//cell,(x1-1)//cell+1)
                for y in range(y0//cell,(y1-1)//cell+1))

    for row in rows.to_dict('records'):
        fiber_id = int(row['fiber_id'])
        if fiber_id > len(bounds) or bounds[fiber_id-1] is None:
            raise ValueError('A measurable accepted fiber is missing from the instance mask.')
        ys,xs = bounds[fiber_id-1]
        yy,xx = np.nonzero(labels[ys,xs] == fiber_id)
        xx, yy = xx + xs.start, yy + ys.start
        cx,cy = float(xx.mean()),float(yy.mean())
        text = str(fiber_id)  # Never a row index or a newly numbered counter.
        # Use native-image text size. Scaling the font with a 4K image would
        # cover many fiber boundaries; the editor provides zoom for reading it.
        scale = .6  # 14-pixel glyph height, reduced from 16 pixels.
        thickness = max(1, int(round(scale)))
        padding = max(1, int(round(scale * 2)))
        for _ in range(60):
            (tw,th),baseline = cv2.getTextSize(text,FONT,scale,thickness)
            box_width,box_height = tw + 2*padding, th + baseline + 2*padding
            if box_width <= width and box_height <= height:
                break
            scale *= .85
            thickness, padding = 1, 1
        else:
            raise ValueError('Image dimensions are too small to render fiber IDs.')
        gap = padding + max(2,int(round(scale*2)))
        candidates = []
        # Near the outer sides/corners of a bounding box, then local alternatives
        # around the fiber's center. Curved fibers need not contain their centroid.
        for x,y in ((xs.stop+gap,cy-box_height/2), (xs.start-gap-box_width,cy-box_height/2),
                    (cx-box_width/2,ys.start-gap-box_height), (cx-box_width/2,ys.stop+gap),
                    (xs.stop+gap,ys.start), (xs.start-gap-box_width,ys.start),
                    (xs.stop+gap,ys.stop-box_height), (xs.start-gap-box_width,ys.stop-box_height)):
            candidates.append((x,y))
        for radius in (1,2,3,5):
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
                candidates.append((cx+dx*radius*box_width-box_width/2,
                                   cy+dy*radius*box_height-box_height/2))
        def score_rect(rect):
            x,y,x1,y1 = rect
            neighbors = set(r for key in cells(rect) for r in occupied.get(key,()))
            overlap = sum(_intersection(rect, other) for other in neighbors)
            foreground_pixels = int(foreground[y1,x1]-foreground[y,x1]
                                    -foreground[y1,x]+foreground[y,x])
            distance = np.hypot(x+box_width/2-cx,y+box_height/2-cy)
            # Prioritize avoiding other IDs, then balance proximity and ink over
            # foreground. Dense noise must not send every callout to one edge.
            return overlap, foreground_pixels + distance

        best = None
        for x,y in candidates:
            x,y = int(np.clip(round(x),0,width-box_width)),int(np.clip(round(y),0,height-box_height))
            rect = (x,y,x+box_width,y+box_height)
            score = score_rect(rect)
            if best is None or score < best[0]:
                best = (score,rect)
        if best[0][0] > 0:
            # Only crowded neighborhoods need a broader search. Prefer a longer
            # leader to an ID completely hidden beneath another label.
            for y in range(0,height-box_height+1,box_height+2*padding):
                for x in range(0,width-box_width+1,box_width+2*padding):
                    rect = (x,y,x+box_width,y+box_height)
                    score = score_rect(rect)
                    if score < best[0]:
                        best = (score,rect)
        rect = best[1]
        # The closest object pixel anchors the callout to the correct fiber,
        # rather than an empty centroid or another nearby object.
        bx,by = (rect[0]+rect[2])/2,(rect[1]+rect[3])/2
        index = int(np.argmin((xx-bx)**2+(yy-by)**2))
        anchor = [int(xx[index]),int(yy[index])]
        start = [int(np.clip(anchor[0],rect[0],rect[2]-1)),
                 int(np.clip(anchor[1],rect[1],rect[3]-1))]
        layout.append(dict(fiber_id=fiber_id,image_id=row.get('image_id'),text=text,
                           anchor_xy=anchor,box_xyxy=list(rect),leader_start_xy=start,
                           text_xy=[rect[0]+padding,rect[1]+padding+th],
                           font_scale=float(scale),font_thickness=thickness))
        margin = max(1,padding)
        padded = (max(0,rect[0]-margin),max(0,rect[1]-margin),
                  min(width,rect[2]+margin),min(height,rect[3]+margin))
        for key in cells(padded):
            occupied.setdefault(key,[]).append(padded)
    return layout


def fiber_id_layer(labels, layout):
    """Transparent RGBA layer; identical pixels are used in the UI and exports."""
    layer = np.zeros(labels.shape + (4,),np.uint8)
    for item in layout:
        start,anchor = tuple(item['leader_start_xy']),tuple(item['anchor_xy'])
        cv2.line(layer,start,anchor,(0,0,0,255),1,cv2.LINE_AA)
    # Leaders never paint fiber interiors or boundaries. Text placement prefers
    # background too; the editor can hide the entire layer for inspection.
    layer[labels != 0] = 0
    for item in layout:
        cv2.putText(layer,item['text'],tuple(item['text_xy']),FONT,
                    item['font_scale'],(0,0,0,255),item['font_thickness'],cv2.LINE_AA)
    return layer


def composite_id_layer(image, layer):
    canvas = image.copy()
    selected = layer[...,3] != 0
    alpha = layer[selected,3:4].astype(float) / 255
    canvas[selected] = np.rint(canvas[selected]*(1-alpha)+layer[selected,:3]*alpha).astype(np.uint8)
    return canvas

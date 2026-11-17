"""Manual/model decisions layered over immutable baseline segmentation and geometry."""
import json
import uuid

import cv2
import numpy as np
import pandas as pd
from scipy.ndimage import find_objects

from .exclusions import overlap_pixels
from .feedback import fingerprint, image_identity, pack_mask, unpack_mask
from .geometry import measure
from .learning import object_features, predict
from .pipeline import Analysis, DISTANCE_FIELDS
from .preprocessing import normalize
from .quality import classify
from .visualization import COUNTED_COLOR, EXCLUDED_COLOR
from .id_labels import fiber_id_layout, fiber_id_layer, composite_id_layer
from .selection import apply_selection

PIXEL_SETTINGS = ('segmentation', 'sigma_px', 'min_area_px', 'endpoint_exclusion_px',
                  'exclusion_regions_xyxy', 'exclusion_policy')


def masks_overlap(a, b):
    ay, ax, ah, aw = a['bbox']
    by, bx, bh, bw = b['bbox']
    y0, x0, y1, x1 = max(ay, by), max(ax, bx), min(ah, bh), min(aw, bw)
    if y0 >= y1 or x0 >= x1:
        return False
    am, bm = unpack_mask(a['mask'], a['bbox']), unpack_mask(b['mask'], b['bbox'])
    return bool(np.any(am[y0-ay:y1-ay, x0-ax:x1-ax] & bm[y0-by:y1-by, x0-bx:x1-bx]))


def polygon_mask(points, shape):
    points = np.asarray(points, float)
    height, width = shape[:2]
    if (points.ndim != 2 or points.shape[1] != 2 or not 3 <= len(points) <= 512
            or not np.isfinite(points).all()):
        raise ValueError('Draw a simple outline with 3–512 vertices.')
    if (np.any(points < 0) or np.any(points[:, 0] >= width) or np.any(points[:, 1] >= height)):
        raise ValueError('Outline vertices must be inside the original image.')
    points = np.rint(points).astype(np.int32)
    points[:, 0] = np.minimum(points[:, 0], width - 1)
    points[:, 1] = np.minimum(points[:, 1], height - 1)
    if len(np.unique(points, axis=0)) != len(points):
        raise ValueError('Outline vertices must be distinct; do not repeat the first vertex.')

    def intersects(a, b, c, d):
        def cross(p, q, r):
            return int(q[0]-p[0])*int(r[1]-p[1]) - int(q[1]-p[1])*int(r[0]-p[0])
        s1, s2, s3, s4 = cross(a,b,c), cross(a,b,d), cross(c,d,a), cross(c,d,b)
        return (s1*s2 <= 0 and s3*s4 <= 0
                and max(min(a[0],b[0]),min(c[0],d[0])) <= min(max(a[0],b[0]),max(c[0],d[0]))
                and max(min(a[1],b[1]),min(c[1],d[1])) <= min(max(a[1],b[1]),max(c[1],d[1])))

    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if intersects(points[i], points[(i+1)%n], points[j], points[(j+1)%n]):
                raise ValueError('The outline crosses itself. Clear it and trace one fiber boundary.')
    if abs(cv2.contourArea(points)) < 1:
        raise ValueError('The outline must enclose a nonzero area.')
    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0) + 1
    mask = np.zeros((y1-y0, x1-x0), np.uint8)
    cv2.fillPoly(mask, [points - (x0, y0)], 1)
    return mask.astype(bool), (int(y0), int(x0), int(y1), int(x1))


class ReviewSession:
    def __init__(self, baseline, store, image_name, source_sha='', filters=None, render=True):
        self.baseline, self.store, self.image_name = baseline, store, image_name
        self.filters, self.render = filters, render
        self._mask_signature, self._label_cache = None, {}
        self.image_key = image_identity(baseline.original)
        parameters = {k: baseline.parameters[k] for k in PIXEL_SETTINGS}
        self.analysis_key = fingerprint(self.image_key, parameters)
        if not store.has_analysis(self.analysis_key):
            self.register(parameters, source_sha)
        self.refresh()

    def register(self, parameters, source_sha):
        b = self.baseline
        gray = normalize(b.original)
        calibration = b.parameters['calibration_um_per_px']
        objects = []
        slices = find_objects(b.labels)
        for row in b.table.to_dict('records'):
            fiber_id = int(row['fiber_id'])
            ys, xs = slices[fiber_id - 1]
            bbox = (ys.start, xs.start, ys.stop, xs.stop)
            mask = b.labels[ys, xs] == fiber_id
            measurement = {k: v for k, v in row.items() if k != 'calibration_um_per_px'}
            for name in DISTANCE_FIELDS:
                measurement[name + '_px'] = measurement.pop(name + '_um') / calibration
            yy, xx = np.nonzero(mask)
            measurement.update(centroid_x=float(xx.mean() + xs.start), centroid_y=float(yy.mean() + ys.start))
            mask_key = fingerprint(self.image_key, bbox, pack_mask(mask))
            objects.append(dict(object_key=fingerprint(self.analysis_key, mask_key), mask_key=mask_key,
                                fiber_id=fiber_id, kind='detected', bbox=bbox, mask=mask,
                                features=object_features(mask, gray[ys, xs], measurement),
                                original_classification='accepted' if row['review_status'] == 'accepted' else 'rejected',
                                measurement=measurement))
        active = self.store.active_model()
        if active and objects:
            decisions = predict(active['payload'], [o['features'] for o in objects])
            for obj, accepted in zip(objects, decisions):
                obj.update(original_classification='accepted' if accepted else 'rejected',
                           original_model_id=active['model_id'])
        self.store.register(self.analysis_key, self.image_key, self.image_name, source_sha,
                            b.original, parameters, objects)

    def refresh(self):
        # If another session changes the store while loading, don't issue actions
        # against a mixed snapshot; a later click will also check this revision.
        for _ in range(3):
            before = self.store.revision
            self.objects = [o for o in self.store.objects(self.analysis_key) if o['active']]
            self.model = self.store.active_model()
            if before == self.store.revision:
                self.revision = before
                self.result = self.build_result()
                return
        raise ValueError('The review is changing in another session. Refresh and try again.')

    @property
    def context(self):
        policy = (':' + fingerprint(self.filters.parameters(), self.baseline.parameters['calibration_um_per_px'])[:16]
                  if self.filters is not None else '')
        return f'{self.analysis_key}:{self.revision}{policy}'

    def set_filters(self, filters):
        if self.filters != filters:
            self.filters = filters
            self.result = self.build_result()

    def build_result(self):
        b = self.baseline
        calibration = b.parameters['calibration_um_per_px']
        decisions = (predict(self.model['payload'], [o['features'] for o in self.objects])
                     if self.model and self.objects else None)
        rows = []
        for index, obj in enumerate(self.objects):
            row = dict(obj['measurement'])
            baseline_class = 'accepted' if row['review_status'] == 'accepted' else 'rejected'
            automatic = ('accepted' if decisions[index] else 'rejected') if decisions is not None else baseline_class
            decision = obj['manual_classification'] or automatic
            row.update(image_id=self.image_name, fiber_id=obj['fiber_id'],
                       review_status='accepted' if decision == 'accepted' else 'review_required',
                       classification=decision, baseline_classification=baseline_class,
                       original_classification=obj['original_classification'],
                       original_model_version=obj['original_model_id'], automatic_classification=automatic,
                       manual_classification=obj['manual_classification'],
                       decision_source='manual' if obj['manual_classification'] else 'model' if self.model else 'baseline',
                       model_version=self.model['model_id'] if self.model else 'baseline',
                       object_key=obj['object_key'], object_source=obj['kind'], analysis_key=self.analysis_key,
                       bbox_xyxy=json.dumps([obj['bbox'][1],obj['bbox'][0],obj['bbox'][3],obj['bbox'][2]]),
                       last_correction_id=obj['state_event'], used_for_training=bool(obj['used_for_training']),
                       used_for_evaluation=bool(obj['used_for_evaluation']))
            for name in DISTANCE_FIELDS:
                value = row.pop(name + '_px')
                row[name + '_um'] = float(value) * calibration if value is not None else np.nan
            row['calibration_um_per_px'] = calibration
            rows.append(row)
        by_id = {r['fiber_id']: r for r in rows}
        manuals = [o for o in self.objects if o['kind'] == 'manual']
        for obj in self.objects:
            # An outline replaces only its own pixels in the review layer. The
            # intersected original is retained for audit and cannot double count.
            if any(other['fiber_id'] > obj['fiber_id'] and masks_overlap(obj, other) for other in manuals):
                row = by_id[obj['fiber_id']]
                row.update(classification='rejected', review_status='review_required', decision_source='manual_outline')
                row['qc_flags'] = ';'.join(filter(None, [row['qc_flags'], 'superseded_by_manual_outline']))
        signature = tuple((o['fiber_id'], o['mask_key']) for o in manuals)
        if signature != self._mask_signature:
            self._labels = b.labels.copy() if manuals else b.labels
            for obj in manuals:
                y0,x0,y1,x1 = obj['bbox']
                self._labels[y0:y1,x0:x1][unpack_mask(obj['mask'], obj['bbox'])] = obj['fiber_id']
            self._mask_signature, self._label_cache = signature, {}
            self._visible = set(np.unique(self._labels)) - {0}
            self.editor_assets = None
            self.sprite_cache = {}
        labels = self._labels
        visible = self._visible
        for row in rows:
            row['visible_in_overlay'] = row['fiber_id'] in visible
            finite = all(np.isfinite(row[name]) and row[name] > 0 for name in ('length_um', 'mean_diameter_um'))
            row['measurement_status'] = ('unavailable' if not finite else 'partial_or_flagged'
                                         if row['border_touch'] or row['crossing_merge'] or row['excluded_region'] else 'measurable')
            row['included_in_measurement_statistics'] = row['classification'] == 'accepted' and row['measurement_status'] == 'measurable'
            row.pop('_centerline_xy', None)
        table = pd.DataFrame(rows) if rows else self.empty_table()
        if self.filters is not None:
            table = apply_selection(table, self.filters)
        accepted = table.review_status.eq('accepted') if self.filters is not None else table.classification.eq('accepted')
        measured = table[table.included_in_measurement_statistics.astype(bool)]
        summary = dict(candidate_count=len(table), accepted_count=int(accepted.sum()),
                       accepted_isolated_count=len(measured), accepted_measurement_count=len(measured),
                       accepted_pending_measurement_count=int(accepted.sum())-len(measured),
                       review_required_count=int((~accepted).sum()),
                       excluded_region_count=int(table.excluded_region.sum()),
                       manual_reviewed_count=int(table.manual_classification.notna().sum()),
                       manual_added_count=len(manuals),
                       accepted_mean_length_um=float(measured.length_um.mean()),
                       accepted_mean_diameter_um=float(measured.mean_diameter_um.mean()))
        if self.filters is not None:
            summary['accepted_isolated_count'] = int((~measured.border_touch.astype(bool)).sum())
            summary['partial_length_included_count'] = int(measured.border_touch.astype(bool).sum())
            summary['manual_accepted_but_ineligible_count'] = int((table.manual_classification.eq('accepted')
                & ~table.included_in_measurement_statistics.astype(bool)).sum())
            summary['selection_categories'] = {name: int(table.selection_category.eq(name).sum())
                                               for name in ('accepted','rejected','out_of_range','too_small','ambiguous','noise')}
        if self.render:
            id_labels = fiber_id_layout(labels, table)
            annotation = composite_id_layer(self.annotate(table, manuals), fiber_id_layer(labels, id_labels))
        else:
            selected = set(measured.fiber_id)
            missing = selected - self._label_cache.keys()
            if missing:
                additions = fiber_id_layout(labels, table[table.fiber_id.isin(missing)],
                                            reserved=self._label_cache.values())
                self._label_cache.update((item['fiber_id'], item) for item in additions)
            id_labels = [self._label_cache[i] for i in sorted(selected)]
            annotation = None
        parameters = {**b.parameters, 'review': dict(analysis_key=self.analysis_key, image_key=self.image_key,
                      revision=self.revision, active_model=self.model['model_id'] if self.model else 'baseline',
                      history_event_ids=[int(o['state_event']) for o in self.objects if o['state_event']],
                      measurement_policy='Accepted objects with complete simple centerlines and reliable width; border/crossing/excluded-region objects stay out of measurement aggregates.',
                      dataset_role=self.store.image_role(self.image_key))}
        if self.filters is not None:
            parameters['selection_filters'] = self.filters.parameters()
            parameters['review']['measurement_policy'] = ('Only selected measurable fibers; configurable mean-width, '
                'length, physical area, border and research-range gates. Manual acceptance can override a '
                'research range/model suggestion, but cannot fabricate geometry or bypass reliability gates.')
        manual_masks = [(o['fiber_id'], o['bbox'], unpack_mask(o['mask'], o['bbox'])) for o in manuals]
        centerlines = dict(getattr(b, 'centerlines', {}))
        for obj in manuals:
            xy = np.asarray(obj['measurement']['_centerline_xy'], np.int32)
            centerlines[obj['fiber_id']] = xy[:, ::-1] if xy.size else np.empty((0,2), np.int32)
        return Analysis(b.original, b.mask, labels, annotation, table, summary, parameters,
                        baseline_labels=b.labels, manual_masks=manual_masks, id_labels=id_labels,
                        centerlines=centerlines)

    def empty_table(self):
        table = self.baseline.table.copy()
        for name in ('classification','manual_classification','decision_source','measurement_status'):
            table[name] = pd.Series(dtype=str)
        for name in ('visible_in_overlay','included_in_measurement_statistics'):
            table[name] = pd.Series(dtype=bool)
        return table

    def annotate(self, table, manuals):
        b = self.baseline
        # Reuse the already-computed baseline centerlines. Reclassifying an
        # object must not rerun skeletonization or measurement.
        gray = (normalize(b.original) * 255).astype(np.uint8)
        canvas = np.repeat(gray[...,None], 3, axis=2)
        colors = np.zeros((max([int(b.labels.max())] + table.fiber_id.tolist()) + 1, 3), np.uint8)
        for row in table.itertuples():
            colors[row.fiber_id] = COUNTED_COLOR if row.review_status == 'accepted' else EXCLUDED_COLOR
        if getattr(b,'centerlines',{}):
            for fiber_id, yx in b.centerlines.items():
                if len(yx):
                    canvas[yx[:,0],yx[:,1]] = colors[fiber_id]
        else:
            # Compatibility for callers constructing an Analysis without the
            # optional retained centerline coordinates.
            skeleton = np.any(b.annotated != b.annotated[...,:1], axis=2) & (b.labels > 0)
            canvas[skeleton] = colors[b.labels[skeleton]]
        for obj in manuals:
            xy = np.asarray(obj['measurement']['_centerline_xy'], int)
            if xy.size:
                canvas[xy[:,1],xy[:,0]] = colors[obj['fiber_id']]
        for left,top,right,bottom in b.parameters['exclusion_regions_xyxy']:
            cv2.rectangle(canvas, (left,top), (right-1,bottom-1), EXCLUDED_COLOR, 2)
        return canvas

    def correct(self, fiber_id, label, role, token=None):
        selected = next((o for o in self.objects if o['fiber_id'] == fiber_id), None)
        if not selected:
            raise ValueError('Select an active object.')
        if label == 'accepted':
            accepted = set(self.result.table.loc[self.result.table.classification.eq('accepted'), 'fiber_id'])
            conflicts = [o['fiber_id'] for o in self.objects if o is not selected
                         and (o['fiber_id'] in accepted or (o['kind'] == 'manual' and o['fiber_id'] > fiber_id))
                         and masks_overlap(selected, o)]
            if conflicts:
                raise ValueError(f'This overlaps objects {conflicts}. Reject an overlapping accepted object, or undo its newer outline, before accepting this object.')
        row = self.result.table.loc[self.result.table.fiber_id.eq(fiber_id)].iloc[0]
        selection_context = (dict(filters=self.filters.parameters(), calibration_um_per_px=self.baseline.parameters['calibration_um_per_px'],
                                  automatic_category=row.automatic_category, selection_category=row.selection_category,
                                  exclusion_reason=row.exclusion_reason) if self.filters is not None else None)
        return self.store.correct(self.analysis_key, fiber_id, label, role, row.automatic_classification,
                                  None if not self.model else self.model['model_id'], token, self.revision,
                                  selection_context=selection_context)

    def add_outline(self, points, role, token=None):
        mask, bbox = polygon_mask(points, self.baseline.original.shape)
        selected = dict(bbox=bbox, mask=pack_mask(mask))
        accepted = set(self.result.table.loc[self.result.table.classification.eq('accepted'), 'fiber_id'])
        conflicts = [o['fiber_id'] for o in self.objects if o['fiber_id'] in accepted and masks_overlap(selected, o)]
        if conflicts:
            raise ValueError(f'The outline overlaps accepted objects {conflicts}. Reject them explicitly first; other objects are never changed silently.')
        y0,x0,y1,x1 = bbox
        b = self.baseline
        m = measure(mask, 1., b.parameters['endpoint_exclusion_px'])
        border = y0 == 0 or x0 == 0 or y1 == b.mask.shape[0] or x1 == b.mask.shape[1]
        flags, status = classify(m, border, int(mask.sum()), b.parameters['min_area_px'])
        overlap = overlap_pixels(mask, bbox, b.parameters['exclusion_regions_xyxy'])
        if overlap:
            flags.append('excluded_region')
            status = 'review_required'
        yy, xx = np.nonzero(mask)
        row = dict(image_id=self.image_name, area_px=int(mask.sum()), length_px=m['length_um'],
                   mean_diameter_px=m['mean_diameter_um'], diameter_sd_px=m['diameter_sd_um'],
                   diameter_samples=m['diameter_samples'],
                   endpoints=json.dumps([[int(x+x0),int(y+y0)] for y,x in m['endpoints']]),
                   border_touch=border, crossing_merge=bool(m['junctions']), review_status=status,
                   qc_flags=';'.join(flags), diameter_method='2x_distance_transform_approximation',
                   excluded_region=bool(overlap), exclusion_overlap_px=overlap,
                   centroid_x=float(xx.mean()+x0), centroid_y=float(yy.mean()+y0),
                   _centerline_xy=[[int(x+x0),int(y+y0)] for y,x in np.argwhere(m['skeleton'])])
        mask_key = fingerprint(self.image_key, bbox, pack_mask(mask))
        obj = dict(object_key=fingerprint(self.analysis_key, 'manual', uuid.uuid4().hex),
                   mask_key=mask_key, kind='manual', bbox=bbox, mask=mask, measurement=row,
                   features=object_features(mask, normalize(b.original)[y0:y1,x0:x1], row),
                   original_classification='not_detected')
        context = (dict(filters=self.filters.parameters(),calibration_um_per_px=b.parameters['calibration_um_per_px'])
                   if self.filters is not None else None)
        return self.store.add(self.analysis_key, obj, role, token, self.revision, selection_context=context)

    def handle_event(self, event, role):
        if not isinstance(event, dict) or event.get('context') != self.context:
            raise ValueError('This click belongs to an older image or review. Click again on the current image.')
        if not isinstance(event.get('token'), str) or not event['token']:
            raise ValueError('The editor event needs a unique token.')
        if event.get('action') in ('undo', 'redo'):
            return getattr(self.store, event['action'])(self.analysis_key, self.revision, event['token'])
        if event.get('action') == 'outline':
            return self.add_outline(event.get('points'), role, event['token'])
        if event.get('action') not in ('toggle', 'accept', 'reject'):
            raise ValueError('Unknown editor action.')
        x, y = event.get('x'), event.get('y')
        if 'fiber_id' in event and x is None and y is None:
            fiber_id = event['fiber_id']
            if isinstance(fiber_id, bool) or not isinstance(fiber_id, int) or fiber_id <= 0:
                raise ValueError('Select a valid fiber ID.')
            selected = self.result.table.loc[self.result.table.fiber_id.eq(fiber_id)]
            if selected.empty or not selected.iloc[0].visible_in_overlay:
                raise ValueError('Select an active visible object.')
        else:
            if (not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                or not np.isfinite([x,y]).all() or x < 0 or y < 0
                or x >= self.result.labels.shape[1] or y >= self.result.labels.shape[0]):
                raise ValueError('Click coordinates must be inside the original image.')
            fiber_id = int(self.result.labels[int(y),int(x)])
            if 'fiber_id' in event and event['fiber_id'] != fiber_id:
                raise ValueError('The selected object does not match that pixel.')
        if fiber_id == 0:
            raise ValueError('No detected object at that pixel. Zoom in, or outline a missed fiber.')
        row = self.result.table.loc[self.result.table.fiber_id.eq(fiber_id)].iloc[0]
        label = ('accepted' if event['action'] == 'accept' else 'rejected' if event['action'] == 'reject'
                 else 'rejected' if row.classification == 'accepted' else 'accepted')
        self.correct(fiber_id, label, role, event['token'])
        return fiber_id

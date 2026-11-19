"""Independent scientific references layered beside operational binary review."""
from copy import deepcopy
import json
import sqlite3

import cv2
import numpy as np
import pandas as pd
import streamlit as st

from .feedback import fingerprint, json_value, unpack_mask
from .references import (CATEGORIES, SPLITS, ReferenceStore, new_reference,
                         reference_object, upsert_reference, validate_reference, decode_reference_mask)
from .review import polygon_mask
from .visualization import display_rgb
from .scientific_metrics import audit_saved_predictions

LABELS=dict(valid_measurable_fiber='Valid measurable fiber',
            real_fiber_not_measurable='Real fiber but not reliably measurable',
            non_fiber='Non-fiber object',ambiguous='Ambiguous object')


def reference_state(review):
    if not hasattr(review,'reference_store'):
        review.reference_store=ReferenceStore(review.store.directory)
        review.reference_document=review.reference_store.load(review.image_key) or new_reference(
            review.baseline.original,review.image_name,review.baseline.parameters.get('calibration_um_per_px'))
    return review.reference_document


def reference_payload(review):
    config=getattr(review,'reference_config',None)
    if config is None:return dict(reference_enabled=False,reference_context=None)
    doc=reference_state(review)
    return dict(reference_enabled=True,reference_context=fingerprint(doc['version_id'],config))


def save_reference_outline(review, event):
    payload=reference_payload(review)
    if (event.get('context')!=review.context or not payload['reference_enabled']
            or event.get('reference_context')!=payload['reference_context']):
        raise ValueError('Reference configuration or image changed. Confirm the reference form and redraw the boundary.')
    mask,bbox=polygon_mask(event.get('points'),review.baseline.original.shape)
    config=review.reference_config
    obj=reference_object(mask,config['category'],config['reviewer'],object_id=config.get('object_id'),
        length_um=config.get('length_um'),mean_diameter_um=config.get('mean_diameter_um'),
        measurement_source=config['measurement_source'],note=config['note'],offset_yx=bbox[:2])
    obj['human_polygon_xy']=event['points']
    # Replacing a classification-only mask keeps the original prediction link.
    previous=next((o for o in reference_state(review)['objects'] if o['object_id']==obj['object_id']),None)
    if previous:obj['original_prediction']=previous.get('original_prediction')
    doc=upsert_reference(reference_state(review),obj,review.baseline.original)
    review.reference_document=review.reference_store.save(doc,review.baseline.original)
    # One successful request may arrive twice through component rerenders. The
    # version/context check prevents silently creating a second reference.
    return obj['object_id']


def copy_selected_label(review, fiber_id, config):
    source=next((o for o in review.objects if o['fiber_id']==fiber_id and o['active']),None)
    if source is None:raise ValueError('Select an active detected object before assigning a reference category.')
    doc=reference_state(review)
    original=review.baseline.table.loc[review.baseline.table.fiber_id.eq(fiber_id)]
    snapshot=None if original.empty else json_value(dict(image_key=review.image_key,analysis_key=review.analysis_key,
        fiber_id=int(fiber_id),original_row=original.iloc[0].to_dict(),parameters=deepcopy(review.baseline.parameters)))
    previous=next((o for o in doc['objects'] if o.get('original_prediction') and
                   o['original_prediction'].get('analysis_key')==review.analysis_key and
                   o['original_prediction'].get('fiber_id')==fiber_id),None)
    obj=reference_object(unpack_mask(source['mask'],source['bbox']),config['category'],config['reviewer'],
        boundary_source='prediction_reference',object_id=previous['object_id'] if previous else None,
        length_um=config.get('length_um'),mean_diameter_um=config.get('mean_diameter_um'),
        measurement_source=config['measurement_source'],original_prediction=snapshot,note=config['note'],
        offset_yx=source['bbox'][:2])
    if previous and previous['boundary_source']=='independent_manual':
        # Changing a category must not replace an independently drawn boundary
        # with the automatic mask merely because its source ID is selected.
        for field in ('bbox_yxyx','mask_encoding','mask_data','boundary_source','human_polygon_xy'):
            if field in previous:obj[field]=deepcopy(previous[field])
    updated=upsert_reference(doc,obj,review.baseline.original)
    review.reference_document=review.reference_store.save(updated,review.baseline.original)


@st.cache_data(show_spinner=False,max_entries=2)
def reference_preview(original, document):
    rgb=display_rgb(original).copy()
    colors={'valid_measurable_fiber':(25,110,215),'real_fiber_not_measurable':(210,125,20),
            'non_fiber':(170,30,140),'ambiguous':(95,95,95)}
    for obj in document['objects']:
        if obj['boundary_source']!='independent_manual':continue
        mask,(y0,x0,y1,x1)=decode_reference_mask(obj,original.shape)
        contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        shifted=[c+np.array([[[x0,y0]]],np.int32) for c in contours]
        cv2.drawContours(rgb,shifted,-1,colors[obj['category']],2)
    return rgb


def reference_panel(review):
    panel=st.expander('Scientific validation and independent reference annotations',expanded=False,
                      key='scientific_references',on_change='rerun')
    if not panel.open:return
    with panel:
        doc=reference_state(review)
        if st.button('Reload saved reference annotations',key='reload-references-'+review.analysis_key):
            review.reference_document=review.reference_store.load(review.image_key) or doc
            review.reference_config=None;st.rerun()
        st.caption('Reference labels are separate from green/red operational decisions. Copied prediction masks support classification review only; they are never independent segmentation truth. Draw or import independent boundaries to evaluate detection and IoU. Do not treat the 50 selected ImageJ fibers as an exhaustive image mask.')
        key=review.analysis_key
        choices=['new']+[o['object_id'] for o in doc['objects']]
        target=st.selectbox('Reference object to edit',choices,key='reference-edit-'+key,
                            format_func=lambda v:'New independent reference' if v=='new' else v)
        existing=next((o for o in doc['objects'] if o['object_id']==target),{})
        with st.form('reference-form-'+key+':'+target):
            reviewer=st.text_input('Reference reviewer',value=existing.get('reviewer',doc['reviewer']))
            category=st.selectbox('Independent reference category',list(CATEGORIES),
                                  index=list(CATEGORIES).index(existing.get('category','ambiguous')),format_func=LABELS.get)
            specimen=st.text_input('Biological specimen ID (leave blank if unknown)',value=doc['specimen_id'] or '')
            acquisition=st.text_input('Acquisition group (leave blank if unknown)',value=doc['acquisition_group'] or '')
            magnification=st.text_input('Magnification metadata',value=doc.get('magnification') or '')
            calibration=st.number_input('Reference calibration for this original (µm/pixel)',min_value=.000001,
                                         value=doc['calibration_um_per_px'],format='%.8f')
            calibration_source=st.text_input('Calibration source',value=doc.get('calibration_source') or 'Operator confirmation for this original image')
            st.caption('Confirm µm/pixel per original. Magnification and a shared 20 µm scale-bar label do not establish the pixel span. The initial value comes from this image’s analysis entry, not from other images.')
            split=st.selectbox('Scientific dataset split',list(SPLITS),index=list(SPLITS).index(doc['split']))
            exposed=st.checkbox('Previously used for development or parameter tuning',value=doc.get('previously_used_for_development',True))
            exhaustive=st.checkbox('I exhaustively reviewed all fibers/background in the rectangle below',value=doc['coverage']=='exhaustive_regions')
            st.caption('Mark every visible fiber intersecting the reviewed region, including missed and unmeasurable fibers; mark ambiguous regions explicitly. Leave exhaustive coverage unchecked for a selected subset. Unknown pixels are not negatives.')
            h,w=review.baseline.original.shape[:2]
            bounds=doc['reviewed_regions_xyxy'][0] if doc['reviewed_regions_xyxy'] else [0,0,w,h]
            cols=st.columns(4)
            rectangle=[cols[i].number_input(label,min_value=0,max_value=w if i%2==0 else h,value=bounds[i])
                       for i,label in enumerate(('Reference left','Reference top','Reference right','Reference bottom'))]
            length=st.number_input('Independent manual reference length (µm; optional)',min_value=.000001,value=existing.get('length_um'))
            diameter=st.number_input('Independent manual reference mean diameter (µm; optional)',min_value=.000001,value=existing.get('mean_diameter_um'))
            measurement_source=st.text_input('Independent measurement source / ROI identifier',value=existing.get('measurement_source',''))
            note=st.text_input('Reference / failure-mode note',value=existing.get('note',''))
            configure=st.form_submit_button('Configure independent reference outline')
            edit_label=st.form_submit_button('Update reference category and measurements',disabled=target=='new')
            selected=st.session_state.get('review-object-'+key)
            copy_label=st.form_submit_button('Save selected detection category (not boundary truth)',disabled=selected is None)
        if configure or copy_label or edit_label:
            try:
                if not reviewer.strip():raise ValueError('Record the human reviewer before saving reference annotations.')
                updated=deepcopy(doc)
                updated.update(reviewer=reviewer.strip(),specimen_id=specimen.strip() or None,
                    acquisition_group=acquisition.strip() or None,magnification=magnification.strip() or None,
                    calibration_um_per_px=calibration,calibration_source=calibration_source,
                    split=split,previously_used_for_development=exposed,
                    coverage='exhaustive_regions' if exhaustive else 'partial',
                    reviewed_regions_xyxy=[rectangle] if exhaustive else [])
                validate_reference(updated,review.baseline.original)
                if split=='test' and exposed:raise ValueError('Development images cannot be relabeled as an independent final test.')
                if (length is not None or diameter is not None) and not measurement_source.strip():
                    raise ValueError('Record the independent ImageJ/ROI source for manual measurement values.')
                review.reference_document=review.reference_store.save(updated,review.baseline.original)
                review.reference_config=dict(object_id=None if target=='new' else target,category=category,
                    reviewer=reviewer.strip(),length_um=length,mean_diameter_um=diameter,
                    measurement_source=measurement_source,note=note)
                if copy_label:copy_selected_label(review,int(selected),review.reference_config)
                if edit_label:
                    obj=deepcopy(existing)
                    obj.update(category=category,reviewer=reviewer.strip(),length_um=length,mean_diameter_um=diameter,
                               measurement_source=measurement_source,note=note)
                    changed=upsert_reference(review.reference_document,obj,review.baseline.original)
                    review.reference_document=review.reference_store.save(changed,review.baseline.original)
                st.rerun()
            except (ValueError,OSError,sqlite3.Error) as error:st.error(str(error))
        doc=reference_state(review)
        st.caption(f"Saved reference revision: {doc['annotation_revision']} · {len(doc['objects'])} reference objects. Configure the form, then choose Independent reference outline above the main images to draw or replace a boundary.")
        if doc['objects']:
            categories=pd.DataFrame([dict(reference_id=o['object_id'],category=o['category'],boundary_source=o['boundary_source'],
                               source_fiber_id=(o.get('original_prediction') or {}).get('fiber_id'),note=o.get('note','')) for o in doc['objects']])
            st.dataframe(categories,hide_index=True)
            st.download_button('Download reference categories CSV',categories.to_csv(index=False),
                               'reference-categories.csv','text/csv')
            matrix,metrics=audit_saved_predictions(doc)
            st.caption('Selected-object audit: human four-way categories versus immutable original QC decisions. This is not missed-fiber recall or segmentation accuracy. Ambiguous references and objects with no original prediction are excluded from binary rates.')
            st.dataframe(matrix)
            st.write({k:metrics[k] for k in ('samples','precision','recall','f1','false_positive_rate','false_negative_rate')})
            st.download_button('Download original-prediction classification matrix',matrix.to_csv(),
                               'original-prediction-confusion.csv','text/csv')
            st.image(reference_preview(review.baseline.original,doc),caption='Reference-only preview: blue measurable fiber; orange real/unmeasurable; magenta non-fiber; gray ambiguous. Only independently drawn boundaries are shown. Original viewer and prediction masks are unchanged.')
        st.download_button('Download versioned reference JSON',json.dumps(doc,indent=2,allow_nan=False),
                           'reference-'+review.image_key[:12]+'.json','application/json')
        history=review.reference_store.version_headers(review.image_key)
        if len(history)>1:
            previous=st.selectbox('Saved reference version',[h['version_id'] for h in history],
                                  format_func=lambda v:v[:12],key='reference-history-'+key)
            if st.button('Restore reference version as a new revision',key='restore-reference-'+key):
                try:
                    old=next(d for d in review.reference_store.versions(review.image_key) if d['version_id']==previous)
                    old['restored_from_version_id']=previous;old['version_id']=doc['version_id']
                    old['annotation_revision']=doc['annotation_revision']
                    review.reference_document=review.reference_store.save(old,review.baseline.original)
                    review.reference_config=None;st.rerun()
                except (ValueError,OSError,sqlite3.Error) as error:st.error(str(error))
        upload=st.file_uploader('Import independently reviewed reference JSON',type=['json'],key='reference-import-'+key)
        if upload is not None and st.button('Validate and import reference version',key='import-reference-'+key):
            try:
                incoming=json.loads(upload.getvalue());validate_reference(incoming,review.baseline.original)
                incoming['imported_version_id']=incoming.get('version_id')
                incoming['version_id']=doc['version_id'];incoming['annotation_revision']=doc['annotation_revision']
                review.reference_document=review.reference_store.save(incoming,review.baseline.original)
                review.reference_config=None;st.rerun()
            except (ValueError,OSError,sqlite3.Error) as error:st.error(str(error))
        st.caption('Reference versions are stored additively in the existing feedback database and included in its consistent SQLite backup. Binary accepted/rejected corrections are not automatically converted into four-way biological labels or segmentation training masks.')

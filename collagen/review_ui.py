"""Streamlit orchestration for persistent human review and experimental learning."""
import sqlite3
import uuid

import pandas as pd
import streamlit as st

from .editor import fiber_editor, editor_assets, editor_objects, label_sprite
from .export import csv_bytes
from .feedback import FeedbackStore, fingerprint, feedback_directory, image_identity
from .learning import retrain
from .review import ReviewSession
from .id_labels import fiber_id_layout
from .selection import select_baseline
from .quality import approval_block_details
from .ui import summary_cards


def request_review_selection(selected_key, request_key):
    """Mirror an explicit object-ID choice into the temporary canvas cursor."""
    st.session_state[request_key] = dict(token=uuid.uuid4().hex,
                                       fiber_id=st.session_state.get(selected_key))


def next_review_object(table, selected=None, view_mode='all'):
    """Choose an unreviewed visible cursor target without changing decisions."""
    ids = sorted(o['fiber_id'] for o in editor_objects(table)
                 if o['fiber_id'] != selected and o['manual'] is None
                 and (view_mode == 'all' or
                      (o['accepted'] or o['manual'] is not None if view_mode == 'hide_auto' else o['needs_review'])))
    return next((fiber_id for fiber_id in ids if selected is not None and fiber_id > selected),
                ids[0] if ids else None)


def comparison_editor(result, review=None, image_name='', **kwargs):
    """Keep both views tied to the exact source used by this analysis."""
    assets = getattr(review,'editor_assets',None)
    if assets is None:
        assets = editor_assets(result)
        if review is not None:
            review.editor_assets = assets
    if result.id_labels is None:
        result.id_labels = fiber_id_layout(result.labels,result.table)
    sprites = getattr(review,'sprite_cache',{})
    for item in result.id_labels:
        if item['fiber_id'] not in sprites:
            sprites[item['fiber_id']] = label_sprite(result.labels,item)
    asset_key = (fingerprint(review.analysis_key, review._mask_signature) if review is not None else
                 fingerprint('baseline', image_identity(result.original), result.parameters))
    acknowledged = st.session_state.get('editor_assets_ack-' + asset_key, {})
    # Immutable layers are referenced after the browser acknowledges them. A
    # cache miss requests them again; no image files or datasets are exposed.
    payload = {} if acknowledged else assets
    known_sprites = set(acknowledged.get('sprites', ()))
    return fiber_editor(**payload, asset_key=asset_key, objects=editor_objects(result.table),
                        source_image=dict(name=image_name or getattr(review,'image_name',''),
                            sha256=result.parameters.get('source_sha256',''),
                            width=int(result.original.shape[1]),height=int(result.original.shape[0])),
                        review_progress=dict(total=len(result.table),
                            reviewed=int(result.table.manual_classification.notna().sum()) if 'manual_classification' in result.table else 0),
                        id_sprites=[sprites[item['fiber_id']] for item in result.id_labels
                                    if sprites[item['fiber_id']]['sprite_key'] not in known_sprites],
                        fiber_id_labels=result.id_labels, **kwargs)


def reviewed_results(baseline, image_name, source_sha, filters=None):
    try:
        cached = st.session_state.get('_active_review')
        store = (cached.store if cached is not None and cached.store.directory == feedback_directory()
                 else FeedbackStore())
        if (cached is None or cached.baseline is not baseline or cached.image_name != image_name
                or cached.store.path != store.path):
            review = ReviewSession(baseline, store, image_name, source_sha, filters=filters, render=False)
            st.session_state['_active_review'] = review
        else:
            review = cached
            review.set_filters(filters)
            if review.revision != store.revision:
                review.refresh()
    except (OSError, sqlite3.Error, ValueError) as error:
        st.error(f'Manual review storage is unavailable: {error}. Baseline analysis remains available. Set COLLAGEN_FEEDBACK_DIR to a writable folder to enable saved review.')
        if filters is not None:
            baseline = select_baseline(baseline,filters)
        summary_cards(baseline.summary)
        with st.expander('Analysis summary details'):
            st.json(baseline.summary)
        comparison_editor(baseline, image_name=image_name, review_enabled=False, key='baseline-comparison', default=None,
                          message='Baseline results; saved manual review is unavailable.')
        return baseline

    key = review.analysis_key
    notice_key, ack_key = 'review_notice-' + key, 'editor_ack-' + key
    if st.session_state.get('review_context') != review.context:
        st.session_state.pop('archive', None)
        st.session_state.pop('feedback_backup', None)
        st.session_state['review_context'] = review.context
    result = review.result
    selected_key, selection_key = 'review-object-' + key, 'review-selection-' + key
    pending_selection_key = 'review-next-selection-' + key
    if pending_selection_key in st.session_state:
        st.session_state[selected_key] = st.session_state.pop(pending_selection_key)
    if selected_key not in st.session_state:
        st.session_state[selected_key] = next_review_object(result.table)
    summary_cards(result.summary)
    role_locked = store.image_role(review.image_key)
    with st.container(key='dataset-review'):
        role_columns=st.columns([1,2],vertical_alignment='center')
        with role_columns[0]:
            role = st.selectbox('Dataset role for this image', ['training', 'evaluation'],
                                index=1 if role_locked == 'evaluation' else 0, disabled=role_locked is not None,
                                format_func=lambda s: 'Training examples' if s == 'training' else 'Evaluation only — never used for fitting',
                                key='dataset-role-' + review.image_key)
        with role_columns[1]:
            st.caption('Choose before your first correction. The role is locked after saving a review. Reserve at least two independent images for evaluation and two for training; explicitly review both accepted and rejected objects in each group.')
    notice = st.session_state.get(notice_key, {})
    if notice:
        getattr(st, notice['level'])(notice['text'])
    model_name = review.model['model_id'][:12] if review.model else 'baseline rules'
    st.caption(f'Object classifier: {model_name}.')
    client_key = 'review_client-' + key
    if client_key not in st.session_state:
        st.session_state[client_key] = uuid.uuid4().hex
    event = comparison_editor(result, review=review, context=review.context,
                         client_key=st.session_state[client_key], policy_key=review.context.rsplit(':',1)[-1] if filters else '',
                         geometry_key=str(review._mask_signature),
                         selection_request=st.session_state.get(selection_key),
                         ack_token=st.session_state.get(ack_key),
                         ack_fiber_id=st.session_state.get('review-object-' + key),
                         event_ok=notice.get('level') == 'success', message=notice.get('text', ''),
                         key='fiber-editor-' + key, default=None)
    if event is not None and not isinstance(event, dict):
        st.warning('The editor returned an invalid event. Reload the image before editing.')
        event = None
    if event is not None and event.get('token') != st.session_state.get(ack_key):
        if isinstance(event, dict):
            st.session_state[ack_key] = event.get('token')
        try:
            asset_key = fingerprint(review.analysis_key, review._mask_signature)
            if event.get('action') != 'request_assets' and event.get('asset_key') == asset_key:
                known = event.get('known_sprites', [])
                valid = {sprite['sprite_key'] for sprite in review.sprite_cache.values()}
                st.session_state['editor_assets_ack-' + asset_key] = {
                    'sprites':[key for key in known if isinstance(key,str) and key in valid] if isinstance(known,list) else []}
            if event.get('action') == 'request_assets':
                if event.get('context') != review.context or not isinstance(event.get('token'),str):
                    raise ValueError('Refresh image layers on the current review.')
                st.session_state.pop('editor_assets_ack-' + asset_key, None)
                selected = None
            elif event.get('action') == 'select':
                if event.get('context') != review.context:
                    raise ValueError('Select a fiber in the current image review.')
                selected = event.get('fiber_id')
                if selected is not None and (isinstance(selected, bool) or not isinstance(selected, int)
                        or selected not in {o['fiber_id'] for o in editor_objects(result.table)}):
                    raise ValueError('Select an active visible object.')
                st.session_state[selected_key] = selected
            else:
                selected = review.handle_event(event, role)
            if event.get('action') in ('toggle', 'accept', 'reject', 'outline'):
                target = event.get('next_fiber_id') if 'next_fiber_id' in event else int(selected)
                if target is not None and (isinstance(target, bool) or not isinstance(target, int)
                        or target not in set(result.table.fiber_id)):
                    target = int(selected)
                st.session_state[selected_key] = target
            if event.get('action') in ('select', 'accept', 'reject'):
                st.session_state['review-guided-' + key] = event.get('guided') is not False
                if event.get('view_mode') in ('all', 'hide_auto', 'review'):
                    st.session_state['review-view-' + key] = event['view_mode']
            message = ('Image layers restored.' if event.get('action') == 'request_assets' else
                       'Review target updated; no correction saved.' if event.get('action') == 'select' else
                       'Correction saved.' if selected else 'Nothing to undo/redo.')
            # Preview the quality gate without another image/geometry build.
            if selected and event.get('action') == 'accept':
                row = result.table.loc[result.table.fiber_id.eq(selected)].iloc[0]
                if row.get('automatic_category') in ('too_small','ambiguous'):
                    message = f'Human label saved for fiber {selected}; not included in measurements. ' + approval_block_details(row)['message']
            st.session_state[notice_key] = dict(level='success', text=message)
        except (ValueError, sqlite3.Error, OSError) as error:
            st.session_state[notice_key] = dict(level='warning', text=str(error))
        st.rerun()

    with st.expander('Analysis summary details'):
        st.json(result.summary)
    st.caption(f"Green: {result.summary['review_approved_count']} approved masks · Measured fibers: {result.summary['accepted_measurement_count']}. Green approval and measurement eligibility are separate. CSV review_approved records the highlight; included_in_measurement_statistics records numerical inclusion. Detections and raw measurements are retained.")
    st.caption('Black ID labels identify only measurable accepted fibers and match fiber_id in the CSV opened in Excel. Zoom for dense areas, or hide IDs to inspect boundaries. Click the colored fiber itself to change its review decision.')
    if result.summary.get('manual_accepted_but_ineligible_count',0):
        st.warning('Some green approved masks are excluded from measurements. Inspect their exclusion reasons, adjust the selection thresholds, or outline a separated fiber. Approval does not create missing length or diameter values; these objects stay out of measured counts and statistics.')
    if result.summary['accepted_pending_measurement_count']:
        st.warning('Some accepted objects cannot provide reliable complete measurements. Their original QC flags are retained. Outline a separated individual fiber if appropriate; accepting a merged or truncated object does not reconstruct its missing centerline.')

    with st.expander('Review by object ID and save confirmations', expanded=False):
        st.caption(f'Object classifier: {model_name}. Left-click to accept; right-click to reject. A/R accept/reject the selected object, N advances, Ctrl+Z undoes and Ctrl+Y redoes. Choose Toggle mode for the earlier click behavior. Background never selects a neighbor.')
        ids = result.table.fiber_id.tolist()
        if st.session_state.get(selected_key) is not None and st.session_state.get(selected_key) not in ids:
            st.session_state[selected_key] = None
        selected = st.selectbox('Object ID', ids, index=None, key=selected_key, disabled=not ids,
                                on_change=request_review_selection, args=(selected_key, selection_key))
        st.caption('Use these buttons to confirm an already correct decision as a reviewed training/evaluation example. A click toggle also saves a reviewed example. Each object is one detected mask; connected fibers may share a mask until individually outlined.')
        st.caption('The selected Object ID shares the main viewer target. Guided review highlights that mask in dark blue and advances to the next unreviewed object after acceptance or rejection. Turn Guided review off for free-form inspection or outline repair. Blue is temporary and never exported.')
        columns = st.columns(4)
        accept = columns[0].button('Mark accepted', disabled=selected is None)
        reject = columns[1].button('Mark rejected', disabled=selected is None)
        undo = columns[2].button('Undo last correction')
        redo = columns[3].button('Redo last correction')
        if accept or reject or undo or redo:
            try:
                if undo or redo:
                    changed = getattr(store,'undo' if undo else 'redo')(key, review.revision)
                    if changed and st.session_state.get('review-guided-' + key, True):
                        review.refresh()
                        target = next_review_object(review.result.table, view_mode=st.session_state.get('review-view-' + key, 'all'))
                        st.session_state[pending_selection_key] = target
                        st.session_state[selection_key] = dict(token=uuid.uuid4().hex, fiber_id=target)
                else:
                    changed = review.correct(int(selected), 'accepted' if accept else 'rejected', role)
                    target = (next_review_object(result.table, int(selected), st.session_state.get('review-view-' + key, 'all'))
                              if st.session_state.get('review-guided-' + key, True) else int(selected))
                    st.session_state[pending_selection_key] = target
                    st.session_state[selection_key] = dict(token=uuid.uuid4().hex, fiber_id=target)
                message='Correction saved.' if changed else 'Nothing to undo.'
                if accept and changed:
                    row=result.table.loc[result.table.fiber_id.eq(selected)].iloc[0]
                    blocked=approval_block_details(row)
                    if blocked['message']:
                        message=f'Human label saved for fiber {selected}; not included in measurements. '+blocked['message']
                st.session_state[notice_key] = dict(level='success', text=message)
            except (ValueError, sqlite3.Error, OSError) as error:
                st.session_state[notice_key] = dict(level='warning', text=str(error))
            st.rerun()
        st.caption(f'Saved locally: {store.path}. Re-upload the same original with the same analysis settings to restore its corrections; calibration changes reuse them.')

    panel = st.expander('Feedback learning, model evaluation and backups', expanded=False,
                        key='feedback_learning', on_change='rerun')
    if panel.open:
        with panel:
            _, samples = store.training_snapshot()
            counts = {role: dict(images=len({s['image_key'] for s in samples if s['role'] == role}),
                                 accepted=sum(s['role'] == role and s['label'] == 'accepted' for s in samples),
                                 rejected=sum(s['role'] == role and s['label'] == 'rejected' for s in samples))
                      for role in ('training', 'evaluation')}
            st.json(counts)
            st.caption('Experimental object classification only: segmentation and geometry stay unchanged. Only current explicit human labels are used. Each role needs two images and at least five accepted and five rejected examples. Evaluation images are never fitted. A candidate must improve balanced accuracy by at least one percentage point without lower accepted precision against both the active model and baseline. Selected corrections are a biased sample; passing this gate does not establish scientific accuracy or missed-fiber detection performance.')
            if st.button('Retrain Model Using Corrections'):
                try:
                    with st.spinner('Training and evaluating reviewed objects…'):
                        _, report = retrain(store)
                    st.session_state[notice_key] = dict(level='success' if report['promoted'] else 'info', text=report['reason'])
                except (ValueError, sqlite3.Error, OSError) as error:
                    st.session_state[notice_key] = dict(level='warning', text=str(error))
                st.rerun()
            models = store.models()
            if models:
                st.json(models[0]['report'])
                approved = [m for m in models if m['approved']]
                if approved:
                    previous = st.selectbox('Preserved approved model', [m['model_id'] for m in approved],
                                            format_func=lambda s: s[:12])
                    if st.button('Restore selected approved model'):
                        store.activate(previous)
                        st.rerun()
            if review.model and st.button('Use baseline classifier'):
                store.activate()
                st.rerun()
            history = pd.DataFrame(store.history(key))
            if not history.empty:
                st.dataframe(history, hide_index=True)
                st.download_button('Download correction history CSV', csv_bytes(history), 'correction_history.csv', 'text/csv')
            st.caption('The database backup contains original images, masks, current labels, Undo history, model versions and training/evaluation membership. Keep it outside the application folder when updating the program.')
            if st.button('Prepare feedback backup'):
                st.session_state['feedback_backup'] = store.backup()
            if 'feedback_backup' in st.session_state:
                st.download_button('Download feedback database backup', st.session_state['feedback_backup'],
                                   'collagen-feedback.sqlite3', 'application/octet-stream')
    return result

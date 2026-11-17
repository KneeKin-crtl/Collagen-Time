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


def comparison_editor(result, review=None, **kwargs):
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
        st.json(baseline.summary)
        comparison_editor(baseline, review_enabled=False, key='baseline-comparison', default=None,
                          message='Baseline results; saved manual review is unavailable.')
        return baseline

    key = review.analysis_key
    notice_key, ack_key = 'review_notice-' + key, 'editor_ack-' + key
    if st.session_state.get('review_context') != review.context:
        st.session_state.pop('archive', None)
        st.session_state.pop('feedback_backup', None)
        st.session_state['review_context'] = review.context
    result = review.result
    st.json(result.summary)
    role_locked = store.image_role(review.image_key)
    role = st.selectbox('Dataset role for this image', ['training', 'evaluation'],
                        index=1 if role_locked == 'evaluation' else 0, disabled=role_locked is not None,
                        format_func=lambda s: 'Training examples' if s == 'training' else 'Evaluation only — never used for fitting',
                        key='dataset-role-' + review.image_key)
    st.caption('Choose before your first correction. The role is locked after saving a review. Reserve at least two independent images for evaluation and two for training; explicitly review both accepted and rejected objects in each group.')
    notice = st.session_state.get(notice_key, {})
    if notice:
        getattr(st, notice['level'])(notice['text'])
    model_name = review.model['model_id'][:12] if review.model else 'baseline rules'
    st.caption(f'Object classifier: {model_name}. Left-click to accept; right-click to reject. A/R accept/reject the selected object, N advances, Ctrl+Z undoes and Ctrl+Y redoes. Choose Toggle mode for the earlier click behavior. Background never selects a neighbor.')
    client_key = 'review_client-' + key
    if client_key not in st.session_state:
        st.session_state[client_key] = uuid.uuid4().hex
    event = comparison_editor(result, review=review, context=review.context,
                         client_key=st.session_state[client_key], policy_key=review.context.rsplit(':',1)[-1] if filters else '',
                         geometry_key=str(review._mask_signature),
                         ack_token=st.session_state.get(ack_key),
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
            if event.get('action') == 'request_assets':
                if event.get('context') != review.context or not isinstance(event.get('token'),str):
                    raise ValueError('Refresh image layers on the current review.')
                st.session_state.pop('editor_assets_ack-' + asset_key, None)
                selected = None
            else:
                if event.get('asset_key') == asset_key:
                    known = event.get('known_sprites', [])
                    valid = {sprite['sprite_key'] for sprite in review.sprite_cache.values()}
                    st.session_state['editor_assets_ack-' + asset_key] = {
                        'sprites':[key for key in known if isinstance(key,str) and key in valid] if isinstance(known,list) else []}
                selected = review.handle_event(event, role)
            if event.get('action') in ('toggle', 'accept', 'reject', 'outline'):
                st.session_state['review-object-' + key] = int(selected)
            message = ('Image layers restored.' if event.get('action') == 'request_assets' else
                       'Correction saved.' if selected else 'Nothing to undo/redo.')
            # Preview the quality gate without another image/geometry build.
            if selected and event.get('action') == 'accept':
                row = result.table.loc[result.table.fiber_id.eq(selected)].iloc[0]
                if row.get('automatic_category') in ('too_small','ambiguous'):
                    message = 'Human acceptance saved; measurements remain excluded: ' + row.get('exclusion_reason','unreliable geometry')
            st.session_state[notice_key] = dict(level='success', text=message)
        except (ValueError, sqlite3.Error, OSError) as error:
            st.session_state[notice_key] = dict(level='warning', text=str(error))
        st.rerun()

    st.caption(f"Green: {result.summary['accepted_measurement_count']} selected measurable fibers · Red: {result.summary['review_required_count']} excluded detections. Automatic exclusions and manual rejections have separate categories and reasons in the table and CSV. Detections and raw measurements are retained.")
    st.caption('Black ID labels identify only measurable accepted fibers and match fiber_id in the CSV opened in Excel. Zoom for dense areas, or hide IDs to inspect boundaries. Click the colored fiber itself to change its review decision.')
    if result.summary.get('manual_accepted_but_ineligible_count',0):
        st.warning('Some objects you marked as real fibers still fail measurement eligibility and remain red. Inspect their exclusion reasons, adjust the selection thresholds, or outline a separated fiber. A review label cannot create missing or reliable geometry.')
    if result.summary['accepted_pending_measurement_count']:
        st.warning('Some accepted objects cannot provide reliable complete measurements. Their original QC flags are retained. Outline a separated individual fiber if appropriate; accepting a merged or truncated object does not reconstruct its missing centerline.')

    with st.expander('Review by object ID and save confirmations', expanded=False):
        ids = result.table.fiber_id.tolist()
        selected_key = 'review-object-' + key
        if st.session_state.get(selected_key) not in ids:
            st.session_state.pop(selected_key, None)
        selected = st.selectbox('Object ID', ids, key=selected_key, disabled=not ids)
        st.caption('Use these buttons to confirm an already correct decision as a reviewed training/evaluation example. A click toggle also saves a reviewed example. Each object is one detected mask; connected fibers may share a mask until individually outlined.')
        columns = st.columns(4)
        accept = columns[0].button('Mark accepted', disabled=not ids)
        reject = columns[1].button('Mark rejected', disabled=not ids)
        undo = columns[2].button('Undo last correction')
        redo = columns[3].button('Redo last correction')
        if accept or reject or undo or redo:
            try:
                if undo or redo:
                    changed = getattr(store,'undo' if undo else 'redo')(key, review.revision)
                else:
                    changed = review.correct(int(selected), 'accepted' if accept else 'rejected', role)
                st.session_state[notice_key] = dict(level='success', text='Correction saved.' if changed else 'Nothing to undo.')
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

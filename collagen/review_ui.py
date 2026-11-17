"""Streamlit orchestration for persistent human review and experimental learning."""
import sqlite3

import pandas as pd
import streamlit as st

from .editor import fiber_editor, image_url, original_preview
from .export import csv_bytes
from .feedback import FeedbackStore
from .learning import retrain
from .review import ReviewSession
from .visualization import count_overlay
from .id_labels import fiber_id_layer


def comparison_editor(result, **kwargs):
    """Keep both views tied to the exact source used by this analysis."""
    original_image, original_note = original_preview(result.original)
    return fiber_editor(original_image=original_image, original_note=original_note,
                        image=image_url(count_overlay(result.original, result.labels, result.table)),
                        labels_image=image_url(fiber_id_layer(result.labels, result.id_labels)),
                        fiber_id_labels=result.id_labels, **kwargs)


def reviewed_results(baseline, image_name, source_sha):
    try:
        store = FeedbackStore()
        review = ReviewSession(baseline, store, image_name, source_sha)
    except (OSError, sqlite3.Error, ValueError) as error:
        st.error(f'Manual review storage is unavailable: {error}. Baseline analysis remains available. Set COLLAGEN_FEEDBACK_DIR to a writable folder to enable saved review.')
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
    st.caption(f'Object classifier: {model_name}. Click an object to toggle acceptance; background clicks do not select a neighboring object. Use the outline tool to add a missed fiber or isolate one from a rejected merged object.')
    event = comparison_editor(result, context=review.context, ack_token=st.session_state.get(ack_key),
                         event_ok=notice.get('level') == 'success', message=notice.get('text', ''),
                         key='fiber-editor-' + key, default=None)
    if event is not None and not isinstance(event, dict):
        st.warning('The editor returned an invalid event. Reload the image before editing.')
        event = None
    if event is not None and event.get('token') != st.session_state.get(ack_key):
        if isinstance(event, dict):
            st.session_state[ack_key] = event.get('token')
        try:
            selected = review.handle_event(event, role)
            if event.get('action') in ('toggle', 'outline'):
                st.session_state['review-object-' + key] = int(selected)
            st.session_state[notice_key] = dict(level='success', text='Correction saved.' if selected else 'Nothing to undo.')
        except (ValueError, sqlite3.Error, OSError) as error:
            st.session_state[notice_key] = dict(level='warning', text=str(error))
        st.rerun()

    st.caption(f"Green: {result.summary['accepted_count']} accepted objects · Red: {result.summary['review_required_count']} rejected candidates · Measurable accepted fibers: {result.summary['accepted_measurement_count']}. Accepted objects with unavailable or partial geometry stay out of measurement statistics.")
    st.caption('Black ID labels identify only measurable accepted fibers and match fiber_id in the CSV opened in Excel. Zoom for dense areas, or hide IDs to inspect boundaries. Click the colored fiber itself to change its review decision.')
    if result.summary['accepted_pending_measurement_count']:
        st.warning('Some accepted objects cannot provide reliable complete measurements. Their original QC flags are retained. Outline a separated individual fiber if appropriate; accepting a merged or truncated object does not reconstruct its missing centerline.')

    with st.expander('Review by object ID and save confirmations', expanded=False):
        ids = result.table.fiber_id.tolist()
        selected_key = 'review-object-' + key
        if st.session_state.get(selected_key) not in ids:
            st.session_state.pop(selected_key, None)
        selected = st.selectbox('Object ID', ids, key=selected_key, disabled=not ids)
        st.caption('Use these buttons to confirm an already correct decision as a reviewed training/evaluation example. A click toggle also saves a reviewed example. Each object is one detected mask; connected fibers may share a mask until individually outlined.')
        columns = st.columns(3)
        accept = columns[0].button('Mark accepted', disabled=not ids)
        reject = columns[1].button('Mark rejected', disabled=not ids)
        undo = columns[2].button('Undo last correction')
        if accept or reject or undo:
            try:
                if undo:
                    changed = store.undo(key, review.revision)
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

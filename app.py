import hashlib
import pandas as pd
import streamlit as st
from skimage.color import label2rgb
from collagen.preprocessing import normalize
from collagen.loading import load_image
from collagen.pipeline import analyze_pixels, calibrate_analysis
from collagen.segmentation import ThresholdSegmenter
from collagen.exclusions import suggest_scale_bar_region, validate_regions
from collagen.export import csv_bytes, result_archive
from collagen.validation import comparison_report
from collagen.workbook import import_workbook, workbook_summary, image_manifest
from collagen.resources import asset_path
from collagen.visualization import count_overlay, display_rgb


@st.cache_data(show_spinner=False, max_entries=2)
def load_preview(data, filename):
    image = load_image(data, filename)
    return image, suggest_scale_bar_region(image)


@st.cache_data(show_spinner=False, max_entries=2)
def cached_pixels(data, filename, settings):
    """Cache full-resolution candidates and their detailed decision annotation."""
    image, _ = load_preview(data, filename)
    return analyze_pixels(image, filename, ThresholdSegmenter(**settings['segmentation']),
                          settings['sigma'], settings['min_area'], settings['exclusion_px'],
                          settings['exclusion_regions'])


st.set_page_config(page_title='Collagen Fiber Analyzer', page_icon=str(asset_path('collagen.png')), layout='wide')
st.title('Collagen Fiber Analyzer')
st.caption('Non-AI research baseline. Distance-transform width is approximate. Scientific accuracy on real microscopy images has not been established.')
st.caption('Use originals. Review unclear/bunched candidates. Analysis runs only when you press Analyze image; results reflect the last submitted settings.')
upload = st.file_uploader('Microscopy image (single plane)', type=['tif', 'tiff', 'png', 'jpg', 'jpeg'])
image, proposal, data, image_key = None, None, None, None
if upload is not None:
    data = upload.getvalue()
    image_key = hashlib.sha256(data).hexdigest() + ':' + upload.name
    try:
        image, proposal = load_preview(data, upload.name)
    except ValueError as error:
        st.error(str(error))
if st.session_state.get('request', {}).get('image_key') != image_key:
    st.session_state.pop('request', None)
    st.session_state.pop('archive', None)
    st.session_state.pop('fiber_details', None)
with st.sidebar:
    with st.form('analysis_controls'):
        calibration = st.number_input('Calibration (µm/pixel)', min_value=0.000001, value=None,
                                      format='%.6f', key='calibration-' + str(image_key))
        st.caption('Required for this original image. Read the verified value from ImageJ Set Scale; screen DPI and resized references are not calibration.')
        method = st.selectbox('Threshold', ['otsu', 'manual', 'local'])
        bright = st.checkbox('Bright fibers on dark background', value=False)
        threshold = st.slider('Manual threshold (normalized intensity)', 0.0, 1.0, 0.5)
        block = st.number_input('Local threshold block size (odd)', 3, 501, 35, 2)
        sigma = st.number_input('Gaussian sigma (pixels)', 0.0, 10.0, 0.0)
        opening = st.number_input('Opening radius (pixels)', 0, 10, 0)
        closing = st.number_input('Closing radius (pixels)', 0, 10, 0)
        min_area = st.number_input('Minimum accepted area (pixels)', 1, value=20)
        exclusion = st.number_input('Endpoint/junction exclusion (pixels)', 0.0, value=2.0)
        exclude_region = False
        scale_review = 'no_proposal'
        rectangle = None
        if image is not None:
            height, width = image.shape[:2]
            bounds = proposal or (0, 0, 0, 0)
            st.caption('Exclusion coordinates use original pixels: left/top inclusive, right/bottom exclusive. Intersecting objects stay in QC outputs but cannot be accepted.')
            if proposal is not None:
                with st.expander('Scale-bar region preview', expanded=False):
                    x0, y0, x1, y1 = proposal
                    st.image(normalize(image[y0:y1, x0:x1]), caption='Check the proposed exclusion region against the original.')
                scale_review = st.selectbox('Scale-bar review', ['Review required', 'Exclude rectangle', 'No exclusion needed (reviewed)'], key='review-' + image_key)
                exclude_region = scale_review == 'Exclude rectangle'
            else:
                exclude_region = st.checkbox('Confirm exclusion rectangle', value=False, key='exclude-' + image_key)
            left = st.number_input('Exclusion left', 0, width, bounds[0], key='left-' + image_key)
            top = st.number_input('Exclusion top', 0, height, bounds[1], key='top-' + image_key)
            right = st.number_input('Exclusion right', 0, width, bounds[2], key='right-' + image_key)
            bottom = st.number_input('Exclusion bottom', 0, height, bounds[3], key='bottom-' + image_key)
            rectangle = (left, top, right, bottom)
        submitted = st.form_submit_button('Analyze image')

if submitted:
    try:
        if image is None:
            raise ValueError('Choose a valid image before analyzing.')
        if calibration is None:
            raise ValueError('Enter the verified calibration in µm/pixel before analyzing.')
        if scale_review == 'Review required':
            raise ValueError('Review the proposed scale-bar region: exclude its rectangle or explicitly confirm no exclusion is needed.')
        regions = validate_regions([rectangle] if exclude_region else (), image.shape)
        settings = dict(segmentation=dict(method=method, bright=bright, threshold=threshold,
                        block_size=int(block), opening_radius=int(opening), closing_radius=int(closing)),
                        sigma=sigma, min_area=int(min_area), exclusion_px=exclusion,
                        exclusion_regions=regions)
        request = dict(image_key=image_key, calibration=calibration, settings=settings,
                       scale_bar_review=scale_review)
        if st.session_state.get('request') != request:
            st.session_state.pop('archive', None)
        st.session_state['request'] = request
    except ValueError as error:
        st.session_state.pop('request', None)
        st.session_state.pop('archive', None)
        st.error(str(error))

request = st.session_state.get('request')
if request is not None and image is not None and request['image_key'] == image_key:
    try:
        with st.spinner('Analyzing image…'):
            pixels = cached_pixels(data, upload.name, request['settings'])
            result = calibrate_analysis(pixels, request['calibration'])
        result.parameters.update(source_sha256=hashlib.sha256(data).hexdigest(),
                                 calibration_source='entered by operator for this original image',
                                 scale_bar_review=request['scale_bar_review'])
        st.json(result.summary)
        st.image(count_overlay(result.original, result.labels, result.table),
                 caption='Fiber counting results: green = included in the final count; red = identified candidates excluded from the final count.')
        st.caption(f"Green: {result.summary['accepted_isolated_count']} counted fibers · Red: {result.summary['review_required_count']} excluded candidates. Colors follow the API counting decisions. Unlabeled areas remain unchanged; they are not automatically excluded fibers.")
        details = st.expander('View Detailed Fiber Analysis', expanded=False,
                              key='fiber_details', on_change='rerun')
        if details.open:
            with details:
                cols = st.columns(2)
                cols[0].image(display_rgb(image), caption='Original (unchanged resolution)')
                cols[1].image(result.mask.astype('uint8') * 255, caption='Segmentation mask (excluded candidates retained)')
                cols[0].image(label2rgb(result.labels, bg_label=0), caption='Labeled candidates')
                cols[1].image(result.annotated, caption='Detailed IDs and centerlines: green counted; red excluded; rectangles mark excluded regions')
        st.dataframe(result.table, hide_index=True)
        st.download_button('Download per-fiber CSV', csv_bytes(result.table), 'fibers.csv', 'text/csv')
        if st.button('Prepare QC archive'):
            with st.spinner('Preparing QC archive…'):
                st.session_state['archive'] = result_archive(result)
        if 'archive' in st.session_state:
            st.download_button('Download all QC outputs', st.session_state['archive'], 'analysis.zip', 'application/zip')
        manual = st.file_uploader('Optional ImageJ ground-truth CSV (match IDs explicitly)', type='csv')
        if manual:
            report = comparison_report(result.table, pd.read_csv(manual))
            st.subheader('ImageJ comparison')
            st.json(report.counts)
            st.caption('Errors use accepted matched fibers with both measurements present, separately for length and diameter. Bias = mean(automated − manual); MAE = mean absolute error, in µm. Empty statistics mean no usable pairs. Unmatched IDs describe coverage, not detection accuracy.')
            st.dataframe(report.metrics, hide_index=True)
            st.dataframe(report.rows, hide_index=True)
            st.download_button('Download comparison', csv_bytes(report.rows), 'comparison.csv', 'text/csv')
            st.download_button('Download error statistics', csv_bytes(report.metrics), 'comparison_statistics.csv', 'text/csv')
            st.download_button('Download match counts', csv_bytes(pd.DataFrame([report.counts])), 'comparison_counts.csv', 'text/csv')
    except (ValueError, TypeError) as error:
        st.session_state.pop('archive', None)
        st.error(str(error))

with st.expander('Review ImageJ Excel workbook'):
    workbook = st.file_uploader('Bowlin measurement workbook', type='xlsx')
    if workbook:
        try:
            records = import_workbook(workbook.getvalue())
            st.caption('Numbered per-image measurements only; pooled copies excluded. Workbook units and length/diameter correspondence still need confirmation.')
            st.dataframe(workbook_summary(records), hide_index=True)
            st.dataframe(records, hide_index=True)
            st.download_button('Download normalized manual measurements', csv_bytes(records), 'manual_measurements.csv', 'text/csv')
            st.download_button('Download image mapping template', csv_bytes(image_manifest(records)), 'image_mapping.csv', 'text/csv')
            st.info('Fill the template with original filenames and calibration. Check manual ROI correspondence explicitly before comparing with automated IDs.')
        except ValueError as error:
            st.error(str(error))

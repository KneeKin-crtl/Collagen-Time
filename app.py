import hashlib
import pandas as pd
import streamlit as st
from skimage.color import label2rgb
from collagen.preprocessing import normalize
from collagen.loading import load_image
from collagen.pipeline import analyze_pixels, calibrate_analysis, ensure_annotation
from collagen.segmentation import ThresholdSegmenter
from collagen.exclusions import suggest_scale_bar_region, validate_regions
from collagen.export import csv_bytes, result_archive
from collagen.validation import comparison_report
from collagen.workbook import import_workbook, workbook_summary, image_manifest
from collagen.resources import asset_path
from collagen.visualization import count_overlay, display_rgb
from collagen.review_ui import reviewed_results
from collagen.selection import SelectionFilters
from collagen.ui import laboratory_header, empty_workspace, measurement_table


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
laboratory_header()
with st.container(key='scientific-note'):
    st.caption('Conventional segmentation with optional feedback-trained object classification. Distance-transform width is approximate. Scientific accuracy on real microscopy images has not been established.')
st.caption('Use originals. Review unclear/bunched candidates. Analysis runs only when you press Analyze image; results reflect the last submitted settings.')
with st.container(key='image-upload'):
    st.markdown('<div class="lab-kicker">Image input</div>', unsafe_allow_html=True)
    upload = st.file_uploader('Microscopy image (single plane)', type=['tif', 'tiff', 'png', 'jpg', 'jpeg'])
    image, proposal, data, image_key = None, None, None, None
    if upload is not None:
        data = upload.getvalue()
        image_key = hashlib.sha256(data).hexdigest() + ':' + upload.name
        try:
            image, proposal = load_preview(data, upload.name)
            with st.container(key='loaded-image-' + image_key.split(':',1)[0]):
                st.caption(f'Loaded image: {upload.name} · {image.shape[1]} × {image.shape[0]} pixels')
        except ValueError as error:
            st.error(str(error))
if st.session_state.get('request', {}).get('image_key') != image_key:
    st.session_state.pop('request', None)
    st.session_state.pop('archive', None)
    st.session_state.pop('fiber_details', None)
with st.sidebar:
    st.subheader('Processing settings')
    st.caption('Configure the original image, then submit analysis.')
    with st.form('analysis_controls'):
        with st.expander('Image Calibration', expanded=True):
            calibration = st.number_input('Calibration (µm/pixel)', min_value=0.000001, value=None,
                                          format='%.6f', key='calibration-' + str(image_key))
            st.caption('Required for this original image. Read the verified value from ImageJ Set Scale; screen DPI and resized references are not calibration.')
        with st.expander('Thresholding', expanded=True):
            method = st.selectbox('Threshold', ['otsu', 'manual', 'local'])
            bright = st.checkbox('Bright fibers on dark background', value=False)
            threshold = st.slider('Manual threshold (normalized intensity)', 0.0, 1.0, 0.5)
            block = st.number_input('Local threshold block size (odd)', 3, 501, 35, 2)
            sigma = st.number_input('Gaussian sigma (pixels)', 0.0, 10.0, 0.0)
        with st.expander('Morphological Processing', expanded=False):
            opening = st.number_input('Opening radius (pixels)', 0, 10, 0)
            closing = st.number_input('Closing radius (pixels)', 0, 10, 0)
            min_area = st.number_input('Minimum accepted area (pixels)', 1, value=20)
            exclusion = st.number_input('Endpoint/junction exclusion (pixels)', 0.0, value=2.0)
        with st.expander('Fiber selection and measurement eligibility', expanded=False):
            research_range = st.checkbox('Enable research-interest diameter range', value=False)
            min_diameter_nm = st.number_input('Minimum fiber diameter (nm)', min_value=0., value=150.)
            max_diameter_nm = st.number_input('Maximum fiber diameter (nm)', min_value=0., value=335.)
            min_length_um = st.number_input('Minimum fiber length (µm)', min_value=0., value=0.)
            min_area_um2 = st.number_input('Minimum component area (µm²)', min_value=0., value=0., format='%.6f')
            min_width_px = st.number_input('Minimum reliable mean width (pixels)', min_value=0., value=3.)
            exclude_border = st.checkbox('Exclude border-truncated fibers from measurements', value=True)
            crossing_policy = st.selectbox('Crossing / merged candidates', ['review','exclude'],
                format_func=lambda value: 'Require manual review' if value == 'review' else 'Automatically exclude; retain for audit')
            st.caption('Filters retain every detection. Diameter range is optional and is not a definition of a valid fiber. Area uses µm², not diameter. Width screening uses mean distance-transform diameter / calibration; it does not prove optical resolution. Unchecking border exclusion permits flagged partial lengths. Crossing/merged objects with missing geometry cannot contribute measurements.')
        with st.expander('Advanced Settings — Image Regions', expanded=True):
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
        filters = SelectionFilters(research_range_enabled=research_range, min_diameter_nm=min_diameter_nm,
                    max_diameter_nm=max_diameter_nm, min_length_um=min_length_um, min_area_um2=min_area_um2,
                    min_width_px=min_width_px, exclude_border=exclude_border, crossing_policy=crossing_policy)
        request = dict(image_key=image_key, calibration=calibration, settings=settings,
                       scale_bar_review=scale_review, selection_filters=filters.parameters())
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
        baseline_request = {k:request[k] for k in ('image_key','calibration','settings')}
        if st.session_state.get('_baseline_request') != baseline_request:
            with st.spinner('Analyzing image…'):
                pixels = cached_pixels(data, upload.name, request['settings'])
                st.session_state['_baseline'] = calibrate_analysis(pixels, request['calibration'], render=False)
                st.session_state['_baseline_request'] = baseline_request
        result = st.session_state['_baseline']
        result.parameters.update(source_sha256=hashlib.sha256(data).hexdigest(),
                                 calibration_source='entered by operator for this original image',
                                 scale_bar_review=request['scale_bar_review'])
        filters = SelectionFilters(**request.get('selection_filters', {}))
        if filters.research_range_enabled and filters.min_diameter_nm / (request['calibration']*1000) < filters.min_width_px:
            st.warning('Part of the selected diameter range is below your configured reliable mean width in pixels. These dimensions may be unresolved; a range filter cannot establish measurement accuracy.')
        result = reviewed_results(result, upload.name, hashlib.sha256(data).hexdigest(), filters=filters)
        details = st.expander('View Detailed Fiber Analysis', expanded=False,
                              key='fiber_details', on_change='rerun')
        if details.open:
            with details:
                cols = st.columns(2)
                cols[0].image(display_rgb(image), caption='Original (unchanged resolution)')
                cols[1].image(result.mask.astype('uint8') * 255, caption='Segmentation mask (excluded candidates retained)')
                cols[0].image(label2rgb(result.labels, bg_label=0), caption='Labeled candidates')
                cols[1].image(ensure_annotation(result), caption='Measured accepted fiber IDs and centerlines; rejected or unmeasurable objects have no ID label')
        with st.container(key='measurement-results'):
            st.subheader('Measurements & results')
            measurement_table(result.table)
            st.download_button('Download per-fiber CSV', csv_bytes(result.table), 'fibers.csv', 'text/csv')
            if 'included_in_measurement_statistics' in result.table:
                st.download_button('Download measurable accepted fibers CSV',
                                   csv_bytes(result.table[result.table.included_in_measurement_statistics.astype(bool)]),
                                   'accepted_measurements.csv', 'text/csv')
            if st.button('Prepare QC archive'):
                with st.spinner('Preparing QC archive…'):
                    st.session_state['archive'] = result_archive(result)
            if 'archive' in st.session_state:
                st.download_button('Download all QC outputs', st.session_state['archive'], 'analysis.zip', 'application/zip')
            with st.expander('Compare with ImageJ ground truth', expanded=False):
                manual = st.file_uploader('Optional ImageJ ground-truth CSV (match IDs explicitly)', type='csv')
                if manual:
                    report = comparison_report(result.table, pd.read_csv(manual))
                    st.subheader('ImageJ comparison')
                    st.json(report.counts)
                    st.caption('Errors use measurable accepted matched fibers with both measurements present, separately for length and diameter. Manual acceptance does not bypass incomplete-geometry exclusions. Bias = mean(automated − manual); MAE = mean absolute error, in µm. Empty statistics mean no usable pairs. Unmatched IDs describe coverage, not detection accuracy.')
                    st.dataframe(report.metrics, hide_index=True)
                    st.dataframe(report.rows, hide_index=True)
                    st.download_button('Download comparison', csv_bytes(report.rows), 'comparison.csv', 'text/csv')
                    st.download_button('Download error statistics', csv_bytes(report.metrics), 'comparison_statistics.csv', 'text/csv')
                    st.download_button('Download match counts', csv_bytes(pd.DataFrame([report.counts])), 'comparison_counts.csv', 'text/csv')
    except (ValueError, TypeError) as error:
        st.session_state.pop('archive', None)
        st.error(str(error))

if request is None:
    empty_workspace()

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

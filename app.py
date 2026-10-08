import pandas as pd
import streamlit as st
from skimage.color import label2rgb
from collagen.preprocessing import normalize
from collagen.loading import load_image
from collagen.pipeline import analyze
from collagen.segmentation import ThresholdSegmenter
from collagen.export import csv_bytes, result_archive
from collagen.validation import compare_ground_truth

st.set_page_config(page_title="Collagen Fiber Analyzer", layout="wide")
st.title("Collagen Fiber Analyzer")
st.caption("Non-AI research baseline. Distance-transform width approximates perpendicular edge-to-edge width. Scientific accuracy has not been validated on real microscopy images.")
upload = st.file_uploader("Microscopy image (single plane)", type=["tif", "tiff", "png", "jpg", "jpeg"])
with st.sidebar:
    calibration = st.number_input("Calibration (µm/pixel)", min_value=0.000001, value=1.0, format="%.6f")
    method = st.selectbox("Threshold", ["otsu", "manual", "local"])
    bright = st.checkbox("Bright fibers on dark background", value=True)
    threshold = st.slider("Manual threshold (normalized intensity)", 0.0, 1.0, 0.5)
    block = st.number_input("Local threshold block size (odd)", 3, 501, 35, 2)
    sigma = st.number_input("Gaussian sigma (pixels)", 0.0, 10.0, 0.0)
    opening = st.number_input("Opening radius (pixels)", 0, 10, 0)
    closing = st.number_input("Closing radius (pixels)", 0, 10, 0)
    min_area = st.number_input("Minimum accepted area (pixels)", 1, value=20)
    exclusion = st.number_input("Endpoint/junction exclusion (pixels)", 0.0, value=2.0)
if upload:
    try:
        image = load_image(upload.getvalue(), upload.name)
        result = analyze(image, calibration, upload.name, ThresholdSegmenter(method, bright, threshold, int(block), int(opening), int(closing)), sigma, int(min_area), exclusion)
        st.json(result.summary)
        cols = st.columns(2)
        cols[0].image(normalize(image), caption="Original (unchanged resolution)", clamp=True)
        cols[1].image(result.mask.astype("uint8") * 255, caption="Segmentation mask")
        cols[0].image(label2rgb(result.labels, bg_label=0), caption="Labeled candidates")
        cols[1].image(result.annotated, caption="IDs and centerlines: green accepted; orange requires review")
        st.dataframe(result.table, hide_index=True)
        st.download_button("Download per-fiber CSV", csv_bytes(result.table), "fibers.csv", "text/csv")
        st.download_button("Download all QC outputs", result_archive(result), "analysis.zip", "application/zip")
        manual = st.file_uploader("Optional ImageJ ground-truth CSV (match IDs explicitly)", type="csv")
        if manual:
            comparison = compare_ground_truth(result.table, pd.read_csv(manual))
            st.dataframe(comparison)
            st.download_button("Download comparison", csv_bytes(comparison), "comparison.csv", "text/csv")
    except (ValueError, TypeError) as error:
        st.error(str(error))

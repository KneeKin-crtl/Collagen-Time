"""Presentation helpers only; all displayed counts come from analysis summaries."""
import base64
from functools import lru_cache
import html

import streamlit as st

from .resources import asset_path


@lru_cache(maxsize=1)
def style_sheet():
    return (asset_path('fiber_editor/lab-theme.css').read_text(encoding='utf-8')+'\n'
            +asset_path('lab-ui.css').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def logo_url():
    return 'data:image/png;base64,'+base64.b64encode(asset_path('collagen.png').read_bytes()).decode('ascii')


def laboratory_header():
    st.html('<style>'+style_sheet()+'</style>')
    with st.container(key='lab-header'):
        st.html(f'<header class="lab-header"><img class="lab-logo" src="{logo_url()}" '
                'alt="Collagen Fiber Analyzer custom logo" width="42" height="42">'
                '<div><h1>Collagen Fiber Analyzer</h1><p>Microscopy Review &amp; Measurement</p></div></header>')


def summary_cards(summary):
    # These are existing saved-result fields, not new presentation calculations.
    cards=(('candidate_count','Total detections','', '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>'),
           ('manual_reviewed_count','Reviewed objects','', '<path d="m4 12 5 5L20 6"/>'),
           ('accepted_measurement_count','Measured fibers','accepted', '<circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/>'),
           ('review_required_count','Excluded / review','excluded', '<circle cx="12" cy="12" r="9"/><path d="m8 8 8 8m0-8-8 8"/>'))
    items=[]
    for field,label,kind,icon in cards:
        if field not in summary:
            continue
        value=html.escape(str(summary[field]))
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#64748B" stroke-width="1.5">{icon}</svg>'
        url='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode('ascii')
        items.append(f'<div class="{kind}" data-summary-field="{field}"><dt><img src="{url}" alt="" aria-hidden="true">{label}</dt><dd>{value}</dd></div>')
    st.html('<dl class="lab-summary" aria-label="Detection and review summary">'+''.join(items)+'</dl>')


def empty_workspace():
    st.html('<section class="lab-empty" aria-label="Getting started"><div class="lab-kicker">Image workspace</div>'
            '<h2>Ready for your next microscope image</h2>'
            '<p>Use an original image and its verified calibration to begin.</p>'
            '<ol><li>Upload an image</li><li>Set calibration and processing controls</li>'
            '<li>Analyze, review and export</li></ol></section>')


def measurement_table(table):
    """Order and label existing columns without changing table/export data."""
    first=['fiber_id','length_um','mean_diameter_um','diameter_sd_um','area_um2',
           'review_approved','review_status','included_in_measurement_statistics','exclusion_reason']
    order=[name for name in first if name in table]+[name for name in table if name not in first]
    st.dataframe(table,hide_index=True,column_order=order,column_config={
        'fiber_id':st.column_config.NumberColumn('Fiber ID'),
        'length_um':st.column_config.NumberColumn('Length (µm)'),
        'mean_diameter_um':st.column_config.NumberColumn('Mean diameter (µm, approx.)'),
        'diameter_sd_um':st.column_config.NumberColumn('Diameter SD (µm)'),
        'area_um2':st.column_config.NumberColumn('Area (µm²)'),
        'review_approved':st.column_config.CheckboxColumn('Review approved (green)'),
        'included_in_measurement_statistics':st.column_config.CheckboxColumn('Included in measurements'),
    })

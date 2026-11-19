import json
import numpy as np


def approval_block_details(row):
    """Explain an existing approval gate; never change selection or geometry."""
    if row.get('automatic_category') not in ('too_small', 'ambiguous'):
        return dict(message='', can_outline=False)
    reasons=set(filter(None,str(row.get('automatic_exclusion_reason','')).split(';')))
    flags=set(filter(None,str(row.get('qc_flags','')).split(';')))
    parts=[]
    geometry_problem='ambiguous_or_unavailable_geometry' in reasons or 'no_reliable_diameter_samples' in reasons
    if row.get('crossing_merge',False):
        parts.append('The detected mask has crossing or branching geometry.')
    elif 'non_simple_centerline' in flags:
        parts.append('The detected mask is not one open centerline.')
    if geometry_problem:
        try:
            endpoints=json.loads(row.get('endpoints'))
        except (TypeError,ValueError):
            endpoints=None
        if isinstance(endpoints,list) and len(endpoints)!=2:
            parts.append(f'{len(endpoints)} endpoints were detected; one isolated fiber needs two.')
        missing=[name for name,key in [('Length','length_um'),('Diameter','mean_diameter_um')]
                 if not np.isfinite(row.get(key,np.nan)) or row.get(key,0)<=0]
        if missing:
            parts.append(' / '.join(missing)+' unavailable.')
        if row.get('diameter_samples')==0 or 'no_reliable_diameter' in flags:
            parts.append('No reliable diameter samples are available.')
    if 'excluded_region' in reasons:
        parts.append('The mask intersects an explicitly excluded image region.')
    if 'border_truncated' in reasons:
        parts.append('The mask touches the image border and border exclusion is enabled.')
    if 'superseded_by_manual_outline' in reasons:
        parts.append('A newer manual outline owns this region.')
    if not parts:
        parts.append('Measurement screening blocks inclusion: '+(', '.join(sorted(reasons)) or 'review required')+'.')
    can_outline=geometry_problem and not reasons.intersection(
        {'excluded_region','border_truncated','superseded_by_manual_outline'})
    if can_outline:
        parts.append('Use Outline selected fiber to trace one separated fiber boundary; the original detection stays in the audit history.')
    else:
        parts.append('Review the exclusion settings or the detected boundary before including measurements.')
    return dict(message=' '.join(parts),can_outline=bool(can_outline))


def counted_candidates(table):
    """The counting decision shared by summary statistics and visualization."""
    return table['review_status'].eq('accepted')


def review_approved_candidates(table):
    """Saved review approval for mask color, independent of measurement gates.

    Explicit human labels win over the automatic decision. Superseded/hidden
    masks stay out of the current review layer while retaining their history.
    Never use this flag to select numerical measurement aggregates.
    """
    approved=counted_candidates(table).copy()
    if 'manual_classification' in table:
        manual=table.manual_classification
        approved=manual.eq('accepted').fillna(False) | (manual.isna() & approved)
    if 'qc_flags' in table:
        approved &= ~table.qc_flags.fillna('').astype(str).str.contains(r'(?:^|;)superseded_by_manual_outline(?:;|$)',regex=True)
    if 'visible_in_overlay' in table:
        approved &= table.visible_in_overlay.fillna(False).astype(bool)
    return approved.astype(bool)


def classify(measurement, border, area, min_area):
    flags = []
    if area < min_area:
        flags.append("below_min_area")
    if border:
        flags.append("border_truncated")
    if measurement["junctions"]:
        flags.append("crossing_or_merge")
    elif not measurement["isolated"]:
        flags.append("non_simple_centerline")
    if measurement["diameter_samples"] == 0:
        flags.append("no_reliable_diameter")
    return flags, "review_required" if flags else "accepted"

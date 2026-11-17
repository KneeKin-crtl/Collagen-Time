"""Calibrated selection policy over retained measurements; never edits geometry."""
from dataclasses import asdict, dataclass, replace
import json

import numpy as np
import pandas as pd


CATEGORIES = ('accepted', 'rejected', 'out_of_range', 'too_small', 'ambiguous', 'noise')


@dataclass(frozen=True)
class SelectionFilters:
    research_range_enabled: bool = False
    min_diameter_nm: float = 150.
    max_diameter_nm: float = 335.
    min_length_um: float = 0.
    min_area_um2: float = 0.
    min_width_px: float = 3.
    exclude_border: bool = True
    crossing_policy: str = 'review'

    def __post_init__(self):
        if not isinstance(self.research_range_enabled,bool) or not isinstance(self.exclude_border,bool):
            raise ValueError('Range and border options must be true or false.')
        for name in ('min_diameter_nm', 'max_diameter_nm', 'min_length_um', 'min_area_um2', 'min_width_px'):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative.')
        if self.max_diameter_nm < self.min_diameter_nm:
            raise ValueError('Maximum diameter must be at least the minimum diameter.')
        if self.crossing_policy not in ('review', 'exclude'):
            raise ValueError('Crossing policy must be review or exclude.')

    def parameters(self):
        return asdict(self)


def _below(value, threshold):
    # Inclusive boundaries, tolerant only of floating-point conversion rounding.
    return value < threshold and not np.isclose(value, threshold, rtol=1e-12, atol=0.)


def apply_selection(table, filters):
    """Keep every row and every raw measurement, adding auditable eligibility.

    Width reliability uses the *mean retained distance-transform diameter* in
    pixels, not component area or an optical resolution claim. A manual accept
    may override a research range/model suggestion, but cannot create geometry
    or bypass configured reliability/size/border/exclusion gates.
    """
    if not isinstance(filters, SelectionFilters):
        raise ValueError('Use validated SelectionFilters.')
    result = table.copy(deep=True)
    payload = json.dumps(filters.parameters(), sort_keys=True, allow_nan=False)
    extra = []
    for row in result.to_dict('records'):
        calibration = row['calibration_um_per_px']
        if not np.isfinite(calibration) or calibration <= 0:
            raise ValueError('Selection requires a verified positive µm/pixel calibration.')
        length, diameter = row['length_um'], row['mean_diameter_um']
        area = row['area_px'] * calibration**2
        width = diameter / calibration
        flags = list(filter(None, str(row.get('qc_flags', '')).split(';')))
        reasons, hard = [], []
        category = 'accepted'
        finite = np.isfinite([length, diameter]).all() and length > 0 and diameter > 0
        if row.get('excluded_region', False):
            hard.append('excluded_region')
        if 'superseded_by_manual_outline' in flags:
            hard.append('superseded_by_manual_outline')
        if row.get('crossing_merge', False) or not finite or 'non_simple_centerline' in flags:
            hard.append('ambiguous_or_unavailable_geometry')
        if row.get('border_touch', False) and filters.exclude_border:
            hard.append('border_truncated')
        if hard:
            category, reasons = 'ambiguous', hard.copy()
            if (np.isfinite(length) and length > 0 and not np.isfinite(diameter)
                    and 'no_reliable_diameter' in flags and not row.get('crossing_merge',False)
                    and 'non_simple_centerline' not in flags and not row.get('border_touch',False)
                    and not row.get('excluded_region',False) and 'superseded_by_manual_outline' not in flags):
                category='too_small'
                reasons=['no_reliable_diameter_samples']
        else:
            if 'below_min_area' in flags:
                reasons.append('below_baseline_min_area_px')
            if _below(area, filters.min_area_um2):
                reasons.append('below_min_area_um2')
            if _below(length, filters.min_length_um):
                reasons.append('below_min_length_um')
            if _below(width, filters.min_width_px):
                reasons.append('below_reliable_mean_width_px')
            if reasons:
                category = 'too_small'
                hard.extend(reasons)
            elif filters.research_range_enabled and (
                    _below(diameter * 1000, filters.min_diameter_nm)
                    or _below(filters.max_diameter_nm, diameter * 1000)):
                category, reasons = 'out_of_range', ['outside_research_diameter_range_nm']
            elif row.get('automatic_classification') == 'rejected':
                if row.get('decision_source') == 'model' or row.get('model_version', 'baseline') != 'baseline':
                    category, reasons = 'noise', ['classifier_non_fiber_suggestion_unvalidated']
                elif not (row.get('border_touch', False) and not filters.exclude_border
                          and set(flags) <= {'border_truncated'}):
                    category, reasons = 'ambiguous', ['baseline_review_required']
                    hard.extend(reasons)
        automatic_category, automatic_reasons = category, reasons.copy()
        manual = row.get('manual_classification')
        override = ''
        if manual == 'rejected':
            category, reasons = 'rejected', ['manual_rejection']
        elif manual == 'accepted' and not hard:
            category, reasons = 'accepted', []
            if automatic_category != 'accepted':
                override = 'manual_override_' + automatic_category
        accepted = category == 'accepted'
        quality = list(dict.fromkeys(flags + automatic_reasons + ([override] if override else [])))
        if row.get('border_touch', False) and not filters.exclude_border:
            quality.append('partial_length_border_included_by_policy')
        requires_review = category == 'ambiguous' and not (
            row.get('crossing_merge', False) and filters.crossing_policy == 'exclude')
        if manual == 'accepted' and not accepted:
            requires_review = True
        extra.append(dict(selection_category=category, automatic_category=automatic_category,
                          automatic_exclusion_reason=';'.join(automatic_reasons),
                          exclusion_reason=';'.join(reasons), quality_flags=';'.join(dict.fromkeys(quality)),
                          selection_override=override, area_um2=float(area), mean_width_px=float(width),
                          mean_diameter_nm=float(diameter * 1000),
                          diameter_sd_nm=float(row['diameter_sd_um'] * 1000),
                          filter_thresholds=payload, requires_manual_review=requires_review,
                          classification='accepted' if accepted else 'rejected',
                          review_status='accepted' if accepted else 'review_required',
                          included_in_measurement_statistics=accepted,
                          measurement_status='unavailable' if not finite else 'partial_or_flagged'
                          if hard or row.get('border_touch', False) else 'measurable'))
    for name in ('selection_category', 'automatic_category', 'automatic_exclusion_reason', 'exclusion_reason',
                 'quality_flags', 'selection_override', 'area_um2', 'mean_width_px', 'mean_diameter_nm',
                 'diameter_sd_nm', 'filter_thresholds', 'requires_manual_review', 'classification',
                 'review_status', 'included_in_measurement_statistics', 'measurement_status'):
        result[name] = pd.Series([row[name] for row in extra], index=result.index,
                                 dtype=bool if name in ('requires_manual_review', 'included_in_measurement_statistics') else None)
    return result


def select_baseline(baseline, filters):
    """Apply the same selection gates when persistent review is unavailable."""
    table=baseline.table.copy()
    table['automatic_classification']=np.where(table.review_status.eq('accepted'),'accepted','rejected')
    table['manual_classification']=None
    table['decision_source']='baseline'
    table['model_version']='baseline'
    table=apply_selection(table,filters)
    selected=table[table.included_in_measurement_statistics]
    summary={**baseline.summary,'accepted_count':len(selected),'accepted_measurement_count':len(selected),
             'accepted_isolated_count':int((~selected.border_touch.astype(bool)).sum()),
             'accepted_pending_measurement_count':0,'review_required_count':len(table)-len(selected),
             'manual_reviewed_count':0,'accepted_mean_length_um':float(selected.length_um.mean()),
             'accepted_mean_diameter_um':float(selected.mean_diameter_um.mean()),
             'selection_categories':{category:int(table.selection_category.eq(category).sum()) for category in CATEGORIES}}
    return replace(baseline,table=table,summary=summary,annotated=None,id_labels=None,
                   parameters={**baseline.parameters,'selection_filters':filters.parameters()})

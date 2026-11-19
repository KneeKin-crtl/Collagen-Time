from dataclasses import dataclass

import numpy as np
import pandas as pd
from .scientific_metrics import agreement_statistics

KEYS = ["image_id", "fiber_id"]
METRICS = ["length_um", "mean_diameter_um"]


def compare_ground_truth(measured, manual):
    """Match IDs, retaining every candidate; compute errors for accepted pairs only.

    Missing measurements remain missing. Acceptance comes from the analysis CSV,
    not from the manual table. Neither input is modified.
    """
    required = KEYS + METRICS
    tables = []
    for table in (measured, manual):
        if not set(required) <= set(table.columns):
            raise ValueError("Both CSVs need image_id, fiber_id, length_um, mean_diameter_um.")
        if table[KEYS].isna().any().any() or table.duplicated(KEYS).any():
            raise ValueError("Image/fiber IDs must be present and unique.")
        table = table.copy()
        for metric in METRICS:
            values = pd.to_numeric(table[metric], errors="raise").astype(float)
            if ((values.dropna() <= 0) | ~np.isfinite(values.dropna())).any():
                raise ValueError("Measurements must be positive finite values or missing.")
            table[metric] = values
        tables.append(table)
    measured, manual = tables
    if "review_status" not in measured or not measured.review_status.isin(["accepted", "review_required"]).all():
        raise ValueError("Analysis CSV needs review_status: accepted or review_required for each candidate.")
    policy = 'included_in_measurement_statistics'
    if policy in measured and not measured[policy].isin([True,False]).all():
        raise ValueError('Measurement inclusion must be explicitly true or false.')
    joined = measured[required + ["review_status"] + ([policy] if policy in measured else [])].merge(
        manual[required], on=KEYS, how="outer",
        suffixes=("_auto", "_manual"), indicator=True, validate="one_to_one",
    )
    accepted = joined["_merge"].eq("both") & joined.review_status.eq("accepted")
    joined["accepted_matched"] = accepted
    eligible = accepted & joined[policy].eq(True) if policy in joined else accepted
    joined['measurement_eligible_matched'] = eligible
    for metric in METRICS:
        included = eligible & joined[metric + "_auto"].notna() & joined[metric + "_manual"].notna()
        joined[metric + "_included"] = included
        joined[metric + "_error"] = (joined[metric + "_auto"] - joined[metric + "_manual"]).where(included)
        joined[metric + "_relative_error_pct"] = 100 * joined[metric + "_error"] / joined[metric + "_manual"]
        joined[metric + '_absolute_error'] = joined[metric + '_error'].abs()
        joined[metric + '_absolute_relative_error_pct'] = joined[metric + '_relative_error_pct'].abs()
        joined[metric + '_pair_mean'] = ((joined[metric + '_auto']+joined[metric + '_manual'])/2).where(included)
    return joined


@dataclass
class ComparisonReport:
    rows: pd.DataFrame
    counts: dict
    metrics: pd.DataFrame


def comparison_report(measured, manual):
    """Summarize coverage and accepted-only errors, independently per metric.

    Bias is mean(auto - manual); MAE is mean(abs(auto - manual)), in µm.
    With zero usable pairs both are NaN, never zero.
    """
    rows = compare_ground_truth(measured, manual)
    matched = rows["_merge"].eq("both")
    auto_only = rows["_merge"].eq("left_only")
    manual_only = rows["_merge"].eq("right_only")
    accepted = rows.accepted_matched
    counts = {
        "measured_count": int((matched | auto_only).sum()),
        "manual_count": int((matched | manual_only).sum()),
        "matched_count": int(matched.sum()),
        "unmatched_measured_count": int(auto_only.sum()),
        "unmatched_manual_count": int(manual_only.sum()),
        "accepted_matched_count": int(accepted.sum()),
        "review_required_matched_count": int((matched & ~accepted).sum()),
    }
    metrics = []
    for metric in METRICS:
        errors = rows.loc[rows[metric + "_included"], metric + "_error"]
        paired=rows[rows[metric+'_included']]
        agreement=agreement_statistics(paired[metric+'_auto'],paired[metric+'_manual'])
        metrics.append({
            "measurement": metric,
            "paired_count": int(errors.size),
            "missing_pair_count": int(rows.measurement_eligible_matched.sum() - errors.size),
            'excluded_by_measurement_policy_count': int((accepted & ~rows.measurement_eligible_matched).sum()),
            "bias_um": float(errors.mean()),
            "mean_absolute_error_um": float(errors.abs().mean()),
            'median_absolute_error_um': agreement['median_absolute_error_um'],
            'mean_absolute_relative_error_pct': agreement['mean_absolute_relative_error_pct'],
            'difference_sd_um': agreement['difference_sd_um'],
            'bland_altman_lower_um': agreement['bland_altman_lower_um'],
            'bland_altman_upper_um': agreement['bland_altman_upper_um'],
        })
    return ComparisonReport(rows, counts, pd.DataFrame(metrics))

import numpy as np
import pandas as pd

KEYS = ["image_id", "fiber_id"]
METRICS = ["length_um", "mean_diameter_um"]


def compare_ground_truth(measured, manual):
    """Join user-matched ImageJ IDs; never infer correspondence from row order."""
    required = KEYS + METRICS
    for table in (measured, manual):
        if not set(required) <= set(table.columns):
            raise ValueError("Both CSVs need image_id, fiber_id, length_um, mean_diameter_um.")
        if table[KEYS].isna().any().any() or table.duplicated(KEYS).any():
            raise ValueError("Image/fiber IDs must be present and unique.")
        for metric in METRICS:
            values = pd.to_numeric(table[metric], errors="raise")
            if ((values.dropna() <= 0) | ~np.isfinite(values.dropna())).any():
                raise ValueError("Measurements must be positive finite values or missing.")
    joined = measured[required].merge(manual[required], on=KEYS, how="outer", suffixes=("_auto", "_manual"), indicator=True)
    for metric in METRICS:
        joined[metric + "_error"] = joined[metric + "_auto"] - joined[metric + "_manual"]
        joined[metric + "_relative_error_pct"] = 100 * joined[metric + "_error"] / joined[metric + "_manual"]
    return joined

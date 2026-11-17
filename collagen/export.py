from io import BytesIO
import json
import zipfile
import numpy as np
import tifffile
from .visualization import count_overlay


def csv_bytes(table):
    return table.to_csv(index=False).encode("utf-8")


def result_archive(result):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, array in {"original": result.original, "segmentation": result.mask.astype(np.uint8),
                            "labels": result.labels.astype(np.uint32), "annotated": result.annotated,
                            "count_overlay": count_overlay(result.original, result.labels, result.table)}.items():
            buffer = BytesIO()
            tifffile.imwrite(buffer, array)
            archive.writestr(name + ".tiff", buffer.getvalue())
        archive.writestr("fibers.csv", csv_bytes(result.table))
        summary = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in result.summary.items()}
        archive.writestr("summary.json", json.dumps(summary, indent=2, allow_nan=False))
        archive.writestr("analysis_parameters.json", json.dumps(result.parameters, indent=2, allow_nan=False))
    return output.getvalue()

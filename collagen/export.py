from io import BytesIO
import json
import zipfile
import numpy as np
import tifffile
from .visualization import count_overlay
from .id_labels import fiber_id_layout, fiber_id_layer, composite_id_layer


def csv_bytes(table):
    return table.to_csv(index=False).encode("utf-8")


def result_archive(result):
    output = BytesIO()
    id_labels = result.id_labels if result.id_labels is not None else fiber_id_layout(result.labels, result.table)
    labeled_overlay = composite_id_layer(count_overlay(result.original,result.labels,result.table),
                                         fiber_id_layer(result.labels,id_labels))
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, array in {"original": result.original, "segmentation": result.mask.astype(np.uint8),
                            "labels": result.labels.astype(np.uint32), "annotated": result.annotated,
                            "count_overlay": labeled_overlay}.items():
            buffer = BytesIO()
            tifffile.imwrite(buffer, array)
            archive.writestr(name + ".tiff", buffer.getvalue())
        archive.writestr("fibers.csv", csv_bytes(result.table))
        archive.writestr('fiber_id_labels.json',json.dumps(id_labels,indent=2,allow_nan=False))
        summary = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in result.summary.items()}
        archive.writestr("summary.json", json.dumps(summary, indent=2, allow_nan=False))
        archive.writestr("analysis_parameters.json", json.dumps(result.parameters, indent=2, allow_nan=False))
        if result.baseline_labels is not None:
            buffer = BytesIO()
            tifffile.imwrite(buffer, result.baseline_labels.astype(np.uint32))
            archive.writestr('baseline_labels.tiff', buffer.getvalue())
            if result.manual_masks:
                buffer = BytesIO()
                masks = {f'mask_{fiber_id}': mask for fiber_id, bbox, mask in result.manual_masks}
                masks.update({f'bbox_yxyx_{fiber_id}': np.asarray(bbox) for fiber_id,bbox,mask in result.manual_masks})
                np.savez_compressed(buffer, **masks)
                archive.writestr('manual_masks.npz', buffer.getvalue())
    return output.getvalue()

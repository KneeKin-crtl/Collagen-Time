import math
import numpy as np
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize


def skeleton_graph(skeleton):
    points = set(map(tuple, np.argwhere(skeleton)))
    graph = {p: {} for p in points}
    for y, x in points:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                q = (y + dy, x + dx)
                if (dy == dx == 0) or q not in points:
                    continue
                # Avoid diagonal shortcuts across a connected orthogonal corner.
                if dy and dx and ((y, x + dx) in points or (y + dy, x) in points):
                    continue
                graph[(y, x)][q] = math.hypot(dy, dx)
    return graph


def measure(mask, calibration, exclusion_px=2.0):
    if not np.isfinite(calibration) or calibration <= 0:
        raise ValueError("Calibration must be finite and positive.")
    if not np.isfinite(exclusion_px) or exclusion_px < 0:
        raise ValueError("Exclusion distance must be finite and nonnegative.")
    skeleton = skeletonize(mask)
    graph = skeleton_graph(skeleton)
    endpoints = sorted(p for p, edges in graph.items() if len(edges) == 1)
    junctions = [p for p, edges in graph.items() if len(edges) > 2]
    # Require a single connected open path before reporting a fiber length.
    seen = set()
    if graph:
        todo = [next(iter(graph))]
        while todo:
            p = todo.pop()
            if p not in seen:
                seen.add(p)
                todo.extend(graph[p])
    isolated = len(endpoints) == 2 and not junctions and len(seen) == len(graph)
    length = sum(sum(edges.values()) for edges in graph.values()) / 2 * calibration if isolated else np.nan
    # Pad so the outside of the image is background, including border objects.
    distance = distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
    reliable = skeleton.copy()
    for y, x in endpoints + junctions:
        # Exclude at least one local radius to avoid cap/junction width bias.
        radius = max(exclusion_px, distance[y, x])
        yy, xx = np.ogrid[:mask.shape[0], :mask.shape[1]]
        reliable &= (yy - y)**2 + (xx - x)**2 > radius**2
    diameters = 2 * distance[reliable] * calibration if isolated else np.array([])
    return {"skeleton": skeleton, "endpoints": endpoints, "junctions": junctions,
            "isolated": isolated, "length_um": length,
            "mean_diameter_um": float(diameters.mean()) if diameters.size else np.nan,
            "diameter_sd_um": float(diameters.std(ddof=0)) if diameters.size else np.nan,
            "diameter_samples": int(diameters.size)}

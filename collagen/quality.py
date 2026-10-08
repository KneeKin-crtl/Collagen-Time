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

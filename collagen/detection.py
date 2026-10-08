from skimage.measure import label, regionprops


def detect(mask):
    labels = label(mask, connectivity=2)
    return labels, regionprops(labels)

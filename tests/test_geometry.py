import math
import numpy as np
import pytest
from skimage.draw import line, disk
from collagen.geometry import measure, skeleton_graph


def test_weighted_horizontal_vertical_diagonal():
    for end, expected in [((10, 30), 20), ((30, 10), 20), ((30, 30), 20 * math.sqrt(2))]:
        mask = np.zeros((50, 50), bool)
        rr, cc = line(10, 10, *end)
        mask[rr, cc] = True
        result = measure(mask, .5, 0)
        assert result['isolated']
        assert result['length_um'] == pytest.approx(expected * .5)


def test_corner_has_no_diagonal_shortcut():
    mask = np.zeros((5, 5), bool)
    mask[1, 1:3] = True
    mask[2, 2] = True
    graph = skeleton_graph(mask)
    assert sum(sum(v.values()) for v in graph.values()) / 2 == 2


def test_straight_width_and_length():
    mask = np.zeros((80, 140), bool)
    mask[30:41, 20:121] = True
    result = measure(mask, .25)
    assert result['isolated']
    # Raster distance transform is biased by roughly one pixel for odd widths.
    assert result['mean_diameter_um'] == pytest.approx(11 * .25, abs=.3)
    assert result['diameter_sd_um'] < .1
    assert result['length_um'] == pytest.approx(100 * .25, abs=2)


def test_curved_known_arc():
    mask = np.zeros((160, 160), bool)
    radius = 45
    for theta in np.linspace(0, math.pi / 2, 600):
        rr, cc = disk((60 + radius * math.sin(theta), 60 + radius * math.cos(theta)), 4, shape=mask.shape)
        mask[rr, cc] = True
    result = measure(mask, .4)
    assert result['isolated']
    # Eight-connected digital arc length has orientation-dependent raster bias.
    assert result['length_um'] == pytest.approx(radius * math.pi / 2 * .4, rel=.12)
    assert result['mean_diameter_um'] == pytest.approx(8 * .4, abs=.5)


def test_crossing_has_no_fiber_length():
    mask = np.zeros((70, 70), bool)
    mask[32:37, 10:60] = True
    mask[10:60, 32:37] = True
    result = measure(mask, 1)
    assert result['junctions']
    assert not result['isolated']
    assert np.isnan(result['length_um'])


@pytest.mark.parametrize('calibration', [0, -1, float('nan'), float('inf')])
def test_invalid_calibration(calibration):
    with pytest.raises(ValueError):
        measure(np.zeros((10, 10), bool), calibration)

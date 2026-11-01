import numpy as np
import pytest

from pipeline import panorama


def stripes(width=720, height=360):
    """A panorama whose red channel names the column: 0 at the left edge, 255 at the right."""
    img = np.zeros((height, width, 3), np.uint8)
    img[:, :, 2] = np.linspace(0, 255, width).astype(np.uint8)[None, :]
    return img


@pytest.mark.parametrize("yaw, expected", [(0, 127), (90, 191), (-90, 64)])
def test_a_view_looks_the_way_it_is_turned(yaw, expected):
    out = panorama.view(stripes(), yaw, pitch=0, size=(64, 48))
    assert abs(int(out[24, 32, 2]) - expected) <= 2


def test_a_view_turned_past_the_back_of_the_panorama_wraps():
    out = panorama.view(stripes(), 179, pitch=0, size=(64, 48), fov=20)
    assert out.shape == (48, 64, 3)
    assert int(out[24, 20, 2]) > 200 and int(out[24, 60, 2]) < 60  # the seam runs through the view


def test_looking_down_moves_the_view_toward_the_bottom_of_the_panorama():
    img = np.zeros((360, 720, 3), np.uint8)
    img[:, :, 1] = np.linspace(0, 255, 360).astype(np.uint8)[:, None]
    level = panorama.view(img, 0, pitch=0, size=(64, 48))
    down = panorama.view(img, 0, pitch=-20, size=(64, 48))
    assert int(down[24, 32, 1]) > int(level[24, 32, 1]) + 20


def test_the_angle_to_a_box_follows_the_views_width():
    assert panorama.box_angle([450, 0, 550, 100]) == pytest.approx(0)
    assert panorama.box_angle([900, 0, 1000, 100]) > 30
    assert panorama.box_angle([0, 0, 100, 100]) < -30

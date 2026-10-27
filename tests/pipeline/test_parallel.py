import time

import pytest

from pipeline.meta_api import RunAborted
from pipeline.parallel import run_parallel


def test_results_come_back_in_item_order():
    def slow_square(x):
        time.sleep(0.01 * (5 - x))
        return x * x

    assert run_parallel(slow_square, range(5), workers=4) == [0, 1, 4, 9, 16]


def test_the_first_error_is_raised():
    def boom(x):
        if x == 2:
            raise ValueError("bad item")
        return x

    with pytest.raises(ValueError, match="bad item"):
        run_parallel(boom, range(5), workers=3)


def test_run_aborted_escapes_from_a_worker_thread():
    def abort(x):
        raise RunAborted("stop")

    with pytest.raises(RunAborted):
        run_parallel(abort, range(3), workers=2)


def test_one_worker_runs_in_order_without_threads():
    seen = []
    run_parallel(seen.append, [3, 1, 2], workers=1)
    assert seen == [3, 1, 2]

from __future__ import annotations

import threading
import time

from tabletalk.executors import LocalExecutor


def test_local_executor_preserves_order_and_bounds_concurrency() -> None:
    lock = threading.Lock()
    active = 0
    maximum = 0

    def work(value: int) -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        return value * 2

    assert LocalExecutor(2).map(work, range(6)) == (0, 2, 4, 6, 8, 10)
    assert maximum == 2

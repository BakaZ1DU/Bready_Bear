import threading

from operation import OperationGate


def test_robot_jobs_are_mutually_exclusive():
    gate = OperationGate()
    assert gate.begin_robot()

    started = threading.Event()
    acquired = threading.Event()

    def second_robot():
        started.set()
        gate.begin_robot()
        try:
            acquired.set()
        finally:
            gate.end_robot()

    worker = threading.Thread(target=second_robot, daemon=True)
    worker.start()
    assert started.wait(1)

    # 第一机器人未释放之前，第二个任务不能获得权限。
    assert not acquired.wait(0.2)

    gate.end_robot()
    assert acquired.wait(1)
    worker.join(timeout=1)
    assert not worker.is_alive()

"""全局微信操作闸门：机器人回复优先，自动发送让路。"""
from __future__ import annotations

import threading


class OperationGate:
    def __init__(self):
        self._cond = threading.Condition()
        self._auto_running = False
        self._robot_running = False
        self._robot_waiting = 0

    def begin_auto(self) -> bool:
        """自动发送（朋友圈/群计划）尝试占用；机器人等待/运行时让路。"""
        with self._cond:
            if self._robot_running or self._robot_waiting:
                return False
            if self._auto_running:
                return False
            self._auto_running = True
            return True

    def end_auto(self) -> None:
        with self._cond:
            self._auto_running = False
            self._cond.notify_all()

    def begin_robot(self) -> bool:
        """机器人回复优先：先登记等待，等正在进行的自动发送结束后占用。"""
        with self._cond:
            self._robot_waiting += 1
            try:
                while self._auto_running:
                    self._cond.wait(timeout=1.0)
                self._robot_running = True
                return True
            finally:
                self._robot_waiting -= 1
                self._cond.notify_all()

    def end_robot(self) -> None:
        with self._cond:
            self._robot_running = False
            self._cond.notify_all()

    @property
    def robot_busy(self) -> bool:
        with self._cond:
            return self._robot_running or self._robot_waiting > 0

    @property
    def auto_running(self) -> bool:
        with self._cond:
            return self._auto_running


GATE = OperationGate()

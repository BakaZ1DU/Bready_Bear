"""后台调度：只执行用户手工创建的朋友圈/群消息计划，不自动生成文案。"""
from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import store
from group_ui import GroupUI
from operation import GATE
from wechat_ui import WeChatUI, AutomationError, enum_windows


class Scheduler:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_error = ""
        self.busy_plan = ""
        self.window_bindings: dict[str, int] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()

    @property
    def paused(self) -> bool:
        return self.pause_event.is_set()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.paused:
                    self._tick()
            except Exception as exc:
                self.last_error = str(exc)
                store.log("ERROR", f"调度异常：{exc}")
            self.stop_event.wait(max(2, int(self.cfg.get("scheduler", {}).get("check_seconds", 5) or 5)))

    def _tick(self) -> None:
        for plan in store.due_plans():
            if self.stop_event.is_set() or self.paused:
                return
            self._process(plan)

    def _wechat_cfg(self) -> dict:
        cfg = dict(self.cfg)
        cfg.update(self.cfg.get("wechat", {}) or {})
        return cfg

    def _has_window(self, account: dict) -> bool:
        h = int(self.window_bindings.get(account.get("id", ""), 0) or 0)
        if h <= 0:
            return False
        return any(w.hwnd == h and w.title == "微信" for w in enum_windows())

    def _hwnd(self, account: dict) -> int | None:
        h = int(self.window_bindings.get(account.get("id", ""), 0) or 0)
        return h or None

    def _process(self, plan: dict) -> None:
        accounts = store.load_accounts()
        account = store.account_by_id(accounts, plan.get("account_id", ""))
        if not account or not account.get("enabled", True):
            store.update_plan(plan["id"], status=store.STATUS_SKIPPED, last_error="账号未启用或不存在")
            return
        selected_id = store.selected_account_id(accounts)
        if selected_id and plan.get("account_id") != selected_id:
            sel = store.selected_account(accounts)
            if plan.get("status") != store.STATUS_WAITING:
                store.update_plan(plan["id"], status=store.STATUS_WAITING,
                                  last_error=f"当前操作账号是 {sel.get('name') if sel else selected_id}，请切换右上角账号")
            return        
        enabled = [a for a in accounts if a.get("enabled", True)]
        active = store.active_account(accounts)
        wxid = (account.get("wxid") or "").strip()
        bound = self._has_window(account)
        if not bound and not wxid and len(enabled) > 1:
            if plan.get("status") != store.STATUS_WAITING:
                store.update_plan(plan["id"], status=store.STATUS_WAITING,
                                  last_error="多账号模式必须先填写该账号的 wxid")
                store.log("WARNING", f"计划等待绑定 wxid：{account.get('name')}")
            return
        if not bound and wxid and (not active or active.get("id") != account["id"]):
            if plan.get("status") != store.STATUS_WAITING:
                store.update_plan(plan["id"], status=store.STATUS_WAITING,
                                  last_error=f"请切换到 {account.get('name')}（当前：{store.active_hint(accounts)}）")
                store.log("WARNING", f"计划等待账号切换：{account.get('name')} / {plan.get('kind')}")
            return
        if plan.get("status") == store.STATUS_WAITING:
            store.update_plan(plan["id"], status=store.STATUS_PLANNED, last_error="")
        if plan.get("kind") == store.KIND_GROUP:
            mode = (self.cfg.get("group", {}) or {}).get("mode", "current")
            if mode == "current":
                store.update_plan(
                    plan["id"],
                    status=store.STATUS_WAITING_CONFIRMATION,
                    last_error="当前聊天模式无法验证目标群。请手动打开目标群并核对后，再点击立即发送。",
                )
                store.log("WARNING", f"群任务等待人工确认：{plan['id']}")
                return
        with self._lock:
            self._do_publish(plan, account, manual_confirmed=False)

    def _do_publish(self, plan: dict, account: dict, *, manual_confirmed: bool = False) -> None:
        pid = plan["id"]

        # 先取得全局微信 UI 操作权限；占用时保留原状态，稍后再试。
        if not GATE.begin_auto():
            store.log("INFO", f"微信忙碌，任务 {pid} 保留原状态")
            return

        try:
            claimed = store.claim_plan(pid, manual_confirmed=manual_confirmed)
            if claimed is None:
                store.log("INFO", f"任务 {pid} 已被执行或状态不再允许发送")
                return

            self.busy_plan = pid
            store.log("INFO", f"开始执行任务：{pid}")

            try:
                if claimed.get("kind") == store.KIND_MOMENT:
                    cfg = self._wechat_cfg()
                    images = [Path(p) for p in claimed.get("images", [])]
                    if not images:
                        raise AutomationError("朋友圈没有图片")
                    WeChatUI(cfg, logger=store.log, hwnd=self._hwnd(account)).publish(
                        claimed.get("text", ""), images, preview_only=False, post_id=pid
                    )
                elif claimed.get("kind") == store.KIND_GROUP:
                    group = (claimed.get("group") or account.get("group_name") or "").strip()
                    if not group:
                        raise AutomationError("没有配置群名称")
                    GroupUI(self._wechat_cfg(), hwnd=self._hwnd(account)).send(
                        group, claimed.get("text", "")
                    )
                else:
                    raise AutomationError("未知计划类型")

                store.update_plan(
                    pid,
                    status=store.STATUS_SENT,
                    sent_at=store.now_local().isoformat(timespec="seconds"),
                    last_error="",
                )
                store.log("INFO", f"任务完成：{pid}")
            except Exception as exc:
                msg = str(exc)
                self.last_error = msg
                attempts = int(claimed.get("attempts", 0)) + 1
                # 失败阶段未可靠分类前一律转人工核实，绝不自动重发。
                store.update_plan(
                    pid,
                    status=store.STATUS_NEEDS_REVIEW,
                    attempts=attempts,
                    last_error=f"执行异常，请核实是否已发送：{msg}",
                    next_retry_at="",
                )
                store.log("ERROR", f"任务 {pid} 待核实：{msg}")
        finally:
            self.busy_plan = ""
            GATE.end_auto()

    def run_now(self, plan_id: str) -> tuple[bool, str]:
        plan = store.get_plan(plan_id)
        if not plan:
            return False, "计划不存在"
        if plan.get("status") == store.STATUS_NEEDS_REVIEW:
            return False, "任务发送结果不确定，请先在微信中核实"
        allowed_statuses = {
            store.STATUS_PLANNED,
            store.STATUS_WAITING,
            store.STATUS_WAITING_CONFIRMATION,
        }
        if plan.get("status") not in allowed_statuses:
            return False, "当前任务状态不允许立即发送"
        accounts = store.load_accounts()
        account = store.account_by_id(accounts, plan.get("account_id", ""))
        if not account:
            return False, "账号不存在"
        selected_id = store.selected_account_id(accounts)
        if selected_id and plan.get("account_id") != selected_id:
            sel = store.selected_account(accounts)
            return False, f"当前操作账号是「{sel.get('name') if sel else selected_id}」，请先在右上角切换账号"
        enabled = [a for a in accounts if a.get("enabled", True)]
        active = store.active_account(accounts)
        wxid = (account.get("wxid") or "").strip()
        if not self._has_window(account) and not wxid and len(enabled) > 1:
            return False, "请先在账号页填写该账号的 wxid"
        if not self._has_window(account) and wxid and (not active or active.get("id") != account["id"]):
            store.update_plan(plan_id, status=store.STATUS_WAITING,
                              last_error=f"请切换到 {account.get('name')}（当前：{store.active_hint(accounts)}）")
            return False, f"当前是「{store.active_hint(accounts)}」，请切换到「{account.get('name')}」"
        threading.Thread(
            target=self._do_publish,
            args=(plan, account),
            kwargs={"manual_confirmed": True},
            daemon=True,
        ).start()
        return True, "已提交立即发送请求；请在任务列表确认最终结果"

    def send_group_now(self, account_id: str, group: str, text: str) -> tuple[bool, str]:
        accounts = store.load_accounts()
        account = store.account_by_id(accounts, account_id)
        if not account:
            return False, "账号不存在"
        selected_id = store.selected_account_id(accounts)
        if selected_id and account_id != selected_id:
            sel = store.selected_account(accounts)
            return False, f"当前操作账号是「{sel.get('name') if sel else selected_id}」，请先在右上角切换账号"
        active = store.active_account(accounts)
        wxid = (account.get("wxid") or "").strip()
        enabled = [a for a in accounts if a.get("enabled", True)]
        if not self._has_window(account) and not wxid and len(enabled) > 1:
            return False, "请先在账号页填写该账号的 wxid，避免发错号"
        if not self._has_window(account) and wxid and (not active or active.get("id") != account["id"]):
            return False, f"当前是「{store.active_hint(accounts)}」，请切换到「{account.get('name')}」"
        group = (group or account.get("group_name") or "").strip()
        if not group:
            return False, "请填写群名称"
        if not text.strip():
            return False, "消息不能为空"
        if GATE.robot_busy:
            return False, "机器人正在回复，群消息稍后再发"
        threading.Thread(target=self._send_group, args=(account, group, text), daemon=True).start()
        return True, "已开始发送"

    def _send_group(self, account: dict, group: str, text: str) -> None:
        if not GATE.begin_auto():
            self.last_error = "机器人正在回复，群消息延后"
            store.log("WARNING", "机器人优先，手动群消息未发送")
            return
        try:
            GroupUI(self._wechat_cfg(), hwnd=self._hwnd(account)).send(group, text)
            store.log("INFO", f"群消息发送成功：{account.get('name')}")
        except Exception as exc:
            self.last_error = str(exc)
            store.log("ERROR", f"群消息发送失败：{exc}")
        finally:
            GATE.end_auto()

    def snapshot(self) -> dict:
        accounts = store.load_accounts()
        plans = store.list_plans()
        counts: dict[str, int] = {}
        for p in plans:
            counts[p.get("status", "")] = counts.get(p.get("status", ""), 0) + 1
        return {
            "paused": self.paused,
            "active_hint": store.active_hint(accounts),
            "selected_id": store.selected_account_id(accounts),
            "selected_name": (store.selected_account(accounts) or {}).get("name", ""),
            "active_wxid": store.active_wxid(),
            "counts": counts,
            "total": len(plans),
            "last_error": self.last_error,
            "accounts": len([a for a in accounts if a.get("enabled", True)]),
            "busy": self.busy_plan,
        }

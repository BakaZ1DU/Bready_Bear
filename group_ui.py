"""微信群消息发送（UI 自动化）。

只负责“往指定群发一条文本”，不读取聊天记录。
自动回复任意群消息暂不可靠；桌面端提供半自动：粘贴群消息 -> AI 生成 -> 发送。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from pathlib import Path

import store
from wechat_ui import WeChatUI, _get_rect, enable_dpi_awareness, enum_windows, user32

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


class GroupUI:
    def __init__(self, cfg: dict, hwnd: int | None = None):
        self.cfg = cfg
        self.wx = WeChatUI(cfg, logger=store.log, hwnd=hwnd)

    def _focus(self):
        main = self.wx.ensure_ready()
        # 临时置顶，避免被控制台/浏览器/其他窗口挡住，导致点不到聊天框
        user32.SetWindowPos(main.hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)  # TOPMOST
        self.wx._normalize_window(main)
        self._close_popups(main)
        self.wx._close_obstructions(main)
        self.wx._force_foreground(main.hwnd)
        time.sleep(0.4)
        return main

    def _restore(self, main) -> None:
        try:
            user32.SetWindowPos(main.hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)  # NOTOPMOST
        except Exception:
            pass

    def _close_popups(self, main) -> None:
        """关闭“直播/工具资源下载”等挡住微信的小弹窗。"""
        try:
            for w in enum_windows():
                if w.pid != main.pid or w.hwnd == main.hwnd:
                    continue
                if not user32.IsWindowVisible(w.hwnd):
                    continue
                if w.title not in ("微信", "Weixin"):
                    continue
                if w.rect.width < 700 and w.rect.height < 600:
                    user32.PostMessageW(w.hwnd, 0x0010, 0, 0)  # WM_CLOSE
                    time.sleep(0.25)
            if not user32.IsWindowVisible(main.hwnd):
                user32.ShowWindow(main.hwnd, 9)  # SW_RESTORE
                time.sleep(0.3)
        except Exception:
            pass

    def _search_group(self, main, r, group_name: str) -> bool:
        """点搜索框并确认输入内容真的进了搜索框，而不是聊天输入框。"""
        for attempt in range(3):
            self.wx._chord([0x11], 0x46)  # Ctrl+F
            time.sleep(0.45)
            self.wx._click(r.left + int(r.width * 0.17), r.top + 37)
            time.sleep(0.35)
            self.wx._chord([0x11], 0x41)
            self.wx._set_clipboard(group_name)
            self.wx._chord([0x11], 0x56)
            time.sleep(0.7)
            # 用 Ctrl+A/C 回读，确认搜索框里是群名
            self.wx._chord([0x11], 0x41)
            time.sleep(0.15)
            self.wx._chord([0x11], 0x43)
            time.sleep(0.2)
            got = self.wx._get_clipboard().strip()
            if got == group_name.strip():
                return True
            store.log("WARNING", f"群搜索框未聚焦，重试 {attempt + 1}/3")
            self._close_popups(main)
            self.wx._force_foreground(main.hwnd)
        return False

    def _has_popup(self, main) -> bool:
        try:
            for w in enum_windows():
                if w.pid == main.pid and w.hwnd != main.hwnd and user32.IsWindowVisible(w.hwnd):
                    if w.title in ("微信", "Weixin") and w.rect.width < 700 and w.rect.height < 600:
                        return True
        except Exception:
            pass
        return False

    def open_group(self, group_name: str):
        main = self._focus()
        r = _get_rect(main.hwnd)
        if not self._search_group(main, r, group_name):
            raise RuntimeError("微信搜索框没有聚焦，已中止，避免误点直播/发错群")
        self.wx._tap(0x0D)  # Enter 打开搜索的第一个结果
        time.sleep(1.4)
        if self._has_popup(main):
            self._close_popups(main)
            raise RuntimeError("微信打开了直播/工具资源弹窗，已中止，未发送任何消息")
        if not user32.IsWindowVisible(main.hwnd):
            user32.ShowWindow(main.hwnd, 9)
            time.sleep(0.3)
        self.wx._force_foreground(main.hwnd)
        r = _get_rect(main.hwnd)
        return main, r

    def _focus_composer(self, main, r) -> bool:
        """在输入框里粘贴一个标记并回读；确认焦点在输入框，而不是工具栏。"""
        marker = "__BREADYBEAR_FOCUS_CHECK__"
        for xr in (0.45, 0.55, 0.35, 0.70):
            for dy in (60, 70, 50, 80, 90, 100):
                self.wx._click(r.left + int(r.width * xr), r.bottom - dy)
                time.sleep(0.3)
                self.wx._set_clipboard(marker)
                self.wx._chord([0x11], 0x41)
                self.wx._tap(0x2E)
                self.wx._chord([0x11], 0x56)
                time.sleep(0.3)
                self.wx._chord([0x11], 0x41)
                time.sleep(0.12)
                self.wx._chord([0x11], 0x43)
                time.sleep(0.2)
                got = self.wx._get_clipboard()
                if marker in got:
                    # 验证完立刻清掉标记，绝不能让标记留在输入框里被发出去。
                    self.wx._chord([0x11], 0x41)
                    self.wx._tap(0x2E)
                    time.sleep(0.15)
                    return True
                self._close_popups(main)
                self.wx._force_foreground(main.hwnd)
        return False

    def send(self, group_name: str, text: str, screenshot: bool = True) -> Path | None:
        if not text.strip():
            raise ValueError("消息不能为空")
        if (self.cfg.get("group", {}) or {}).get("mode", "current") == "current":
            return self.send_current(text, screenshot=screenshot)
        main, r = self.open_group(group_name)
        if not self._focus_composer(main, r):
            raise RuntimeError("没有找到微信聊天输入框，已中止，避免误点直播/发错群")
        self._send_text(text, r, screenshot=screenshot)
        store.log("INFO", f"群消息已发送到「{group_name}」：{text[:30]}")
        return self._last_shot

    def send_current(self, text: str, screenshot: bool = True) -> Path | None:
        if not text.strip():
            raise ValueError("消息不能为空")
        if (self.cfg.get("group", {}) or {}).get("background", False):
            raise RuntimeError("后台发送属于实验性功能，当前版本已禁用；请关闭后台模式并使用前台发送。")
        main = self._focus()
        r = _get_rect(main.hwnd)
        try:
            if not self._focus_composer(main, r):
                raise RuntimeError("没有找到微信聊天输入框，请先手动打开目标群")
            self._send_text(text, r, screenshot=screenshot)
        finally:
            self._restore(main)
        store.log("INFO", f"群消息已发送到当前聊天：{text[:30]}")
        return self._last_shot

    # ---------- background send: no physical mouse/keyboard ----------
    def _bg_click(self, hwnd: int, x: int, y: int) -> None:
        lp = (y << 16) | (x & 0xFFFF)
        user32.PostMessageW(hwnd, 0x0200, 0, lp)  # WM_MOUSEMOVE
        user32.PostMessageW(hwnd, 0x0201, 1, lp)  # WM_LBUTTONDOWN
        user32.PostMessageW(hwnd, 0x0202, 0, lp)  # WM_LBUTTONUP

    def _bg_key(self, hwnd: int, vk: int) -> None:
        user32.PostMessageW(hwnd, 0x0100, vk, 1 | (vk << 16))  # WM_KEYDOWN
        user32.PostMessageW(hwnd, 0x0101, vk, 1 | (vk << 16) | 0xC0000000)  # WM_KEYUP

    def _composer_ratio(self, r) -> float:
        try:
            from PIL import ImageGrab
            box = (r.left + int(r.width * 0.06), r.top + int(r.height * 0.74),
                   r.left + int(r.width * 0.94), r.top + int(r.height * 0.93))
            img = ImageGrab.grab(bbox=box).convert("L")
            data = list(img.getdata())
            dark = sum(1 for v in data if v < 110)
            return dark / max(1, len(data))
        except Exception:
            return 0.0

    def send_current_background(self, text: str, screenshot: bool = True) -> Path | None:
        """只向窗口投递消息，不移动真实鼠标、不按真实键盘。"""
        main = self._focus()
        r = _get_rect(main.hwnd)
        self._bg_click(main.hwnd, int(r.width * 0.55), int(r.height * 0.82))
        time.sleep(0.35)
        self.wx._set_clipboard(text.strip())
        time.sleep(0.15)
        if self.wx._get_clipboard().strip() != text.strip():
            raise RuntimeError("剪贴板写入失败，已中止，未发送")
        user32.PostMessageW(main.hwnd, 0x0302, 0, 0)  # WM_PASTE
        time.sleep(0.5)
        if not self._composer_ratio(r):
            raise RuntimeError("输入框没有出现文字，已中止，未按发送")
        self._bg_key(main.hwnd, 0x0D)  # Enter
        time.sleep(1.2)
        if self._composer_ratio(r):
            raise RuntimeError("微信没有把消息发出去（输入框仍有文字），请检查后重试")
        shot = self.wx.save_screenshot("group_background") if screenshot else None
        store.log("INFO", f"群消息后台发送成功（未移动鼠标/键盘）：{text[:30]}")
        return shot

    def prepare_current(self, text: str) -> None:
        """只把内容填进当前聊天输入框，不发送；用户自己按 Enter。"""
        if not text.strip():
            raise ValueError("消息不能为空")
        main = self._focus()
        r = _get_rect(main.hwnd)
        self._bg_click(main.hwnd, int(r.width * 0.55), int(r.height * 0.82))
        time.sleep(0.3)
        self.wx._set_clipboard(text.strip())
        time.sleep(0.15)
        if self.wx._get_clipboard().strip() != text.strip():
            raise RuntimeError("剪贴板写入失败，未填入")
        user32.PostMessageW(main.hwnd, 0x0302, 0, 0)
        time.sleep(0.5)
        if not self._composer_ratio(r):
            raise RuntimeError("输入框没有出现文字，请先手动点一下微信输入框再试")
        store.log("INFO", "内容已填入微信输入框，等待用户按 Enter")

    def send_current_focused(self, text: str, screenshot: bool = True) -> Path | None:
        """用户先手动点好输入框，这里只粘贴/回车，不再自动点聊天框。"""
        if not text.strip():
            raise ValueError("消息不能为空")
        main = self._focus()
        r = _get_rect(main.hwnd)
        try:
            self._send_text(text, r, screenshot=screenshot)
        finally:
            self._restore(main)
        store.log("INFO", f"群消息已发送（手动聚焦输入框）：{text[:30]}")
        return self._last_shot

    def prepare_current_focused(self, text: str) -> None:
        """用户先手动点好输入框，这里只粘贴，不发送。"""
        if not text.strip():
            raise ValueError("消息不能为空")
        main = self._focus()
        try:
            self.wx._chord([0x11], 0x41)
            self.wx._tap(0x2E)
            self.wx._set_clipboard(text.strip())
            self.wx._chord([0x11], 0x56)
            time.sleep(0.5)
        finally:
            self._restore(main)
        store.log("INFO", "内容已填入微信输入框（手动聚焦），等待用户按 Enter")

    def _find_green_send(self, r) -> tuple[int, int] | None:
        """在右下角识别微信绿色发送按钮，不依赖固定坐标。"""
        try:
            from PIL import ImageGrab
            x0 = r.left + int(r.width * 0.65)
            y0 = r.top + int(r.height * 0.85)
            img = ImageGrab.grab(bbox=(x0, y0, r.right, r.bottom), all_screens=True).convert("RGB")
        except Exception:
            return None
        w, h = img.size
        px = img.load()
        pts = []
        for y in range(h):
            for x in range(w):
                rr, gg, bb = px[x, y]
                if gg > 130 and gg > rr + 35 and gg > bb + 25:
                    pts.append((x, y))
        if len(pts) < 80:
            return None
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        return x0 + int(cx), y0 + int(cy)

    def _send_text(self, text: str, r, screenshot: bool = True) -> None:
        clean = text.strip()
        self.wx._chord([0x11], 0x41)
        self.wx._tap(0x2E)
        self.wx._set_clipboard(clean)
        self.wx._chord([0x11], 0x56)
        time.sleep(0.5)
        # 发送前确认文字真的进了输入框
        self.wx._set_clipboard("")
        self.wx._chord([0x11], 0x41)
        self.wx._chord([0x11], 0x43)
        time.sleep(0.2)
        got = self.wx._get_clipboard().strip()
        norm_got = "".join(got.split())
        norm_clean = "".join(clean.split())
        if not norm_got or not (norm_got == norm_clean or norm_clean.startswith(norm_got) or norm_got.startswith(norm_clean[:80])):
            raise RuntimeError("文字没有进入微信输入框，已中止，未发送")
        self.wx._tap(0x27)  # Right：取消全选，光标到末尾
        send_pt = self._find_green_send(r)
        if send_pt:
            self.wx._click(*send_pt)
        else:
            self.wx._tap(0x0D)
        time.sleep(1.2)
        # 发送后输入框应清空
        self.wx._set_clipboard("")
        self.wx._chord([0x11], 0x41)
        self.wx._chord([0x11], 0x43)
        time.sleep(0.2)
        if self.wx._get_clipboard().strip():
            raise RuntimeError("微信没有把消息发出去（输入框仍有文字），请检查后重试")
        self._last_shot = self.wx.save_screenshot("group_current") if screenshot else None

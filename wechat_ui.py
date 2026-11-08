"""微信 PC 4.x 朋友圈发布 UI 自动化。

原理：Windows 原生鼠标/键盘 + 截图颜色定位，不注入微信进程、不修改微信文件。
流程：主窗口 -> 朋友圈 -> 相机 -> 文件选择框 -> 编辑框 -> 发表。
"""
from __future__ import annotations

import ctypes
import random
import time
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- Win32 常量 ----
SW_RESTORE = 9
GW_OWNER = 4
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_DELETE = 0x2E
VK_END = 0x23
VK_A = 0x41
VK_C = 0x43
VK_L = 0x4C
VK_N = 0x4E
VK_V = 0x56
CF_UNICODETEXT = 13
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DESKTOP_SWITCHDESKTOP = 0x0100

if ctypes.sizeof(ctypes.c_void_p) == 8:
    LONG_PTR = ctypes.c_longlong
else:
    LONG_PTR = ctypes.c_long

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


# ---- 函数签名（64 位下必须显式声明，避免句柄被截断） ----
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.SetWindowTextW.restype = wintypes.BOOL
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE

kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


def enable_dpi_awareness() -> None:
    try:
        # PER_MONITOR_AWARE_V2
        ctx = ctypes.c_void_p(-4)
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        if fn(ctx):
            return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    cls: str
    rect: RECT

    @property
    def area(self) -> int:
        return self.rect.width * self.rect.height


class AutomationError(RuntimeError):
    pass


class WeChatNotReady(AutomationError):
    pass


def _get_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _get_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_rect(hwnd: int) -> RECT:
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def enum_windows() -> list[WindowInfo]:
    result: list[WindowInfo] = []

    def cb(hwnd, _):
        try:
            result.append(WindowInfo(hwnd, _pid(hwnd), _get_text(hwnd), _get_class(hwnd), _get_rect(hwnd)))
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return result


def _process_path(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def is_screen_locked() -> bool:
    h = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not h:
        return True
    user32.CloseDesktop(h)
    return False


class WeChatUI:
    def __init__(self, cfg: dict, logger: Callable[[str, str], None] | None = None,
                 hwnd: int | None = None):
        self.cfg = cfg
        self._log = logger or (lambda level, msg: None)
        self.last_preview: Path | None = None
        self.preferred_hwnd = int(hwnd) if hwnd else None

    # ---------- 日志与截图 ----------
    def log(self, level: str, msg: str) -> None:
        self._log(level, msg)

    def _preview_dir(self) -> Path:
        d = Path(__file__).resolve().parent / "logs" / "previews"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_screenshot(self, prefix: str, rect: tuple[int, int, int, int] | None = None) -> Path:
        from PIL import ImageGrab

        path = self._preview_dir() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        try:
            if rect:
                img = ImageGrab.grab(bbox=rect, all_screens=True)
            else:
                img = ImageGrab.grab(all_screens=True)
            img.save(path)
        except Exception as exc:
            self.log("WARNING", f"截图失败：{exc}")
        return path

    # ---------- 输入 ----------
    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _click(self, x: int, y: int) -> None:
        x += random.randint(-1, 1)
        y += random.randint(-1, 1)
        user32.SetCursorPos(int(x), int(y))
        self._sleep(random.uniform(0.10, 0.18))
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        self._sleep(random.uniform(0.05, 0.10))
        user32.mouse_event(0x0004, 0, 0, 0, 0)

    def _key_down(self, vk: int) -> None:
        user32.keybd_event(vk, 0, 0, 0)
        self._sleep(random.uniform(0.015, 0.035))

    def _key_up(self, vk: int) -> None:
        user32.keybd_event(vk, 0, 0x0002, 0)
        self._sleep(random.uniform(0.015, 0.035))

    def _tap(self, vk: int) -> None:
        self._key_down(vk)
        self._key_up(vk)

    def _chord(self, mods: Iterable[int], vk: int) -> None:
        mods = list(mods)
        for m in mods:
            self._key_down(m)
        self._tap(vk)
        for m in reversed(mods):
            self._key_up(m)
        self._sleep(0.12)

    def _set_clipboard(self, text: str) -> None:
        for _ in range(30):
            if user32.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            raise AutomationError("系统剪贴板被占用，无法写入。")
        try:
            user32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(0x0042, len(data))
            if not h:
                raise AutomationError("剪贴板内存分配失败。")
            p = kernel32.GlobalLock(h)
            if not p:
                kernel32.GlobalFree(h)
                raise AutomationError("剪贴板内存锁定失败。")
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                raise AutomationError("写入剪贴板失败。")
        finally:
            user32.CloseClipboard()

    def _get_clipboard(self) -> str:
        for _ in range(20):
            if user32.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            return ""
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = kernel32.GlobalLock(h)
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()

    def _force_foreground(self, hwnd: int) -> bool:
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
                self._sleep(0.4)
            fg = user32.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            tid_target = user32.GetWindowThreadProcessId(hwnd, None)
            cur = kernel32.GetCurrentThreadId()
            attached: list[int] = []
            for tid in (tid_fg, tid_target):
                if tid and tid != cur and tid not in attached:
                    if user32.AttachThreadInput(cur, tid, True):
                        attached.append(tid)
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.SetFocus(hwnd)
            finally:
                for tid in attached:
                    user32.AttachThreadInput(cur, tid, False)
            self._sleep(0.35)
            return user32.GetForegroundWindow() == hwnd
        except Exception:
            return False

    # ---------- 窗口查找 ----------
    def find_main_window(self) -> WindowInfo | None:
        if self.preferred_hwnd:
            for w in enum_windows():
                if w.hwnd != self.preferred_hwnd:
                    continue
                if w.title == "微信" and w.cls.startswith("Qt") and w.rect.width >= 600 and w.rect.height >= 500:
                    return w
            self.preferred_hwnd = None
        candidates = []
        for w in enum_windows():
            if w.title != "微信" or not w.cls.startswith("Qt"):
                continue
            if w.rect.width < 600 or w.rect.height < 500:
                continue
            path = _process_path(w.pid).lower()
            if path and not path.endswith("weixin.exe"):
                continue
            candidates.append(w)
        if not candidates:
            return None
        candidates.sort(key=lambda w: w.area, reverse=True)
        return candidates[0]

    def _wechat_pids(self) -> set[int]:
        pids: set[int] = set()
        for w in enum_windows():
            if w.cls.startswith("Qt") or w.cls == "#32770":
                if _process_path(w.pid).lower().endswith("weixin.exe"):
                    pids.add(w.pid)
        main = self.find_main_window()
        if main:
            pids.add(main.pid)
        return pids

    def find_file_dialog(self, pid: int | None = None) -> WindowInfo | None:
        for w in enum_windows():
            if not user32.IsWindowVisible(w.hwnd):
                continue
            if w.cls != "#32770":
                continue
            if w.title not in ("选择文件", "打开", "Open", "Select File"):
                continue
            if w.rect.width < 400 or w.rect.height < 250:
                continue
            if pid is not None and w.pid != pid:
                continue
            return w
        return None

    def find_composer(self, pid: int | None = None) -> WindowInfo | None:
        for w in enum_windows():
            if not user32.IsWindowVisible(w.hwnd):
                continue
            if w.title != "朋友圈" or not w.cls.startswith("Qt"):
                continue
            if w.rect.width < 280 or w.rect.height < 320:
                continue
            if pid is not None and w.pid != pid:
                continue
            return w
        return None

    def find_confirm_dialog(self, pid: int | None = None) -> WindowInfo | None:
        for w in enum_windows():
            if not user32.IsWindowVisible(w.hwnd):
                continue
            if not w.cls.startswith("Qt"):
                continue
            if w.title not in ("Weixin", "微信"):
                continue
            if w.rect.width > 520 or w.rect.height > 360:
                continue
            if w.rect.width < 200 or w.rect.height < 120:
                continue
            if pid is not None and w.pid != pid:
                continue
            return w
        return None

    def _wait(self, fn, timeout: float, what: str):
        end = time.time() + timeout
        while time.time() < end:
            value = fn()
            if value:
                return value
            time.sleep(0.25)
        raise AutomationError(f"等待{what}超时（{timeout:.0f} 秒）。")

    def _normalize_window(self, main: WindowInfo) -> None:
        """最大化时还原即可；不要主动缩放/移动用户的微信窗口。"""
        try:
            if user32.IsZoomed(main.hwnd):
                user32.ShowWindow(main.hwnd, SW_RESTORE)
                self._sleep(0.8)
        except Exception:
            pass

    def _close_obstructions(self, main: WindowInfo) -> None:
        """关闭/最小化会挡住微信主窗口的“设置/笔记”等附属窗口。

        设置窗口是独立顶层窗口，打开时鼠标点击会落在它上面，
        导致自动化看似点了相机，实际点到了设置/笔记。
        """
        for w in enum_windows():
            if not user32.IsWindowVisible(w.hwnd):
                continue
            if w.hwnd == main.hwnd or w.pid != main.pid:
                continue
            if not w.cls.startswith("Qt"):
                continue
            if w.rect.width > 900 or w.rect.height > 900:
                continue
            title = (w.title or "").strip()
            if title in ("设置", "Preferences", "Settings"):
                self.log("INFO", f"关闭挡住的微信设置窗口：{w.hwnd}")
                user32.PostMessageW(w.hwnd, 0x0010, 0, 0)  # WM_CLOSE
                self._sleep(0.7)
            elif title in ("笔记", "Notes"):
                self.log("INFO", f"最小化独立笔记窗口：{w.hwnd}")
                user32.ShowWindow(w.hwnd, 6)  # SW_MINIMIZE
                self._sleep(0.4)

    # ---------- 启动 / 就绪 ----------
    def ensure_ready(self) -> WindowInfo:
        main = self.find_main_window()
        if main is None:
            if self._wechat_pids():
                raise WeChatNotReady(
                    "微信已启动，但主窗口在登录页/被弹窗挡住。请先在电脑上进入微信主界面。"
                )
            if self.cfg.get("start_wechat_if_needed", True):
                exe = Path(str(self.cfg.get("wechat_exe", r"D:\Weixin\Weixin.exe")))
                if exe.exists():
                    import subprocess

                    self.log("INFO", "微信主窗口未找到，尝试启动微信…")
                    try:
                        subprocess.Popen([str(exe)], cwd=str(exe.parent))
                    except Exception as exc:
                        raise WeChatNotReady(f"启动微信失败：{exc}") from exc
                else:
                    raise WeChatNotReady(f"找不到微信程序：{exe}")
            else:
                raise WeChatNotReady("微信没有运行。")
            try:
                main = self._wait(self.find_main_window, 60, "微信主窗口")
            except AutomationError as exc:
                raise WeChatNotReady(
                    "还没有检测到已登录的微信主窗口。请先在电脑上登录微信，再启动定时任务。"
                ) from exc
        if not user32.IsWindowVisible(main.hwnd):
            user32.ShowWindow(main.hwnd, 9)  # SW_RESTORE
            self._sleep(0.4)
        if is_screen_locked():
            raise WeChatNotReady("电脑当前处于锁屏状态，微信界面无法操作。解锁后会自动重试。")
        return main

    # ---------- 发布主流程 ----------
    def _header_right(self, r: RECT) -> int:
        """返回朋友圈深色相机栏的右边界（屏幕坐标）。

        微信右侧可能打开“笔记/收藏”等白色侧栏，此时相机不在窗口最右边，
        而是在深色横栏与白色侧栏的交界处。用列亮度找到这个边界。
        """
        try:
            from PIL import ImageGrab
        except Exception:
            return r.right
        x0 = r.left + int(r.width * 0.38)
        try:
            img = ImageGrab.grab(
                bbox=(x0, r.top + 32, r.right, r.top + 88),
                all_screens=True,
            ).convert("L")
        except Exception:
            return r.right
        w, h = img.size
        if w < 120:
            return r.right
        pix = img.load()
        means = []
        for x in range(w):
            s = 0
            for y in range(h):
                s += pix[x, y]
            means.append(s / h)
        # 找第一段连续 80 列、平均亮度 > 235 的白色侧栏
        run = 80
        for x in range(0, max(1, w - run)):
            if sum(means[x:x + run]) / run > 235:
                return x0 + x
        return r.right

    def _find_color_icon(self, r: RECT, name: str, box: tuple[int, int, int, int],
                         threshold: float = 12.0) -> tuple[int, int] | None:
        """在小区域里按彩色图标模板找中心点。"""
        try:
            from PIL import Image, ImageGrab
        except Exception:
            return None
        tpl_path = Path(__file__).resolve().parent / "assets" / name
        if not tpl_path.exists():
            return None
        try:
            tpl = Image.open(tpl_path).convert("RGB")
            reg = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
        except Exception:
            return None
        tw, th = tpl.size
        rw, rh = reg.size
        if rw < tw or rh < th:
            return None
        rp, tp = reg.load(), tpl.load()
        best = (999.0, None)
        for y in range(0, rh - th + 1):
            for x in range(0, rw - tw + 1):
                s = 0
                n = 0
                for ty in range(0, th, 2):
                    for tx in range(0, tw, 2):
                        r1, g1, b1 = rp[x + tx, y + ty]
                        r2, g2, b2 = tp[tx, ty]
                        s += abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
                        n += 3
                score = s / max(1, n)
                if score < best[0]:
                    best = (score, (box[0] + x + tw // 2, box[1] + y + th // 2))
        return best[1] if best[0] <= threshold else None

    def _find_camera_point(self, r: RECT, header_right: int | None = None) -> tuple[int, int] | None:
        """用相机图标模板找相机中心，避免把铃铛/笔记图标当成相机。"""
        try:
            from PIL import Image, ImageGrab
        except Exception:
            return None
        tpl_path = Path(__file__).resolve().parent / "assets" / "camera.png"
        if not tpl_path.exists():
            return None
        hx = header_right if header_right is not None else self._header_right(r)
        x0 = max(r.left, hx - 170)
        x1 = max(r.left + 30, hx - 4)
        y0 = r.top + 30
        y1 = r.top + 90
        if x1 - x0 < 40 or y1 - y0 < 40:
            return None
        try:
            tpl = Image.open(tpl_path).convert("L")
            reg = ImageGrab.grab(bbox=(x0, y0, x1, y1), all_screens=True).convert("L")
        except Exception:
            return None
        tw, th = tpl.size
        tp = tpl.load()
        bright = [(x, y) for y in range(th) for x in range(tw) if tp[x, y] > 230]
        bg = [(x, y) for y in range(th) for x in range(tw) if tp[x, y] <= 230]
        if not bright or not bg:
            return None
        cx = sum(x for x, _ in bright) / len(bright)
        cy = sum(y for _, y in bright) / len(bright)
        rw, rh = reg.size
        if rw < tw or rh < th:
            return None
        rp = reg.load()
        best = (0.0, None)
        for y in range(0, rh - th + 1):
            for x in range(0, rw - tw + 1):
                b = sum(1 for mx, my in bright if rp[x + mx, y + my] > 220) / len(bright)
                g = sum(1 for mx, my in bg if rp[x + mx, y + my] < 220) / len(bg)
                score = b * g
                if score > best[0]:
                    best = (score, (x0 + x + int(cx), y0 + y + int(cy)))
        return best[1] if best[0] >= 0.85 else None

    def _camera_x(self, r: RECT, header_right: int | None = None) -> int | None:
        """只认相机模板；找不到就报错，绝不回退去点笔记/铃铛。"""
        hx = header_right if header_right is not None else self._header_right(r)
        pt = self._find_camera_point(r, hx)
        return pt[0] if pt else None

    def _header_is_dark(self, r: RECT, header_right: int) -> bool:
        try:
            from PIL import ImageGrab
        except Exception:
            return True
        x0 = max(r.left, header_right - 230)
        x1 = max(r.left + 30, header_right - 100)
        if x1 - x0 < 40:
            return False
        try:
            img = ImageGrab.grab(
                bbox=(x0, r.top + 36, x1, r.top + 80),
                all_screens=True,
            ).convert("L")
        except Exception:
            return True
        px = list(img.getdata())
        return bool(px) and (sum(px) / len(px)) < 230

    def _moments_ready(self, r: RECT) -> bool:
        """判断朋友圈是否真的加载好：深色相机栏 + 右侧白色相机图标。"""
        header_right = self._header_right(r)
        header_left = r.left + int(r.width * 0.20)
        if header_right - header_left < 180:
            return False
        if not self._header_is_dark(r, header_right):
            return False
        return self._find_camera_point(r, header_right) is not None

    def _open_moments_page(self, main: WindowInfo, r: RECT) -> None:
        # 微信 4 的“笔记”是主窗口内的侧栏，不是独立窗口；先按 Esc 收起，
        # 否则右侧笔记栏会挡住相机，旧逻辑会误点笔记图标。
        self._tap(0x1B)
        self._sleep(0.4)
        # 朋友圈已经开着就直接用，避免每次点击入口导致重新加载/开新窗口
        if self._moments_ready(r):
            self.log("INFO", "朋友圈已在当前页。")
            return

        # 1) 如果“发现”面板已经展开，直接用彩色朋友圈图标定位
        panel_box = (r.left, r.top + 65, r.left + min(360, int(r.width * 0.36)), r.top + 180)
        pt = self._find_color_icon(r, "moments_icon.png", panel_box, threshold=12)
        if pt:
            self.log("INFO", f"点击朋友圈图标：{pt}")
            self._click(*pt)
            if self._wait_ready(r, 5):
                return

        # 2) 面板没展开：先用绿色罗盘图标打开“发现”
        compass_box = (r.left, r.top + 90, r.left + 70, r.top + 460)
        cp = self._find_color_icon(r, "discover_green.png", compass_box, threshold=12)
        if cp:
            self.log("INFO", f"点击发现图标：{cp}")
            self._click(*cp)
            self._sleep(0.8)
            pt = self._find_color_icon(r, "moments_icon.png", panel_box, threshold=12)
            if pt:
                self.log("INFO", f"点击朋友圈图标：{pt}")
                self._click(*pt)
                if self._wait_ready(r, 5):
                    return

        # 3) 模板不可用/缩放异常时的旧坐标回退
        row = (r.left + 150, r.top + 110)
        self._click(*row)
        if self._wait_ready(r, 4):
            return
        for cy in (200, 255, 281, 320, 360):
            self._click(r.left + 24, r.top + cy)
            self._sleep(0.5)
            self._click(*row)
            if self._wait_ready(r, 4):
                return

        shot = self.save_screenshot("moments_not_ready", r.as_tuple())
        raise AutomationError(
            f"朋友圈页面没有加载完成（右上角看不到相机栏），请检查微信网络/账号状态。截图：{shot}"
        )

    def _wait_ready(self, r: RECT, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self._moments_ready(r):
                self._sleep(0.8)
                if self._moments_ready(r):
                    self._sleep(0.3)
                    return True
            time.sleep(0.3)
        return False

    def publish(
        self,
        text: str,
        images: Iterable[Path],
        *,
        preview_only: bool = False,
        post_id: str = "",
    ) -> Path | None:
        """发布一条朋友圈。

        preview_only=True 时只把文案和图片填进编辑框，不点“发表”，
        返回预览截图路径，编辑框保持打开供人工检查。
        """
        images = [Path(p) for p in images]
        if not images:
            raise AutomationError("这条计划没有图片；当前版本要求至少 1 张图片。")
        if self.find_file_dialog():
            raise AutomationError("微信当前有文件选择框未关闭，请先手动关闭。")
        if self.find_composer():
            raise AutomationError("微信当前已有未关闭的朋友圈编辑框，请先手动处理，避免覆盖你的草稿。")

        main = self.ensure_ready()
        pid = main.pid
        self.log("INFO", "聚焦微信主窗口…")
        if not self._force_foreground(main.hwnd):
            self.log("WARNING", "微信窗口未能置于最前，仍尝试继续。")
        self._close_obstructions(main)

        # 1. 打开“朋友圈”页（页面是异步加载的，必须等相机栏出现）
        r = _get_rect(main.hwnd)
        self._open_moments_page(main, r)
        before = self._failed_banner_count(r)

        # 2. 点右上角相机，打开文件选择框（不同窗口尺寸/侧栏下位置略有差异）
        self.log("INFO", "打开朋友圈相机…")
        dialog = None
        hx = self._header_right(r)
        cx = self._camera_x(r, hx)
        self.log("INFO", f"界面定位：窗口={r.as_tuple()} 深色栏右边界={hx} 相机={cx}")
        candidates = []
        if cx:
            candidates += [(cx, 56), (cx, 62), (cx - 4, 56)]
        candidates += [
            (hx - 38, 56),
            (hx - 45, 56),
            (r.right - 48, 56),
            (r.right - 48, 70),
        ]
        for x, y in candidates:
            self._click(x, r.top + y)
            try:
                dialog = self._wait(lambda: self.find_file_dialog(pid), 5, "图片选择框")
                break
            except AutomationError:
                continue
        if dialog is None:
            shot = self.save_screenshot("no_file_dialog")
            raise AutomationError(f"没有打开图片选择框。截图：{shot}")

        # 3. 选择图片
        self.log("INFO", f"选择 {len(images)} 张图片…")
        self._select_files(dialog, images)
        composer = self._wait(lambda: self.find_composer(pid), 30, "朋友圈编辑框")
        self._sleep(1.0)
        composer = self.find_composer(pid) or composer

        # 4. 填文案
        self.log("INFO", "填写文案…")
        self._fill_text(composer, text)
        self._sleep(0.4)

        privacy = str(self.cfg.get("privacy", "keep")).lower()
        if privacy in ("public", "private"):
            self.log("INFO", f"设置可见范围：{'私密' if privacy == 'private' else '公开'}…")
            self._set_privacy(composer, private=(privacy == "private"))
            self._sleep(0.5)
        else:
            self.log("INFO", "保持微信当前的可见范围设置。")

        # 5. 截图存档
        cr = _get_rect(composer.hwnd)
        tag = post_id or "manual"
        shot = self.save_screenshot(f"{tag}_filled", cr.as_tuple())
        self.last_preview = shot
        self.log("INFO", f"发布前预览截图：{shot}")

        if preview_only:
            self.log("INFO", "测试模式：已填充，未点击发表。")
            return shot

        # 微信需要一点时间处理图片；太快点发表容易变成“照片未发送”
        settle = float(self.cfg.get("image_settle_seconds", 4) or 0)
        if settle > 0:
            self.log("INFO", f"等待图片处理 {settle:.0f} 秒…")
            self._sleep(settle)

        # 6. 点发表
        btn = self._find_publish_button(cr)
        if btn is None:
            raise AutomationError(f"没有定位到绿色“发表”按钮。截图：{shot}")
        bx, by = btn
        self.log("INFO", f"点击发表按钮 ({bx},{by})…")
        self._click(bx, by)
        self._sleep(1.2)

        # 7. 等待编辑框关闭，并检查是否出现“照片未发送”
        end = time.time() + float(self.cfg.get("publish_wait_seconds", 35) or 35)
        while time.time() < end:
            if self.find_composer(pid) is None:
                # 等待朋友圈信息流重新出现；空白页无法判断成败，不能报成功
                feed_ok = False
                ready_until = time.time() + 10
                while time.time() < ready_until:
                    if self._moments_ready(_get_rect(main.hwnd)):
                        feed_ok = True
                        break
                    time.sleep(0.5)
                if not feed_ok:
                    shot2 = self.save_screenshot("feed_not_ready_after_publish")
                    raise AutomationError(
                        "发表后朋友圈没有刷新出来，无法确认是否成功。"
                        f"请手动检查朋友圈。截图：{shot2}"
                    )
                # 微信的“照片未发送”提示会延迟几秒才出现，持续观察
                check_until = time.time() + float(self.cfg.get("failure_check_seconds", 15) or 15)
                while time.time() < check_until:
                    after = self._failed_banner_count(_get_rect(main.hwnd))
                    if after > before:
                        shot2 = self.save_screenshot("photo_not_sent")
                        raise AutomationError(
                            "微信提示“照片未发送”：图片上传失败，朋友圈没有发出去。"
                            f"请检查网络/账号图片权限。截图：{shot2}"
                        )
                    time.sleep(1.0)
                small = self._find_unexpected_dialog(pid)
                if small:
                    shot2 = self.save_screenshot("after_publish_dialog")
                    raise AutomationError(f"发表后微信弹出了提示窗口，请查看截图：{shot2}")
                self.log("INFO", "编辑框已关闭，未检测到“照片未发送”，发表完成。")
                self._force_foreground(main.hwnd)
                return shot
            time.sleep(0.6)

        shot2 = self.save_screenshot("publish_timeout")
        raise AutomationError(f"发表后编辑框仍未关闭，可能网络较慢或需要人工确认。截图：{shot2}")

    def _failed_banner_count(self, main_rect: RECT) -> int:
        """数一数朋友圈右侧的深灰“照片未发送”提示条。

        微信图片上传失败时会在信息流上方叠加一条深灰色提示，不会创建普通帖子。
        通过发布前后数量变化判断本次是否失败，避免把“编辑框关了”误判成成功。
        """
        try:
            from PIL import ImageGrab
        except Exception:
            return 0
        r = main_rect
        x1 = r.left + int(r.width * 0.50)
        x2 = r.left + int(r.width * 0.90)
        y1 = r.top + int(r.height * 0.44)
        y2 = r.top + int(r.height * 0.68)
        try:
            img = ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True).convert("RGB")
        except Exception:
            return 0
        w, h = img.size
        if w < 80 or h < 60:
            return 0
        pix = img.load()
        rows: list[int] = []
        for y in range(h):
            dark = 0
            red = 0
            for x in range(0, w, 2):
                rr, gg, bb = pix[x, y]
                if 58 <= rr <= 105 and abs(rr - gg) <= 8 and abs(gg - bb) <= 8:
                    dark += 1
                elif rr >= 150 and gg <= 115 and bb <= 115:
                    red += 1
            if dark >= 20 and red >= 2:
                rows.append(y)
        if not rows:
            return 0
        groups: list[list[int]] = []
        current: list[int] = []
        for y in rows:
            if current and y - current[-1] > 4:
                groups.append(current)
                current = []
            current.append(y)
        if current:
            groups.append(current)
        return sum(1 for g in groups if len(g) >= 18 and len(g) <= 80)

    def _find_unexpected_dialog(self, pid: int) -> WindowInfo | None:
        for w in enum_windows():
            if not user32.IsWindowVisible(w.hwnd) or w.pid != pid:
                continue
            if not w.cls.startswith("Qt"):
                continue
            if w.title in ("微信", "朋友圈"):
                continue
            if w.rect.width <= 600 and w.rect.height <= 400 and w.rect.width >= 180:
                return w
        return None

    def _select_files(self, dialog: WindowInfo, images: list[Path]) -> None:
        """在文件对话框里进入计划自己的“图片”文件夹，再全选打开。

        不依赖文件名输入框的焦点/历史内容，避免选中上一次残留的图片。
        """
        self._force_foreground(dialog.hwnd)
        self._sleep(0.35)
        folder = str(images[0].parent)

        # 清掉文件名输入框里可能残留的旧路径（Explorer 对话框的 edt1 = 0x480）
        edit = user32.GetDlgItem(dialog.hwnd, 0x480)
        if edit:
            user32.SetWindowTextW(edit, "")

        # Ctrl+L 地址栏 -> 全选旧内容 -> 粘贴目标文件夹 -> 回车
        self._chord([VK_CONTROL], VK_L)
        self._sleep(0.35)
        self._chord([VK_CONTROL], VK_A)
        self._sleep(0.15)
        self._set_clipboard(folder)
        self._chord([VK_CONTROL], VK_V)
        self._sleep(0.4)
        self._tap(VK_RETURN)
        self._sleep(1.4)

        # 点击文件列表区域，确保焦点在列表；再全选当前文件夹里的所有图片
        r = _get_rect(dialog.hwnd)
        self._click(r.left + int(r.width * 0.45), r.top + int(r.height * 0.42))
        self._sleep(0.35)
        self._chord([VK_CONTROL], VK_A)
        self._sleep(0.4)
        self._tap(VK_RETURN)
        self._sleep(1.0)

        end = time.time() + 8
        while time.time() < end:
            if self.find_file_dialog(dialog.pid) is None:
                return
            time.sleep(0.3)
        self._tap(VK_RETURN)
        end = time.time() + 6
        while time.time() < end:
            if self.find_file_dialog(dialog.pid) is None:
                return
            time.sleep(0.3)
        raise AutomationError("文件选择框没有关闭，图片可能未被选中。")

    def _fill_text(self, composer: WindowInfo, text: str) -> None:
        r = _get_rect(composer.hwnd)
        # 编辑框在窗口上部，点左侧空白处
        self._click(r.left + int(r.width * 0.22), r.top + int(r.height * 0.13))
        self._sleep(0.25)
        self._chord([VK_CONTROL], VK_A)
        self._tap(VK_DELETE)
        self._sleep(0.15)
        self._set_clipboard(text)
        self._chord([VK_CONTROL], VK_V)
        self._sleep(0.8)

        if not self.cfg.get("verify_text", True):
            return

        expected = text.replace("\r\n", "\n").strip()
        for attempt in range(2):
            self._set_clipboard("__WXROBOT_CLIP_SENTINEL__")
            self._chord([VK_CONTROL], VK_A)
            self._chord([VK_CONTROL], VK_C)
            self._sleep(0.25)
            actual = self._get_clipboard().replace("\r\n", "\n").strip()
            if self._text_matches(expected, actual):
                self._tap(VK_END)
                return
            self.log("WARNING", f"文案回读校验失败（第 {attempt + 1} 次），重新粘贴。")
            self._click(r.left + int(r.width * 0.22), r.top + int(r.height * 0.13))
            self._chord([VK_CONTROL], VK_A)
            self._tap(VK_DELETE)
            self._set_clipboard(text)
            self._chord([VK_CONTROL], VK_V)
            self._sleep(0.8)

        raise AutomationError("文案没有成功写入编辑框（回读校验失败），已停止，未发表。")

    def _set_privacy(self, composer: WindowInfo, *, private: bool) -> None:
        """把“谁可以看”切成“公开”或“私密”。"""
        r = _get_rect(composer.hwnd)
        # “谁可以看”行，约在编辑框 69% 高度
        self._click(r.left + int(r.width * 0.50), r.top + int(r.height * 0.69))
        self._sleep(0.8)
        # 面板第一行“公开”、第二行“私密”
        self._click(r.left + 85, r.top + (205 if private else 157))
        self._sleep(0.45)
        # “确定”按钮
        self._click(r.left + 145, r.top + 424)
        self._sleep(0.7)

    @staticmethod
    def _text_matches(expected: str, actual: str) -> bool:
        if not actual:
            return False
        exp = expected.strip()
        act = actual.strip()
        if exp == act:
            return True
        if len(exp) <= 40:
            return exp in act
        return exp[:30] in act and exp[-30:] in act

    def _find_publish_button(self, rect: RECT) -> tuple[int, int] | None:
        try:
            from PIL import ImageGrab
        except Exception:
            return None
        try:
            img = ImageGrab.grab(bbox=rect.as_tuple(), all_screens=True).convert("RGB")
        except Exception:
            return None
        w, h = img.size
        if w < 50 or h < 50:
            return None
        pix = img.load()
        # 只扫描下半部分，避开蓝色图片等内容
        rows: dict[int, list[int]] = {}
        for y in range(int(h * 0.55), h):
            xs = []
            for x in range(w):
                r, g, b = pix[x, y]
                if g >= 130 and (g - r) >= 45 and (g - b) >= 25 and r <= 145:
                    xs.append(x)
            if len(xs) >= 25:
                rows[y] = xs
        if not rows:
            return None

        # 按连续行分组，找横向的绿色按钮
        groups: list[list[int]] = []
        current: list[int] = []
        for y in sorted(rows):
            if current and y - current[-1] > 3:
                groups.append(current)
                current = []
            current.append(y)
        if current:
            groups.append(current)

        candidates = []
        for ys in groups:
            xs_all = [x for y in ys for x in rows[y]]
            if not xs_all:
                continue
            x1, x2 = min(xs_all), max(xs_all)
            bw, bh = x2 - x1 + 1, ys[-1] - ys[0] + 1
            if 45 <= bw <= 260 and 16 <= bh <= 80 and 1.4 <= bw / max(1, bh) <= 7.0:
                candidates.append((ys[-1], x1, x2, ys[0], bw, bh))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0], reverse=True)
        _, x1, x2, y1, bw, bh = candidates[0]
        return rect.left + (x1 + x2) // 2, rect.top + (y1 + candidates[0][0]) // 2

    # ---------- 清理 ----------
    def cleanup(self, *, keep_draft: bool = False) -> None:
        """尽量关闭文件框/编辑框，避免阻塞下一次发布。不会点“发表”。"""
        pid = None
        main = self.find_main_window()
        if main:
            pid = main.pid

        dialog = self.find_file_dialog(pid)
        if dialog:
            self._force_foreground(dialog.hwnd)
            self._tap(VK_ESCAPE)
            self._sleep(0.5)

        confirm = self.find_confirm_dialog(pid)
        if confirm:
            self._click_confirm(confirm, keep=keep_draft)
            self._sleep(0.6)

        composer = self.find_composer(pid)
        if composer:
            self._force_foreground(composer.hwnd)
            self._tap(VK_ESCAPE)
            self._sleep(0.7)
            confirm = self.find_confirm_dialog(pid)
            if confirm:
                self._click_confirm(confirm, keep=keep_draft)
                self._sleep(0.6)
            composer = self.find_composer(pid)
            if composer:
                r = _get_rect(composer.hwnd)
                # 取消按钮在底部右侧，绿色发表按钮右边
                self._click(r.right - 100, r.bottom - 40)
                self._sleep(0.7)
                confirm = self.find_confirm_dialog(pid)
                if confirm:
                    self._click_confirm(confirm, keep=keep_draft)

        small = self._find_unexpected_dialog(pid) if pid else None
        if small:
            self._force_foreground(small.hwnd)
            self._tap(VK_ESCAPE)

    def _click_confirm(self, w: WindowInfo, *, keep: bool) -> None:
        r = _get_rect(w.hwnd)
        # 实测 330x199：保留在左，不保存在右
        fx = 0.32 if keep else 0.70
        fy = 0.64
        self._click(r.left + int(r.width * fx), r.top + int(r.height * fy))

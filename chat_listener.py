"""微信群 AI 聊天监听：截图 + Windows OCR，读取最后一条左侧消息并回复。"""
from __future__ import annotations

import json
import random
import re
import subprocess
import threading
import time
from pathlib import Path

import store
from ai import DeepSeekClient
from group_ui import GroupUI
from operation import GATE
from wechat_ui import WeChatUI, _get_rect, enable_dpi_awareness

ROOT = Path(__file__).resolve().parent
OCR_SCRIPT = ROOT / "ocr_helper.ps1"
CROP = (0.42, 0.11, 0.98, 0.78)
NO_EMOTION_PROMPT = (
    "你是一个没有情绪的人机测试助手。只陈述事实、结论和必要步骤，"
    "不使用表情、感叹、寒暄或情绪词。回答简短、机械、直接。"
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def read_last_message(hwnd: int, image_path: Path) -> tuple[str, str]:
    main = WeChatUI(store.load_config(), hwnd=hwnd).ensure_ready()
    WeChatUI(store.load_config(), hwnd=hwnd)._force_foreground(main.hwnd)
    time.sleep(0.25)
    r = _get_rect(main.hwnd)
    x1 = r.left + int(r.width * CROP[0])
    y1 = r.top + int(r.height * CROP[1])
    x2 = r.left + int(r.width * CROP[2])
    y2 = r.top + int(r.height * CROP[3])
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(image_path)

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT), "-Path", str(image_path)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    raw = proc.stdout.strip()
    start = raw.find("[")
    if start < 0:
        return "", raw[-300:]
    try:
        lines = json.loads(raw[start:])
    except Exception:
        return "", raw[-300:]
    w = max(1, x2 - x1)
    good = []
    for item in lines:
        text = _clean(item.get("text", ""))
        if not text:
            continue
        x, y = int(item.get("x", 0)), int(item.get("y", 0))
        if y < 35:
            continue
        if x < 45 or x > w * 0.72:
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
            continue
        if any(k in text for k in ("加入群聊", "群聊参与者", "以上是", "撤回了一条", "拍了拍")):
            continue
        good.append((y, x, text))
    if not good:
        return "", raw[-300:]
    good.sort()
    # 取最后一块相邻文本；OCR 会把一句话拆成多行。
    group = [good[-1]]
    for y, x, text in reversed(good[:-1]):
        if group[0][0] - y <= 20 and abs(group[0][1] - x) <= 35:
            group.append((y, x, text))
        else:
            break
    group.reverse()
    message = _clean(" ".join(t for _, _, t in group))
    key = f"{message}|{good[-1][0]}"
    return message, key


class GroupChatListener:
    def __init__(self, app, hwnd: int, nickname: str, reply_all: bool, interval: float = 4.0,
                 probability: int = 30):
        self.app = app
        self.hwnd = hwnd
        self.nickname = nickname.strip()
        self.reply_all = reply_all
        self.probability = max(0, min(100, int(probability)))
        self.interval = max(2.0, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_key = ""
        self._last_reply_at = 0.0
        self._image = ROOT / "data" / "ocr_current.png"
        self.last_error = ""

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="group-ai-listener", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        # 启动时把当前最后一条消息作为基线，避免回复历史消息。
        try:
            text, key = read_last_message(self.hwnd, self._image)
            self._last_key = key
            self.app.after(0, lambda: self.app.bot_listen_status.configure(text=f"监听中 · 基线：{text[:30]}"))
        except Exception as exc:
            self.last_error = str(exc)
            self.app.after(0, lambda e=str(exc): self.app.bot_listen_status.configure(text=f"监听失败：{e[:80]}"))
            return

        while not self._stop.is_set():
            time.sleep(self.interval)
            if self._stop.is_set():
                break
            try:
                text, key = read_last_message(self.hwnd, self._image)
                store.log("INFO", f"群监听读取：{text[:40]}")
                if not text or key == self._last_key:
                    continue
                self._last_key = key
                if not self._should_reply(text):
                    continue
                if time.time() - self._last_reply_at < 8:
                    continue
                self.app.after(0, lambda t=text: self.app.bot_listen_status.configure(text=f"收到：{t[:50]}"))
                cfg = store.load_config()
                ai = DeepSeekClient(cfg)
                if not ai.available:
                    raise RuntimeError("DeepSeek API 未配置")
                persona = (cfg.get("bot", {}) or {}).get("persona", "")
                no_emotion = bool((cfg.get("bot", {}) or {}).get("no_emotion", False))
                system = NO_EMOTION_PROMPT if no_emotion else (persona or "你是烘焙助手，回答简短自然。")
                answer = ai.chat([
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ]).strip()
                if not answer:
                    raise RuntimeError("AI 没有返回内容")
                store.robot_event("群监听AI", answer)
                GATE.begin_robot()
                try:
                    GroupUI(cfg, hwnd=self.hwnd).send_current(answer)
                finally:
                    GATE.end_robot()
                self._last_reply_at = time.time()
                store.robot_event("群监听已发送", answer, True)
                self.app.after(0, lambda a=answer: self.app.bot_listen_status.configure(text=f"已回复：{a[:50]}"))
            except Exception as exc:
                self.last_error = str(exc)
                store.robot_event("群监听失败", str(exc), False)
                self.app.after(0, lambda e=str(exc): self.app.bot_listen_status.configure(text=f"失败：{e[:90]}"))

    def _should_reply(self, text: str) -> bool:
        if self.reply_all:
            return True
        # @ 机器人是明确点名，必须 100% 回复。
        if "@" in text:
            return True
        # 其他普通消息按设置的概率回复。
        return random.SystemRandom().random() * 100 < self.probability

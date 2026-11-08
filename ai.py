"""DeepSeek 群回复助手（只手动触发，不自动生成朋友圈/群文案）。"""
from __future__ import annotations

import os

import store

try:
    import requests
except Exception:
    requests = None


class DeepSeekClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        ai = cfg.get("ai", {})
        self.key = store.load_api_key()
        self.base = (ai.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.model = ai.get("model") or "deepseek-chat"
        self.timeout = int(ai.get("timeout", 60) or 60)
        self.temperature = float(ai.get("temperature", 0.8) or 0.8)

    @property
    def available(self) -> bool:
        return bool(self.key and requests)

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        if not self.available:
            raise RuntimeError("未配置 DeepSeek API Key，请到“设置”里填写")
        r = requests.post(
            f"{self.base}/chat/completions",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature if temperature is None else temperature,
                "stream": False,
            },
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"DeepSeek HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()

    def reply(self, message: str) -> str:
        prompt = (
            "你在一个烘焙内部群里，用烘焙老师的身份回复群友。"
            f"对方说：{message[:800]}\n"
            "要求：先回应问题，再给一个可执行的小建议；80 字以内；不要 Markdown；不要引号；不要解释。"
        )
        return self.chat([
            {"role": "system", "content": "你是中文烘焙老师，回答实用、简短、自然。"},
            {"role": "user", "content": prompt},
        ])

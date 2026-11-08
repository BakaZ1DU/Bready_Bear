"""Bready Bear 本地数据层。

- 配置 / 账号
- 朋友圈、群消息计划队列
- 活动微信账号探测
- 日志
"""
from __future__ import annotations

import json
import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
ACCOUNTS_PATH = ROOT / "accounts.json"
DATA_DIR = ROOT / "data"
PLANS_PATH = DATA_DIR / "plans.json"
DAILY_DIR = DATA_DIR / "daily"
LOG_DIR = ROOT / "logs"
IMAGES_DIR = DATA_DIR / "images"

STATUS_PLANNED = "planned"
STATUS_WAITING = "waiting_account"
STATUS_PUBLISHING = "publishing"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_CANCELED = "canceled"

STATUS_LABELS = {
    STATUS_PLANNED: "待发布",
    STATUS_WAITING: "等待切换账号",
    STATUS_PUBLISHING: "发布中",
    STATUS_SENT: "已发布",
    STATUS_FAILED: "失败",
    STATUS_SKIPPED: "已跳过",
    STATUS_CANCELED: "已取消",
}

KIND_MOMENT = "moment"
KIND_GROUP = "group"
KIND_LABELS = {KIND_MOMENT: "朋友圈", KIND_GROUP: "群消息"}

_lock = threading.RLock()


def now_local() -> datetime:
    return datetime.now().astimezone()


def ensure_dirs() -> None:
    for p in (DATA_DIR, DAILY_DIR, LOG_DIR, IMAGES_DIR):
        p.mkdir(parents=True, exist_ok=True)
    for acct in load_accounts():
        (ROOT / acct.get("image_dir", f"data/accounts/{acct['id']}/images")).mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    defaults = {
        "site_name": "Bready Bear 烘焙管家",
        "ui": {"theme": "light", "language": "zh"},
        "session": {"selected_account": ""},
        "startup": {"minimize": False},
        "image_settle_seconds": 4,
        "publish_wait_seconds": 35,
        "ai": {
            "provider": "deepseek",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "temperature": 1.0,
            "timeout": 60,
        },
        "wechat": {"exe": "", "privacy": "keep", "max_images": 9},
        "group": {"mode": "current", "background": False},
        "bot": {
            "persona_name": "烘焙助手",
            "persona": "你是烘焙店的 AI 助手，说话自然、简短、专业，先回答问题再给一个可执行的小建议。",
        },
        "scheduler": {
            "check_seconds": 5,
            "max_retries": 2,
            "retry_delay_minutes": 10,
        },
    }
    data = _read_json(CONFIG_PATH, {})
    if isinstance(data, dict):
        _deep_update(defaults, data)
    return defaults


def _deep_update(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def save_config(cfg: dict) -> None:
    # API Key 不写进 config.json，避免打包时泄露；单独存 data/secret.json
    cfg.setdefault("ai", {})["api_key"] = ""
    _write_json(CONFIG_PATH, cfg)


def load_api_key() -> str:
    data = _read_json(DATA_DIR / "secret.json", {})
    return str((data or {}).get("deepseek_api_key", "")).strip()


def save_api_key(key: str) -> None:
    ensure_dirs()
    _write_json(DATA_DIR / "secret.json", {"deepseek_api_key": key.strip()})


def load_accounts() -> list[dict]:
    data = _read_json(ACCOUNTS_PATH, [])
    if not isinstance(data, list) or not data:
        data = [
            {"id": "acct_a", "name": "账号A（主号）", "wxid": "", "phone": "手机1", "enabled": True,
             "role": "poster", "moments_per_day": 4, "group_name": "", "persona": "温柔专业的烘焙店长", "note": "",
             "image_dir": "data/accounts/acct_a/images"},
            {"id": "acct_b", "name": "账号B（备用）", "wxid": "", "phone": "手机2", "enabled": True,
             "role": "bot", "moments_per_day": 4, "group_name": "", "persona": "活泼亲切的烘焙老师", "note": "",
             "image_dir": "data/accounts/acct_b/images"},
        ]
        _write_json(ACCOUNTS_PATH, data)
    return data


def save_accounts(accounts: list[dict]) -> None:
    _write_json(ACCOUNTS_PATH, accounts)


def selected_account_id(accounts: list[dict] | None = None) -> str:
    accounts = accounts or load_accounts()
    cfg = load_config()
    sid = (cfg.get("session", {}) or {}).get("selected_account", "")
    posters = [a for a in accounts if a.get("enabled", True) and a.get("role", "poster") in ("poster", "both")]
    if sid and any(a.get("id") == sid for a in posters):
        return sid
    if posters:
        return posters[0]["id"]
    enabled = [a for a in accounts if a.get("enabled", True)]
    return (enabled or accounts)[0]["id"] if accounts else ""


def poster_accounts(accounts: list[dict] | None = None) -> list[dict]:
    accounts = accounts or load_accounts()
    return [a for a in accounts if a.get("enabled", True) and a.get("role", "poster") in ("poster", "both")]


def bot_account(accounts: list[dict] | None = None) -> dict | None:
    accounts = accounts or load_accounts()
    for a in accounts:
        if a.get("enabled", True) and a.get("role") == "bot":
            return a
    return None


def set_selected_account_id(account_id: str) -> None:
    cfg = load_config()
    cfg.setdefault("session", {})["selected_account"] = account_id
    save_config(cfg)


def selected_account(accounts: list[dict] | None = None) -> dict | None:
    accounts = accounts or load_accounts()
    sid = selected_account_id(accounts)
    for a in accounts:
        if a.get("id") == sid:
            return a
    return None


def account_by_id(accounts: list[dict], account_id: str) -> dict | None:
    for a in accounts:
        if a.get("id") == account_id:
            return a
    return None


def active_wxid() -> str:
    """根据 xwechat/login 目录最近修改时间推测当前登录账号。"""
    root = Path(os.environ.get("APPDATA", "")) / "Tencent" / "xwechat" / "login"
    best = ("", 0.0)
    if not root.exists():
        return ""
    for d in root.iterdir():
        if not d.is_dir():
            continue
        key = d / "key_info.dat"
        try:
            mtime = key.stat().st_mtime if key.exists() else d.stat().st_mtime
        except OSError:
            continue
        if mtime > best[1]:
            best = (d.name, mtime)
    return best[0]


def active_account(accounts: list[dict] | None = None) -> dict | None:
    accounts = accounts or load_accounts()
    wxid = active_wxid()
    if not wxid:
        return None
    for a in accounts:
        if a.get("wxid") and a["wxid"] == wxid:
            return a
    return None


def active_hint(accounts: list[dict] | None = None) -> str:
    accounts = accounts or load_accounts()
    a = active_account(accounts)
    if a:
        return a.get("name", a["id"])
    wxid = active_wxid()
    return f"未匹配到账号配置：{wxid}" if wxid else "未检测到登录账号"


def _load_plans() -> list[dict]:
    data = _read_json(PLANS_PATH, [])
    return data if isinstance(data, list) else []


def _save_plans(plans: list[dict]) -> None:
    _write_json(PLANS_PATH, plans)


def list_plans(date: str | None = None, account_id: str | None = None) -> list[dict]:
    with _lock:
        plans = _load_plans()
    if date:
        plans = [p for p in plans if str(p.get("scheduled_at", "")).startswith(date)]
    if account_id:
        plans = [p for p in plans if p.get("account_id") == account_id]
    plans.sort(key=lambda p: p.get("scheduled_at", ""))
    return plans


def get_plan(plan_id: str) -> dict | None:
    for p in _load_plans():
        if p.get("id") == plan_id:
            return p
    return None


def create_plan(account_id: str, kind: str, scheduled_at: datetime, text: str,
                images: list[str] | None = None, group: str = "", source: str = "manual",
                key: str = "") -> dict:
    with _lock:
        plans = _load_plans()
        if key and any(p.get("key") == key and p.get("status") != STATUS_CANCELED for p in plans):
            return next(p for p in plans if p.get("key") == key)
        plan = {
            "id": uuid.uuid4().hex[:12],
            "key": key,
            "account_id": account_id,
            "kind": kind,
            "scheduled_at": scheduled_at.isoformat(timespec="seconds"),
            "status": STATUS_PLANNED,
            "text": text.strip(),
            "images": images or [],
            "group": group,
            "source": source,
            "attempts": 0,
            "last_error": "",
            "created_at": now_local().isoformat(timespec="seconds"),
            "sent_at": "",
            "next_retry_at": "",
        }
        plans.append(plan)
        _save_plans(plans)
        return plan


def update_plan(plan_id: str, **fields) -> dict | None:
    with _lock:
        plans = _load_plans()
        for p in plans:
            if p.get("id") == plan_id:
                p.update(fields)
                _save_plans(plans)
                return p
    return None


def delete_plan(plan_id: str) -> bool:
    with _lock:
        plans = _load_plans()
        kept = [p for p in plans if p.get("id") != plan_id]
        if len(kept) == len(plans):
            return False
        _save_plans(kept)
        return True


def reset_interrupted() -> int:
    with _lock:
        plans = _load_plans()
        n = 0
        for p in plans:
            if p.get("status") == STATUS_PUBLISHING:
                p["status"] = STATUS_PLANNED
                p["last_error"] = "上次发布中断，已恢复"
                n += 1
        if n:
            _save_plans(plans)
        return n


def due_plans(now: datetime | None = None) -> list[dict]:
    now = now or now_local()
    out = []
    for p in list_plans():
        if p.get("status") not in (STATUS_PLANNED, STATUS_WAITING):
            continue
        try:
            when = datetime.fromisoformat(p["scheduled_at"])
        except Exception:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if p.get("next_retry_at"):
            try:
                if datetime.fromisoformat(p["next_retry_at"]) > now:
                    continue
            except Exception:
                pass
        if when <= now:
            out.append(p)
    return out


def daily_planned(account_id: str, date: str) -> list[dict]:
    return [p for p in list_plans(date=date, account_id=account_id)]


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def log(level: str, message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now_local().strftime('%Y-%m-%d %H:%M:%S')} [{level.upper()}] {message}\n"
    with _lock:
        with (LOG_DIR / "breadybear.log").open("a", encoding="utf-8") as f:
            f.write(line)
    print(line, end="")


def read_logs(lines: int = 120) -> list[str]:
    p = LOG_DIR / "breadybear.log"
    if not p.exists():
        return []
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except Exception:
        return []


def robot_event(kind: str, text: str, ok: bool = True, detail: str = "") -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / "robot_console.jsonl"
    item = {
        "time": now_local().strftime("%H:%M:%S"),
        "kind": kind,
        "ok": ok,
        "text": text[:1000],
        "detail": detail[:500],
    }
    with _lock:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            if len(lines) > 500:
                p.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")
        except Exception:
            pass


def read_robot_events(limit: int = 80) -> list[dict]:
    p = DATA_DIR / "robot_console.jsonl"
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    except Exception:
        pass
    return out

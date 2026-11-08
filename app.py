"""Bready Bear 桌面控制台（Tkinter，无手机端/无 Web 服务）。"""
from __future__ import annotations

import os
import ctypes
import json
import shutil
import subprocess
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from PIL import Image, ImageTk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import store
import i18n
from ai import DeepSeekClient
from group_ui import GroupUI
from operation import GATE
from scheduler import Scheduler
from wechat_ui import enable_dpi_awareness, enum_windows

ROOT = Path(__file__).resolve().parent


def _install_i18n_messagebox() -> None:
    for name in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel"):
        orig = getattr(messagebox, name, None)
        if not orig or getattr(orig, "_bready_i18n", False):
            continue

        def make(fn):
            def wrapped(title, message="", *args, **kwargs):
                return fn(i18n.tr_runtime(str(title)), i18n.tr_runtime(str(message)), *args, **kwargs)
            wrapped._bready_i18n = True
            return wrapped

        setattr(messagebox, name, make(orig))


_install_i18n_messagebox()

PALETTES = {
    "light": {
        "bg": "#f4f1ec", "card": "#fffdf9", "card2": "#faf5ee", "sidebar": "#2d231b",
        "side_text": "#eadccd", "side_muted": "#a89786", "accent": "#a56a42",
        "accent2": "#7a4b2a", "text": "#33261c", "muted": "#8b7766", "line": "#e6d9ca",
        "green": "#2f8f5b", "red": "#b5483f", "amber": "#c8873b", "blue": "#3567a5",
        "soft": "#efe3d5",
    },
    "dark": {
        "bg": "#151311", "card": "#211d19", "card2": "#2a241f", "sidebar": "#0d0b09",
        "side_text": "#e6d6c4", "side_muted": "#b9a795", "accent": "#c08a5a",
        "accent2": "#a56a42", "text": "#f2e8dc", "muted": "#b9a795", "line": "#3b3128",
        "green": "#6fbf8f", "red": "#e07a72", "amber": "#e0a45c", "blue": "#7fa8e0",
        "soft": "#33291f",
    },
}


def parse_time(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt).astimezone()
        except ValueError:
            pass
    raise ValueError("时间格式应为 2026-11-08 08:30")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        enable_dpi_awareness()
        self.geometry("1180x900")
        self.minsize(1040, 800)
        try:
            self.iconbitmap(str(ROOT / "app.ico"))
        except Exception:
            pass
        self.cfg = store.load_config()
        store.ensure_dirs()
        self.accounts = store.load_accounts()
        self.selected_id = store.selected_account_id(self.accounts)
        self.ai = DeepSeekClient(self.cfg)
        self.scheduler = Scheduler(self.cfg)
        self.theme_name = self.cfg.get("ui", {}).get("theme", "light")
        i18n.set_lang(self.cfg.get("ui", {}).get("language", "zh"))
        self.title(f"Bready Bear · {i18n.tr('烘焙内容管家')}")
        self._busy_win = None
        self.robot_console_win = None
        if self.theme_name not in PALETTES:
            self.theme_name = "light"
        self.p = PALETTES[self.theme_name]
        self.page = "dashboard"
        self.current_plan: dict | None = None
        self.current_group_plan: dict | None = None
        self.moment_images: list[str] = []
        self._thumb = None
        self._log_len = 0
        self._build_styles()
        self._build_ui()
        self.scheduler.start()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(500, self._tick)
        self.after(1000, self._refresh_logs)
        self._refresh_all()

    # ---------- style ----------
    def _build_styles(self):
        p = self.p
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".", font=("Microsoft YaHei UI", 10), background=p["bg"], foreground=p["text"])
        s.configure("Card.TFrame", background=p["card"])
        s.configure("Card.TLabel", background=p["card"], foreground=p["text"])
        s.configure("Muted.TLabel", background=p["card"], foreground=p["muted"], font=("Microsoft YaHei UI", 9))
        s.configure("TLabel", background=p["bg"], foreground=p["text"])
        s.configure("Treeview", background=p["card"], fieldbackground=p["card"], foreground=p["text"],
                    rowheight=28, borderwidth=0, font=("Microsoft YaHei UI", 9))
        s.configure("Treeview.Heading", background=p["card2"], foreground=p["muted"],
                    font=("Microsoft YaHei UI", 9), borderwidth=0, padding=(6, 6))
        s.map("Treeview", background=[("selected", p["soft"])], foreground=[("selected", p["text"])])
        s.configure("TCombobox", fieldbackground=p["card"], background=p["card2"], foreground=p["text"],
                    arrowcolor=p["text"], padding=6)
        s.map("TCombobox",
              fieldbackground=[("readonly", p["card"]), ("disabled", p["card2"])],
              foreground=[("readonly", p["text"]), ("disabled", p["muted"])],
              background=[("readonly", p["card2"]), ("active", p["soft"])],
              selectbackground=[("readonly", p["soft"])],
              selectforeground=[("readonly", p["text"])])
        s.configure("TEntry", fieldbackground=p["card"], foreground=p["text"], insertcolor=p["text"], padding=6)
        s.map("TEntry",
              fieldbackground=[("readonly", p["card"]), ("disabled", p["card2"])],
              foreground=[("readonly", p["text"]), ("disabled", p["muted"])])
        s.configure("TCheckbutton", background=p["card"], foreground=p["text"])
        s.map("TCheckbutton", background=[("active", p["card"])], foreground=[("active", p["text"])])
        self.option_add("*TCombobox*Listbox.background", p["card"])
        self.option_add("*TCombobox*Listbox.foreground", p["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", p["soft"])
        self.option_add("*TCombobox*Listbox.selectForeground", p["text"])
        s.configure("TScrollbar", background=p["card2"], troughcolor=p["bg"], borderwidth=0)

    def _btn(self, parent, text, command, kind="primary", **kw):
        p = self.p
        bg = {"primary": p["accent"], "ghost": p["card2"], "danger": "#f3dedb" if self.theme_name == "light" else "#3a2623"}[kind]
        fg = {"primary": "#fff", "ghost": p["text"], "danger": p["red"]}[kind]
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=p["accent2"],
                      activeforeground="#fff", relief="flat", bd=0, padx=12, pady=7,
                      font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2", **kw)
        return b

    def _card(self, parent, **kw):
        p = self.p
        return tk.Frame(parent, bg=p["card"], highlightbackground=p["line"], highlightthickness=1, **kw)

    # ---------- UI ----------
    def _build_ui(self):
        p = self.p
        self.configure(bg=p["bg"])
        for w in self.winfo_children():
            w.destroy()

        self.sidebar = tk.Frame(self, bg=p["sidebar"], width=214)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text="B", bg=p["accent"], fg="#fff", font=("Microsoft YaHei UI", 22, "bold"),
                 width=2, height=1).pack(pady=(24, 8))
        tk.Label(self.sidebar, text="Bready Bear", bg=p["sidebar"], fg=p["side_text"],
                 font=("Microsoft YaHei UI", 17, "bold")).pack()
        tk.Label(self.sidebar, text="烘焙内容管家", bg=p["sidebar"], fg=p["side_muted"],
                 font=("Microsoft YaHei UI", 9)).pack(pady=(0, 22))

        self.nav_buttons = {}
        for key, label in (("dashboard", "总览"), ("auto", "自动发送"), ("bot", "机器人"),
                           ("prefs", "设置"), ("developer", "开发者")):
            b = tk.Button(self.sidebar, text="  " + label, anchor="w", bg=p["sidebar"], fg=p["side_text"],
                          activebackground=p["soft"], activeforeground=p["text"], relief="flat", bd=0,
                          padx=18, pady=11, font=("Microsoft YaHei UI", 10), cursor="hand2",
                          command=lambda k=key: self.show_page(k))
            b.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = b

        self.side_status = tk.Label(self.sidebar, text="", bg=p["sidebar"], fg=p["side_muted"],
                                    font=("Microsoft YaHei UI", 8), justify="left", wraplength=180)
        self.side_status.pack(side="bottom", pady=18, padx=14, anchor="w")
        self.theme_btn = tk.Button(self.sidebar, text="切换深色" if self.theme_name == "light" else "切换浅色",
                                   bg=p["sidebar"], fg=p["side_muted"], relief="flat", bd=0,
                                   command=self.toggle_theme, cursor="hand2", font=("Microsoft YaHei UI", 9))
        self.theme_btn.pack(side="bottom", pady=4)

        main = tk.Frame(self, bg=p["bg"])
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=p["bg"], height=76)
        header.pack(fill="x", padx=26, pady=(16, 0))
        header.pack_propagate(False)
        left = tk.Frame(header, bg=p["bg"])
        left.pack(side="left", fill="y")
        self.page_title = tk.Label(left, text="总览", bg=p["bg"], fg=p["text"],
                                   font=("Microsoft YaHei UI", 21, "bold"))
        self.page_title.pack(anchor="w")
        self.page_sub = tk.Label(left, text="", bg=p["bg"], fg=p["muted"], font=("Microsoft YaHei UI", 9))
        self.page_sub.pack(anchor="w", pady=(4, 0))
        right = tk.Frame(header, bg=p["bg"])
        right.pack(side="right")
        tk.Label(right, text="操作账号", bg=p["bg"], fg=p["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(0, 4))
        self.account_selector = ttk.Combobox(right, state="readonly", width=18)
        self.account_selector.pack(side="left", padx=(0, 8))
        self.account_selector.bind("<<ComboboxSelected>>", self.on_select_account)
        self.active_badge = tk.Label(right, text="", bg=p["soft"], fg=p["accent"], padx=10, pady=5,
                                     font=("Microsoft YaHei UI", 9, "bold"))
        self.active_badge.pack(side="left", padx=6)
        self.ai_badge = tk.Label(right, text="", bg=p["soft"], fg=p["muted"], padx=10, pady=5,
                                 font=("Microsoft YaHei UI", 9))
        self.ai_badge.pack(side="left", padx=6)
        self.pause_btn = self._btn(right, "暂停", self.toggle_pause, "ghost")
        self.pause_btn.pack(side="left", padx=6)

        self.content = tk.Frame(main, bg=p["bg"])
        self.content.pack(fill="both", expand=True, padx=26, pady=(8, 8))

        log_frame = tk.Frame(main, bg=p["card"], highlightbackground=p["line"], highlightthickness=1)
        log_frame.pack(fill="x", padx=26, pady=(0, 14))
        tk.Label(log_frame, text="运行日志", bg=p["card"], fg=p["muted"],
                 font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
        self.log_box = tk.Text(log_frame, height=5, bg=p["card"], fg=p["muted"], relief="flat", bd=0,
                               font=("Consolas", 8), padx=10, pady=4, state="disabled")
        self.log_box.pack(fill="x", padx=6, pady=(0, 6))

        self.pages = {}
        self.pages["dashboard"] = self._build_dashboard(self.content)
        self.pages["auto"] = self._build_auto(self.content)
        self.pages["bot"] = self._build_bot(self.content)
        self.pages["developer"] = self._build_developer(self.content)
        self.pages["persona"] = self._build_persona(self.content)
        self.pages["test"] = self._build_test(self.content)
        self.pages["prefs"] = self._build_prefs(self.content)
        self.pages["accounts"] = self._build_accounts(self.content)
        self.pages["settings"] = self._build_settings(self.content)
        self.show_page(self.page)
        try:
            self._refresh_header()
        except Exception:
            pass
        try:
            i18n.apply_texts(self)
        except Exception:
            pass

    def show_page(self, key: str):
        if key in ("developer", "accounts", "settings", "persona", "test") and not getattr(self, "dev_unlocked", False):
            if not messagebox.askokcancel(
                "开发者设置",
                "这里会修改账号、API、人设和测试，普通使用者不要乱改。\n确定进入开发者设置吗？",
            ):
                return
            self.dev_unlocked = True
        self.page = key
        p = self.p
        active_key = key if key in self.nav_buttons else ("developer" if key in ("accounts", "settings", "persona", "test") else key)
        for k, b in self.nav_buttons.items():
            b.configure(bg=p["accent"] if k == active_key else p["sidebar"],
                        fg="#fff" if k == active_key else p["side_text"])
        for k, frame in self.pages.items():
            if k == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        titles = {"dashboard": ("总览", "今天安排了什么、当前账号对不对"),
                  "auto": ("自动发送", "朋友圈和群消息都在这里排期，统一执行"),
                  "bot": ("微信机器人", "启动后手动发群消息，AI 用客户自己的 Key"),
                  "prefs": ("设置", "语言、主题、发送习惯、启动方式、数据"),
                  "developer": ("开发者", "账号、API、人设都在这里；普通使用不需要进来"),
                  "persona": ("机器人人设", "只写是谁、什么性格、怎么说话"),
                  "test": ("测试", "测试发送消息、朋友圈、DeepSeek 人设"),
                  "accounts": ("账号与角色", "运营账号 / 机器人账号"),
                  "settings": ("API 设置", "DeepSeek API")}
        self.page_title.configure(text=titles[key][0])
        self.page_sub.configure(text=titles[key][1])
        if key == "dashboard":
            self._refresh_dashboard()
        elif key == "auto":
            if not self.a_type.get():
                self.new_auto()
            self._refresh_auto_list()
        elif key == "bot":
            self._refresh_bot()
        elif key == "developer":
            pass
        elif key == "persona":
            self._refresh_persona()
        elif key == "test":
            pass
        elif key == "prefs":
            self._refresh_prefs()
        elif key == "accounts":
            self._refresh_account_cards()
        elif key == "settings":
            self._refresh_settings()
        try:
            i18n.apply_texts(self)
        except Exception:
            pass

    # ---------- dashboard ----------
    def _build_dashboard(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        self.stat_labels = {}
        row = tk.Frame(frame, bg=p["bg"])
        row.pack(fill="x")
        for i, (key, label) in enumerate((("moments", "朋友圈计划"), ("group", "群消息"),
                                          ("waiting", "等待切号"), ("sent", "已完成"))):
            card = self._card(row)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0), ipady=8)
            row.grid_columnconfigure(i, weight=1)
            tk.Label(card, text=label, bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(10, 0))
            lab = tk.Label(card, text="0", bg=p["card"], fg=p["accent"], font=("Microsoft YaHei UI", 24, "bold"))
            lab.pack(anchor="w", padx=14, pady=(0, 8))
            self.stat_labels[key] = lab

        tk.Label(frame, text="账号", bg=p["bg"], fg=p["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(18, 8))
        self.account_box = tk.Frame(frame, bg=p["bg"])
        self.account_box.pack(fill="x")

        tk.Label(frame, text="接下来", bg=p["bg"], fg=p["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(18, 8))
        cols = ("time", "kind", "account", "status", "text")
        self.next_tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        for c, t, w in (("time", "时间", 110), ("kind", "类型", 70), ("account", "账号", 110),
                        ("status", "状态", 90), ("text", "内容", 520)):
            self.next_tree.heading(c, text=i18n.tr(t))
            self.next_tree.column(c, width=w, anchor="w", stretch=(c == "text"))
        self.next_tree.pack(fill="both", expand=True)
        return frame

    def _refresh_dashboard(self):
        p = self.p
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        selected_id = store.selected_account_id()
        plans = [x for x in store.list_plans()
                 if str(x.get("scheduled_at", "")).startswith(today)
                 and (not selected_id or x.get("account_id") == selected_id)]
        counts = {"moments": 0, "group": 0, "waiting": 0, "sent": 0}
        for x in plans:
            if x.get("kind") == store.KIND_MOMENT:
                counts["moments"] += 1
            if x.get("kind") == store.KIND_GROUP:
                counts["group"] += 1
            if x.get("status") == store.STATUS_WAITING:
                counts["waiting"] += 1
            if x.get("status") == store.STATUS_SENT:
                counts["sent"] += 1
        for k, lab in self.stat_labels.items():
            lab.configure(text=str(counts.get(k, 0)))
        for w in self.account_box.winfo_children():
            w.destroy()
        active = store.active_hint(self.accounts)
        for i, a in enumerate(self.accounts):
            card = self._card(self.account_box)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0), pady=2, ipady=6)
            self.account_box.grid_columnconfigure(i, weight=1)
            head = tk.Frame(card, bg=p["card"])
            head.pack(fill="x", padx=14, pady=(10, 4))
            tk.Label(head, text=i18n.display_account(a.get("name", a["id"])), bg=p["card"], fg=p["text"],
                     font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
            is_active = active == a.get("name")
            is_selected = selected_id == a.get("id")
            label = i18n.tr("操作中" if is_selected else ("机器人" if a.get("role") == "bot" else ("微信中" if is_active else ("启用" if a.get("enabled", True) else "停用"))))
            tk.Label(head, text=label,
                     bg=p["soft"], fg=p["green"] if (is_selected or is_active) else p["muted"], padx=8, pady=2,
                     font=("Microsoft YaHei UI", 8)).pack(side="right")
            tk.Label(card, text=f"{a.get('phone','')} · {i18n.tr('未绑定 wxid') if not a.get('wxid') else a.get('wxid')}",
                     bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=14)
            tk.Label(card, text=i18n.tr_runtime(f"每天 {a.get('moments_per_day',4)} 条 · 群：{a.get('group_name') or '未配置'}"),
                     bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=14, pady=(0, 8))

        self.next_tree.delete(*self.next_tree.get_children())
        nexts = [x for x in plans if x.get("status") in (store.STATUS_PLANNED, store.STATUS_WAITING)]
        nexts.sort(key=lambda x: x.get("scheduled_at", ""))
        for x in nexts[:8]:
            self.next_tree.insert("", "end", values=(
                x.get("scheduled_at", "")[5:16], i18n.tr(store.KIND_LABELS.get(x.get("kind"), "")),
                self._account_name(x.get("account_id")), i18n.tr(store.STATUS_LABELS.get(x.get("status"), "")),
                (x.get("text") or "")[:60],
            ))

    def _account_name(self, account_id: str) -> str:
        for a in self.accounts:
            if a["id"] == account_id:
                return i18n.display_account(a.get("name", account_id))
        return i18n.display_account(account_id)

    # ---------- auto send ----------
    def _build_auto(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        right = self._card(frame, width=380)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)
        left = tk.Frame(frame, bg=p["bg"])
        left.pack(side="left", fill="both", expand=True)

        bar = tk.Frame(left, bg=p["bg"])
        bar.pack(fill="x", pady=(0, 8))
        self._btn(bar, "新建发送", self.new_auto, "primary").pack(side="left")
        self._btn(bar, "刷新", lambda: self._refresh_auto_list(), "ghost").pack(side="left", padx=8)
        tk.Label(bar, text="只显示右上角所选账号", bg=p["bg"], fg=p["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="right")

        cols = ("time", "kind", "account", "target", "status", "text")
        self.auto_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("time", "时间", 88), ("kind", "类型", 55), ("account", "账号", 82),
                        ("target", "目标", 82), ("status", "状态", 62), ("text", "内容", 155)):
            self.auto_tree.heading(c, text=i18n.tr(t))
            self.auto_tree.column(c, width=w, anchor="w", stretch=(c == "text"))
        self.auto_tree.pack(fill="both", expand=True)
        self.auto_tree.bind("<<TreeviewSelect>>", self._on_auto_select)

        tk.Label(right, text="发送内容", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        top = tk.Frame(right, bg=p["card"])
        top.pack(fill="x", padx=16)
        tk.Label(top, text="类型", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=0, sticky="w")
        tk.Label(top, text="发布时间", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.a_type = ttk.Combobox(top, values=[i18n.tr("朋友圈"), i18n.tr("群消息")], state="readonly", width=9)
        self.a_type.grid(row=1, column=0, sticky="ew")
        self.a_type.bind("<<ComboboxSelected>>", self._on_auto_type_change)
        self.m_time = ttk.Entry(top)
        self.m_time.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        top.grid_columnconfigure(0, weight=1)
        top.grid_columnconfigure(1, weight=2)

        row2 = tk.Frame(right, bg=p["card"])
        row2.pack(fill="x", padx=16, pady=(7, 0))
        tk.Label(row2, text="账号", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=0, sticky="w")
        tk.Label(row2, text="群名称", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.m_account = ttk.Combobox(row2, values=[a["name"] for a in self.accounts if a.get("enabled", True)], state="readonly")
        self.m_account.grid(row=1, column=0, sticky="ew")
        self.a_target = ttk.Combobox(row2, values=self._known_groups())
        self.a_target.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        row2.grid_columnconfigure(0, weight=1)
        row2.grid_columnconfigure(1, weight=1)

        tk.Label(right, text="内容（手写）", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(8, 0))
        self.m_text = tk.Text(right, height=5, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                              insertbackground=p["text"], font=("Microsoft YaHei UI", 9), padx=8, pady=6,
                              highlightthickness=1, highlightbackground=p["line"])
        self.m_text.pack(fill="x", padx=16, pady=(3, 6))

        self.image_box = tk.Frame(right, bg=p["card"])
        self.image_box.pack(fill="x", padx=16)
        tk.Label(self.image_box, text="图片（拖动排序，最多 9 张）", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        self.m_grid = tk.Frame(self.image_box, bg=p["card"])
        self.m_grid.pack(fill="x", pady=(3, 2))
        self._grid_refs = []
        self._drag_index = None
        tk.Label(self.image_box, text="点 + 添加；拖动缩略图排序；右键移除；双击看大图",
                 bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 7)).pack(anchor="w")

        self.save_btn = self._btn(right, "保存计划", self.save_auto, "primary")
        self.save_btn.pack(fill="x", padx=16, pady=(8, 4))
        rowb = tk.Frame(right, bg=p["card"])
        rowb.pack(fill="x", padx=16)
        self._btn(rowb, "立即发布", lambda: self.run_plan(self.current_plan), "ghost").pack(side="left", fill="x", expand=True)
        self._btn(rowb, "删除", self.delete_current_plan, "danger").pack(side="left", padx=(6, 0))
        self.auto_hint = tk.Label(right, text="朋友圈和群消息都在这里排队；内容必须手写，程序不自动生成。", bg=p["card"], fg=p["muted"],
                                  font=("Microsoft YaHei UI", 8), wraplength=320, justify="left")
        self.auto_hint.pack(anchor="w", padx=16, pady=(6, 8))
        return frame

    def _known_groups(self) -> list[str]:
        groups = []
        for a in self.accounts:
            g = (a.get("group_name") or "").strip()
            if g and g not in groups:
                groups.append(g)
        for p in store.list_plans():
            g = (p.get("group") or "").strip()
            if g and g not in groups:
                groups.append(g)
        return groups

    def _refresh_auto_list(self):
        self.accounts = store.load_accounts()
        sel_account = store.selected_account(self.accounts)
        self.selected_id = sel_account["id"] if sel_account else ""
        if hasattr(self, "m_account") and sel_account:
            self.m_account.configure(values=[i18n.display_account(sel_account.get("name", ""))])
            self.m_account.set(i18n.display_account(sel_account.get("name", "")))
        if hasattr(self, "a_target"):
            self.a_target.configure(values=self._known_groups())
        self.auto_tree.delete(*self.auto_tree.get_children())
        for x in store.list_plans():
            if self.selected_id and x.get("account_id") != self.selected_id:
                continue
            target = x.get("group") or i18n.tr("朋友圈")
            self.auto_tree.insert("", "end", iid=x["id"], values=(
                x.get("scheduled_at", "")[5:16], i18n.tr(store.KIND_LABELS.get(x.get("kind"), "")),
                self._account_name(x.get("account_id")), target,
                i18n.tr(store.STATUS_LABELS.get(x.get("status"), "")), (x.get("text") or "")[:60],
            ))
        if self.current_plan and self.current_plan.get("id") in self.auto_tree.get_children():
            self.auto_tree.selection_set(self.current_plan["id"])

    def _on_auto_select(self, _e=None):
        s = self.auto_tree.selection()
        if not s:
            return
        plan = store.get_plan(s[0])
        if plan:
            self.current_plan = plan
            self._load_auto(plan)

    def _load_auto(self, plan: dict):
        is_group = plan.get("kind") == store.KIND_GROUP
        self.a_type.current(1 if is_group else 0)
        self.m_time.delete(0, "end")
        self.m_time.insert(0, plan.get("scheduled_at", "")[:16].replace("T", " "))
        self.m_account.set(self._account_name(plan.get("account_id")))
        self.a_target.delete(0, "end")
        self.a_target.insert(0, plan.get("group", ""))
        self.m_text.delete("1.0", "end")
        self.m_text.insert("1.0", plan.get("text", ""))
        self.moment_images = list(plan.get("images", []))
        self._refresh_images()
        self._on_auto_type_change()

    def new_auto(self):
        self.current_plan = None
        self.auto_tree.selection_remove(self.auto_tree.selection())
        self.a_type.current(0)
        dt = (datetime.now().astimezone() + timedelta(hours=1)).replace(second=0, microsecond=0)
        self.m_time.delete(0, "end")
        self.m_time.insert(0, dt.strftime("%Y-%m-%d %H:%M"))
        if self.accounts:
            sel = store.selected_account(self.accounts) or self.accounts[0]
            self.m_account.configure(values=[i18n.display_account(sel.get("name", ""))])
            self.m_account.set(i18n.display_account(sel.get("name", "")))
        self.a_target.delete(0, "end")
        sel = store.selected_account(self.accounts) or (self.accounts[0] if self.accounts else {})
        self.a_target.insert(0, sel.get("group_name", ""))
        self.m_text.delete("1.0", "end")
        self.moment_images = []
        self._refresh_images()
        self._on_auto_type_change()

    def _on_auto_type_change(self, _e=None):
        is_group = self.a_type.current() == 1
        if is_group:
            self.image_box.pack_forget()
        elif hasattr(self, "save_btn"):
            self.image_box.pack(fill="x", padx=16, before=self.save_btn)
        else:
            self.image_box.pack(fill="x", padx=16)
        if hasattr(self, "auto_hint"):
            if is_group:
                mode = store.load_config().get("group", {}).get("mode", "current")
                if mode == "current":
                    self.auto_hint.configure(text="群消息发送到当前微信聊天。请先在微信打开目标群，避免搜索误点直播；设置里可切换自动搜索。")
                else:
                    self.auto_hint.configure(text="群消息会搜索群名后发送；搜索可能误触直播/工具资源，建议先用测试群。")
            else:
                self.auto_hint.configure(text="朋友圈需要 1–9 张图；文案和图片必须自己填，程序不自动生成。")

    def _account_id_by_name(self, name: str) -> str:
        for a in self.accounts:
            if a.get("name") == name:
                return a["id"]
        return self.accounts[0]["id"] if self.accounts else ""

    def add_moment_images(self):
        paths = filedialog.askopenfilenames(filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp"), ("所有文件", "*.*")])
        for p in paths:
            if p not in self.moment_images:
                self.moment_images.append(p)
        self._refresh_images()

    def remove_image_at(self, idx: int):
        if 0 <= idx < len(self.moment_images):
            self.moment_images.pop(idx)
            self._refresh_images()

    def _on_image_press(self, idx: int, _e=None):
        self._drag_index = idx

    def _on_image_release(self, _e=None):
        if self._drag_index is None:
            return
        x, y = self.winfo_pointerxy()
        target = None
        for i, cell in enumerate(self._grid_cells):
            if cell.winfo_rootx() <= x <= cell.winfo_rootx() + cell.winfo_width() and \
               cell.winfo_rooty() <= y <= cell.winfo_rooty() + cell.winfo_height():
                target = i
                break
        src = self._drag_index
        self._drag_index = None
        if target is None or target == src or src >= len(self.moment_images):
            return
        img = self.moment_images.pop(src)
        self.moment_images.insert(min(target, len(self.moment_images)), img)
        self._refresh_images()

    def _refresh_images(self):
        if not hasattr(self, "m_grid"):
            return
        for w in self.m_grid.winfo_children():
            w.destroy()
        self._grid_refs = []
        self._grid_cells = []
        for c in range(3):
            self.m_grid.grid_columnconfigure(c, weight=0)
        for idx, raw in enumerate(self.moment_images[:9]):
            row, col = divmod(idx, 3)
            cell = tk.Frame(self.m_grid, width=66, height=66, bg=self.p["card2"],
                            highlightbackground=self.p["line"], highlightthickness=1)
            cell.grid(row=row, column=col, padx=3, pady=3)
            cell.grid_propagate(False)
            self._grid_cells.append(cell)
            try:
                img = Image.open(raw)
                img.thumbnail((62, 62), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                lab = tk.Label(cell, image=photo, bg=self.p["card2"], cursor="fleur")
                lab.pack(fill="both", expand=True)
                lab.bind("<ButtonPress-1>", lambda e, i=idx: self._on_image_press(i, e))
                lab.bind("<ButtonRelease-1>", self._on_image_release)
                lab.bind("<Double-Button-1>", lambda e, path=raw: os.startfile(path))
                menu = tk.Menu(self, tearoff=0)
                menu.add_command(label="打开大图", command=lambda path=raw: os.startfile(path))
                menu.add_command(label="移除", command=lambda i=idx: self.remove_image_at(i))
                lab.bind("<Button-3>", lambda e, m=menu: m.tk_popup(e.x_root, e.y_root))
                self._grid_refs.append(photo)
            except Exception:
                tk.Label(cell, text=Path(raw).name[:6], bg=self.p["card2"],
                         fg=self.p["muted"], font=("Microsoft YaHei UI", 8)).pack(fill="both", expand=True)
        if len(self.moment_images) < 9:
            idx = len(self.moment_images)
            row, col = divmod(idx, 3)
            add = tk.Frame(self.m_grid, width=66, height=66, bg=self.p["card"],
                           highlightbackground=self.p["line"], highlightthickness=1)
            add.grid(row=row, column=col, padx=3, pady=3)
            add.grid_propagate(False)
            self._grid_cells.append(add)
            tk.Button(add, text="+", command=self.add_moment_images, bg=self.p["card"], fg=self.p["accent"],
                      relief="flat", bd=0, font=("Microsoft YaHei UI", 18), cursor="hand2").pack(fill="both", expand=True)

    def save_auto(self):
        try:
            is_group = self.a_type.current() == 1
            when = parse_time(self.m_time.get())
            account_id = store.selected_account_id(store.load_accounts())
            if not account_id:
                raise ValueError("没有可用账号，请先在右上角选择账号")
            text = self.m_text.get("1.0", "end-1c").strip()
            if not text:
                raise ValueError("内容不能为空")
            if len(text) > 1500:
                raise ValueError("内容最多 1500 字")
            if when < store.now_local() - timedelta(minutes=1):
                raise ValueError("发布时间已经过去")
            if self.current_plan and self.current_plan.get("status") == store.STATUS_SENT:
                raise ValueError("已发布的计划不能修改")

            if is_group:
                group = self.a_target.get().strip()
                if not group:
                    raise ValueError("群消息必须填写群名称")
                fields = dict(account_id=account_id, kind=store.KIND_GROUP,
                              scheduled_at=when.isoformat(timespec="seconds"), text=text,
                              group=group, images=[], status=store.STATUS_PLANNED, last_error="")
                plan = store.update_plan(self.current_plan["id"], **fields) if self.current_plan else \
                    store.create_plan(account_id, store.KIND_GROUP, when, text, group=group, source="manual")
                self.moment_images = []
            else:
                if not (1 <= len(self.moment_images) <= 9):
                    raise ValueError("朋友圈请选择 1–9 张图片")
                fields = dict(account_id=account_id, kind=store.KIND_MOMENT,
                              scheduled_at=when.isoformat(timespec="seconds"), text=text,
                              group="", images=self.moment_images, status=store.STATUS_PLANNED, last_error="")
                plan = store.update_plan(self.current_plan["id"], **fields) if self.current_plan else \
                    store.create_plan(account_id, store.KIND_MOMENT, when, text,
                                      images=self.moment_images, source="manual")
                copied = self._copy_plan_images(account_id, plan["id"], self.moment_images)
                if copied:
                    plan = store.update_plan(plan["id"], images=copied)
                    self.moment_images = copied

            self.current_plan = plan
            messagebox.showinfo("已保存", "发送计划已保存。到点后按当前账号执行。")
            self._refresh_auto_list()
            self._refresh_dashboard()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _copy_plan_images(self, account_id: str, plan_id: str, images: list[str]) -> list[str]:
        dest_dir = ROOT / "data" / "accounts" / account_id / "images" / "plans" / plan_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for i, raw in enumerate(images, 1):
            src = Path(raw)
            if not src.exists():
                continue
            try:
                src.resolve().relative_to(dest_dir.resolve())
                out.append(str(src))
                continue
            except Exception:
                pass
            name = f"{i:02d}_{src.name}"
            dest = dest_dir / name
            shutil.copy2(src, dest)
            out.append(str(dest))
        return out

    def run_plan(self, plan):
        if not plan:
            messagebox.showinfo("提示", "先选中一条计划")
            return
        if plan.get("kind") == store.KIND_GROUP:
            group = plan.get("group") or "当前群"
            if not messagebox.askyesno(
                "确认微信群",
                f"请确认微信当前打开的是「{group}」。\n\n如果不是，先点取消，在微信切到目标群再发送。",
            ):
                return
        if not messagebox.askyesno("立即发布", f"现在发布这条内容？\n\n{plan.get('text','')[:80]}"):
            return
        ok, msg = self.scheduler.run_now(plan["id"])
        if ok:
            self._set_busy(True)
        (messagebox.showinfo if ok else messagebox.showwarning)("发布", msg)
        self._refresh_auto_list()

    def delete_current_plan(self):
        if not self.current_plan:
            return
        if not messagebox.askyesno("删除", "删除这条发送计划？"):
            return
        store.delete_plan(self.current_plan["id"])
        self.current_plan = None
        self._refresh_auto_list()
        self._refresh_dashboard()

    # ---------- group ----------
    def _build_group(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        top = self._card(frame)
        top.pack(fill="x", pady=(0, 12))
        form = tk.Frame(top, bg=p["card"])
        form.pack(fill="x", padx=16, pady=14)
        tk.Label(form, text="账号", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=0, sticky="w")
        self.g_account = ttk.Combobox(form, values=[a["name"] for a in self.accounts if a.get("enabled", True)], state="readonly", width=18)
        self.g_account.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        tk.Label(form, text="群名称", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=1, sticky="w")
        self.g_name = ttk.Entry(form)
        self.g_name.grid(row=1, column=1, sticky="ew", padx=(0, 8))
        tk.Label(form, text="时间", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=2, sticky="w")
        self.g_time = ttk.Entry(form, width=18)
        self.g_time.grid(row=1, column=2, sticky="ew")
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=2)
        form.grid_columnconfigure(2, weight=1)
        tk.Label(top, text="消息内容（手写）", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        self.g_text = tk.Text(top, height=3, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                              font=("Microsoft YaHei UI", 9), padx=8, pady=6, highlightthickness=1,
                              highlightbackground=p["line"])
        self.g_text.pack(fill="x", padx=16, pady=(3, 8))
        gb = tk.Frame(top, bg=p["card"])
        gb.pack(fill="x", padx=16, pady=(0, 12))
        self._btn(gb, "保存群消息", self.save_group_plan, "primary").pack(side="left")
        self._btn(gb, "立即发送", self.send_group_now, "ghost").pack(side="left", padx=8)
        self._btn(gb, "删除", self.delete_group_plan, "danger").pack(side="left")
        self._btn(gb, "清空", self.clear_group_form, "ghost").pack(side="left", padx=8)

        cols = ("time", "status", "account", "group", "text")
        self.group_tree = ttk.Treeview(frame, columns=cols, show="headings", height=5, selectmode="browse")
        for c, t, w in (("time", "时间", 110), ("status", "状态", 80), ("account", "账号", 100),
                        ("group", "群", 130), ("text", "内容", 360)):
            self.group_tree.heading(c, text=t)
            self.group_tree.column(c, width=w, anchor="w", stretch=(c == "text"))
        self.group_tree.pack(fill="both", expand=True)
        self.group_tree.bind("<<TreeviewSelect>>", self._on_group_select)

        ai = self._card(frame)
        ai.pack(fill="x", pady=(12, 0))
        tk.Label(ai, text="DeepSeek 群回复（手动）", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(ai, text="粘贴群友问题，生成回复，再点“发送回复”。不会自动读群消息。", bg=p["card"],
                 fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        self.reply_in = tk.Text(ai, height=2, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                                font=("Microsoft YaHei UI", 9), padx=8, pady=5)
        self.reply_in.pack(fill="x", padx=16, pady=(6, 4))
        self.reply_out = tk.Text(ai, height=2, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                                 font=("Microsoft YaHei UI", 9), padx=8, pady=5)
        self.reply_out.pack(fill="x", padx=16, pady=(0, 4))
        rb = tk.Frame(ai, bg=p["card"])
        rb.pack(fill="x", padx=16, pady=(0, 12))
        self._btn(rb, "AI 生成回复", self.ai_reply, "ghost").pack(side="left")
        self._btn(rb, "发送回复", self.send_reply, "primary").pack(side="left", padx=8)
        return frame

    def _refresh_group_list(self):
        self.group_tree.delete(*self.group_tree.get_children())
        for x in store.list_plans():
            if x.get("kind") != store.KIND_GROUP:
                continue
            self.group_tree.insert("", "end", iid=x["id"], values=(
                x.get("scheduled_at", "")[5:16], store.STATUS_LABELS.get(x.get("status"), ""),
                self._account_name(x.get("account_id")), x.get("group", ""), (x.get("text") or "")[:60],
            ))

    def _on_group_select(self, _e=None):
        s = self.group_tree.selection()
        if not s:
            return
        plan = store.get_plan(s[0])
        if not plan:
            return
        self.current_group_plan = plan
        self.g_account.set(self._account_name(plan.get("account_id")))
        self.g_name.delete(0, "end")
        self.g_name.insert(0, plan.get("group", ""))
        self.g_time.delete(0, "end")
        self.g_time.insert(0, plan.get("scheduled_at", "")[:16].replace("T", " "))
        self.g_text.delete("1.0", "end")
        self.g_text.insert("1.0", plan.get("text", ""))

    def clear_group_form(self):
        self.current_group_plan = None
        self.group_tree.selection_remove(self.group_tree.selection())
        if self.accounts:
            self.g_account.set(self.accounts[0]["name"])
            self.g_name.delete(0, "end")
            self.g_name.insert(0, self.accounts[0].get("group_name", ""))
        dt = (datetime.now().astimezone() + timedelta(minutes=10)).replace(second=0, microsecond=0)
        self.g_time.delete(0, "end")
        self.g_time.insert(0, dt.strftime("%Y-%m-%d %H:%M"))
        self.g_text.delete("1.0", "end")

    def save_group_plan(self):
        try:
            when = parse_time(self.g_time.get())
            account_id = self._account_id_by_name(self.g_account.get())
            group = self.g_name.get().strip()
            text = self.g_text.get("1.0", "end-1c").strip()
            if not group:
                raise ValueError("请填写群名称")
            if not text:
                raise ValueError("消息不能为空")
            if self.current_group_plan:
                plan = store.update_plan(self.current_group_plan["id"], account_id=account_id,
                                         scheduled_at=when.isoformat(timespec="seconds"), group=group,
                                         text=text, status=store.STATUS_PLANNED, last_error="")
            else:
                plan = store.create_plan(account_id, store.KIND_GROUP, when, text, group=group, source="manual")
            self.current_group_plan = plan
            messagebox.showinfo("已保存", "群消息计划已保存。")
            self._refresh_group_list()
            self._refresh_dashboard()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def delete_group_plan(self):
        if not self.current_group_plan:
            messagebox.showinfo("提示", "先选中一条群消息计划")
            return
        if not messagebox.askyesno("删除", "删除这条群消息计划？"):
            return
        store.delete_plan(self.current_group_plan["id"])
        self.current_group_plan = None
        self._refresh_group_list()
        self._refresh_dashboard()

    def send_group_now(self):
        account_id = self._account_id_by_name(self.g_account.get())
        group = self.g_name.get().strip()
        text = self.g_text.get("1.0", "end-1c").strip()
        if not messagebox.askyesno("发送群消息", f"现在用「{self.g_account.get()}」发送到「{group}」？"):
            return
        ok, msg = self.scheduler.send_group_now(account_id, group, text)
        (messagebox.showinfo if ok else messagebox.showwarning)("群消息", msg)

    def ai_reply(self):
        text = self.reply_in.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("提示", "先粘贴群友的问题")
            return
        if not self.ai.available:
            messagebox.showwarning("未配置 API", "请到“设置”填写 DeepSeek API Key。")
            return
        self.reply_out.delete("1.0", "end")
        self.reply_out.insert("1.0", "生成中…")

        def work():
            try:
                ans = self.ai.reply(text)
                self.after(0, lambda: (self.reply_out.delete("1.0", "end"), self.reply_out.insert("1.0", ans)))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("AI 失败", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def send_reply(self):
        ans = self.reply_out.get("1.0", "end-1c").strip()
        if not ans:
            messagebox.showinfo("提示", "先生成回复")
            return
        self.g_text.delete("1.0", "end")
        self.g_text.insert("1.0", ans)
        self.send_group_now()

    # ---------- developer ----------
    def _build_developer(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        card = self._card(frame)
        card.pack(fill="x")
        tk.Label(card, text="开发者设置", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Label(card, text="普通使用者不需要进来。账号、API、人设都收在这里。",
                 bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=18, pady=(0, 10))
        for label, sub, page in (
            ("账号与角色", "设置运营账号 / 机器人账号、wxid、群名", "accounts"),
            ("API 设置", "客户自己的 DeepSeek Key、模型", "settings"),
            ("机器人人设", "人设内容、保存、测试", "persona"),
            ("测试", "测试发群消息、测试发朋友圈、测试人设", "test"),
        ):
            row = tk.Frame(card, bg=p["card2"], highlightbackground=p["line"], highlightthickness=1)
            row.pack(fill="x", padx=18, pady=5)
            left = tk.Frame(row, bg=p["card2"])
            left.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            tk.Label(left, text=label, bg=p["card2"], fg=p["text"],
                     font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            tk.Label(left, text=sub, bg=p["card2"], fg=p["muted"],
                     font=("Microsoft YaHei UI", 8)).pack(anchor="w")
            self._btn(row, "进入", lambda pg=page: self.show_page(pg), "ghost").pack(side="right", padx=12)
        tk.Label(card, text="修改后点各页的保存按钮。API Key 不预置，由客户填写。",
                 bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=18, pady=(10, 14))
        return frame

    def _build_persona(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        card = self._card(frame)
        card.pack(fill="both", expand=True)
        tk.Label(card, text="机器人人设", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Label(card, text="只写“是谁、什么性格、怎么说话”，不要写工具用法。用于微信 AI 回复。",
                 bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=18)
        name_row = tk.Frame(card, bg=p["card"])
        name_row.pack(fill="x", padx=18, pady=(10, 4))
        tk.Label(name_row, text="人设名字", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.b_persona_name = ttk.Entry(name_row, width=24)
        self.b_persona_name.pack(side="left", padx=8)
        self.b_persona_text = tk.Text(card, height=10, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                                      insertbackground=p["text"], font=("Microsoft YaHei UI", 9),
                                      padx=10, pady=8, highlightthickness=1, highlightbackground=p["line"])
        self.b_persona_text.pack(fill="both", expand=True, padx=18, pady=(2, 10))
        bar = tk.Frame(card, bg=p["card"])
        bar.pack(fill="x", padx=18, pady=(0, 16))
        self._btn(bar, "保存人设", self.save_persona, "primary").pack(side="left")
        self._btn(bar, "测试 DeepSeek 人设", self.test_persona_reply, "ghost").pack(side="left", padx=8)
        return frame

    def _refresh_persona(self):
        if not hasattr(self, "b_persona_text"):
            return
        cfg = store.load_config()
        bot = cfg.get("bot", {})
        self.b_persona_name.delete(0, "end")
        self.b_persona_name.insert(0, bot.get("persona_name", "烘焙助手"))
        self.b_persona_text.delete("1.0", "end")
        self.b_persona_text.insert("1.0", bot.get("persona", ""))

    # ---------- developer tests ----------
    def _build_test(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        card = self._card(frame)
        card.pack(fill="x", pady=(0, 10))
        tk.Label(card, text="测试发群消息", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(card, text="只发到当前打开的微信群；会占用键鼠。", bg=p["card"], fg=p["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        self.test_msg_text = tk.Text(card, height=2, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                                     insertbackground=p["text"], font=("Microsoft YaHei UI", 9), padx=8, pady=4)
        self.test_msg_text.pack(fill="x", padx=16, pady=(6, 6))
        self.test_msg_text.insert("1.0", "Bready Bear 测试消息，请忽略")
        row1 = tk.Frame(card, bg=p["card"])
        row1.pack(fill="x", padx=16, pady=(0, 10))
        self._btn(row1, "测试发送消息", self.test_send_group, "primary").pack(side="left")
        self._btn(row1, "只填入（自己按 Enter）", self.test_prepare_group, "ghost").pack(side="left", padx=8)

        card2 = self._card(frame)
        card2.pack(fill="x", pady=(0, 10))
        tk.Label(card2, text="测试发朋友圈", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(card2, text="“只打开编辑框”不会发布，方便检查相机/图片/文案。", bg=p["card"], fg=p["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        self.test_moment_text = tk.Text(card2, height=2, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                                        insertbackground=p["text"], font=("Microsoft YaHei UI", 9), padx=8, pady=4)
        self.test_moment_text.pack(fill="x", padx=16, pady=(6, 6))
        self.test_moment_text.insert("1.0", "Bready Bear 朋友圈测试，请忽略")
        row2 = tk.Frame(card2, bg=p["card"])
        row2.pack(fill="x", padx=16, pady=(0, 10))
        self._btn(row2, "只打开朋友圈编辑框", lambda: self.test_moments(False), "primary").pack(side="left")
        self._btn(row2, "真正发布测试", lambda: self.test_moments(True), "danger").pack(side="left", padx=8)

        card3 = self._card(frame)
        card3.pack(fill="x")
        row3 = tk.Frame(card3, bg=p["card"])
        row3.pack(fill="x", padx=16, pady=12)
        self._btn(row3, "测试 DeepSeek 人设", self.test_persona_reply, "ghost").pack(side="left")
        tk.Label(row3, text="使用开发者 → 机器人人设 里的内容。", bg=p["card"], fg=p["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=10)
        return frame

    def _dev_wechat_cfg(self) -> dict:
        cfg = store.load_config()
        cfg.setdefault("group", {})["mode"] = "current"
        cfg.update(cfg.get("wechat", {}) or {})
        return cfg

    def test_send_group(self):
        text = self.test_msg_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("提示", "测试消息不能为空。")
            return
        if not self._selected_wechat_ready():
            return
        cfg = self._dev_wechat_cfg()
        account = self._active_wechat_account()

        def work():
            self.after(0, self._set_busy, True)
            GATE.begin_robot()
            try:
                store.robot_event("测试发送", text)
                GroupUI(cfg, hwnd=self._hwnd_for(account)).send_current(text)
                store.robot_event("测试成功", text, True)
                self.after(0, lambda: messagebox.showinfo("测试成功", "测试消息已发出。"))
            except Exception as exc:
                err = str(exc)
                store.robot_event("测试失败", text, False, err)
                self.after(0, lambda e=err: messagebox.showerror("测试失败", e))
            finally:
                GATE.end_robot()
                self.after(0, self._set_busy, False)

        threading.Thread(target=work, daemon=True).start()

    def test_prepare_group(self):
        text = self.test_msg_text.get("1.0", "end-1c").strip()
        if not text:
            return
        cfg = self._dev_wechat_cfg()
        account = self._active_wechat_account()

        def work():
            try:
                GATE.begin_robot()
                try:
                    GroupUI(cfg, hwnd=self._hwnd_for(account)).prepare_current(text)
                finally:
                    GATE.end_robot()
                self.after(0, lambda: messagebox.showinfo("已填入", "文字已填入微信输入框，请自己按 Enter。"))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda e=err: messagebox.showerror("填入失败", e))

        threading.Thread(target=work, daemon=True).start()

    def _test_image(self) -> Path:
        from PIL import Image, ImageDraw
        d = ROOT / "data" / "dev"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "moment_test.jpg"
        im = Image.new("RGB", (900, 900), (240, 226, 210))
        dr = ImageDraw.Draw(im)
        dr.rectangle((0, 0, 900, 16), fill=(165, 106, 66))
        dr.text((80, 120), "Bready Bear", fill=(90, 55, 35))
        dr.text((80, 190), "朋友圈测试图片", fill=(90, 55, 35))
        im.save(path, quality=92)
        return path

    def test_moments(self, really_publish: bool):
        text = self.test_moment_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("提示", "测试文案不能为空。")
            return
        if really_publish and not messagebox.askyesno("真正发布", "这会真的发一条朋友圈，确定吗？"):
            return
        if not self._poster_wechat_ready():
            return
        cfg = self._dev_wechat_cfg()
        account = store.selected_account(store.load_accounts())
        img = self._test_image()

        def work():
            self.after(0, self._set_busy, True)
            if not GATE.begin_auto():
                self.after(0, lambda: (messagebox.showinfo("稍后再试", "机器人正在回复，朋友圈测试已让路。"), self._set_busy(False)))
                return
            try:
                WeChatUI(cfg, logger=store.log, hwnd=self._hwnd_for(account)).publish(
                    text, [img], preview_only=not really_publish, post_id="dev-test"
                )
                msg = "测试朋友圈已发布。" if really_publish else "已打开朋友圈编辑框，请检查后手动发布或关闭。"
                self.after(0, lambda: messagebox.showinfo("测试完成", msg))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda e=err: messagebox.showerror("朋友圈测试失败", e))
            finally:
                GATE.end_auto()
                self.after(0, self._set_busy, False)

        threading.Thread(target=work, daemon=True).start()

    # ---------- normal settings ----------
    def _build_prefs(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])

        lang = self._card(frame)
        lang.pack(fill="x", pady=(0, 10))
        tk.Label(lang, text="界面语言", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(lang, text="切换后整个界面直接换一套文本，不再朗读、不卡顿。",
                 bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        lr = tk.Frame(lang, bg=p["card"])
        lr.pack(fill="x", padx=16, pady=(8, 10))
        self.p_lang = ttk.Combobox(lr, values=["中文", "English", "日本語"], state="readonly", width=12)
        self.p_lang.pack(side="left")
        self.p_lang.bind("<<ComboboxSelected>>", self._change_language)
        tk.Label(lr, text="中文 / English / 日本語", bg=p["card"], fg=p["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=10)

        gen = self._card(frame)
        gen.pack(fill="x", pady=(0, 10))
        tk.Label(gen, text="常规", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        gr = tk.Frame(gen, bg=p["card"])
        gr.pack(fill="x", padx=16, pady=3)
        tk.Label(gr, text="主题", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.p_theme = ttk.Combobox(gr, values=[i18n.tr("浅色"), i18n.tr("深色")], state="readonly", width=8)
        self.p_theme.pack(side="left", padx=(6, 16))
        tk.Label(gr, text="群消息方式", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.p_group_mode = ttk.Combobox(gr, values=[i18n.tr("当前聊天（安全）"), i18n.tr("自动搜索（可能误点直播）")],
                                         state="readonly", width=20)
        self.p_group_mode.pack(side="left", padx=6)
        gr2 = tk.Frame(gen, bg=p["card"])
        gr2.pack(fill="x", padx=16, pady=3)
        tk.Label(gr2, text="图片处理等待（秒）", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.p_settle = ttk.Spinbox(gr2, from_=1, to=20, width=6)
        self.p_settle.pack(side="left", padx=(6, 16))
        tk.Label(gr2, text="发表确认等待（秒）", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.p_wait = ttk.Spinbox(gr2, from_=10, to=120, width=6)
        self.p_wait.pack(side="left", padx=6)
        self.p_start_min = tk.BooleanVar(value=False)
        self.p_autostart = tk.BooleanVar(value=False)
        tk.Checkbutton(gen, text="启动时最小化到任务栏", variable=self.p_start_min,
                       bg=p["card"], fg=p["text"], selectcolor=p["card"], activebackground=p["card"],
                       activeforeground=p["text"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(4, 0))
        tk.Checkbutton(gen, text="开机自动启动（当前用户）", variable=self.p_autostart,
                       bg=p["card"], fg=p["text"], selectcolor=p["card"], activebackground=p["card"],
                       activeforeground=p["text"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(0, 8))

        data = self._card(frame)
        data.pack(fill="x")
        tk.Label(data, text="数据", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        dr = tk.Frame(data, bg=p["card"])
        dr.pack(fill="x", padx=16, pady=(2, 8))
        self._btn(dr, "打开数据目录", lambda: os.startfile(str(store.DATA_DIR)), "ghost").pack(side="left")
        self._btn(dr, "打开日志", lambda: os.startfile(str(store.LOG_DIR)), "ghost").pack(side="left", padx=8)
        self._btn(dr, "备份配置", self._backup_config, "ghost").pack(side="left")
        self._btn(frame, "保存设置", self.save_prefs, "primary").pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(frame, text="语言、主题、启动设置保存后立即生效。",
                 bg=p["bg"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        return frame

    def _refresh_prefs(self):
        cfg = store.load_config()
        lang = cfg.get("ui", {}).get("language", "zh")
        self.p_lang.set(i18n.LANG_LABELS.get(lang, "中文"))
        self.p_theme.current(1 if self.theme_name == "dark" else 0)
        self.p_group_mode.current(1 if cfg.get("group", {}).get("mode") == "search" else 0)
        self.p_settle.delete(0, "end")
        self.p_settle.insert(0, str(cfg.get("image_settle_seconds", 4)))
        self.p_wait.delete(0, "end")
        self.p_wait.insert(0, str(cfg.get("publish_wait_seconds", 35)))
        self.p_start_min.set(bool(cfg.get("startup", {}).get("minimize", False)))
        self.p_autostart.set(self._autostart_enabled())

    def _change_language(self, _e=None):
        label = self.p_lang.get()
        lang = {"中文": "zh", "English": "en", "日本語": "ja"}.get(label, "zh")
        cfg = store.load_config()
        cfg.setdefault("ui", {})["language"] = lang
        store.save_config(cfg)
        i18n.set_lang(lang)
        self._build_ui()
        self.title(f"Bready Bear · {i18n.tr('烘焙内容管家')}")
        self.show_page("prefs")

    def _autostart_path(self) -> Path:
        return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Bready Bear.lnk"

    def _autostart_enabled(self) -> bool:
        return self._autostart_path().exists()

    def _set_autostart(self, enabled: bool):
        lnk = self._autostart_path()
        if enabled:
            ps = (
                "$ws=New-Object -ComObject WScript.Shell;"
                f"$s=$ws.CreateShortcut('{lnk}');"
                f"$s.TargetPath='{ROOT / '启动.bat'}';"
                f"$s.WorkingDirectory='{ROOT}';"
                f"$s.IconLocation='{ROOT / 'app.ico'},0';"
                "$s.Save();"
            )
        else:
            ps = f"Remove-Item -LiteralPath '{lnk}' -Force -ErrorAction SilentlyContinue;"
        subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return lnk.exists()

    def _backup_config(self):
        try:
            d = store.DATA_DIR / "backup"
            d.mkdir(parents=True, exist_ok=True)
            target = d / f"config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            shutil.copy2(store.CONFIG_PATH, target)
            messagebox.showinfo("备份完成", f"已备份到：\n{target}")
        except Exception as exc:
            messagebox.showerror("备份失败", str(exc))

    def save_prefs(self):
        cfg = store.load_config()
        lang = {"中文": "zh", "English": "en", "日本語": "ja"}.get(self.p_lang.get(), "zh")
        cfg.setdefault("ui", {})["language"] = lang
        theme = "dark" if self.p_theme.current() == 1 else "light"
        cfg["ui"]["theme"] = theme
        cfg.setdefault("group", {})["mode"] = "search" if self.p_group_mode.current() == 1 else "current"
        try:
            cfg["image_settle_seconds"] = max(1, min(20, int(self.p_settle.get() or 4)))
        except Exception:
            cfg["image_settle_seconds"] = 4
        try:
            cfg["publish_wait_seconds"] = max(10, min(120, int(self.p_wait.get() or 35)))
        except Exception:
            cfg["publish_wait_seconds"] = 35
        cfg.setdefault("startup", {})["minimize"] = self.p_start_min.get()
        store.save_config(cfg)
        self._set_autostart(self.p_autostart.get())
        theme_changed = theme != self.theme_name
        self.theme_name = theme
        self.p = PALETTES[theme]
        i18n.set_lang(lang)
        if theme_changed:
            self._build_styles()
            self._build_ui()
            self.show_page("prefs")
        else:
            self._refresh_prefs()
        messagebox.showinfo("已保存", "设置已保存。")

    def _build_bot(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        top = self._card(frame)
        top.pack(fill="x", pady=(0, 10))
        head = tk.Frame(top, bg=p["card"])
        head.pack(fill="x", padx=16, pady=(12, 2))
        tk.Label(head, text="微信机器人", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        self.bot_run_btn = self._btn(head, "启动微信机器人", self.toggle_wechat_bot, "primary")
        self.bot_run_btn.pack(side="right")
        self._btn(head, "控制台", self.open_robot_console, "ghost").pack(side="right", padx=8)
        self.bot_account_label = tk.Label(top, text="", bg=p["card"], fg=p["muted"],
                                          font=("Microsoft YaHei UI", 9), justify="left")
        self.bot_account_label.pack(anchor="w", padx=16)
        self.bot_status_label = tk.Label(top, text="未启动", bg=p["card"], fg=p["muted"],
                                         font=("Microsoft YaHei UI", 9, "bold"))
        self.bot_status_label.pack(anchor="w", padx=16, pady=(2, 8))

        main = self._card(frame)
        main.pack(fill="x")
        tk.Label(main, text="发送到当前微信群", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(main, text="先在微信打开目标群；只发当前聊天，不搜索，不会点直播。",
                 bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        group_row = tk.Frame(main, bg=p["card"])
        group_row.pack(fill="x", padx=16, pady=(8, 4))
        tk.Label(group_row, text="目标群（只作记录）", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.b_wx_group = ttk.Entry(group_row)
        self.b_wx_group.pack(side="left", fill="x", expand=True, padx=8)
        tk.Label(main, text="群友问题（AI 回复用，可空）", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        self.b_wx_question = tk.Text(main, height=2, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                                     insertbackground=p["text"], font=("Microsoft YaHei UI", 9), padx=8, pady=4)
        self.b_wx_question.pack(fill="x", padx=16, pady=(2, 4))
        tk.Label(main, text="要发送的内容", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        self.b_wx_text = tk.Text(main, height=3, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                                 insertbackground=p["text"], font=("Microsoft YaHei UI", 9), padx=8, pady=4)
        self.b_wx_text.pack(fill="x", padx=16, pady=(2, 6))
        wxbar = tk.Frame(main, bg=p["card"])
        wxbar.pack(fill="x", padx=16, pady=(0, 4))
        self._btn(wxbar, "发送到微信群", self.send_wx_current, "primary").pack(side="left")
        self._btn(wxbar, "AI 生成并发送", self.ai_send_wx, "ghost").pack(side="left", padx=8)

        self.wx_bot_running = False
        self._refresh_bot()
        return frame

    def toggle_wechat_bot(self):
        if self.wx_bot_running:
            self.wx_bot_running = False
            self._refresh_bot()
            return
        bot = store.bot_account()
        if not bot:
            messagebox.showwarning("没有机器人账号", "请到“账号”页，把某个账号的角色设为“机器人（群内互动）”。")
            return
        if not self._bot_wechat_ready():
            return
        self.wx_bot_running = True
        self._refresh_bot()

    def _bot_wechat_ready(self) -> bool:
        accounts = store.load_accounts()
        bot = store.bot_account(accounts)
        if not bot:
            messagebox.showwarning("没有机器人账号", "请到“账号”页设置一个“机器人”角色。")
            return False
        if self._hwnd_for(bot):
            return True
        active = store.active_account(accounts)
        wxid = (bot.get("wxid") or "").strip()
        if wxid and (not active or active.get("id") != bot.get("id")):
            messagebox.showwarning(
                "账号不匹配",
                f"机器人账号是「{bot.get('name')}」，但微信当前登录的是「{store.active_hint(accounts)}」。\n请先把微信切到机器人账号。",
            )
            return False
        return True

    def _refresh_bot(self):
        if not hasattr(self, "bot_status_label"):
            return
        bot = store.bot_account()
        if bot:
            self.bot_account_label.configure(text=f"{i18n.tr('机器人账号')}：{i18n.display_account(bot.get('name'))} · {bot.get('wxid') or i18n.tr('未绑定 wxid')}")
        else:
            self.bot_account_label.configure(text=f"{i18n.tr('机器人账号')}：{i18n.tr('未设置（到账号页选择角色）')}")
        if self.wx_bot_running:
            self.bot_status_label.configure(text="运行中（手动触发）", fg=self.p["green"])
            self.bot_run_btn.configure(text="停止微信机器人")
        else:
            self.bot_status_label.configure(text="未启动", fg=self.p["muted"])
            self.bot_run_btn.configure(text="启动微信机器人")

    def open_robot_console(self):
        if self.robot_console_win and self.robot_console_win.winfo_exists():
            self.robot_console_win.lift()
            return
        p = self.p
        win = tk.Toplevel(self)
        win.title("机器人控制台 · AI 发送监视")
        win.geometry("680x430")
        win.configure(bg=p["bg"])
        tk.Label(win, text="AI 生成 / 发送记录", bg=p["bg"], fg=p["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.robot_console_text = tk.Text(win, bg=p["card"], fg=p["text"], relief="flat", bd=0,
                                          font=("Consolas", 9), padx=10, pady=8, state="disabled")
        self.robot_console_text.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.robot_console_win = win
        self._refresh_robot_console()
        self.after(1000, self._robot_console_tick)

    def _refresh_robot_console(self):
        win = self.robot_console_win
        if not win or not win.winfo_exists():
            return
        events = store.read_robot_events(100)
        lines = []
        for e in events:
            mark = "✓" if e.get("ok", True) else "✗"
            lines.append(f"{e.get('time','')} {mark} [{e.get('kind','')}] {e.get('text','')}")
            if e.get("detail"):
                lines.append(f"    {e['detail']}")
        self.robot_console_text.configure(state="normal")
        self.robot_console_text.delete("1.0", "end")
        self.robot_console_text.insert("1.0", "\n".join(lines) or "暂无记录。")
        self.robot_console_text.see("end")
        self.robot_console_text.configure(state="disabled")

    def _robot_console_tick(self):
        if not self.robot_console_win or not self.robot_console_win.winfo_exists():
            self.robot_console_win = None
            return
        self._refresh_robot_console()
        self.after(1000, self._robot_console_tick)

    def _hwnd_for(self, account: dict | None):
        try:
            h = int((account or {}).get("wechat_hwnd") or 0)
        except Exception:
            return None
        if h <= 0:
            return None
        for w in enum_windows():
            if w.hwnd == h and w.title == "微信":
                return h
        return None

    def _active_wechat_account(self) -> dict | None:
        accounts = store.load_accounts()
        return store.bot_account(accounts) or store.selected_account(accounts)

    def _poster_wechat_ready(self) -> bool:
        accounts = store.load_accounts()
        sel = store.selected_account(accounts)
        if not sel:
            return False
        if self._hwnd_for(sel):
            return True
        active = store.active_account(accounts)
        wxid = (sel.get("wxid") or "").strip()
        if wxid and (not active or active.get("id") != sel.get("id")):
            messagebox.showwarning(
                "账号不匹配",
                f"当前操作账号是「{sel.get('name')}」，但微信登录的是「{store.active_hint(accounts)}」。\n请切换微信账号。",
            )
            return False
        return True

    def _selected_wechat_ready(self) -> bool:
        accounts = store.load_accounts()
        sel = store.bot_account(accounts) or store.selected_account(accounts)
        if not sel:
            return False
        if self._hwnd_for(sel):
            return True
        active = store.active_account(accounts)
        wxid = (sel.get("wxid") or "").strip()
        if wxid and (not active or active.get("id") != sel.get("id")):
            messagebox.showwarning(
                "账号不匹配",
                f"机器人账号是「{sel.get('name')}」，但微信当前登录的是「{store.active_hint(accounts)}」。\n请先把微信切到机器人账号。",
            )
            return False
        return True

    def _require_bot_started(self) -> bool:
        if self.wx_bot_running:
            return True
        messagebox.showinfo("微信机器人未启动", "先点右上角“启动微信机器人”。")
        return False

    def prepare_wx_current(self):
        text = self.b_wx_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("提示", "先填写要发送到微信群的内容。")
            return
        if not self._require_bot_started():
            return
        if not self._selected_wechat_ready():
            return
        cfg = store.load_config()
        cfg.setdefault("group", {})["mode"] = "current"
        cfg.update(cfg.get("wechat", {}) or {})
        account = self._active_wechat_account()

        def work():
            try:
                GATE.begin_robot()
                try:
                    GroupUI(cfg, hwnd=self._hwnd_for(account)).prepare_current(text)
                finally:
                    GATE.end_robot()
                self.after(0, lambda: messagebox.showinfo("已填入", "内容已填入微信输入框。\n请到微信按 Enter 发送。"))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda e=err: messagebox.showerror("填入失败", e))

        threading.Thread(target=work, daemon=True).start()

    def send_wx_current(self):
        text = self.b_wx_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("提示", "先填写要发送到微信群的内容。")
            return
        if not self._require_bot_started():
            return
        if not self._selected_wechat_ready():
            return
        if not messagebox.askyesno("发送到微信群", "确认发送到当前打开的微信群？\n\n" + text[:100]):
            return
        cfg = store.load_config()
        cfg.setdefault("group", {})["mode"] = "current"
        cfg.update(cfg.get("wechat", {}) or {})
        account = self._active_wechat_account()

        def work():
            self.after(0, self._set_busy, True)
            GATE.begin_robot()
            try:
                store.robot_event("手动发送", text)
                GroupUI(cfg, hwnd=self._hwnd_for(account)).send_current(text)
                store.robot_event("已发送", text, True)
                self.after(0, lambda: messagebox.showinfo("已发送", "已发送到当前微信群。"))
            except Exception as exc:
                err = str(exc)
                store.robot_event("发送失败", text, False, err)
                self.after(0, lambda e=err: messagebox.showerror("发送失败", e))
            finally:
                GATE.end_robot()
                self.after(0, self._set_busy, False)

        threading.Thread(target=work, daemon=True).start()

    def ai_send_wx(self):
        question = self.b_wx_question.get("1.0", "end-1c").strip() or self.b_wx_text.get("1.0", "end-1c").strip()
        if not question:
            messagebox.showinfo("提示", "先填群友问题，或填写要回复的内容。")
            return
        if not self.ai.available:
            messagebox.showwarning("未配置 API", "请先到“设置”填写 DeepSeek API Key。")
            return
        if not self._require_bot_started():
            return
        if not self._selected_wechat_ready():
            return
        if not messagebox.askyesno("AI 回复并发送", "用当前人设生成回复，并发送到当前微信群？"):
            return
        persona = self.b_persona_text.get("1.0", "end-1c").strip()
        cfg = store.load_config()
        cfg.setdefault("group", {})["mode"] = "current"
        cfg.update(cfg.get("wechat", {}) or {})
        account = self._active_wechat_account()

        def work():
            self.after(0, self._set_busy, True)
            try:
                answer = self.ai.chat([
                    {"role": "system", "content": persona or "你是烘焙助手，回答简短自然。"},
                    {"role": "user", "content": question},
                ])
                self.after(0, lambda: (self.b_wx_text.delete("1.0", "end"), self.b_wx_text.insert("1.0", answer)))
                store.robot_event("AI生成", answer)
                GATE.begin_robot()
                try:
                    GroupUI(cfg, hwnd=self._hwnd_for(account)).send_current(answer)
                finally:
                    GATE.end_robot()
                store.robot_event("AI已发送", answer, True)
                self.after(0, lambda: messagebox.showinfo("已发送", "AI 回复已发送到当前微信群。"))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda e=err: messagebox.showerror("发送失败", e))
            finally:
                self.after(0, self._set_busy, False)

        threading.Thread(target=work, daemon=True).start()

    def save_persona(self):
        name = self.b_persona_name.get().strip()
        text = self.b_persona_text.get("1.0", "end-1c").strip()
        if not name:
            messagebox.showerror("保存失败", "人设名字不能为空")
            return
        if not text:
            messagebox.showerror("保存失败", "人设内容不能为空")
            return
        cfg = store.load_config()
        cfg.setdefault("bot", {})
        cfg["bot"]["persona_name"] = name
        cfg["bot"]["persona"] = text
        store.save_config(cfg)
        messagebox.showinfo("已保存", "人设已保存（用于微信 AI 回复）。")

    def test_persona_reply(self):
        if not self.ai.available:
            messagebox.showwarning("未配置 API", "请先到“设置”填写 DeepSeek API Key。")
            return
        p = self.p
        win = tk.Toplevel(self)
        win.title("测试 DeepSeek 人设")
        win.geometry("560x420")
        win.configure(bg=p["bg"])
        tk.Label(win, text="用户消息", bg=p["bg"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(12, 2))
        inp = tk.Text(win, height=4, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                      insertbackground=p["text"], font=("Microsoft YaHei UI", 9), padx=8, pady=6)
        inp.pack(fill="x", padx=16)
        out = tk.Text(win, height=8, bg=p["card2"], fg=p["text"], relief="flat", bd=0,
                      insertbackground=p["text"], font=("Microsoft YaHei UI", 9), padx=8, pady=6)
        tk.Label(win, text="回复", bg=p["bg"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(8, 2))
        out.pack(fill="both", expand=True, padx=16)

        def run():
            msg = inp.get("1.0", "end-1c").strip()
            if not msg:
                return
            out.delete("1.0", "end")
            out.insert("1.0", "生成中…")
            persona = self.b_persona_text.get("1.0", "end-1c").strip()

            def show(text: str):
                out.delete("1.0", "end")
                out.insert("1.0", text)

            def work():
                try:
                    ans = self.ai.chat([
                        {"role": "system", "content": persona or "你是一个烘焙助手，回答简短自然。"},
                        {"role": "user", "content": msg},
                    ])
                    self.after(0, lambda a=ans: show(a))
                except Exception as exc:
                    err = str(exc)
                    self.after(0, lambda e=err: show(f"失败：{e}"))

            threading.Thread(target=work, daemon=True).start()

        self._btn(win, "生成回复", run, "primary").pack(anchor="w", padx=16, pady=8)

    # ---------- accounts ----------
    def _build_accounts(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        bar = tk.Frame(frame, bg=p["bg"])
        bar.pack(fill="x", pady=(0, 8))
        self._btn(bar, "新增账号", self.add_account, "primary").pack(side="left")
        self._btn(bar, "保存账号", self.save_accounts, "ghost").pack(side="left", padx=8)
        tk.Label(bar, text="最多 6 个；右上角选择哪个账号，朋友圈/群消息就只执行哪个账号。",
                 bg=p["bg"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left", padx=10)
        wrap = tk.Frame(frame, bg=p["bg"])
        wrap.pack(fill="both", expand=True)
        self.account_canvas = tk.Canvas(wrap, bg=p["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.account_canvas.yview)
        self.account_editor = tk.Frame(self.account_canvas, bg=p["bg"])
        self.account_window = self.account_canvas.create_window((0, 0), window=self.account_editor, anchor="nw")
        self.account_canvas.configure(yscrollcommand=scroll.set)
        self.account_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.account_editor.bind("<Configure>", lambda e: self.account_canvas.configure(scrollregion=self.account_canvas.bbox("all")))
        self.account_canvas.bind("<Configure>", lambda e: self.account_canvas.itemconfigure(self.account_window, width=e.width))
        self._refresh_account_cards()
        return frame

    def _refresh_account_cards(self):
        p = self.p
        for w in self.account_editor.winfo_children():
            w.destroy()
        self.account_vars = {}
        for i, a in enumerate(self.accounts):
            card = self._card(self.account_editor)
            card.pack(fill="x", pady=(0, 10))
            head = tk.Frame(card, bg=p["card"])
            head.pack(fill="x", padx=14, pady=(10, 2))
            tk.Label(head, text=i18n.display_account(a.get("name", a["id"])), bg=p["card"], fg=p["text"],
                     font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
            tk.Label(head, text=f"ID: {a['id']}", bg=p["card"], fg=p["muted"],
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=8)
            hwnd = a.get("wechat_hwnd")
            tk.Label(head, text=("窗口已绑定" if hwnd else "窗口未绑定"), bg=p["card"],
                     fg=p["green"] if hwnd else p["muted"],
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=4)
            self._btn(head, "选择微信窗口", lambda aid=a["id"]: self.bind_wechat_window(aid), "ghost").pack(side="right", padx=6)
            if len(self.accounts) > 1:
                self._btn(head, "删除", lambda aid=a["id"]: self.delete_account(aid), "danger").pack(side="right")
            grid = tk.Frame(card, bg=p["card"])
            grid.pack(fill="x", padx=14, pady=(0, 12))
            vars = {}
            fields = [("name", "名称", 1), ("role", "角色", 1), ("wxid", "wxid", 1), ("phone", "手机备注", 1),
                      ("group_name", "群名称", 2), ("persona", "人设", 2), ("note", "备注", 2)]
            r = 0
            for key, label, span in fields:
                tk.Label(grid, text=label, bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(
                    row=r, column=0, sticky="w", padx=(0, 8), pady=3)
                var = tk.StringVar(value=str(a.get(key, "")))
                if key == "role":
                    role_labels = {"poster": "运营（朋友圈/群消息）", "bot": "机器人（群内互动）", "both": "两者"}
                    var.set(i18n.tr(role_labels.get(a.get("role", "poster"), "运营（朋友圈/群消息）")))
                    ent = ttk.Combobox(grid, textvariable=var, state="readonly",
                                       values=[i18n.tr("运营（朋友圈/群消息）"), i18n.tr("机器人（群内互动）"), i18n.tr("两者")])
                else:
                    ent = ttk.Entry(grid, textvariable=var)
                ent.grid(row=r, column=1, columnspan=span, sticky="ew", pady=3)
                vars[key] = var
                r += 1
            tk.Label(grid, text="每天朋友圈", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).grid(
                row=r, column=0, sticky="w", padx=(0, 8), pady=3)
            var = tk.StringVar(value=str(a.get("moments_per_day", 4)))
            ttk.Entry(grid, textvariable=var, width=6).grid(row=r, column=1, sticky="w", pady=3)
            vars["moments_per_day"] = var
            en = tk.BooleanVar(value=bool(a.get("enabled", True)))
            tk.Checkbutton(grid, text="启用", variable=en, bg=p["card"], fg=p["text"],
                           selectcolor=p["card"], activebackground=p["card"],
                           activeforeground=p["text"]).grid(row=r, column=2, sticky="w")
            vars["enabled"] = en
            grid.grid_columnconfigure(1, weight=1)
            self.account_vars[a["id"]] = vars
        self.account_editor.update_idletasks()
        if hasattr(self, "account_canvas"):
            self.account_canvas.configure(scrollregion=self.account_canvas.bbox("all"))

    def add_account(self):
        if len(self.accounts) >= 6:
            messagebox.showinfo("提示", "最多 6 个账号。")
            return
        used = {a.get("id") for a in self.accounts}
        idx = 1
        while f"acct_{idx}" in used:
            idx += 1
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        pos = len(self.accounts)
        name = f"账号{letters[pos]}" if pos < len(letters) else f"账号{pos + 1}"
        account = {
            "id": f"acct_{idx}", "name": name, "wxid": "", "phone": "", "enabled": True,
            "role": "poster", "moments_per_day": 4, "group_name": "", "persona": "烘焙店主", "note": "",
            "image_dir": f"data/accounts/acct_{idx}/images",
        }
        self.accounts.append(account)
        store.save_accounts(self.accounts)
        self.accounts = store.load_accounts()
        self._refresh_account_cards()
        self._refresh_header()

    def delete_account(self, account_id: str):
        if len(self.accounts) <= 1:
            return
        if not messagebox.askyesno("删除账号", "删除这个账号配置？已有计划不会被删除。"):
            return
        self.accounts = [a for a in self.accounts if a.get("id") != account_id]
        store.save_accounts(self.accounts)
        self.accounts = store.load_accounts()
        if store.selected_account_id(self.accounts) == account_id:
            store.set_selected_account_id(self.accounts[0]["id"])
        self._refresh_account_cards()
        self._refresh_header()

    def bind_wechat_window(self, account_id: str):
        wins = [w for w in enum_windows()
                if w.title == "微信" and w.cls.startswith("Qt") and w.rect.width >= 600 and w.rect.height >= 500]
        if not wins:
            messagebox.showwarning("绑定失败", "没有检测到微信主窗口。")
            return
        if len(wins) == 1:
            self._save_window_binding(account_id, wins[0].hwnd)
            return
        p = self.p
        win = tk.Toplevel(self)
        win.title("选择微信窗口")
        win.geometry("420x300")
        win.configure(bg=p["bg"])
        tk.Label(win, text="检测到多个微信窗口，选择这个账号对应的窗口：", bg=p["bg"], fg=p["text"],
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w", padx=16, pady=(14, 8))
        for i, w in enumerate(wins, 1):
            text = f"微信窗口 {i}（{w.rect.left}, {w.rect.top}）"
            self._btn(win, text, lambda h=w.hwnd: (self._save_window_binding(account_id, h), win.destroy()),
                      "ghost").pack(fill="x", padx=16, pady=4)
        tk.Label(win, text="提示：两个窗口分别登录运营号和机器人号，各自绑定一次。",
                 bg=p["bg"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=8)

    def _save_window_binding(self, account_id: str, hwnd: int):
        self.accounts = store.load_accounts()
        for a in self.accounts:
            if a.get("id") == account_id:
                a["wechat_hwnd"] = str(hwnd)
                break
        store.save_accounts(self.accounts)
        self.accounts = store.load_accounts()
        self._refresh_account_cards()
        messagebox.showinfo("已绑定", f"已绑定微信窗口 HWND {hwnd}。\n重启微信后需要重新绑定。")

    def save_accounts(self):
        try:
            for a in self.accounts:
                v = self.account_vars[a["id"]]
                a["name"] = v["name"].get().strip() or a["id"]
                role_text = v["role"].get()
                a["role"] = "bot" if any(x in role_text for x in ("机器人", "Bot", "ボット")) else ("both" if any(x in role_text for x in ("两者", "Both", "両方")) else "poster")
                a["wxid"] = v["wxid"].get().strip()
                a["phone"] = v["phone"].get().strip()
                a["group_name"] = v["group_name"].get().strip()
                a["persona"] = v["persona"].get().strip()
                a["note"] = v.get("note", tk.StringVar()).get().strip()
                a["moments_per_day"] = max(1, min(6, int(v["moments_per_day"].get() or 4)))
                a["enabled"] = bool(v["enabled"].get())
            store.save_accounts(self.accounts)
            self.accounts = store.load_accounts()
            sel = store.selected_account(self.accounts)
            if sel:
                self.m_account.configure(values=[i18n.display_account(sel.get("name", ""))])
                self.m_account.set(i18n.display_account(sel.get("name", "")))
            self._refresh_header()
            messagebox.showinfo("已保存", "账号配置已保存。")
            self._refresh_dashboard()
            self._refresh_account_cards()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    # ---------- settings ----------
    def _build_settings(self, parent):
        p = self.p
        frame = tk.Frame(parent, bg=p["bg"])
        card = self._card(frame)
        card.pack(fill="x")
        tk.Label(card, text="DeepSeek API", bg=p["card"], fg=p["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        self.s_key = ttk.Entry(card, show="*")
        self.s_base = ttk.Entry(card)
        self.s_model = ttk.Entry(card)
        for label, widget in (("API Key", self.s_key), ("Base URL", self.s_base), ("模型", self.s_model)):
            tk.Label(card, text=label, bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(6, 0))
            widget.pack(fill="x", padx=16, pady=(2, 0))
        tk.Label(card, text="API Key 由客户在设置里填写；本软件不预置任何 Key，只保存在本机 config.json。",
                 bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(6, 0))
        theme_row = tk.Frame(card, bg=p["card"])
        theme_row.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(theme_row, text="主题", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.s_theme = ttk.Combobox(theme_row, values=["浅色", "深色"], state="readonly", width=10)
        self.s_theme.pack(side="left", padx=8)
        tk.Label(theme_row, text="群消息", bg=p["card"], fg=p["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(16, 0))
        self.s_group_mode = ttk.Combobox(theme_row, values=["当前聊天（安全）", "自动搜索（可能误点直播）"],
                                         state="readonly", width=20)
        self.s_group_mode.pack(side="left", padx=8)
        self._btn(card, "保存设置", self.save_settings, "primary").pack(anchor="w", padx=16, pady=(10, 14))
        self.settings_info = tk.Label(frame, text="", bg=p["bg"], fg=p["muted"], justify="left",
                                      font=("Microsoft YaHei UI", 9))
        self.settings_info.pack(anchor="w", pady=(14, 0))
        return frame

    def _refresh_settings(self):
        cfg = store.load_config()
        self.s_key.delete(0, "end")
        self.s_key.insert(0, store.load_api_key())
        self.s_base.delete(0, "end")
        self.s_base.insert(0, cfg.get("ai", {}).get("base_url", "https://api.deepseek.com"))
        self.s_model.delete(0, "end")
        self.s_model.insert(0, cfg.get("ai", {}).get("model", "deepseek-chat"))
        self.s_theme.set("深色" if self.theme_name == "dark" else "浅色")
        self.s_group_mode.set("自动搜索（可能误点直播）" if cfg.get("group", {}).get("mode") == "search" else "当前聊天（安全）")
        self.settings_info.configure(text=(
            f"当前账号：{store.active_hint(self.accounts)}\n"
            "排期：朋友圈/群消息都靠在对应页面手工新建，不自动生成时间槽。\n"
            "本版本没有手机端/局域网服务；所有操作都在本机完成。\n"
            "文案、群消息都需要你手工填写；DeepSeek 只用于机器人人设测试。"
        ))

    def save_settings(self):
        cfg = store.load_config()
        cfg.setdefault("ai", {})
        store.save_api_key(self.s_key.get().strip())
        cfg["ai"]["base_url"] = self.s_base.get().strip() or "https://api.deepseek.com"
        cfg["ai"]["model"] = self.s_model.get().strip() or "deepseek-chat"
        cfg.setdefault("ui", {})["theme"] = "dark" if self.s_theme.get() == "深色" else "light"
        cfg.setdefault("group", {})["mode"] = "search" if "自动搜索" in self.s_group_mode.get() else "current"
        store.save_config(cfg)
        self.cfg = cfg
        self.ai = DeepSeekClient(cfg)
        if cfg["ui"]["theme"] != self.theme_name:
            self.theme_name = cfg["ui"]["theme"]
            self.p = PALETTES[self.theme_name]
            self._build_styles()
            self._build_ui()
            self.show_page("settings")
        messagebox.showinfo("已保存", "设置已保存。")

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.p = PALETTES[self.theme_name]
        cfg = store.load_config()
        cfg.setdefault("ui", {})["theme"] = self.theme_name
        store.save_config(cfg)
        self._build_styles()
        self._build_ui()
        self.show_page(self.page)

    # ---------- status/log ----------
    def _set_busy(self, active: bool, text: str = "正在操作微信，请不要动鼠标键盘"):
        try:
            if active:
                if self._busy_win and self._busy_win.winfo_exists():
                    return
                p = self.p
                win = tk.Toplevel(self)
                win.overrideredirect(True)
                win.attributes("-topmost", True)
                win.configure(bg=p["accent"])
                tk.Label(win, text="⚠", bg=p["accent"], fg="#fff",
                         font=("Microsoft YaHei UI", 18, "bold")).pack(side="left", padx=(16, 6), pady=10)
                tk.Label(win, text=text + "\n鼠标键盘会被自动操作，请勿触碰", bg=p["accent"], fg="#fff",
                         font=("Microsoft YaHei UI", 10, "bold"), justify="left").pack(side="left", padx=(0, 18), pady=8)
                win.update_idletasks()
                x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
                y = self.winfo_rooty() + 90
                win.geometry(f"+{x}+{y}")
                self._busy_win = win
            else:
                if self._busy_win is not None:
                    try:
                        self._busy_win.destroy()
                    except Exception:
                        pass
                    self._busy_win = None
        except Exception:
            pass

    def _refresh_header(self, snap: dict | None = None):
        snap = snap or self.scheduler.snapshot()
        accounts = store.load_accounts()
        posters = store.poster_accounts(accounts) or accounts
        names = [i18n.display_account(a.get("name", a["id"])) for a in posters]
        self.account_selector.configure(values=names)
        sel = store.selected_account(posters) or (posters[0] if posters else None)
        if sel:
            self.selected_id = sel["id"]
            sel_display = i18n.display_account(sel.get("name", ""))
            if self.account_selector.get() != sel_display:
                self.account_selector.set(sel_display)
        self.active_badge.configure(text=f"{i18n.tr('微信')}：{snap['active_hint']}")
        self.ai_badge.configure(text="DeepSeek 已配置" if self.ai.available else i18n.tr("AI 未配置"))
        self.pause_btn.configure(text=i18n.tr("继续" if snap["paused"] else "暂停"))
        sel_name = i18n.display_account(sel.get("name")) if sel else i18n.tr("未选择")
        self.side_status.configure(text=(
            f"{i18n.tr('操作账号')}：{sel_name}\n"
            f"{i18n.tr('微信')}：{snap['active_hint']}\n"
            f"{i18n.tr('待发布')} {snap['counts'].get(store.STATUS_PLANNED, 0)} · {i18n.tr('等待')} {snap['counts'].get(store.STATUS_WAITING, 0)}"
        ))

    def on_select_account(self, _e=None):
        name = self.account_selector.get()
        accounts = store.load_accounts()
        account = next((a for a in accounts if i18n.display_account(a.get("name")) == name), None)
        if not account:
            return
        self.selected_id = account["id"]
        store.set_selected_account_id(account["id"])
        self.new_auto()
        self._refresh_header()
        self._refresh_dashboard()
        self._refresh_auto_list()
        store.log("INFO", f"切换操作账号：{account.get('name')}（朋友圈/群消息只执行这个账号）")

    def _tick(self):
        if not self.winfo_exists():
            return
        try:
            self.accounts = store.load_accounts()
            snap = self.scheduler.snapshot()
            self._refresh_header(snap)
            self._set_busy(bool(snap.get("busy")))
            if self.page == "dashboard":
                self._refresh_dashboard()
            elif self.page == "auto":
                self._refresh_auto_list()
            elif self.page == "bot":
                pass
            i18n.apply_texts(self)
        except Exception:
            pass
        self.after(3000, self._tick)

    def _refresh_logs(self):
        if not self.winfo_exists():
            return
        lines = store.read_logs(80)
        if len(lines) != self._log_len:
            self._log_len = len(lines)
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("end", "\n".join(lines))
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(2000, self._refresh_logs)

    def _refresh_all(self):
        self.accounts = store.load_accounts()
        self._refresh_header()
        self._refresh_dashboard()
        self._refresh_auto_list()
        self._refresh_bot()
        self._refresh_persona()
        self._refresh_account_cards()
        self._refresh_settings()
        self.new_auto()

    def toggle_pause(self):
        if self.scheduler.paused:
            self.scheduler.resume()
        else:
            self.scheduler.pause()
        self._tick()

    def _close(self):
        if messagebox.askyesno("退出", "退出后定时发布将停止，确定吗？"):
            self.scheduler.stop()
            self.destroy()


def _single_instance() -> bool:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    k.CreateMutexW.restype = ctypes.c_void_p
    h = k.CreateMutexW(None, False, "Local\\BreadyBear_single")
    if not h:
        return True
    if ctypes.get_last_error() == 183:
        return False
    globals()["_MUTEX"] = h
    return True


def main():
    if not _single_instance():
        try:
            ctypes.windll.user32.MessageBoxW(0, "Bready Bear 已经在运行。\n请在任务栏查看已打开的窗口。", "Bready Bear", 0x40)
        except Exception:
            pass
        return
    app = App()
    try:
        if store.load_config().get("startup", {}).get("minimize", False):
            app.iconify()
    except Exception:
        pass
    app.mainloop()


if __name__ == "__main__":
    main()

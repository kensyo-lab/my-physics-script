#!/usr/bin/env python3
"""
SPICE Kernel Downloader — GUI版
Apache Index 形式の HTTP サーバーから階層を維持して全ファイルをダウンロード。

依存: Python 3.8+ 標準ライブラリのみ (tkinter 含む)
起動: python spice_downloader_gui.py
"""

import time
import threading
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from html.parser import HTMLParser
from urllib.parse import urljoin, unquote, urlsplit
from datetime import datetime
from collections import deque
from typing import Optional
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

# ──────────────────────────────────────────────
# デフォルト設定
# ──────────────────────────────────────────────
DEFAULT_URL        = "http://spiftp.esac.esa.int/data/SPICE/VENUS-EXPRESS/"
DEFAULT_OUTPUT_DIR = "./VENUS-EXPRESS"
DEFAULT_WORKERS    = 2
DEFAULT_RETRY      = 3
DEFAULT_RETRY_DELAY= 5
DEFAULT_TIMEOUT    = 60
DEFAULT_CHUNK_SIZE = 1024 * 1024   # 1 MB
WORKERS_WARN_THRESHOLD = 3         # これを超えたら警告


# ──────────────────────────────────────────────
# コア: スキャン＆ダウンロードロジック
# ──────────────────────────────────────────────

class ApacheIndexParser(HTMLParser):
    def __init__(self, current_url: str, root_url: str):
        super().__init__()
        self.current_url = current_url
        self.root_url    = root_url
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        if not href or href.startswith("?") or href.startswith("#"):
            return
        if href in ("../", "/"):
            return
        full_url = urljoin(self.current_url, href)
        if not full_url.startswith(self.root_url):
            return
        self.links.append(full_url)


def fetch_html(url: str, timeout: int) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; SPICE-Downloader-GUI/2.0)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def list_directory(
    url: str, root_url: str, timeout: int, log_cb=None
) -> tuple[list[str], list[str]]:
    try:
        html = fetch_html(url, timeout)
    except Exception as e:
        if log_cb:
            log_cb(f"[WARN] ディレクトリ取得失敗: {url} → {e}")
        return [], []
    parser = ApacheIndexParser(current_url=url, root_url=root_url)
    parser.feed(html)
    dirs, files = [], []
    for link in parser.links:
        (dirs if link.endswith("/") else files).append(link)
    return dirs, files


def collect_all_files(
    root_url: str,
    timeout: int,
    log_cb,
    stop_event: threading.Event,
    allowed_exts: Optional[set] = None,
) -> list[str]:
    all_files: list[str] = []
    queue    = [root_url]
    visited  : set[str] = set()

    while queue and not stop_event.is_set():
        url = queue.pop()
        if url in visited:
            continue
        visited.add(url)
        log_cb(f"[SCAN] {url}")
        dirs, files = list_directory(url, root_url, timeout, log_cb=log_cb)
        if allowed_exts is not None:
            files = [
                f for f in files
                if Path(urlsplit(f).path).suffix.lower() in allowed_exts
            ]
        all_files.extend(files)
        queue.extend(dirs)

    all_files = list(dict.fromkeys(all_files))
    return all_files


def url_to_local_path(file_url: str, root_url: str, output_dir: Path) -> Path:
    relative = unquote(file_url[len(root_url):])
    return output_dir / relative


def download_one(
    file_url   : str,
    root_url   : str,
    output_dir : Path,
    timeout    : int,
    retry_max  : int,
    retry_delay: int,
    chunk_size : int,
    skip_existing: bool,
    stop_event : threading.Event,
) -> tuple[str, str, str]:
    """Returns (file_url, status, detail)  status: ok | skip | stopped | error"""
    local_path = url_to_local_path(file_url, root_url, output_dir)

    if skip_existing and local_path.exists() and local_path.stat().st_size > 0:
        return file_url, "skip", ""

    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = local_path.parent / (local_path.name + ".tmp")

    attempts   = max(1, retry_max + 1)   # retry_max=0 → 1回試行
    last_error = ""

    for attempt in range(1, attempts + 1):
        if stop_event.is_set():
            return file_url, "stopped", ""
        try:
            req = urllib.request.Request(
                file_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; SPICE-Downloader-GUI/2.0)"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp, \
                    open(tmp_path, "wb") as f:
                while not stop_event.is_set():
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
            if stop_event.is_set():
                if tmp_path.exists():
                    tmp_path.unlink()
                return file_url, "stopped", ""
            tmp_path.replace(local_path)
            return file_url, "ok", ""
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            if attempt < attempts and not stop_event.is_set():
                time.sleep(retry_delay)

    return file_url, "error", last_error


def cleanup_tmp(output_dir: Path) -> int:
    stale = list(output_dir.rglob("*.tmp"))
    for p in stale:
        try:
            p.unlink()
        except OSError:
            pass
    return len(stale)


# ──────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────

FONT_MONO  = ("Courier New", 10)
FONT_LABEL = ("Courier New", 10, "bold")
FONT_TITLE = ("Courier New", 14, "bold")

COLOR_BG     = "#0a0e0a"
COLOR_PANEL  = "#0f1a0f"
COLOR_BORDER = "#1e3a1e"
COLOR_GREEN  = "#00ff41"
COLOR_DIM    = "#2a5a2a"
COLOR_YELLOW = "#ffe94d"
COLOR_RED    = "#ff4444"
COLOR_WHITE  = "#c8ffc8"
COLOR_TROUGH = "#0f1a0f"


class SpiceDownloaderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SPICE Kernel Downloader")
        self.configure(bg=COLOR_BG)
        self.resizable(True, True)
        self.minsize(820, 680)

        # 状態
        self._stop_event       = threading.Event()
        self._worker_thread    : Optional[threading.Thread]  = None
        self._caffeinate_proc  : Optional[object]           = None
        self._failed_urls      : list[str] = []
        self._log_lines    : deque     = deque(maxlen=5000)
        self._total        = 0
        self._done         = 0
        self._current_file = tk.StringVar(value="—")
        self._status_var   = tk.StringVar(value="STANDBY")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI構築 ──────────────────────────────
    def _build_ui(self):
        # タイトルバー
        hdr = tk.Frame(self, bg=COLOR_BG)
        hdr.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(
            hdr, text="◈ SPICE KERNEL DOWNLOADER",
            font=FONT_TITLE, fg=COLOR_GREEN, bg=COLOR_BG
        ).pack(side="left")
        self._status_label = tk.Label(
            hdr, textvariable=self._status_var,
            font=FONT_LABEL, fg=COLOR_DIM, bg=COLOR_BG
        )
        self._status_label.pack(side="right")

        tk.Frame(self, bg=COLOR_BORDER, height=1).pack(fill="x", padx=16, pady=2)

        # 設定パネル
        cfg = tk.Frame(self, bg=COLOR_PANEL, bd=0, highlightthickness=1,
                       highlightbackground=COLOR_BORDER)
        cfg.pack(fill="x", padx=16, pady=6)
        cfg.columnconfigure(1, weight=1)

        self._add_entry(cfg, 0, "URL",        DEFAULT_URL,        "_url_var")
        self._add_dir_row(cfg, 1)
        self._add_entry(cfg, 2, "拡張子フィルタ", "",
                        "_ext_var", hint="空=全件  例: .bc .bsp .tls")

        # 数値設定行
        num_row = tk.Frame(cfg, bg=COLOR_PANEL)
        num_row.grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=4)

        self._workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        self._timeout_var = tk.IntVar(value=DEFAULT_TIMEOUT)
        self._retry_var   = tk.IntVar(value=DEFAULT_RETRY)

        self._add_spinner(num_row, "並列数", self._workers_var, 1, 8,
                          self._on_workers_change)
        self._warn_label = tk.Label(
            num_row, text="", font=("Courier New", 9),
            fg=COLOR_YELLOW, bg=COLOR_PANEL
        )
        self._warn_label.pack(side="left", padx=(4, 16))

        self._add_spinner(num_row, "タイムアウト(秒)", self._timeout_var, 10, 300)
        tk.Label(num_row, text="  ", bg=COLOR_PANEL).pack(side="left")
        self._add_spinner(num_row, "リトライ回数", self._retry_var, 0, 10)

        # スキップチェック
        self._skip_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            num_row, text="既存スキップ", variable=self._skip_var,
            font=FONT_MONO, fg=COLOR_WHITE, bg=COLOR_PANEL,
            selectcolor=COLOR_BG, activebackground=COLOR_PANEL,
            activeforeground=COLOR_GREEN
        ).pack(side="left", padx=12)

        tk.Frame(self, bg=COLOR_BORDER, height=1).pack(fill="x", padx=16, pady=2)

        # プログレス
        prog_frame = tk.Frame(self, bg=COLOR_BG)
        prog_frame.pack(fill="x", padx=16, pady=4)
        prog_frame.columnconfigure(0, weight=1)

        # 全体プログレスバー
        tk.Label(prog_frame, text="TOTAL", font=("Courier New", 8),
                 fg=COLOR_DIM, bg=COLOR_BG).grid(row=0, column=0, sticky="w")
        self._pct_label = tk.Label(prog_frame, text="0 / 0  (0.0%)",
                                   font=FONT_MONO, fg=COLOR_GREEN, bg=COLOR_BG)
        self._pct_label.grid(row=0, column=1, sticky="e")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Green.Horizontal.TProgressbar",
                        troughcolor=COLOR_TROUGH, background=COLOR_GREEN,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_GREEN,
                        darkcolor=COLOR_GREEN)
        style.configure("Dim.Horizontal.TProgressbar",
                        troughcolor=COLOR_TROUGH, background=COLOR_DIM,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_DIM,
                        darkcolor=COLOR_DIM)

        self._total_bar = ttk.Progressbar(
            prog_frame, style="Green.Horizontal.TProgressbar",
            orient="horizontal", length=100, mode="determinate"
        )
        self._total_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        # 現在ファイル
        tk.Label(prog_frame, text="NOW ", font=("Courier New", 8),
                 fg=COLOR_DIM, bg=COLOR_BG).grid(row=2, column=0, sticky="w")
        self._cur_bar = ttk.Progressbar(
            prog_frame, style="Dim.Horizontal.TProgressbar",
            orient="horizontal", length=100, mode="indeterminate"
        )
        self._cur_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        tk.Label(prog_frame, textvariable=self._current_file,
                 font=("Courier New", 9), fg=COLOR_DIM, bg=COLOR_BG,
                 anchor="w").grid(row=4, column=0, columnspan=2, sticky="ew")

        # 統計ラベル
        stat_row = tk.Frame(self, bg=COLOR_BG)
        stat_row.pack(fill="x", padx=16, pady=2)
        self._stat_ok   = self._stat_label(stat_row, "OK",    COLOR_GREEN)
        self._stat_skip = self._stat_label(stat_row, "SKIP",  COLOR_DIM)
        self._stat_err  = self._stat_label(stat_row, "ERROR", COLOR_RED)

        tk.Frame(self, bg=COLOR_BORDER, height=1).pack(fill="x", padx=16, pady=2)

        # ボタン群
        btn_row = tk.Frame(self, bg=COLOR_BG)
        btn_row.pack(fill="x", padx=16, pady=6)

        self._btn_start  = self._btn(btn_row, "▶  START",   self._on_start,  COLOR_GREEN)
        self._btn_stop   = self._btn(btn_row, "■  STOP",    self._on_stop,   COLOR_YELLOW, state="disabled")
        self._btn_resume = self._btn(btn_row, "↺  RESUME",  self._on_resume, COLOR_DIM, state="disabled")
        self._btn_retry  = self._btn(btn_row, "⟳  RETRY ERR", self._on_retry_errors, COLOR_RED, state="disabled")
        self._btn(btn_row, "🗑  LOG CLEAR", self._clear_log, COLOR_DIM)

        # ログ
        log_header = tk.Frame(self, bg=COLOR_BG)
        log_header.pack(fill="x", padx=16)
        tk.Label(log_header, text="LOG", font=FONT_LABEL,
                 fg=COLOR_DIM, bg=COLOR_BG).pack(side="left")
        self._btn(log_header, "💾 SAVE LOG", self._save_log, COLOR_DIM, padx=4, pady=1)

        self._log_box = scrolledtext.ScrolledText(
            self, bg=COLOR_PANEL, fg=COLOR_GREEN,
            font=("Courier New", 9), bd=0,
            highlightthickness=1, highlightbackground=COLOR_BORDER,
            insertbackground=COLOR_GREEN, state="disabled"
        )
        self._log_box.pack(fill="both", expand=True, padx=16, pady=(2, 12))
        self._log_box.tag_config("error",  foreground=COLOR_RED)
        self._log_box.tag_config("warn",   foreground=COLOR_YELLOW)
        self._log_box.tag_config("ok",     foreground=COLOR_GREEN)
        self._log_box.tag_config("scan",   foreground=COLOR_DIM)
        self._log_box.tag_config("system", foreground=COLOR_WHITE)

    def _add_entry(self, parent, row, label, default, attr, hint=""):
        tk.Label(parent, text=label, font=FONT_LABEL,
                 fg=COLOR_DIM, bg=COLOR_PANEL, width=14, anchor="e"
                 ).grid(row=row, column=0, padx=(10, 4), pady=3, sticky="e")
        var = tk.StringVar(value=default)
        setattr(self, attr, var)
        e = tk.Entry(parent, textvariable=var, font=FONT_MONO,
                     bg=COLOR_BG, fg=COLOR_GREEN, insertbackground=COLOR_GREEN,
                     bd=0, highlightthickness=1, highlightbackground=COLOR_BORDER)
        e.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        if hint:
            tk.Label(parent, text=hint, font=("Courier New", 8),
                     fg=COLOR_DIM, bg=COLOR_PANEL
                     ).grid(row=row, column=2, padx=(0, 10), sticky="w")

    def _add_dir_row(self, parent, row):
        tk.Label(parent, text="保存先", font=FONT_LABEL,
                 fg=COLOR_DIM, bg=COLOR_PANEL, width=14, anchor="e"
                 ).grid(row=row, column=0, padx=(10, 4), pady=3, sticky="e")
        self._dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        e = tk.Entry(parent, textvariable=self._dir_var, font=FONT_MONO,
                     bg=COLOR_BG, fg=COLOR_GREEN, insertbackground=COLOR_GREEN,
                     bd=0, highlightthickness=1, highlightbackground=COLOR_BORDER)
        e.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        btn = self._btn(parent, "…", self._browse_dir, COLOR_DIM,
                        padx=4, pady=2, auto_pack=False)
        btn.grid(row=row, column=2, padx=(0, 10))

    def _add_spinner(self, parent, label, var, min_, max_, command=None):
        tk.Label(parent, text=label, font=("Courier New", 9),
                 fg=COLOR_DIM, bg=COLOR_PANEL).pack(side="left")
        sb = tk.Spinbox(
            parent, from_=min_, to=max_, textvariable=var, width=4,
            font=FONT_MONO, bg=COLOR_BG, fg=COLOR_GREEN,
            insertbackground=COLOR_GREEN, bd=0,
            highlightthickness=1, highlightbackground=COLOR_BORDER,
            buttonbackground=COLOR_PANEL,
            command=command
        )
        sb.pack(side="left", padx=(2, 10))
        if command:
            sb.bind("<FocusOut>", lambda e: command())

    def _btn(self, parent, text, cmd, fg=COLOR_GREEN,
             state="normal", padx=8, pady=3, auto_pack=True):
        b = tk.Button(
            parent, text=text, command=cmd,
            font=FONT_MONO, fg=fg, bg=COLOR_BG,
            activeforeground=COLOR_BG, activebackground=fg,
            bd=0, highlightthickness=1, highlightbackground=COLOR_BORDER,
            padx=padx, pady=pady, cursor="hand2", state=state
        )
        if auto_pack:
            b.pack(side="left", padx=4)
        return b

    def _stat_label(self, parent, text, color):
        f = tk.Frame(parent, bg=COLOR_BG)
        f.pack(side="left", padx=8)
        tk.Label(f, text=text, font=("Courier New", 8),
                 fg=color, bg=COLOR_BG).pack()
        var = tk.StringVar(value="0")
        tk.Label(f, textvariable=var, font=("Courier New", 13, "bold"),
                 fg=color, bg=COLOR_BG).pack()
        return var

    # ── ログ ────────────────────────────────
    def _log(self, msg: str):
        now = datetime.now().strftime("%H:%M:%S")
        line = f"{now}  {msg}"
        self._log_lines.append(line)

        # タグ判定
        tag = "ok"
        ml = msg.lower()
        if "[error]" in ml or "失敗" in ml or "例外" in ml:
            tag = "error"
        elif "[warn" in ml or "警告" in ml:
            tag = "warn"
        elif "[scan]" in ml:
            tag = "scan"
        elif "[system]" in ml:
            tag = "system"

        def _insert():
            if not self.winfo_exists():
                return
            try:
                self._log_box.config(state="normal")
                self._log_box.insert("end", line + "\n", tag)
                self._log_box.see("end")
                self._log_box.config(state="disabled")
            except tk.TclError:
                pass
        self.after(0, _insert)

    def _clear_log(self):
        try:
            self._log_box.config(state="normal")
            self._log_box.delete("1.0", "end")
            self._log_box.config(state="disabled")
        except tk.TclError:
            pass
        self._log_lines.clear()

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("テキスト", "*.txt"), ("全ファイル", "*.*")],
            initialfile=f"spice_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if path:
            Path(path).write_text("\n".join(self._log_lines), encoding="utf-8")
            self._log(f"[SYSTEM] ログ保存: {path}")

    # ── ウィジェット操作 ──────────────────────
    def _set_status(self, text: str, color=COLOR_GREEN):
        def _f():
            if not self.winfo_exists():
                return
            try:
                self._status_var.set(text)
                self._status_label.config(fg=color)
            except tk.TclError:
                pass
        self.after(0, _f)

    def _update_progress(self, done: int, total: int, fname: str = ""):
        def _f():
            if not self.winfo_exists():
                return
            try:
                pct = done / total * 100 if total else 0
                self._pct_label.config(
                    text=f"{done} / {total}  ({pct:.1f}%)"
                )
                self._total_bar["maximum"] = max(total, 1)
                self._total_bar["value"]   = done
                if fname:
                    self._current_file.set(fname)  # 呼び出し元でPath.name済み
            except tk.TclError:
                pass
        self.after(0, _f)

    def _update_stats(self, ok: int, skip: int, err: int):
        def _f():
            if not self.winfo_exists():
                return
            try:
                self._stat_ok.set(str(ok))
                self._stat_skip.set(str(skip))
                self._stat_err.set(str(err))
            except tk.TclError:
                pass
        self.after(0, _f)

    def _set_buttons(self, running: bool, has_errors: bool = False):
        def _f():
            if not self.winfo_exists():
                return
            try:
                self._btn_start.config(
                    state="disabled" if running else "normal")
                self._btn_stop.config(
                    state="normal" if running else "disabled")
                self._btn_resume.config(
                    state="disabled" if running else "normal")
                self._btn_retry.config(
                    state="normal" if (not running and has_errors) else "disabled")
            except tk.TclError:
                pass
        self.after(0, _f)

    def _cur_bar_start(self):
        def _f():
            if not self.winfo_exists():
                return
            try:
                self._cur_bar.start(12)
            except tk.TclError:
                pass
        self.after(0, _f)

    def _cur_bar_stop(self):
        def _f():
            if not self.winfo_exists():
                return
            try:
                self._cur_bar.stop()
                self._cur_bar.config(value=0)
            except tk.TclError:
                pass
        self.after(0, _f)

    # ── イベント ─────────────────────────────
    def _browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self._dir_var.set(d)

    def _on_workers_change(self):
        try:
            v = self._workers_var.get()
        except Exception:
            return
        if v >= WORKERS_WARN_THRESHOLD:
            self._warn_label.config(
                text=f"⚠ {v}並列: サーバ負荷に配慮してください"
            )
        else:
            self._warn_label.config(text="")

    def _on_start(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showerror("エラー", "URLを入力してください")
            return
        self._failed_urls.clear()
        self._done  = 0
        self._total = 0
        self._update_stats(0, 0, 0)
        self._update_progress(0, 0)
        self._stop_event.clear()
        self._start_download(url, self._dir_var.get().strip(), retry_failed=False)

    def _on_stop(self):
        self._stop_event.set()
        self._set_status("STOPPING …", COLOR_YELLOW)
        self._log("[SYSTEM] 中断要求を送信しました")

    def _on_resume(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showerror("エラー", "URLを入力してください")
            return
        self._stop_event.clear()
        self._log("[SYSTEM] ─── RESUME ───")
        self._start_download(url, self._dir_var.get().strip(), retry_failed=False)

    def _on_retry_errors(self):
        if not self._failed_urls:
            messagebox.showinfo("情報", "失敗URLがありません")
            return
        self._stop_event.clear()
        self._log(f"[SYSTEM] ─── RETRY {len(self._failed_urls)} errors ───")
        self._start_download(
            self._url_var.get().strip(),
            self._dir_var.get().strip(),
            retry_failed=True
        )

    # ── ダウンロードスレッド ──────────────────
    def _start_download(self, url: str, out_dir: str, retry_failed: bool):
        self._set_buttons(running=True)
        self._set_status("RUNNING", COLOR_GREEN)
        self._cur_bar_start()
        self._caffeinate_start()

        root_url   = url.rstrip("/") + "/"
        output_dir = Path(out_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        workers     = self._workers_var.get()
        timeout     = self._timeout_var.get()
        retry_max   = self._retry_var.get()
        skip_ex     = self._skip_var.get()
        ext_text    = self._ext_var.get().strip()
        allowed_exts = (
            {e.strip().lower() for e in ext_text.split() if e.strip()}
            if ext_text else None
        )

        def _thread():
            try:
                # .tmp クリーンアップ
                removed = cleanup_tmp(output_dir)
                if removed:
                    self._log(f"[SYSTEM] 残存 .tmp を {removed} 件削除")

                # ファイル一覧収集
                if retry_failed:
                    targets = list(self._failed_urls)
                    self._failed_urls.clear()
                else:
                    self._log("[SYSTEM] ── Phase 1: スキャン開始 ──")
                    targets = collect_all_files(
                        root_url, timeout, self._log,
                        self._stop_event, allowed_exts
                    )
                    self._log(f"[SYSTEM] {len(targets)} ファイル検出")

                if not targets or self._stop_event.is_set():
                    self._log("[SYSTEM] 対象なし または 中断")
                    self._finish(False)
                    return

                self._total = len(targets)
                self._done  = 0
                results = {"ok": 0, "skip": 0, "stopped": 0, "error": 0}
                self._log("[SYSTEM] ── Phase 2: ダウンロード開始 ──")

                with ThreadPoolExecutor(max_workers=workers) as exe:
                    future_map: dict[Future, str] = {
                        exe.submit(
                            download_one,
                            u, root_url, output_dir,
                            timeout, retry_max, DEFAULT_RETRY_DELAY,
                            DEFAULT_CHUNK_SIZE, skip_ex, self._stop_event
                        ): u
                        for u in targets
                    }
                    for future in as_completed(future_map):
                        src_url = future_map[future]
                        try:
                            _, status, detail = future.result()
                        except Exception as e:
                            self._log(f"[ERROR] 予期しない例外: {src_url} → {e}")
                            status, detail = "error", str(e)

                        results[status] = results.get(status, 0) + 1
                        self._done += 1

                        if status == "error":
                            self._failed_urls.append(src_url)
                            name = Path(urlsplit(src_url).path).name
                            self._log(f"[ERROR] {name} → {detail}" if detail else f"[ERROR] {name}")
                        elif status == "ok":
                            fname = Path(urlsplit(src_url).path).name
                            self._log(f"[OK]    {fname}")
                        # stopped は静かに通過（ユーザー操作なのでログ不要）

                        self._update_progress(
                            self._done, self._total,
                            Path(urlsplit(src_url).path).name
                        )
                        self._update_stats(
                            results["ok"], results["skip"], results["error"]
                        )

                # 失敗URL保存
                if self._failed_urls:
                    fail_path = output_dir / "failed_downloads.txt"
                    fail_path.write_text(
                        "\n".join(dict.fromkeys(self._failed_urls)),
                        encoding="utf-8"
                    )
                    self._log(f"[WARN] 失敗URL保存: {fail_path}")

                completed = not self._stop_event.is_set()
                self._log(
                    f"[SYSTEM] 完了: OK={results['ok']} "
                    f"SKIP={results['skip']} "
                    f"STOPPED={results['stopped']} "
                    f"ERROR={results['error']}"
                )
                self._finish(completed)

            except Exception as e:
                self._log(f"[ERROR] スレッド例外: {e}")
                self._finish(False)

        self._worker_thread = threading.Thread(target=_thread, daemon=True)
        self._worker_thread.start()

    def _finish(self, completed: bool):
        self._cur_bar_stop()
        self._caffeinate_stop()
        has_errors = len(self._failed_urls) > 0

        if completed:
            self._set_status("DONE", COLOR_GREEN)
            self._log("[SYSTEM] ─── ALL DONE ───")
            self._beep_done()
        elif self._stop_event.is_set():
            self._set_status("STOPPED", COLOR_YELLOW)
            self._log("[SYSTEM] ─── STOPPED ───")
        else:
            self._set_status("ERROR", COLOR_RED)

        self._set_buttons(running=False, has_errors=has_errors)

    def _caffeinate_start(self):
        """macOS のみ: caffeinate -i でスリープを抑制する"""
        import sys, subprocess
        if sys.platform != "darwin":
            return
        if self._caffeinate_proc and self._caffeinate_proc.poll() is None:
            return  # 既に起動中
        try:
            self._caffeinate_proc = subprocess.Popen(
                ["caffeinate", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log("[SYSTEM] caffeinate 開始 (スリープ抑制中)")
        except FileNotFoundError:
            self._log("[WARN] caffeinate が見つかりません")
        except Exception as e:
            self._log(f"[WARN] caffeinate 起動失敗: {e}")

    def _caffeinate_stop(self):
        """caffeinate プロセスを終了してスリープ抑制を解除する"""
        if self._caffeinate_proc is None:
            return
        try:
            if self._caffeinate_proc.poll() is None:
                self._caffeinate_proc.terminate()
                self._caffeinate_proc.wait(timeout=3)
            self._log("[SYSTEM] caffeinate 終了 (スリープ抑制解除)")
        except Exception as e:
            self._log(f"[WARN] caffeinate 終了失敗: {e}")
        finally:
            self._caffeinate_proc = None

    def _beep_done(self):
        """完了BEEP（OS別対応）"""
        import sys
        try:
            if sys.platform == "win32":
                import winsound
                for freq, dur in [(880, 150), (1100, 150), (1320, 250)]:
                    winsound.Beep(freq, dur)
                    time.sleep(0.05)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"],
                               check=False)
            else:
                import subprocess
                subprocess.run(["paplay", "/usr/share/sounds/freedesktop/"
                                "stereo/complete.oga"], check=False)
        except Exception:
            pass  # BEEP失敗は無視

    def _on_close(self):
        if self._worker_thread and self._worker_thread.is_alive():
            if messagebox.askyesno("確認", "ダウンロード中です。終了しますか？"):
                self._stop_event.set()
                self._set_status("STOPPING …", COLOR_YELLOW)
                # スレッド終了を200msポーリングで待ってからdestroy
                self.after(200, self._poll_close_after_stop)
        else:
            self._caffeinate_stop()
            self.destroy()

    def _poll_close_after_stop(self):
        """STOPイベント後、ワーカー終了を確認してからウィンドウを破棄する"""
        if self._worker_thread and self._worker_thread.is_alive():
            self.after(200, self._poll_close_after_stop)
        else:
            self._caffeinate_stop()
            self.destroy()


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = SpiceDownloaderApp()
    app.mainloop()

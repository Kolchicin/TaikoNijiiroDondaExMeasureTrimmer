# -*- coding: utf-8 -*-
"""
TaikoNijiiroDondaEx 曲谱开头剪裁：根据目标秒数生成新的 TJA 与 OGG。
封装版本会内置 ffmpeg；源码运行时也会从程序目录和 PATH 兜底查找。
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ENC_FALLBACKS = ("cp932", "utf-8-sig", "utf-8")
START_PREROLL_MEASURES = 1
AUTHOR_ID = "@Dr秋水仙素"
APP_NAME = "TaikoNijiiroDondaEx曲谱开头剪裁"
APP_SUBTITLE = "按目标时间生成新的谱面与音频文件"
COLOR_PRIMARY = "#6B4A7A"
COLOR_ACCENT = "#D8B6E6"
COLOR_BG = "#F7F2F8"
COLOR_CARD = "#FFFFFF"
COLOR_RESULT = "#F1E7F5"
COLOR_LOG_BG = "#2B2130"


@dataclass(frozen=True)
class AnalysisResult:
    bpm: float
    measure_sec: float
    boundary_index: int
    measures_to_drop: int
    trim_sec: float
    target_sec: float
    encoding: str


def read_tja_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ENC_FALLBACKS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def write_tja_text(path: Path, text: str, encoding: str) -> None:
    nl = "\r\n" if encoding.lower() in ("cp932", "shift_jis", "sjis") else "\n"
    path.write_bytes(text.replace("\n", nl).encode(encoding, errors="replace"))


def parse_header_bpm(text: str) -> float | None:
    for line in text.splitlines():
        u = line.strip().upper()
        if u.startswith("BPM:"):
            try:
                return float(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                return None
    return None


def measure_duration_sec(bpm: float, beats_per_measure: float = 4.0) -> float:
    return beats_per_measure * (60.0 / bpm)


def first_measure_index_at_or_before(target_sec: float, bpm: float, beats_per_measure: float = 4.0) -> int:
    """返回小节索引 i（从 #START 后第一条小节行算起为 0），使得 i * 小节时长 <= target 且尽量大。"""
    if bpm <= 0 or beats_per_measure <= 0:
        return 0
    m = measure_duration_sec(bpm, beats_per_measure)
    # 避免浮点边界
    return int(math.floor((target_sec + 1e-9) / m + 1e-12))


def trim_seconds_for_measures(n_measures: int, bpm: float, beats_per_measure: float = 4.0) -> float:
    return n_measures * measure_duration_sec(bpm, beats_per_measure)


def cut_measure_count(boundary_idx: int) -> int:
    """保留短预备段，让剪裁后进入点保留自然的谱面上下文。"""
    return max(0, boundary_idx - START_PREROLL_MEASURES)


def is_measure_line(s: str) -> bool:
    s = s.strip()
    if not s.endswith(","):
        return False
    body = s.rstrip(",").strip()
    if not body:
        return False
    return bool(re.match(r"^[0-9]+$", body))


def trim_course_block(lines: list[str], measures_to_drop: int) -> list[str]:
    out: list[str] = []
    pending_cmds: list[str] = []
    measure_idx = 0
    for line in lines:
        raw = line.rstrip("\n\r")
        if is_measure_line(raw):
            if measure_idx < measures_to_drop:
                measure_idx += 1
                continue
            out.extend(pending_cmds)
            pending_cmds.clear()
            out.append(raw)
            measure_idx += 1
        elif raw.strip().startswith("#"):
            pending_cmds.append(raw)
        else:
            out.append(raw)
    # 保留结尾处的状态指令，避免 #END 前的 GOGO 等状态未正常闭合。
    out.extend(pending_cmds)
    return out


def count_measures_in_first_start_block(text: str) -> int | None:
    """首个 #START … #END 块中的小节行数量（用于粗检）。"""
    lines = text.splitlines()
    in_block = False
    n = 0
    for line in lines:
        ls = line.strip()
        if ls == "#START" and not in_block:
            in_block = True
            n = 0
            continue
        if in_block:
            if ls == "#END":
                return n
            if is_measure_line(line):
                n += 1
    return None


def compute_output_stem(original_stem: str, prefix: str) -> str:
    """无前缀时为避免覆盖原文件，输出 stem 加 _trim。"""
    p = prefix.strip()
    return (p + original_stem) if p else (original_stem + "_trim")


def apply_title_and_wave(
    text: str,
    prefix: str,
    tja_stem: str,
    new_wave_basename: str,
) -> str:
    """
    更新 TITLE（无前缀时不改标题）与 WAVE（始终指向新 OGG 文件名）。
    有前缀且无 TITLE 行时，在文首插入 TITLE。
    """
    p = prefix.strip()
    lines = text.split("\n")
    title_found = False
    out_lines: list[str] = []
    for line in lines:
        raw = line.rstrip("\r")
        lu = raw.lstrip("\ufeff").strip().upper()
        if lu.startswith("TITLE:"):
            title_found = True
            old_val = raw.split(":", 1)[1].strip() if ":" in raw else ""
            base = old_val if old_val else tja_stem
            new_val = (p + base) if p else base
            bom = "\ufeff" if raw.startswith("\ufeff") else ""
            out_lines.append(f"{bom}TITLE:{new_val}")
        elif lu.startswith("WAVE:"):
            bom = "\ufeff" if raw.startswith("\ufeff") else ""
            out_lines.append(f"{bom}WAVE:{new_wave_basename}")
        else:
            out_lines.append(raw)

    body = "\n".join(out_lines)
    if p and not title_found:
        insert_line = f"TITLE:{p}{tja_stem}\n"
        if body.startswith("\ufeff"):
            return "\ufeff" + insert_line + body[1:]
        return insert_line + body
    return body


def process_tja_content(
    text: str,
    measures_to_drop: int,
    trim_sec: float,
    keep_offset: bool = True,
) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    n = len(lines)
    header_seen_course = False

    while i < n:
        line = lines[i]
        lu = line.lstrip("\ufeff").strip().upper()

        if lu.startswith("OFFSET:") and not keep_offset:
            try:
                old = float(line.split(":", 1)[1].strip())
                out.append(f"OFFSET:{old + trim_sec}\n")
            except ValueError:
                out.append(line)
            i += 1
            continue

        if lu.startswith("DEMOSTART:"):
            try:
                old = float(line.split(":", 1)[1].strip())
                nd = max(0.0, old - trim_sec)
                out.append(f"DEMOSTART:{nd}\n")
            except ValueError:
                out.append(line)
            i += 1
            continue

        if lu.startswith("COURSE:"):
            header_seen_course = True

        if line.strip() == "#START" and header_seen_course:
            out.append(line)
            i += 1
            block: list[str] = []
            while i < n and lines[i].strip() != "#END":
                block.append(lines[i])
                i += 1
            trimmed = trim_course_block(block, measures_to_drop)
            for ln in trimmed:
                out.append(ln if ln.endswith("\n") else ln + "\n")
            if i < n and lines[i].strip() == "#END":
                out.append(lines[i])
                i += 1
            continue

        out.append(line)
        i += 1

    return "".join(out)


def clamp_fade_in_seconds(raw: str) -> float:
    """解析渐入时长（秒），限制在合理范围。"""
    v = float(raw.strip().replace(",", "."))
    if v <= 0:
        raise ValueError("渐入时长须为正数")
    return max(0.05, min(3.0, v))


def build_ffmpeg_trim_ogg_args(
    ff: str,
    trim_sec: float,
    src_tmp: Path,
    ogg_out: Path,
    fade_in_sec: float | None,
) -> list[str]:
    """
    剪裁起点后的 OGG。一律输出标准 Vorbis OGG，保证时长与流封装兼容性。
    """
    base = [
        ff,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(trim_sec),
        "-i",
        str(src_tmp),
        "-c:a",
        "libvorbis",
        "-q:a",
        "5",
    ]
    if fade_in_sec is not None:
        af = f"afade=t=in:st=0:d={fade_in_sec:.4f}:curve=qsin"
        return [*base, "-af", af, str(ogg_out)]
    return [*base, str(ogg_out)]


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", app_base_dir()))


def bundled_logo_path() -> Path | None:
    for root in (bundled_resource_dir(), app_base_dir()):
        candidate = root / "logo.png"
        if candidate.is_file():
            return candidate
    return None


def find_ffmpeg() -> str | None:
    names = ("ffmpeg.exe", "ffmpeg")
    roots = (
        bundled_resource_dir(),
        app_base_dir(),
        app_base_dir() / "ffmpeg",
        app_base_dir() / "ffmpeg" / "bin",
        app_base_dir() / "vendor" / "ffmpeg" / "bin",
    )
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return str(candidate)

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        r = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return "ffmpeg"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("682x875")
        self.minsize(682, 640)
        self.configure(bg=COLOR_BG)
        self._setup_style()
        self._apply_window_icon()

        self.var_ogg = tk.StringVar()
        self.var_tja = tk.StringVar()
        self.var_sec = tk.StringVar(value="48")
        self.var_keep_offset = tk.BooleanVar(value=True)
        self.var_prefix = tk.StringVar(value="")
        self.var_fade_in = tk.BooleanVar(value=True)
        self.var_fade_sec = tk.StringVar(value="0.35")
        self.var_audio_status = tk.StringVar(
            value="音频引擎：已就绪" if find_ffmpeg() else "音频引擎：未找到"
        )

        root = ttk.Frame(self, style="App.TFrame", padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(root, bg=COLOR_PRIMARY, padx=18, pady=14)
        header.grid(row=0, column=0, sticky=tk.EW)
        tk.Label(
            header,
            text=APP_NAME,
            bg=COLOR_PRIMARY,
            fg="#ffffff",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            header,
            text=APP_SUBTITLE,
            bg=COLOR_PRIMARY,
            fg="#EFE2F4",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor=tk.W, pady=(4, 0))

        files = ttk.LabelFrame(root, text="文件", style="Card.TLabelframe", padding=14)
        files.grid(row=1, column=0, sticky=tk.EW, pady=(14, 0))
        files.columnconfigure(1, weight=1)
        ttk.Label(files, text="OGG 音源", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W, pady=6)
        ttk.Entry(files, textvariable=self.var_ogg).grid(row=0, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Button(files, text="选择文件", style="Browse.TButton", command=self.pick_ogg).grid(row=0, column=2, pady=6)
        ttk.Label(files, text="TJA 谱面", style="Field.TLabel").grid(row=1, column=0, sticky=tk.W, pady=6)
        ttk.Entry(files, textvariable=self.var_tja).grid(row=1, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Button(files, text="选择文件", style="Browse.TButton", command=self.pick_tja).grid(row=1, column=2, pady=6)
        ttk.Label(files, textvariable=self.var_audio_status, style="Hint.TLabel").grid(
            row=2, column=1, columnspan=2, sticky=tk.W, padx=8, pady=(2, 0)
        )

        options = ttk.LabelFrame(root, text="剪裁设置", style="Card.TLabelframe", padding=14)
        options.grid(row=2, column=0, sticky=tk.EW, pady=(12, 0))
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="剪裁目标", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W, pady=6)
        target_fr = ttk.Frame(options, style="Card.TFrame")
        target_fr.grid(row=0, column=1, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(target_fr, textvariable=self.var_sec, width=12).pack(side=tk.LEFT)
        ttk.Label(target_fr, text="秒（从谱面起点计时）", style="Hint.TLabel").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(options, text="自定义前缀", style="Field.TLabel").grid(row=1, column=0, sticky=tk.W, pady=6)
        prefix_fr = ttk.Frame(options, style="Card.TFrame")
        prefix_fr.grid(row=1, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Entry(prefix_fr, textvariable=self.var_prefix, width=24).pack(side=tk.LEFT)
        ttk.Label(prefix_fr, text="用于新文件名与游戏内标题", style="Hint.TLabel").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(options, text="音频处理", style="Field.TLabel").grid(row=2, column=0, sticky=tk.W, pady=6)
        fade_fr = tk.Frame(options, bg="#FBF7FC", padx=10, pady=8, highlightthickness=1, highlightbackground="#E9D8EF")
        fade_fr.grid(row=2, column=1, sticky=tk.W, padx=8, pady=6)
        tk.Checkbutton(
            fade_fr,
            text="开头淡入",
            variable=self.var_fade_in,
            bg="#FBF7FC",
            fg=COLOR_PRIMARY,
            activebackground="#FBF7FC",
            activeforeground=COLOR_PRIMARY,
            selectcolor="#FBF7FC",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT)
        ttk.Entry(fade_fr, textvariable=self.var_fade_sec, width=6).pack(side=tk.LEFT, padx=(10, 4))
        tk.Label(fade_fr, text="秒", bg="#FBF7FC", fg="#80648B", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)

        ttk.Label(options, text="谱面兼容", style="Field.TLabel").grid(row=3, column=0, sticky=tk.W, pady=6)
        advanced = tk.Frame(options, bg="#FBF7FC", padx=10, pady=8, highlightthickness=1, highlightbackground="#E9D8EF")
        advanced.grid(row=3, column=1, sticky=tk.W, padx=8, pady=(4, 0))
        tk.Checkbutton(
            advanced,
            text="保持原 OFFSET（推荐）",
            variable=self.var_keep_offset,
            bg="#FBF7FC",
            fg=COLOR_PRIMARY,
            activebackground="#FBF7FC",
            activeforeground=COLOR_PRIMARY,
            selectcolor="#FBF7FC",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        actions = ttk.Frame(root, style="App.TFrame")
        actions.grid(row=3, column=0, sticky=tk.EW, pady=(14, 0))
        ttk.Button(actions, text="分析", style="Secondary.TButton", command=self.on_analyze).pack(side=tk.LEFT)
        ttk.Button(actions, text="生成新文件", style="Primary.TButton", command=self.on_apply).pack(
            side=tk.LEFT, padx=(10, 0)
        )

        self.result_var = tk.StringVar(value="请选择文件后点击「分析」。")
        ttk.Label(
            root,
            textvariable=self.result_var,
            style="Result.TLabel",
            wraplength=700,
            justify=tk.LEFT,
        ).grid(row=4, column=0, sticky=tk.EW, pady=(12, 0))

        log_card = ttk.LabelFrame(root, text="日志", style="Card.TLabelframe", padding=10)
        log_card.grid(row=5, column=0, sticky=tk.NSEW, pady=(12, 0))
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_card,
            height=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=COLOR_LOG_BG,
            fg="#F6E8FF",
            insertbackground="#F6E8FF",
            relief=tk.FLAT,
            padx=10,
            pady=8,
            font=("Consolas", 10),
        )
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        sb = ttk.Scrollbar(log_card, command=self.log.yview)
        sb.grid(row=0, column=1, sticky=tk.NS)
        self.log.configure(yscrollcommand=sb.set)
        ttk.Label(root, text=AUTHOR_ID, style="Author.TLabel").grid(row=6, column=0, sticky=tk.E, pady=(8, 0))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(5, weight=1)

        self._last_analysis: AnalysisResult | None = None

    def _apply_window_icon(self) -> None:
        logo = bundled_logo_path()
        if logo is None:
            return
        try:
            self._window_icon = tk.PhotoImage(file=str(logo))
            self.iconphoto(True, self._window_icon)
        except tk.TclError:
            pass

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=COLOR_BG)
        style.configure("Card.TFrame", background=COLOR_CARD)
        style.configure("Card.TLabelframe", background=COLOR_CARD, bordercolor="#E7DAEC", relief=tk.SOLID)
        style.configure(
            "Card.TLabelframe.Label",
            background=COLOR_CARD,
            foreground=COLOR_PRIMARY,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure("Field.TLabel", background=COLOR_CARD, foreground=COLOR_PRIMARY, font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Hint.TLabel", background=COLOR_CARD, foreground="#80648B", font=("Microsoft YaHei UI", 9))
        style.configure(
            "Result.TLabel",
            background=COLOR_RESULT,
            foreground=COLOR_PRIMARY,
            padding=10,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure("Author.TLabel", background=COLOR_BG, foreground="#80648B", font=("Microsoft YaHei UI", 8))
        style.configure("TButton", padding=(12, 6), font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", background=COLOR_PRIMARY, foreground="#ffffff", padding=(16, 8), bordercolor=COLOR_PRIMARY)
        style.map("Primary.TButton", background=[("active", "#5C3F69"), ("disabled", "#DCC9E5")])
        style.configure("Secondary.TButton", background=COLOR_CARD, foreground=COLOR_PRIMARY, padding=(16, 8), bordercolor=COLOR_ACCENT)
        style.map("Secondary.TButton", background=[("active", "#F1E7F5")])
        style.configure("Browse.TButton", background="#F1E7F5", foreground=COLOR_PRIMARY, padding=(12, 6), bordercolor=COLOR_ACCENT)
        style.map("Browse.TButton", background=[("active", COLOR_ACCENT)], foreground=[("active", "#ffffff")])

    def pick_ogg(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("OGG", "*.ogg"), ("音频", "*.ogg;*.wav;*.mp3"), ("全部", "*.*")])
        if p:
            self.var_ogg.set(p)

    def pick_tja(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("TJA", "*.tja"), ("全部", "*.*")])
        if p:
            self.var_tja.set(p)

    def log_line(self, s: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, s + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def on_analyze(self) -> None:
        self._last_analysis = None
        tja_path = Path(self.var_tja.get().strip())
        if not tja_path.is_file():
            messagebox.showerror("错误", "请选择有效的 TJA 文件。")
            return
        try:
            target = float(self.var_sec.get().strip().replace(",", "."))
        except ValueError:
            messagebox.showerror("错误", "目标秒数请输入数字。")
            return
        if target < 0:
            messagebox.showerror("错误", "秒数不能为负。")
            return

        text, enc = read_tja_text(tja_path)
        bpm = parse_header_bpm(text)
        if bpm is None or bpm <= 0:
            messagebox.showerror("错误", "无法在 TJA 头部解析 BPM。")
            return

        msec = measure_duration_sec(bpm)
        boundary_idx = first_measure_index_at_or_before(target, bpm)
        drop = cut_measure_count(boundary_idx)
        trim_sec = trim_seconds_for_measures(drop, bpm)

        mcount = count_measures_in_first_start_block(text)
        warn = ""
        if mcount is not None and drop > mcount:
            warn = f"（警告：首个难度仅 {mcount} 小节，将裁空或异常，请检查。）"

        self._last_analysis = AnalysisResult(
            bpm=bpm,
            measure_sec=msec,
            boundary_index=boundary_idx,
            measures_to_drop=drop,
            trim_sec=trim_sec,
            target_sec=target,
            encoding=enc,
        )

        extra = f" 当前难度小节数≈{mcount}。" if mcount is not None else ""
        self.result_var.set(
            f"BPM={bpm:.6g}，小节长≈{msec:.6f}s。"
            f" 将从约 {trim_sec:.3f}s 处生成新音源，并保留进入点前的短预备段。"
            f" 谱面会从第 {drop + 1} 小节开始保留（编码：{enc}）。{extra}{warn}"
        )
        self.log_line(
            f"[分析] {tja_path.name} | 目标 {target:g}s | 输出起点≈{trim_sec:.6f}s"
        )

    def on_apply(self) -> None:
        self.on_analyze()
        if self._last_analysis is None:
            return

        ogg_path = Path(self.var_ogg.get().strip())
        tja_path = Path(self.var_tja.get().strip())
        if not tja_path.is_file():
            messagebox.showerror("错误", "请选择有效的 TJA 文件。")
            return
        if not ogg_path.is_file():
            messagebox.showerror("错误", "请选择有效的 OGG 文件。")
            return

        ff = find_ffmpeg()
        if not ff:
            messagebox.showerror("错误", "找不到内置音频引擎。请确认 ffmpeg.exe 已随程序一起打包。")
            return

        a = self._last_analysis
        idx = cut_measure_count(a.boundary_index)
        trim_sec = trim_seconds_for_measures(idx, a.bpm)
        enc = a.encoding

        mcount = count_measures_in_first_start_block(read_tja_text(tja_path)[0])
        if mcount is not None and idx > mcount:
            messagebox.showerror("错误", f"剪裁位置超过当前难度长度（当前约 {mcount} 小节）。")
            return

        fade_sec: float | None = None
        if self.var_fade_in.get():
            try:
                fade_sec = clamp_fade_in_seconds(self.var_fade_sec.get())
            except ValueError:
                messagebox.showerror("错误", "渐入秒数请输入正数（建议 0.2～0.8）。")
                return

        fade_note = ""
        if fade_sec is not None:
            fade_note = f"\n音频开头渐入约 {fade_sec:.2f}s。"
        fade_note += "\n音频会重新封装为标准 Vorbis OGG，以保证兼容性。"
        prefix = self.var_prefix.get()
        new_stem = compute_output_stem(tja_path.stem, prefix)
        out_tja = tja_path.with_name(new_stem + ".tja")
        out_ogg = tja_path.with_name(new_stem + ".ogg")
        if out_tja.resolve() == tja_path.resolve():
            messagebox.showerror("错误", "输出 TJA 路径与源文件相同，请修改前缀或文件名。")
            return
        if out_ogg.resolve() == ogg_path.resolve():
            messagebox.showerror("错误", "输出 OGG 路径与所选音源相同，请修改前缀或文件名。")
            return
        if out_tja.is_file() or out_ogg.is_file():
            if not messagebox.askyesno(
                "覆盖确认",
                f"目标已存在，将覆盖：\n{out_tja}\n{out_ogg}\n\n仍要继续吗？",
            ):
                return

        if not messagebox.askyesno(
            "确认",
            f"将写入新文件（不改动原文件）：\n{out_tja}\n{out_ogg}\n\n"
            f"源：{tja_path.name} / {ogg_path.name}\n"
            f"谱面从第 {idx + 1} 小节开始保留，音轨从约 {trim_sec:.4f}s 处生成。{fade_note}\n确定继续？",
        ):
            return

        def work() -> None:
            try:
                self._apply_impl(
                    ogg_path,
                    tja_path,
                    out_tja,
                    out_ogg,
                    prefix,
                    idx,
                    trim_sec,
                    enc,
                    ff,
                    fade_sec,
                )
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "完成",
                        f"已生成：\n{out_tja}\n{out_ogg}",
                    ),
                )
            except Exception as e:
                err = str(e)
                self.after(0, lambda: messagebox.showerror("失败", err))
                self.after(0, lambda: self.log_line("[错误] " + err))

        threading.Thread(target=work, daemon=True).start()

    def _apply_impl(
        self,
        ogg_path: Path,
        tja_path: Path,
        out_tja: Path,
        out_ogg: Path,
        prefix: str,
        measures_to_drop: int,
        trim_sec: float,
        enc: str,
        ff: str,
        fade_in_sec: float | None = None,
    ) -> None:
        self.after(0, lambda: self.log_line("[执行] 读取 TJA…"))
        text, _ = read_tja_text(tja_path)
        keep_off = self.var_keep_offset.get()
        new_text = process_tja_content(text, measures_to_drop, trim_sec, keep_offset=keep_off)
        new_text = apply_title_and_wave(new_text, prefix, tja_path.stem, out_ogg.name)

        self.after(0, lambda: self.log_line("[执行] 写入新 TJA…"))
        write_tja_text(out_tja, new_text, enc)

        src_tmp = ogg_path.with_name(ogg_path.stem + "_trim_src.ogg")
        log_ffmpeg = (
            "[执行] 生成 OGG 音频（含淡入）…"
            if fade_in_sec is not None
            else "[执行] 生成 OGG 音频…"
        )
        self.after(0, lambda m=log_ffmpeg: self.log_line(m))
        try:
            shutil.copy2(ogg_path, src_tmp)
            cmd = build_ffmpeg_trim_ogg_args(ff, trim_sec, src_tmp, out_ogg, fade_in_sec)
            subprocess.check_call(cmd, timeout=600)
        finally:
            src_tmp.unlink(missing_ok=True)
        self.after(0, lambda: self.log_line(f"[完成] 已写入 OGG：{out_ogg.name}"))


def main() -> None:
    app = App()
    app.mainloop()


def _fatal_log_path() -> Path:
    return Path(__file__).resolve().parent / "last_error.txt"


if __name__ == "__main__":
    import traceback

    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:
        tb = traceback.format_exc()
        try:
            _fatal_log_path().write_text(tb, encoding="utf-8")
        except OSError:
            pass
        print(tb)
        print("\n详细错误已写入:", _fatal_log_path())
        try:
            import tkinter as tk
            from tkinter import messagebox

            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(
                "剪裁工具异常",
                "程序出错，详情见同目录 last_error.txt\n\n" + str(exc),
            )
            r.destroy()
        except BaseException:
            input("\n按回车关闭…")
        raise SystemExit(1) from exc

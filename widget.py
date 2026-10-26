"""Sena quota pet: transparent compact widget, expandable read-only details."""
import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import warnings
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk
from usage_core import (QuotaClient, UsageError, change_since, credit_amount, headline, load_history,
                        pct, save_history, window_name)
from screens import work_areas, nearest_area, clamp, virtual_bounds, geometry
from i18n import LANGUAGES, tr, quota_name
from presentation import MODES, TransitionTracker, display_windows, quota_state, character_motion
from character_packs import discover_packs, image_path, voice_path, animation_interval
from voice_player import VoicePlayer
from bundle_paths import application_paths

BASE, RESOURCES = application_paths(sys.executable, __file__, getattr(sys, 'frozen', False),
                                    getattr(sys, '_MEIPASS', None))
BG, PANEL, TEXT, MUTED, ACCENT = "#171d2b", "#232d40", "#edf2ff", "#a8b6cf", "#b6c9ff"
KEY = "#010203"


def clock_text(epoch):
    return datetime.fromtimestamp(epoch).strftime("%m/%d %H:%M") if epoch else "미제공"


def countdown(epoch, now):
    if not epoch:
        return "시간 미제공"
    if epoch <= now:
        return "리셋 시각 경과 · 새 수치 확인 중"
    mins = int((epoch - now) // 60)
    days, mins = divmod(mins, 1440)
    hours, mins = divmod(mins, 60)
    return (f"{days}일 " if days else "") + f"{hours}시간 {mins}분 남음"


def read_preferences(path):
    # Preserve Korean for existing unversioned installations; new installs use English.
    default = dict(interval=120, topmost=True, alerts=True, animation=True,
                   language='ko' if path.exists() else 'en', display='auto',
                   voice_enabled=True, volume=50, character='sena')
    try:
        if path.stat().st_size > 16384:
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        for k in ("topmost", "alerts", "animation", "voice_enabled"):
            if isinstance(data.get(k), bool):
                default[k] = data[k]
        if data.get("interval") in (60, 120, 300):
            default["interval"] = data["interval"]
        for k in ("x", "y"):
            if isinstance(data.get(k), int):
                default[k] = data[k]
        if data.get('language') in LANGUAGES:
            default['language'] = data['language']
        if data.get('display') in MODES:
            default['display'] = data['display']
        volume = data.get('volume')
        if isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume <= 100:
            default['volume'] = volume
        character = data.get('character')
        if isinstance(character, str) and 0 < len(character) <= 80 and all(c.isalnum() or c in '-_' for c in character):
            default['character'] = character
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return default


class Widget:
    def __init__(self, root, preview=False, data_dir=None, language=None):
        self.root, self.preview = root, preview
        standard_data = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'LumiStage' / 'SenaUsageWidget'
        self.data_dir = data_dir or (standard_data if getattr(sys, 'frozen', False) else BASE / "data")
        self.history_path = self.data_dir / "history.json"
        self.prefs_path = self.data_dir / "preferences.json"
        self.prefs = read_preferences(self.prefs_path)
        if language in LANGUAGES:
            self.prefs['language'] = language
        self.history = [] if preview else load_history(self.history_path)
        self.snapshot = self.history[-1] if self.history else None
        self.verified_this_run = False
        self.error = ""
        self.storage_error = ""
        self.busy = False
        self.client = None
        self.results = queue.Queue()
        self.next_poll = 0
        self.last_attempt = 0
        self.failures = 0
        self.panel = None
        self.toast = None
        self.notified = set()
        self.closed = False
        self.success_until = 0
        self.frame_index = 0
        self.drag = None
        self.transitions = TransitionTracker()
        self.voices = VoicePlayer()
        self.voice_notice = ''
        self.pack_warning = ''
        self.packs = discover_packs(BASE, RESOURCES)
        root.title(self.t('title') + (' · PREVIEW v1.1' if preview else ''))
        root.configure(bg=KEY)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED, lightcolor=PANEL, darkcolor=PANEL)
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT,
                        arrowcolor=MUTED, bordercolor=PANEL)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
        root.overrideredirect(True)
        root.attributes("-topmost", self.prefs["topmost"])
        if os.name == "nt":
            root.wm_attributes("-transparentcolor", KEY)
        self.width, self.height = 360, 264
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.areas = work_areas(root)
        x, y = self.prefs.get("x", sw - 330), self.prefs.get("y", sh - 340)
        area = nearest_area(self.areas, x + self.width // 2, y + self.height // 2)
        x, y = clamp(area, x, y, self.width, self.height)
        root.geometry(geometry(x, y, self.width, self.height))
        self.canvas = tk.Canvas(root, width=self.width, height=self.height, bg=KEY,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.load_character()
        self.canvas.bind("<ButtonPress-1>", self.drag_start)
        self.canvas.bind("<B1-Motion>", self.drag_move)
        self.canvas.bind("<ButtonRelease-1>", self.drag_end)
        self.canvas.bind("<Button-3>", self.context_menu)
        root.bind("<Escape>", lambda e: self.hide_details())
        root.protocol("WM_DELETE_WINDOW", self.close)
        if preview:
            self.snapshot = {"at": time.time(), "account": "preview", "plan": "plus",
                             "windows": [{"slot": "primary", "remaining": 80, "minutes": 300,
                                          "reset": time.time() + 2 * 3600},
                                         {"slot": "secondary", "remaining": 60, "minutes": 10080,
                                          "reset": time.time() + 4 * 86400}],
                             "coupons": 2, "coupon_expiry": time.time() + 10 * 86400,
                             "credits": {"balance": "1234.56", "unlimited": False}}
            self.verified_this_run = True
            self.prefs['display'] = 'both'
            self.prefs['voice_enabled'] = True
            self.prefs['animation'] = True
        self.tick()
        self.animate()

    @property
    def language(self):
        return self.prefs.get('language', 'ko')

    def t(self, key, **values):
        return tr(self.language, key, **values)

    def font(self, size=10, weight='normal'):
        family = {'en': 'Segoe UI', 'ko': '맑은 고딕', 'ja': 'Yu Gothic UI', 'zh': 'Microsoft YaHei UI'}[self.language]
        return (family, size, weight)

    def load_character(self):
        self.pack = next((p for p in self.packs if p['id'] == self.prefs['character']), self.packs[0])
        try:
            images = {}
            for pose in ('idle_alt', 'full', 'low', 'empty', 'frame_1', 'frame_2', 'frame_3'):
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(image_path(self.pack, pose)) as im:
                        if im.format != 'PNG' or im.width > 4096 or im.height > 4096:
                            raise ValueError('Invalid PNG')
                        images[pose] = ImageTk.PhotoImage(im.convert('RGBA').resize((196, 196), Image.Resampling.LANCZOS))
            self.images = images
            self.prefs['character'] = self.pack['id']
        except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            if self.pack['id'] == 'sena':
                raise
            self.prefs['character'] = 'sena'
            self.pack_warning = self.t('pack_error')
            self.load_character()

    def is_fresh(self):
        if not self.snapshot or not self.verified_this_run or self.error:
            return False
        now = time.time()
        if not 0 <= now - self.snapshot["at"] <= max(360, self.prefs["interval"] * 2 + 45):
            return False
        return all(not w.get("reset") or now < w["reset"] for w in self.snapshot["windows"])

    def draw(self):
        c = self.canvas
        c.delete("all")
        fresh = self.is_fresh()
        windows = display_windows(self.snapshot, self.prefs.get('display', 'auto'))
        state = quota_state(windows, fresh)
        pose, offset = character_motion(state, self.prefs['animation'], getattr(self, 'frame_index', 0),
                                        time.time() < getattr(self, 'success_until', 0))
        # Older test/asset maps use the previous fail/success names.
        fallback = 'fail' if state in ('low', 'empty') else 'success' if state == 'full' else 'idle_alt'
        image = self.images.get(pose, self.images.get(fallback, self.images['idle_alt']))
        c.create_image(108, 156 + offset, image=image)
        color = '#ffaeaa' if state == 'empty' else '#ffc89f' if state == 'low' else MUTED if state == 'unknown' else ACCENT
        points = [172, 7, 337, 7, 351, 7, 351, 20, 351, 137, 351, 150,
                  337, 150, 184, 150, 166, 166, 171, 150, 158, 150, 158, 137, 158, 20, 158, 7]
        c.create_polygon(points, smooth=True, fill=BG, outline="#485576", width=1)
        if len(windows) > 1:
            for index, window in enumerate(windows):
                text = f"{quota_name(window, self.language)}  {pct(window['remaining'])}%"
                c.create_text(253, 30 + index * 29, text=text, fill=color, font=self.font(13, 'bold'))
        else:
            window = windows[0] if windows else None
            title = self.t('left_title', window=quota_name(window, self.language)) if fresh and window else self.t('checking' if self.busy else 'last_value')
            if not window:
                title = self.t('not_provided') if self.snapshot else self.t('checking' if self.busy else 'query_needed')
            c.create_text(253, 25, text=title, fill=MUTED, font=self.font(9))
            c.create_text(253, 55, text=pct(window['remaining']) + '%' if window else '—', fill=color, font=self.font(23, 'bold'))
        amount = credit_amount(self.snapshot)
        credit_title = self.t('credits' if fresh else 'previous_credits')
        if amount == "미제공":
            credit_title = self.t('credits_checking' if self.busy else 'credits_missing')
        c.create_text(253, 87, text=credit_title, fill=MUTED, font=self.font(8))
        display_amount = self.t('unlimited') if amount == '무제한' else '—' if amount == '미제공' else amount
        c.create_text(253, 108, text=display_amount, fill=ACCENT if fresh else MUTED,
                      font=self.font(max(6, min(11, 160 // max(1, len(display_amount)))), 'bold'))
        c.create_text(250, 135, text=self.t(state), fill=color, font=self.font(8))
        c.create_text(339, 141, text="▾", fill=MUTED, font=self.font(8))
        if self.preview:
            c.create_text(180, 252, text=self.t('preview'), fill='#f1c77d', font=self.font(8))

    def animate(self):
        if self.closed:
            return
        if self.prefs["animation"]:
            self.frame_index = (self.frame_index + 1) % 3
            self.draw()
        self.root.after(animation_interval(self.pack), self.animate)

    def drag_start(self, event):
        self.areas = work_areas(self.root)
        self.drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y(), False)

    def drag_move(self, event):
        if not self.drag:
            return
        sx, sy, x, y, moved = self.drag
        dx, dy = event.x_root - sx, event.y_root - sy
        if abs(dx) + abs(dy) > 5:
            self.drag = (sx, sy, x, y, True)
            nx, ny = clamp(virtual_bounds(self.areas), x + dx, y + dy, self.width, self.height)
            self.root.geometry(geometry(nx, ny))

    def drag_end(self, event):
        if self.drag and not self.drag[-1]:
            self.toggle_details()
        elif self.drag:
            # Avoid leaving the pet in a gap between staggered monitors.
            area = self.current_area()
            x, y = clamp(area, self.root.winfo_x(), self.root.winfo_y(), self.width, self.height)
            self.root.geometry(geometry(x, y))
            if self.panel and self.panel.winfo_exists():
                px, py = self.panel_position(area, self.panel.winfo_width(), self.panel.winfo_height())
                self.panel.geometry(geometry(px, py))
        self.drag = None
        self.store_preferences()

    def current_area(self):
        self.areas = work_areas(self.root)
        return nearest_area(self.areas, self.root.winfo_x() + self.width // 2,
                            self.root.winfo_y() + self.height // 2)

    def panel_position(self, area, width, height):
        x = self.root.winfo_x() - width - 10
        if x < area[0]:
            x = self.root.winfo_x() + self.width + 10
        return clamp(area, x, self.root.winfo_y() - 300, width, height)

    def context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=False, bg=BG, fg=TEXT)
        menu.add_command(label=self.t('menu_details'), command=self.toggle_details)
        menu.add_command(label=self.t('refresh'), command=lambda: self.refresh(manual=True))
        menu.add_separator()
        menu.add_command(label=self.t('quit'), command=self.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def hide_details(self):
        if self.panel and self.panel.winfo_exists():
            self.panel.destroy()
        self.panel = None

    def toggle_details(self):
        if self.panel and self.panel.winfo_exists():
            self.hide_details()
            return
        p = self.panel = tk.Toplevel(self.root, bg=BG)
        p.title(self.t('details'))
        p.resizable(False, False)
        p.attributes("-topmost", self.prefs["topmost"])
        area = self.current_area()
        x, y = self.panel_position(area, 440, 660)
        p.geometry(geometry(x, y, 440, 660))
        p.protocol("WM_DELETE_WINDOW", self.hide_details)
        p.bind("<Escape>", lambda e: self.hide_details())
        # Scroll on small/high-DPI displays rather than clipping settings and Quit.
        viewport = tk.Canvas(p, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(p, orient="vertical", command=viewport.yview)
        scroll.pack(side="right", fill="y")
        viewport.pack(side="left", fill="both", expand=True)
        viewport.configure(yscrollcommand=scroll.set)
        body = tk.Frame(viewport, bg=BG)
        body_id = viewport.create_window((0, 0), window=body, anchor="nw")
        viewport.bind("<Configure>", lambda e: viewport.itemconfigure(body_id, width=e.width))
        body.bind("<Configure>", lambda e: viewport.configure(scrollregion=viewport.bbox("all")))
        p.bind("<MouseWheel>", lambda e: viewport.yview_scroll(-int(e.delta / 120), "units"))
        p.bind("<Next>", lambda e: viewport.yview_scroll(1, "pages"))
        p.bind("<Prior>", lambda e: viewport.yview_scroll(-1, "pages"))
        tk.Label(body, text="SENA  /  USAGE", bg=BG, fg=ACCENT,
                 font=("맑은 고딕", 10, "bold")).pack(anchor="w", padx=22, pady=(18, 0))
        self.big = tk.Label(body, bg=BG, fg=TEXT, font=self.font(24, 'bold'))
        self.big.pack(anchor="w", padx=20)
        self.credit_label = tk.Label(body, bg=BG, fg=ACCENT, justify="left",
                                     wraplength=370, font=self.font(12, 'bold'))
        self.credit_label.pack(anchor="w", padx=22, pady=(0, 8))
        self.status_label = tk.Label(body, bg=BG, fg=MUTED, wraplength=370, justify="left", font=self.font(9))
        self.status_label.pack(anchor="w", padx=22, pady=(0, 10))
        self.info = tk.Label(body, bg=PANEL, fg=TEXT, justify="left", anchor="nw", padx=14, pady=12,
                             wraplength=348, font=self.font(10))
        self.info.pack(fill="x", padx=20)
        self.trend = tk.Label(body, bg=BG, fg=MUTED, justify="left", anchor="w", wraplength=370,
                              font=self.font(9))
        self.trend.pack(fill="x", padx=22, pady=12)
        options = tk.Frame(body, bg=BG)
        options.pack(fill="x", padx=18)
        self.vars = {}
        for key in ('topmost', 'alerts', 'animation', 'voice_enabled'):
            v = tk.BooleanVar(value=self.prefs[key])
            self.vars[key] = v
            tk.Checkbutton(options, text=self.t(key), variable=v, command=self.options_changed,
                           bg=BG, fg=TEXT, selectcolor=PANEL, activebackground=BG,
                           activeforeground=TEXT, font=self.font(9)).pack(anchor="w")
        self.language_box = self.option_box(options, 'language', list(LANGUAGES.values()), list(LANGUAGES).index(self.language), self.language_changed)
        self.display_box = self.option_box(options, 'display', [self.t(mode) for mode in MODES], MODES.index(self.prefs['display']), self.options_changed)
        self.character_box = self.option_box(options, 'character', [pack['name'] for pack in self.packs], next(i for i, pack in enumerate(self.packs) if pack['id'] == self.prefs['character']), self.character_changed)
        volume_row = tk.Frame(options, bg=BG)
        volume_row.pack(fill='x', pady=4)
        tk.Label(volume_row, text=self.t('volume'), bg=BG, fg=MUTED, font=self.font(9)).pack(side='left')
        self.volume = tk.IntVar(value=self.prefs['volume'])
        tk.Scale(volume_row, from_=0, to=100, orient='horizontal', variable=self.volume,
                 command=lambda value: self.volume_changed(value), bg=BG, fg=TEXT, highlightthickness=0,
                 troughcolor=PANEL, length=145).pack(side='left', padx=8)
        self.voice_test_box = self.option_box(options, 'voice_sample', [self.t(s) for s in ('full', 'low', 'empty')], 0, lambda: None)
        self.voice_test_button = tk.Button(volume_row, text=self.t('test_voice'), command=self.test_voice,
                                           state='normal',
                                           bg=PANEL, fg=TEXT, relief='flat', font=self.font(8))
        self.voice_test_button.pack(side='right')
        tk.Label(options, text=self.t('voice_test_note'), bg=BG, fg=MUTED,
                 wraplength=370, justify='left', font=self.font(8)).pack(anchor='w', pady=4)
        tk.Button(options, text=self.t('stop_voice'), command=self.stop_voice,
                  bg=PANEL, fg=TEXT, relief='flat', font=self.font(8)).pack(anchor='e')
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", padx=22, pady=10)
        tk.Label(row, text=self.t('interval'), bg=BG, fg=MUTED, font=self.font(9)).pack(side="left")
        self.interval = ttk.Combobox(row, values=[self.t('minutes', n=n) for n in (1, 2, 5)], width=8, state="readonly")
        self.interval.current((60, 120, 300).index(self.prefs['interval']))
        self.interval.pack(side="left", padx=10)
        self.interval.bind("<<ComboboxSelected>>", lambda e: self.options_changed())
        tk.Button(row, text=self.t('refresh'), command=lambda: self.refresh(manual=True), bg=PANEL, fg=TEXT,
                  relief="flat", padx=10).pack(side="right")
        tk.Label(body, text=self.t('readonly'), bg=BG, fg=MUTED, justify='left', wraplength=370, font=self.font(8)).pack(anchor='w', padx=22, pady=(2, 10))
        if self.preview:
            self.demo_box = self.option_box(body, 'preview', ['normal', 'full', 'low', 'empty', 'unknown'], 0, self.demo_changed)
        tk.Button(body, text=self.t('quit'), command=self.close, bg=BG, fg=MUTED, relief='flat').pack(anchor='e', padx=18, pady=(0, 12))
        self.update_details()
        p.update_idletasks()
        height = min(max(660, body.winfo_reqheight() + 8), max(200, area[3] - area[1] - 60))
        x, y = self.panel_position(area, 440, height + 40)
        p.geometry(geometry(x, y, 440, height))

    def option_box(self, parent, label, values, index, callback):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill='x', padx=4, pady=4)
        tk.Label(row, text=self.t(label), bg=BG, fg=MUTED, font=self.font(9)).pack(side='left')
        box = ttk.Combobox(row, values=values, width=24, state='readonly')
        box.current(index)
        box.pack(side='right', padx=4)
        box.bind('<<ComboboxSelected>>', lambda event: callback())
        return box

    def language_changed(self):
        self.options_changed()
        self.prefs['language'] = list(LANGUAGES)[self.language_box.current()]
        self.root.title(self.t('title') + (' · PREVIEW v1.1' if self.preview else ''))
        self.store_preferences()
        self.hide_details()
        self.toggle_details()
        self.draw()

    def character_changed(self):
        self.options_changed()
        self.prefs['character'] = self.packs[self.character_box.current()]['id']
        self.voices.stop()
        self.voices.error = ''
        self.voice_notice = ''
        self.pack_warning = ''
        self.load_character()
        self.store_preferences()
        self.draw()
        self.update_details()

    def volume_changed(self, value):
        self.prefs['volume'] = int(float(value))
        if not self.prefs['volume']:
            self.voices.stop()
            self.voice_notice = 'voice_zero_volume'
        self.store_preferences()
        self.update_details()

    def demo_changed(self):
        mode = self.demo_box.get()
        self.verified_this_run = mode != 'unknown'
        self.snapshot['at'] = time.time()
        self.success_until = time.time() + 15 if mode == 'full' else 0
        for index, window in enumerate(self.snapshot['windows']):
            window['remaining'] = {'full': 100, 'low': 20, 'empty': 0}.get(mode, (80, 60)[index])
        self.draw()
        self.update_details()

    def voice_available(self):
        try:
            return all(voice_path(self.pack, 'ja', event).is_file() for event in ('full', 'low', 'empty'))
        except (OSError, ValueError):
            return False

    def play_voice(self, event):
        if self.prefs['volume'] <= 0:
            self.voice_notice = 'voice_zero_volume'
            return False
        try:
            # UI language never changes the Japanese-only recording selection.
            path = voice_path(self.pack, 'ja', event)
            accepted = self.voices.play(path, self.prefs['volume'])
            self.voice_notice = 'voice_started' if accepted else 'voice_busy'
            return accepted
        except (OSError, ValueError):
            self.voices.error = 'voice_unavailable'
            return False

    def test_voice(self):
        # An explicit audition is independent of the automatic-alert preference.
        index = self.voice_test_box.current()
        event = ('full', 'low', 'empty')[index if index in (0, 1, 2) else 0]
        if self.preview:
            self.demo_box.current(['normal', 'full', 'low', 'empty', 'unknown'].index(event))
            self.demo_changed()
        self.play_voice(event)
        self.update_details()

    def stop_voice(self):
        self.voices.stop()
        self.voice_notice = 'voice_stopped'
        self.update_details()

    def options_changed(self):
        for k, v in self.vars.items():
            self.prefs[k] = v.get()
        self.prefs['interval'] = (60, 120, 300)[self.interval.current()]
        self.prefs['display'] = MODES[self.display_box.current()]
        self.prefs['volume'] = self.volume.get()
        if not self.prefs['voice_enabled']:
            self.voices.stop()
        self.voice_test_button.configure(state='normal')
        self.root.attributes("-topmost", self.prefs["topmost"])
        self.panel.attributes("-topmost", self.prefs["topmost"])
        self.next_poll = max(time.time(), self.last_attempt + self.prefs["interval"])
        self.store_preferences()
        self.draw()
        self.update_details()

    def store_preferences(self):
        if self.preview:
            return
        self.prefs.update(x=self.root.winfo_x(), y=self.root.winfo_y())
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.prefs_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.prefs), encoding="utf-8")
            temp.replace(self.prefs_path)
        except OSError:
            self.storage_error = "위치·설정 저장 실패"

    def update_details(self):
        if not self.panel or not self.panel.winfo_exists():
            return
        s, now = self.snapshot, time.time()
        fresh = self.is_fresh()
        windows = display_windows(s, self.prefs.get('display', 'auto'))
        self.big.configure(text=self.t('remaining', n=pct(min(w['remaining'] for w in windows))) if windows else self.t('not_provided'))
        amount = credit_amount(s)
        credit_line = self.t('credits_missing') if amount == '미제공' else self.t('credit_line', n=self.t('unlimited') if amount == '무제한' else amount)
        if not fresh and amount != "미제공":
            credit_line += ' · ' + self.t('last_value')
        self.credit_label.configure(text=credit_line, fg=ACCENT if fresh else MUTED)
        status = self.t('ok' if fresh else 'stale')
        if self.preview:
            status = self.t('preview')
        elif self.busy:
            status += ' · ' + self.t('checking')
        if s:
            status += '\n' + self.t('checked', time=clock_text(s['at']))
        if self.error:
            status += '\n' + self.t('query_error')
        if self.storage_error:
            status += '\n' + self.t('storage_error')
        if getattr(self, 'pack_warning', ''):
            status += '\n' + self.t('pack_error')
        if hasattr(self, 'pack'):
            voice_key = self.voices.error or ('voice_ready' if self.voice_available() else 'voice_missing')
            status += '\n' + self.t(voice_key)
            notice = getattr(self, 'voice_notice', '')
            if notice in ('voice_started', 'voice_busy') and not self.voices.busy.locked():
                notice = 'voice_finished' if not self.voices.error else ''
            if notice:
                status += '\n' + self.t(notice)
        self.status_label.configure(text=status)
        lines = []
        if s:
            for w in s["windows"]:
                reset = w.get('reset')
                duration = max(0, int(((reset or now) - now) // 60))
                timer = self.t('time_missing') if not reset else self.t('reset_passed') if reset <= now else self.t('countdown', d=duration // 1440, h=duration % 1440 // 60, m=duration % 60)
                lines.extend([quota_name(w, self.language) + '  ' + self.t('remaining', n=pct(w['remaining'])),
                              self.t('reset', time=clock_text(reset) if reset else self.t('not_provided')), timer, ''])
            count = s.get("coupons")
            lines.append(self.t('coupons', n=count if count is not None else self.t('not_provided')))
            if s.get("coupon_expiry"):
                lines.append(self.t('expiry', time=clock_text(s['coupon_expiry'])))
        self.info.configure(text='\n'.join(lines) if lines else self.t('waiting'))
        trends = []
        if s:
            midnight = datetime.fromtimestamp(s["at"]).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            for label, since in ((self.t('recent_hour'), s['at'] - 3600), (self.t('today'), midnight)):
                change = change_since(self.history, s, since)
                if not change:
                    text = self.t('collecting')
                elif change["reset"]:
                    text = self.t('comparison_hold')
                else:
                    start = datetime.fromtimestamp(change["start"]).strftime("%H:%M")
                    text = self.t('drop', time=start, n=pct(change['drop']))
                    if change["gap"]:
                        text += self.t('gap')
                trends.append(label + "  " + text)
        trends.append(self.t('no_estimate'))
        self.trend.configure(text="\n".join(trends))

    def refresh(self, manual=False):
        if self.closed or self.preview or self.busy or time.time() - self.last_attempt < 15:
            return
        self.busy = True
        self.last_attempt = time.time()
        self.client = QuotaClient()
        client = self.client

        def worker():
            try:
                self.results.put((client.fetch(), None))
            except UsageError as e:
                self.results.put((None, str(e)))
            except Exception:
                self.results.put((None, "조회 처리 오류 · 다음 갱신 때 다시 시도합니다."))

        threading.Thread(target=worker, daemon=True).start()
        self.draw()
        self.update_details()

    def notify(self, message):
        if not self.prefs["alerts"]:
            return
        if self.toast and self.toast.winfo_exists():
            self.toast.destroy()
        toast = self.toast = tk.Toplevel(self.root, bg=BG)
        toast.title(self.t('alert_title'))
        toast.attributes("-topmost", True)
        toast.resizable(False, False)
        x, y = clamp(self.current_area(), self.root.winfo_x(), self.root.winfo_y() - 140, 340, 140)
        toast.geometry(geometry(x, y, 330, 100))
        tk.Label(toast, text=message, bg=BG, fg=TEXT, wraplength=305,
                 font=self.font(10), padx=12, pady=12).pack(fill="both", expand=True)
        toast.after(12000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def check_alerts(self, previous):
        if not self.is_fresh():
            self.transitions.update(None)
            return
        s, now = self.snapshot, time.time()
        w = headline(s)
        # Restore the original visual recovery reaction; voice thresholds stay separate.
        if previous and s['account'] == previous['account']:
            old = next((item for item in previous['windows'] if item['slot'] == w['slot']), None)
            if old and w['remaining'] > old['remaining']:
                self.success_until = now + 15
        event = self.transitions.update(s)
        if event:
            self.notify(self.t('full_alert') if event == 'full' else self.t('low_alert', window=quota_name(w, self.language), n=pct(w['remaining'])))
            if self.prefs.get('voice_enabled') and not self.preview:
                self.play_voice(event)
        expiry = s.get("coupon_expiry")
        key = (s["account"], "coupon", expiry)
        if expiry and 0 < expiry - now <= 3 * 86400 and key not in self.notified:
            self.notified.add(key)
            self.notify(self.t('coupon_alert', time=clock_text(expiry)))

    def tick(self):
        if self.closed:
            return
        try:
            snapshot, error = self.results.get_nowait()
            self.busy = False
            self.error = error or ""
            if snapshot:
                previous = self.snapshot
                self.snapshot = snapshot
                self.verified_this_run = True
                self.failures = 0
                self.history.append(snapshot)
                try:
                    save_history(self.history_path, self.history)
                    self.storage_error = ""
                except OSError:
                    self.storage_error = "관측 기록 저장 실패 · 현재 조회값은 정상입니다."
                self.check_alerts(previous)
            else:
                self.failures = min(4, self.failures + 1)
                self.transitions.update(None)
            self.next_poll = time.time() + min(900, self.prefs["interval"] * (2 ** self.failures))
        except queue.Empty:
            pass
        if not self.preview and not self.busy and time.time() >= self.next_poll:
            self.refresh()
        self.draw()
        self.update_details()
        self.root.after(1000, self.tick)

    def close(self):
        self.closed = True
        self.voices.stop()
        self.store_preferences()
        if self.client:
            self.client.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--qa", action="store_true", help="Capture this widget only; close automatically.")
    parser.add_argument('--language', choices=tuple(LANGUAGES), help='Override UI language; existing settings are otherwise preserved.')
    parser.add_argument('--details', action='store_true', help='Open the details panel on startup.')
    parser.add_argument('--preview-quit-after', type=int, default=0, help='Preview only: close after 1-600 seconds.')
    parser.add_argument('--bundle-check', type=Path, help='Write a local asset diagnostic without GUI/account access.')
    parser.add_argument('--check-audio', action='store_true', help='With --bundle-check only: test local playback at volume 10.')
    args = parser.parse_args()
    if args.bundle_check:
        from bundle_check import check_bundle
        raise SystemExit(check_bundle(BASE, RESOURCES, args.bundle_check, audio=args.check_audio))
    if args.check_audio:
        parser.error('--check-audio requires --bundle-check')
    mutex = None
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
        if not args.preview and not args.qa:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            mutex = kernel.CreateMutexW(None, False, "Local\\LumiStageSenaQuotaWidgetV1")
            if ctypes.get_last_error() == 183:
                return
    root = tk.Tk()
    app = Widget(root, preview=args.preview, data_dir=(BASE / "qa-data") if args.qa else None, language=args.language)
    if args.details:
        root.after(100, app.toggle_details)
    if args.preview and 0 < args.preview_quit_after <= 600:
        root.after(args.preview_quit_after * 1000, app.close)
    if args.qa:
        from PIL import ImageGrab

        def capture():
            root.update()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + 300, y + 246)).save(BASE / "widget-preview.png")
            app.toggle_details()
            root.after(1000, capture_panel)

        def capture_panel():
            try:
                if not app.panel or not app.panel.winfo_exists():
                    app.toggle_details()
                p = app.panel
                p.update_idletasks()
                x, y = p.winfo_rootx(), p.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + p.winfo_width(), y + p.winfo_height())).save(BASE / "details-preview.png")
            finally:
                app.close()

        root.after(8000 if not args.preview else 1000, capture)
        root.after(30000, app.close)
    root.mainloop()
    if mutex:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()

# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Offline desktop interface for choosing an image folder."""
from __future__ import annotations

import os
import sys
import subprocess
import tempfile
import traceback
from pathlib import Path
from app_paths import APP_ROOT, FUNCTION_ROOT, FULL_NAME
import workspace_state
from channel_widgets import ChannelCheck

# All Windows entry points share the native splash and dependency checks.
if __name__ == '__main__' and os.name == 'nt' and '--app-start' not in sys.argv:
    executable = APP_ROOT/'BRAIN.exe'
    if executable.is_file():
        subprocess.Popen([str(executable)],cwd=executable.parent)
        raise SystemExit(0)

_startup_ready = None
if __name__ == '__main__' and '--startup-ready' in sys.argv:
    _startup_ready = Path(sys.argv[sys.argv.index('--startup-ready')+1]).resolve()
    if _startup_ready.parent != Path(tempfile.gettempdir()).resolve() or not _startup_ready.name.startswith('BRAIN-ready-'):
        raise ValueError('Invalid startup notification path')
    def _startup_exception(kind,error,tb):
        if not _startup_ready.exists():
            _startup_ready.with_name(_startup_ready.name+'.error').write_text(''.join(traceback.format_exception(kind,error,tb)),encoding='utf-8')
        sys.__excepthook__(kind,error,tb)
    sys.excepthook = _startup_exception

import re
import copy
import math
import logging
import queue
import threading
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError
from alignment import Cancelled, align_files, rigid, render_result
from preview_cache import PreviewCache, MAX_SIDE
from color_picker import ColorPicker, ChannelColors, COLORS
from colors import color_hex
from export_data import export_data,validate_name,source_signatures,validate_sources
from volume_panel import VolumePanel
from pixel_calibration import detect_calibration
from stack_panel import StackPanel
from analysis_tiff import AnalysisTiffView
from image_sources import is_tiff
from credit_panel import CreditPanel
from appearance import Appearance
from ui_controls import RepeatButton,center_dialog
from raw_preview import RawPreviewWorkflow
from scale_bars import ScaleBarControls,horizontal_bar,xy_spacing
from desktop_actions import FolderDrop,ImageCopyMenus

SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg','.tif','.tiff'}
DISPLAY_MAX_SIDE = MAX_SIDE
ALIGNMENT_METHODS = {
    'translation': ('平行移動のみ', '画像を上下・左右に移動して位置を合わせます。'),
    'rigid': ('平行移動＋回転（剛体）', '画像を上下・左右に移動し、回転を加えて位置を合わせます。'),
}


@dataclass(frozen=True)
class ImageFile:
    path: Path
    size: int


def natural_key(value: str):
    return [(1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r'(\d+)', value)]


def scan_folder(folder: Path) -> tuple[ImageFile, ...]:
    """List supported files directly inside the chosen folder."""
    folder = folder.resolve(strict=True)
    if not folder.is_dir():
        raise NotADirectoryError(str(folder))
    paths = sorted((p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS),
                   key=lambda p: natural_key(p.name))
    return tuple(ImageFile(path=p, size=p.stat().st_size) for p in paths)


class BRAIN(RawPreviewWorkflow,tk.Tk):
    def __init__(self, log_directory: Path | None = None):
        if os.name == 'nt':
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('BRAIN.Desktop')
        super().__init__()
        icon = FUNCTION_ROOT/'BRAIN.ico'
        if icon.is_file():
            if os.name == 'nt':
                self.iconbitmap(default=str(icon))
                self.iconbitmap(str(icon))
        self.title('BRAIN')
        self.geometry('1060x780')
        self.minsize(840, 620)
        self.state('zoomed')
        self._log_handler = None
        self._logger = logging.getLogger(f'BRAIN.{id(self)}')
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._resume_plan=None
        self._loaded_resume=None
        self._resume_ignored_paths=frozenset()
        self.folder: Path | None = None
        self.images: tuple[ImageFile, ...] = ()
        self._excluded_paths = set()
        self._analysis_slice_indices = ()
        self._analysis_total_slices = 0
        self._analysis_sequence = ()
        self._analysis_source_sequence=()
        self._analysis_data_indices=()
        self._analysis_omitted_paths=frozenset()
        self._analysis_ignore_excluded=False
        self.ignore_excluded=tk.BooleanVar(value=False)
        self._pending_ignore_excluded=False
        self._preview_slot = 0
        self._preview_is_empty = False
        self._file_rows = {}
        self._file_drag = None
        self.analysis_files: tuple[ImageFile, ...] = ()
        self.preview_index = 0
        self._tiff_pages = 1
        self._input_has_tiff = False
        self.tiff_view = AnalysisTiffView(self)
        self._preview_original = None
        self._preview_display = None
        self._preview_photo = None
        self._preview_job = None
        self._preview_error = None
        self._aligned_original = None
        self._aligned_display = None
        self._aligned_photo = None
        self.alignment_matrices = []
        self.alignment_records = []
        self.alignment_canvas = None
        self._automatic_matrices = []
        self._analysis_settings = None
        self._running_settings = None
        self._manual_events = []
        self._saving_busy = False
        self._saving_cancel = threading.Event()
        self._saving_events = queue.Queue()
        self._saving_dialog = None
        self._saving_poll_job = None
        self._close_after_saving = False
        self._raw_original = None
        self.init_raw_preview()
        self._raw_aligned = None
        self._view_states = {name: {'zoom':1.,'center':(.5,.5)} for name in ('source','result')}
        self._pan_start = None
        self._alignment_dialog = None
        self._alignment_elapsed_job = None
        self._result_method = None
        self._running_method = None
        self._manual_history = []
        self._manual_modified = set()
        self._alignment_busy = False
        self._alignment_cancel = threading.Event()
        self._alignment_events = queue.Queue()
        self._alignment_poll_job = None
        self._preview_cache = None
        self._loading_busy = False
        self._loading_cancel = threading.Event()
        self._loading_events = queue.Queue()
        self._loading_poll_job = None
        self._loading_dialog = None
        self._close_after_loading = False
        self.preview_number = tk.IntVar(value=1)
        self.preview_count = tk.StringVar(value='/ 0')
        self.preview_filename = tk.StringVar(value='画像未読み込み')
        self.folder_text = tk.StringVar(value='フォルダ未選択')
        self.count_text = tk.StringVar(value='画像 0 枚')
        self.status_text = tk.StringVar(value='PNG / JPEG / TIFF画像が2枚以上入ったフォルダを選択してください。')
        self.alignment_method = tk.StringVar(value='rigid')
        self.alignment_color = tk.StringVar(value='Gray')
        self.alignment_channel = tk.StringVar(value='1')
        self.alignment_resolution = tk.StringVar(value='reduced')
        self.alignment_max_side = tk.StringVar(value='1024')
        self.alignment_scope = tk.StringVar(value='Reference')
        self.alignment_status = tk.StringVar(value='対象と色を選択してください。')
        self.result_info = tk.StringVar(value='アライン未実行')
        self.raw_display = tk.BooleanVar(value=False)
        self.save_directory = tk.StringVar(value='')
        self.save_name = tk.StringVar(value='BRAIN_result')
        self.save_format = tk.StringVar(value='PNG')
        self.save_merge_mode = tk.StringVar(value='all')
        self.save_channel_vars = []
        self.save_channel_checks = []
        self.save_create_folder = tk.BooleanVar(value=True)
        self.save_images = tk.BooleanVar(value=True)
        self.save_movements = tk.BooleanVar(value=True)
        self.save_status = tk.StringVar(value='アライン実行後に保存できます。params.jsonは常に保存します。')
        self.manual_scope = tk.StringVar(value='Single')
        self.manual_move_step = tk.DoubleVar(value=1.)
        self.manual_angle_step = tk.DoubleVar(value=.1)
        self.manual_message = tk.StringVar(value='アライン実行後に操作できます。')
        self._last_alignment_method = 'rigid'
        self.alignment_description = tk.StringVar(value=ALIGNMENT_METHODS['rigid'][1])
        self.alignment_data_text = tk.StringVar(value='解析対象は未確定です。')
        self.appearance = Appearance(self)
        self._folder_drops=[]
        self._build()
        self.image_copy_menus=ImageCopyMenus(self)
        self.appearance.apply()
        self.appearance.watch()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.report_callback_exception = self.callback_error
        self.bind('<Left>', lambda event: self.preview_key(event, -1))
        self.bind('<Right>', lambda event: self.preview_key(event, 1))
        try:
            log_dir = log_directory if log_directory is not None else APP_ROOT / 'logs'
            log_dir.mkdir(exist_ok=True)
            self._log_handler = RotatingFileHandler(log_dir / 'BRAIN.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
            self._log_handler.setFormatter(logging.Formatter('%(message)s'))
            self._logger.addHandler(self._log_handler)
        except OSError as exc:
            self.add_log(f'ログファイルを保存できません。画面表示のみで記録します: {exc}')
        self.add_log('BRAINを起動しました。画像フォルダを選択してください。')

    def _build(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TButton', padding=(12, 7), font=('Yu Gothic UI', 10))
        style.configure('TLabel', font=('Yu Gothic UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 17, 'bold'))
        style.configure('Go.TButton', background='#216675', foreground='white')
        style.configure('MergedOn.TButton',background='#216675',foreground='white')
        style.map('Go.TButton', background=[('disabled', '#e0e4e7'), ('active', '#184f5a')],
                  foreground=[('disabled', '#7b8287')])
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1, minsize=360)

        # Each tab owns its contents; only the data tab contains a log panel.
        self.tabs = ttk.Notebook(self)
        self.tabs.grid(row=0, column=0, sticky='nsew', padx=14, pady=(14, 7))
        data = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(data, text='data')
        data.columnconfigure(0, weight=1)
        data.rowconfigure(3, weight=0)
        data.rowconfigure(5, weight=1)

        header = ttk.Frame(data)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(header, text='BRAIN', style='Title.TLabel').pack(side='left')
        ttk.Label(header,text=FULL_NAME,font=('Segoe UI',10)).pack(side='left',padx=(12,0),anchor='s',pady=(0,3))

        picker = ttk.Frame(data)
        picker.grid(row=1, column=0, sticky='ew')
        picker.columnconfigure(0, weight=1)
        self.data_folder_entry=ttk.Entry(picker,textvariable=self.folder_text,state='readonly')
        self.data_folder_entry.grid(row=0,column=0,sticky='ew',padx=(0,8))
        self.register_folder_drop(self.data_folder_entry,self.select_folder)
        ttk.Button(picker, text='フォルダを選択', command=self.choose_folder).grid(row=0, column=1)

        info = ttk.Frame(data)
        info.grid(row=2, column=0, sticky='ew', pady=(8, 6))
        ttk.Label(info, textvariable=self.count_text).pack(side='left')
        ttk.Label(info, text='PNG / JPG / JPEG / TIFF · 選択フォルダ直下のみ', foreground='#647079').pack(side='right')

        listing = ttk.Frame(data)
        listing.grid(row=3, column=0, sticky='nsew')
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.file_list = ttk.Treeview(listing, columns=('number', 'name', 'size','use'), show='headings', height=6)
        self.file_list.configure(displaycolumns=('use','number','name','size'))
        for column, title, width in [('number', '順序', 60), ('name', 'ファイル名', 580), ('size', 'サイズ', 110),('use','解析に使用',95)]:
            self.file_list.heading(column, text=title)
            self.file_list.column(column, width=width, stretch=column=='name', anchor='w' if column=='name' else 'center')
        self.file_list.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(listing, orient='vertical', command=self.file_list.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.file_list.configure(yscrollcommand=scrollbar.set)
        self.file_list.tag_configure('drop-target',background='#cde7ec')
        self.file_list.bind('<<TreeviewSelect>>',lambda event:self.refresh_order_buttons())
        self.file_list.bind('<Alt-Up>',lambda event:self.reorder_images('up'))
        self.file_list.bind('<Alt-Down>',lambda event:self.reorder_images('down'))
        self.file_list.bind('<ButtonPress-1>',self.begin_file_drag)
        self.file_list.bind('<B1-Motion>',self.move_file_drag)
        self.file_list.bind('<ButtonRelease-1>',self.end_file_drag)
        ordering = ttk.Frame(listing); ordering.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(6,0))
        self.order_buttons = {}
        for action,label in [('first','先頭へ'),('up','上へ'),('down','下へ'),('last','末尾へ'),('reverse','逆順'),('natural','ファイル名順に戻す')]:
            button = ttk.Button(ordering,text=label,command=lambda value=action:self.reorder_images(value),state='disabled')
            button.pack(side='left',padx=(0,6)); self.order_buttons[action] = button
        self.use_buttons = []
        for include,label in [(False,'解析から除外'),(True,'解析に使用')]:
            button=ttk.Button(ordering,text=label,command=lambda value=include:self.use_selection(value),state='disabled')
            button.pack(side='left',padx=(0,6));self.use_buttons.append(button)
        self.blank_add_button=ttk.Button(ordering,text='空追加',command=self.add_blank)
        self.blank_add_button.pack(side='left',padx=(0,6))
        self.blank_remove_button=ttk.Button(ordering,text='空削除',command=self.remove_blanks)
        self.blank_remove_button.pack(side='left')
        ttk.Label(listing,text='画像を選んで順序を変更（複数選択可・ドラッグでも移動）。Go analyzeで解析に反映します。',
                  foreground='#647079').grid(row=2,column=0,columnspan=2,sticky='w',pady=(4,0))
        self.ignore_excluded_check=ttk.Checkbutton(listing,text='除外画像を完全に無視する（番号を詰める・追加した空行は残す）',
            variable=self.ignore_excluded,command=self.change_exclusion_mode)
        self.ignore_excluded_check.grid(row=3,column=0,columnspan=2,sticky='w',pady=(4,0))

        footer = ttk.Frame(data)
        footer.grid(row=4, column=0, sticky='ew', pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, wraplength=690).grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.go_button = ttk.Button(footer, text='Go analyze', style='Go.TButton', state='disabled', command=self.go_analyze)
        self.go_button.grid(row=0, column=1)

        self.analysis_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.analysis_tab, text='align')
        self.analysis_tab.columnconfigure(0, weight=0, minsize=290)
        self.analysis_tab.columnconfigure(1, weight=1)
        self.analysis_tab.rowconfigure(0, weight=1, uniform='analysis_halves')
        self.analysis_tab.rowconfigure(1, weight=1, uniform='analysis_halves')
        self.alignment_panel = ttk.LabelFrame(self.analysis_tab, text='アライン設定', padding=8, width=290)
        self.alignment_panel.grid(row=0, column=0, rowspan=2, sticky='nsew', padx=(0,8))
        self.alignment_panel.grid_propagate(False)
        self.alignment_panel.columnconfigure(0, weight=1)
        self.alignment_panel.rowconfigure(0, weight=1)
        self.settings_canvas = tk.Canvas(self.alignment_panel, highlightthickness=0, width=250, height=1)
        self.settings_canvas.grid(row=0,column=0,sticky='nsew')
        settings_scroll = ttk.Scrollbar(self.alignment_panel,orient='vertical',command=self.settings_canvas.yview)
        settings_scroll.grid(row=0,column=1,sticky='ns')
        self.settings_canvas.configure(yscrollcommand=settings_scroll.set)
        settings = ttk.Frame(self.settings_canvas)
        self.alignment_content = settings
        settings.columnconfigure(0,weight=1)
        settings_window = self.settings_canvas.create_window(0,0,window=settings,anchor='nw')
        settings.bind('<Configure>',lambda e:self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox('all')))
        self.settings_canvas.bind('<Configure>',lambda e:self.settings_canvas.itemconfigure(settings_window,width=e.width))
        self.bind_all('<MouseWheel>',self.scroll_settings,add='+')

        source_info = ttk.LabelFrame(settings, text='データ情報', padding=8)
        source_info.grid(row=0, column=0, sticky='ew', pady=(0,10))
        ttk.Label(source_info, textvariable=self.alignment_data_text, wraplength=240, justify='left').pack(anchor='w')

        method_box = ttk.LabelFrame(settings, text='アライン方法', padding=8)
        method_box.grid(row=1, column=0, sticky='ew')
        self.alignment_buttons = {}
        for value, (label, description) in ALIGNMENT_METHODS.items():
            button = ttk.Radiobutton(method_box, text=label, value=value,
                                     variable=self.alignment_method, command=self.change_alignment_method)
            button.pack(fill='x', anchor='w', pady=6)
            self.alignment_buttons[value] = button
        ttk.Separator(method_box).pack(fill='x', pady=8)
        ttk.Label(method_box, textvariable=self.alignment_description,
                  wraplength=230, justify='left').pack(fill='x', anchor='w', pady=(0,6))

        color_box = self.color_box = ttk.LabelFrame(settings, text='色', padding=8)
        color_box.grid(row=2, column=0, sticky='ew', pady=(10,0))
        self.color_combo = ColorPicker(color_box, self.alignment_color)
        self.color_combo.pack(fill='x')
        self.channel_combo = ttk.Combobox(color_box,textvariable=self.alignment_channel,values=['1'],state='readonly')
        self.channel_colors = ChannelColors(self.channel_combo,self.alignment_channel,
            lambda:{str(i+1):color_hex(state['color'].get())
                    for i,state in enumerate(self.tiff_view.channels)} if hasattr(self,'tiff_view') else {})
        self.channel_combo.bind('<<ComboboxSelected>>',lambda event:self.request_preview_draw())

        scope_box = ttk.LabelFrame(settings, text='対象画像', padding=8)
        scope_box.grid(row=3, column=0, sticky='ew', pady=(10,0))
        self.scope_buttons = {}
        for value,label in [('Reference','現在の画像を基準'),('Global','全体最適化')]:
            button = ttk.Radiobutton(scope_box, text=label, value=value, variable=self.alignment_scope)
            button.pack(anchor='w', pady=4)
            self.scope_buttons[value] = button
        ttk.Label(scope_box, text='現在の画像を基準:\n表示中の1枚を固定して全画像を整列\n全体最適化:\n特定の1枚を固定せず全画像を調整',
                  wraplength=230, justify='left').pack(anchor='w', pady=(6,0))
        resolution_box = ttk.LabelFrame(settings,text='計算解像度',padding=8)
        resolution_box.grid(row=4,column=0,sticky='ew',pady=(10,0))
        self.resolution_buttons = {}
        for value,label in [('original','元解像度でアライン'),('reduced','縮小解像度でアライン')]:
            button = ttk.Radiobutton(resolution_box,text=label,value=value,variable=self.alignment_resolution,command=self.refresh_resolution_controls)
            button.pack(anchor='w',pady=4)
            self.resolution_buttons[value] = button
        size_row = ttk.Frame(resolution_box)
        size_row.pack(fill='x',pady=(4,0))
        ttk.Label(size_row,text='最大辺（px）').pack(side='left')
        self.resolution_input = ttk.Entry(size_row,textvariable=self.alignment_max_side,width=8)
        self.resolution_input.pack(side='right')
        ttk.Label(resolution_box,text='保存画像はどちらも元解像度です。\n元解像度での計算は時間がかかります。',wraplength=230,justify='left').pack(anchor='w',pady=(6,0))
        self.align_button = ttk.Button(settings, text='アラインを実行',
                                       command=self.start_alignment, state='disabled')
        self.align_button.grid(row=5, column=0, sticky='ew', pady=(12,4))
        self.cancel_alignment_button = ttk.Button(settings, text='中止',
                                                   command=self.cancel_alignment, state='disabled')
        self.cancel_alignment_button.grid(row=6, column=0, sticky='ew')
        ttk.Label(settings, textvariable=self.alignment_status,
                  wraplength=245, justify='left').grid(row=7, column=0, sticky='ew', pady=8)

        self.manual_panel = ttk.LabelFrame(settings,text='移動・回転（手動補正）',padding=8)
        self.manual_panel.grid(row=8,column=0,sticky='ew',pady=(0,8))
        self._manual_widgets = []
        for value,label in [('Single','この画像だけ移動・回転'),('All','全画像に同じ移動・回転')]:
            widget = ttk.Radiobutton(self.manual_panel,text=label,variable=self.manual_scope,value=value,state='disabled')
            widget.pack(anchor='w',pady=3)
            self._manual_widgets.append(widget)
        move_row = ttk.Frame(self.manual_panel)
        move_row.pack(fill='x',pady=(8,4))
        ttk.Label(move_row,text='移動量（元画像px）').pack(side='left')
        self.manual_move_input = ttk.Spinbox(move_row,textvariable=self.manual_move_step,from_=.1,to=10000,increment=1,width=6,state='disabled')
        self.manual_move_input.pack(side='right')
        self._manual_widgets.append(self.manual_move_input)
        arrows = ttk.Frame(self.manual_panel)
        arrows.pack(pady=3)
        self.manual_move_buttons = {}
        for name,label,axis,sign,row,col in [('up','↑','y',-1,0,1),('left','←','x',-1,1,0),('right','→','x',1,1,2),('down','↓','y',1,2,1)]:
            widget = RepeatButton(arrows,text=label,width=4,state='disabled',command=lambda a=axis,s=sign:self.apply_manual(a,s))
            widget.grid(row=row,column=col,padx=2,pady=2)
            self.manual_move_buttons[name] = widget
            self._manual_widgets.append(widget)
        angle_row = ttk.Frame(self.manual_panel)
        angle_row.pack(fill='x',pady=(6,4))
        ttk.Label(angle_row,text='回転量（度）').pack(side='left')
        self.manual_angle_input = ttk.Spinbox(angle_row,textvariable=self.manual_angle_step,from_=.01,to=180,increment=.1,width=6,state='disabled')
        self.manual_angle_input.pack(side='right')
        rotation_row = ttk.Frame(self.manual_panel)
        rotation_row.pack(fill='x')
        self.manual_rotation_buttons = []
        for label,sign in [('↶ 左回転',-1),('右回転 ↷',1)]:
            widget = RepeatButton(rotation_row,text=label,state='disabled',command=lambda s=sign:self.apply_manual('angle',s))
            widget.pack(side='left',expand=True,fill='x',padx=1)
            self.manual_rotation_buttons.append(widget)
        self.manual_undo_button = ttk.Button(self.manual_panel,text='補正を1つ戻す',command=self.undo_manual,state='disabled')
        self.manual_undo_button.pack(fill='x',pady=(8,4))
        self.manual_reset_button = ttk.Button(self.manual_panel,text='補正をすべてやり直す',command=self.reset_manual,state='disabled')
        self.manual_reset_button.pack(fill='x',pady=(0,4))
        ttk.Label(self.manual_panel,textvariable=self.manual_message,wraplength=225,justify='left').pack(fill='x')

        self.preview_frame = ttk.LabelFrame(self.analysis_tab, text='画像プレビュー（表示用サイズ: —）', padding=6)
        self.preview_frame.grid(row=0, column=1, sticky='nsew', pady=(0,4))
        self.preview_frame.columnconfigure(0, weight=1)
        self.preview_frame.rowconfigure(1, weight=1)
        navigation = ttk.Frame(self.preview_frame)
        navigation.grid(row=0, column=0, sticky='ew', pady=(0,5))
        self.first_button = ttk.Button(navigation, text='最初', command=lambda: self.show_preview(0), state='disabled')
        self.previous_button = ttk.Button(navigation, text='◀ 前へ', command=lambda: self.show_preview(self._preview_slot-1), state='disabled')
        self.next_button = ttk.Button(navigation, text='次へ ▶', command=lambda: self.show_preview(self._preview_slot+1), state='disabled')
        self.last_button = ttk.Button(navigation, text='最後', command=lambda: self.show_preview(self.preview_slot_count()-1), state='disabled')
        for button in (self.first_button, self.previous_button):
            button.pack(side='left', padx=(0,4))
        self.preview_spin = ttk.Spinbox(navigation, from_=1, to=1, textvariable=self.preview_number,
                                        width=6, state='disabled', command=self.jump_preview)
        self.preview_spin.pack(side='left', padx=(6,4))
        self.preview_spin.bind('<Return>', lambda event: self.jump_preview())
        ttk.Label(navigation, textvariable=self.preview_count).pack(side='left', padx=(0,10))
        for button in (self.next_button, self.last_button):
            button.pack(side='left', padx=(0,4))
        self.raw_button = ttk.Checkbutton(navigation,text='生データを表示',variable=self.raw_display,
                                          command=self.change_raw_display,state='disabled')
        self.raw_button.pack(side='left',padx=(4,0))
        self.position_reset_button = ttk.Button(navigation,text='元の位置に戻す',command=self.reset_all_image_views,state='disabled')
        self.position_reset_button.pack(side='left',padx=(6,0))
        self.merged_button = ttk.Button(navigation,text='merged',command=self.tiff_view.toggle_merged,state='disabled')
        self.merged_button.pack(side='left',padx=(6,0))
        self.merged_channels = ttk.Frame(navigation)
        self.analysis_scale=ScaleBarControls(self.preview_frame,self.request_preview_draw)
        self.analysis_scale.grid(row=3,column=0,sticky='e',padx=4)
        self.preview_canvas = tk.Canvas(self.preview_frame, background='#242a30', highlightthickness=0, height=120)
        self.preview_canvas.image_view = True
        self.preview_canvas.grid(row=1, column=0, sticky='nsew')
        self.preview_canvas.bind('<Configure>', lambda event: self.request_preview_draw())
        ttk.Label(self.preview_frame, textvariable=self.preview_filename, anchor='w').grid(row=2, column=0, sticky='ew', pady=(4,0))
        self.analysis_lower = ttk.LabelFrame(self.analysis_tab, text='アライン後', padding=6)
        self.analysis_lower.grid(row=1, column=1, sticky='nsew', pady=(4,0))
        self.analysis_lower.columnconfigure(0, weight=1)
        self.analysis_lower.rowconfigure(0, weight=1)
        self.result_canvas = tk.Canvas(self.analysis_lower, background='#242a30', highlightthickness=0, height=120)
        self.result_canvas.image_view = True
        self.result_canvas.grid(row=0, column=0, sticky='nsew')
        self.result_canvas.bind('<Configure>', lambda event: self.request_preview_draw())
        for canvas,name in [(self.preview_canvas,'source'),(self.result_canvas,'result')]:
            canvas.bind('<MouseWheel>',lambda event,n=name:self.zoom_image(event,n))
            canvas.bind('<ButtonPress-1>',lambda event,n=name:self.start_image_pan(event,n))
            canvas.bind('<B1-Motion>',self.pan_image)
            canvas.bind('<ButtonRelease-1>',lambda event:setattr(self,'_pan_start',None))
            canvas.bind('<Double-Button-1>',lambda event,n=name:self.double_click_image(event,n))
        self.save_tab = ttk.Frame(self.tabs,padding=16)
        self.tabs.add(self.save_tab,text='save')
        self.save_tab.columnconfigure(1,weight=1)
        ttk.Label(self.save_tab,text='保存',style='Title.TLabel').grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,18))
        ttk.Label(self.save_tab,text='保存先ディレクトリ').grid(row=1,column=0,sticky='w',padx=(0,12),pady=8)
        self.save_directory_entry = ttk.Entry(self.save_tab,textvariable=self.save_directory)
        self.save_directory_entry.grid(row=1,column=1,sticky='ew',pady=8)
        self.register_folder_drop(self.save_directory_entry,lambda path:self.save_directory.set(str(path)))
        self.save_browse_button = ttk.Button(self.save_tab,text='参照…',command=self.choose_save_directory)
        self.save_browse_button.grid(row=1,column=2,padx=(8,0),pady=8)
        ttk.Label(self.save_tab,text='保存する名前').grid(row=2,column=0,sticky='w',padx=(0,12),pady=8)
        self.save_name_entry = ttk.Entry(self.save_tab,textvariable=self.save_name)
        self.save_name_entry.grid(row=2,column=1,sticky='ew',pady=8)
        self.save_create_folder_check = ttk.Checkbutton(self.save_tab,text='フォルダを作成',variable=self.save_create_folder)
        self.save_create_folder_check.grid(row=3,column=1,sticky='w',pady=8)
        contents = ttk.LabelFrame(self.save_tab,text='保存内容（保存するものにチェック）',padding=10)
        contents.grid(row=4,column=0,columnspan=3,sticky='ew',pady=12)
        self.save_images_check = ttk.Checkbutton(contents,text='アライン後の画像（選択した形式）',variable=self.save_images,command=self.update_save_merge_controls)
        self.save_images_check.pack(anchor='w',pady=4)
        formats = ttk.Frame(contents); formats.pack(fill='x',pady=4)
        ttk.Label(formats,text='画像の出力形式').pack(side='left',padx=(0,8))
        self.save_format_combo = ttk.Combobox(formats,textvariable=self.save_format,values=['PNG','TIFF','JPEG'],state='readonly',width=10)
        self.save_format_combo.pack(side='left')
        self.save_format_combo.bind('<<ComboboxSelected>>',lambda event:self.update_save_merge_controls())
        self.save_merge_box = ttk.LabelFrame(contents,text='PNG / JPEGのチャンネル合成',padding=8)
        self.save_merge_radios = []
        for value,label in [('all','全チャンネルを重ねて保存'),('selected','チェックしたチャンネルだけ重ねて保存')]:
            button = ttk.Radiobutton(self.save_merge_box,text=label,variable=self.save_merge_mode,value=value,command=self.update_save_merge_controls)
            button.pack(anchor='w',pady=3); self.save_merge_radios.append(button)
        self.save_channels_frame = ttk.Frame(self.save_merge_box)
        self.save_channels_frame.pack(fill='x',pady=4)
        ttk.Label(self.save_merge_box,text='alignタブの表示色・インテンシティで合成し、元解像度のRGB画像（8-bit）として保存します。',wraplength=1000).pack(anchor='w',pady=4)
        self.save_movements_check = ttk.Checkbutton(contents,text='移動・回転のメタデータ一覧（CSV）',variable=self.save_movements)
        self.save_movements_check.pack(anchor='w',pady=4)
        ttk.Label(contents,text='params.json（解析条件・全画像の変換・追加補正）は常に保存します。').pack(anchor='w',pady=(10,0))
        self.save_button = ttk.Button(self.save_tab,text='保存を実行',command=self.start_saving,state='disabled')
        self.save_button.grid(row=5,column=1,sticky='w',pady=10)
        ttk.Label(self.save_tab,textvariable=self.save_status,wraplength=1000).grid(row=6,column=0,columnspan=3,sticky='w',pady=8)
        self.stack_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.stack_tab,text='stack')
        self.stack_panel = StackPanel(self.stack_tab,self)
        self.stack_panel.pack(fill='both',expand=True)
        self.additional_tab = ttk.Frame(self.tabs,padding=16)
        self.tabs.add(self.additional_tab,text='3D reconstruction')
        self.volume_panel = VolumePanel(self.additional_tab,self)
        self.volume_panel.pack(fill='both',expand=True)
        self.credit_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.credit_tab,text='credit')
        self.credit_panel = CreditPanel(self.credit_tab,self)
        self.credit_panel.pack(fill='both',expand=True)
        self.tabs.bind('<<NotebookTabChanged>>',self.sync_stack_tab,add='+')
        ttk.Label(self.analysis_lower, textvariable=self.result_info).grid(row=1, column=0, sticky='ew', pady=(4,0))

        self.log_frame = ttk.LabelFrame(data, text='ログ（新しい順）', padding=4)
        self.log_frame.grid(row=6, column=0, sticky='nsew', pady=(7,0))
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log_table = ttk.Treeview(self.log_frame, columns=('number','time','message'), show='headings', height=8)
        self.log_table.tag_configure('error',background='#c62828',foreground='white')
        self.log_table.heading('number', text='')
        self.log_table.heading('time', text='時刻')
        self.log_table.heading('message', text='ログ内容')
        self.log_table.column('number', width=40, stretch=False, anchor='center')
        self.log_table.column('time', width=165, stretch=False, anchor='w')
        self.log_table.column('message', width=650, stretch=True, anchor='w')
        self.log_table.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(self.log_frame, orient='vertical', command=self.log_table.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(self.log_frame, orient='horizontal', command=self.log_table.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.log_table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.request_preview_draw()

    def sync_stack_tab(self,event=None):
        if self.tabs.select()==str(self.stack_tab): self.stack_panel.sync_analysis()

    def change_alignment_method(self):
        method = self.alignment_method.get()
        label, description = ALIGNMENT_METHODS[method]
        self.alignment_description.set(description)
        if method != self._last_alignment_method:
            self.add_log(f'アライン方法を変更: {label}')
            self._last_alignment_method = method
        self.refresh_manual_controls()

    def scroll_settings(self,event):
        widget = event.widget
        while widget is not None:
            if widget in (self.alignment_content,self.settings_canvas):
                self.settings_canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
                return 'break'
            widget = getattr(widget,'master',None)

    def refresh_manual_controls(self):
        enabled = (not self._raw_busy and not self._alignment_busy and not self._loading_busy and self._result_method is not None and not self._preview_is_empty
                   and self.preview_index<len(self.alignment_matrices) and self.alignment_matrices[self.preview_index] is not None)
        for widget in self._manual_widgets:
            widget.state(['!disabled' if enabled else 'disabled'])
        rotation = enabled and self._result_method=='rigid' and self.alignment_method.get()=='rigid'
        for widget in [self.manual_angle_input,*self.manual_rotation_buttons]:
            widget.state(['!disabled' if rotation else 'disabled'])
        self.manual_undo_button.state(['!disabled' if enabled and self._manual_history else 'disabled'])
        self.manual_reset_button.state(['!disabled' if enabled and self._manual_modified and self._automatic_matrices else 'disabled'])

    def apply_manual(self,axis,direction):
        if self._preview_is_empty or self._raw_busy:return False
        if self._alignment_busy or self._loading_busy or self._result_method is None:
            return False
        if axis not in ('x','y','angle'):
            return False
        if axis=='angle' and (self._result_method!='rigid' or self.alignment_method.get()!='rigid'):
            self.manual_message.set('回転は剛体アライン実行後に操作できます。')
            return False
        try:
            step = self.manual_angle_step.get() if axis=='angle' else self.manual_move_step.get()
            if not math.isfinite(step) or step<=0:
                raise ValueError()
        except (tk.TclError,ValueError):
            self.manual_message.set('移動量・回転量には0より大きい数値を入力してください。')
            return False
        count = len(self.analysis_files)
        if self.manual_scope.get()=='All':
            indices = list(range(count))
        elif self.manual_scope.get()=='Single':
            indices = [self.preview_index]
        else:
            return False
        if not indices or any(i>=len(self.alignment_matrices) or self.alignment_matrices[i] is None for i in indices):
            self.manual_message.set('対象画像のアライン結果が必要です。')
            return False
        amount = step*direction
        dx,dy,angle = (amount if axis==name else 0. for name in ('x','y','angle'))
        correction = rigid(dx,dy,angle,*self.alignment_canvas)
        if not self._automatic_matrices:
            self._automatic_matrices = [m.copy() if m is not None else None for m in self.alignment_matrices]
        self._manual_history.append(([m.copy() if m is not None else None for m in self.alignment_matrices],self._manual_modified.copy(),len(self._manual_events)))
        self._manual_history = self._manual_history[-50:]
        for index in indices:
            self.alignment_matrices[index] = correction @ self.alignment_matrices[index]
        self._manual_modified.update(indices)
        self.volume_panel.invalidate_aligned()
        self._manual_events.append({'time':datetime.now(timezone(timedelta(hours=9))).isoformat(),
                                    'scope':self.manual_scope.get(),'image_indices':[i+1 for i in indices],
                                    'image_files':[self.analysis_files[i].path.name for i in indices],
                                    'dx_px':dx,'dy_px':dy,'rotation_deg_clockwise':angle,
                                    'pivot':[self.alignment_canvas[0]/2,self.alignment_canvas[1]/2]})
        self.stack_panel.invalidate_aligned()
        if self._preview_cache is not None:
            self._preview_cache.clear_results()
        self.refresh_aligned_image()
        self.request_preview_draw()
        target = '全画像' if self.manual_scope.get()=='All' else f'画像 {self._preview_slot+1}'
        self.manual_message.set(f'{target}: X {dx:g} px / Y {dy:g} px / 回転 {angle:g}° を適用')
        self.add_log('手動補正: '+self.manual_message.get())
        self.refresh_manual_controls()
        return True

    def undo_manual(self):
        if self._alignment_busy or self._loading_busy or not self._manual_history:
            return
        self.alignment_matrices,self._manual_modified,event_count = self._manual_history.pop()
        self._manual_events = self._manual_events[:event_count]
        self.volume_panel.invalidate_aligned()
        self.stack_panel.invalidate_aligned()
        if self._preview_cache is not None:
            self._preview_cache.clear_results()
        self.refresh_aligned_image()
        self.request_preview_draw()
        self.manual_message.set('直前の手動補正を取り消しました。')
        self.add_log(self.manual_message.get())
        self.refresh_manual_controls()

    def reset_manual(self):
        if self._alignment_busy or self._loading_busy or not self._automatic_matrices:
            return
        self.alignment_matrices = [m.copy() if m is not None else None for m in self._automatic_matrices]
        self._manual_history.clear()
        self._manual_modified.clear()
        self._manual_events.clear()
        self.volume_panel.invalidate_aligned()
        self.stack_panel.invalidate_aligned()
        if self._preview_cache is not None:
            self._preview_cache.clear_results()
        self.refresh_aligned_image()
        self.request_preview_draw()
        self.manual_message.set('手動補正をすべて取り消し、自動アライン直後に戻しました。')
        self.add_log(self.manual_message.get())
        self.refresh_manual_controls()

    def clear_preview(self):
        self.tiff_view.reset()
        self.clear_raw_images()
        self.raw_display.set(False)
        self.raw_button.state(['disabled'])
        self.position_reset_button.state(['disabled'])
        self.merged_button.state(['disabled'])
        self._view_states = {name: {'zoom':1.,'center':(.5,.5)} for name in ('source','result')}
        if self._preview_original is not None:
            self._preview_original.close()
        self._preview_original = self._preview_photo = None
        if self._preview_display is not None:
            self._preview_display.close()
        self._preview_display = None
        self._preview_error = None
        self.preview_index = 0
        self._preview_slot=0;self._preview_is_empty=False
        self.preview_number.set(1)
        self.preview_count.set('/ 0')
        self.preview_filename.set('画像未読み込み')
        for widget in (self.first_button, self.previous_button, self.next_button, self.last_button, self.preview_spin):
            widget.state(['disabled'])
        self.request_preview_draw()

    def reset_alignment(self):
        self.volume_panel.clear()
        self.stack_panel.clear()
        self._analysis_settings = None
        self._manual_events.clear()
        self.save_button.state(['disabled'])
        self.save_status.set('アライン実行後に保存できます。params.jsonは常に保存します。')
        self._automatic_matrices = []
        self._result_method = None
        self._manual_history.clear()
        self._manual_modified.clear()
        self.manual_message.set('アライン実行後に操作できます。')
        self.alignment_matrices = []
        self.alignment_records = []
        self.alignment_canvas = None
        self.clear_aligned_image()
        if self._preview_cache is not None:
            self._preview_cache.clear_results()
        self.align_button.state(['disabled'])
        self.alignment_status.set('対象と色を選択してください。')
        self.refresh_manual_controls()

    def clear_aligned_image(self):
        if self._aligned_original is not None:
            self._aligned_original.close()
        self._aligned_original = self._aligned_photo = None
        if self._aligned_display is not None:
            self._aligned_display.close()
        self._aligned_display = None
        self.result_info.set('アライン未実行')

    def refresh_aligned_image(self):
        self.clear_aligned_image()
        if self._raw_aligned is not None:
            self._raw_aligned.close()
            self._raw_aligned = None
        index = self.preview_index
        if self._preview_is_empty or index>=len(self.alignment_matrices) or self.alignment_matrices[index] is None:
            return
        try:
            self._aligned_display = self._preview_cache.render(index,
                self.alignment_matrices[index], self.alignment_canvas)
            record = self.alignment_records[index]
            metric_label = '未成立・照合時NCC' if record.get('status')=='unregistered' else '自動NCC（補正前）' if index in self._manual_modified else 'NCC'
            score=record.get('score');metric=f'{metric_label} {score:.3f}' if score is not None else '復元済み（NCC記録なし）'
            self.result_info.set(f"画像 {self._preview_slot+1} / {self.preview_slot_count()} · {metric}"
                                 + (' · '+record['warning'] if record['warning'] else ''))
            if self.raw_display.get() and self._tiff_pages==1 and not self.large_raw_requested():
                self._raw_aligned = render_result(self.analysis_files[index].path,
                    self.alignment_matrices[index],self.alignment_canvas)
        except (OSError, ValueError) as exc:
            self.result_info.set('結果画像を表示できません')
            self.add_log(f'結果画像の読み込みエラー: {exc}')

    def refresh_resolution_controls(self):
        enabled = not self._alignment_busy and self.alignment_resolution.get()=='reduced'
        self.resolution_input.state(['!disabled'] if enabled else ['disabled'])

    def start_alignment(self):
        if self._alignment_busy or self._loading_busy or self._raw_busy or self.stack_panel.busy or len(self.analysis_files)<2:
            return
        if self._preview_is_empty and self.alignment_scope.get()=='Reference':
            self.alignment_status.set('基準に使用する画像を表示してください。no imageは基準にできません。');return
        resolution = self.alignment_resolution.get()
        try:
            if resolution not in ('original','reduced'): raise ValueError()
            proxy_limit = None if resolution=='original' else int(self.alignment_max_side.get())
            if proxy_limit is not None and proxy_limit<2: raise ValueError()
        except ValueError:
            self.alignment_status.set('最大辺には2以上の整数を入力してください。')
            return
        try:
            channel = int(self.alignment_channel.get())-1 if self._tiff_pages>1 else None
            if channel is not None and not 0<=channel<self._tiff_pages: raise ValueError()
        except ValueError:
            self.alignment_status.set('有効なチャンネル番号を選択してください。'); return
        self._alignment_busy = True
        self._alignment_cancel.clear()
        paths = tuple(file.path for file in self.analysis_files)
        current = self.preview_index
        scope,color,method = self.alignment_scope.get(),self.alignment_color.get(),self.alignment_method.get()
        resolution_label = '元解像度' if proxy_limit is None else f'縮小解像度（最大辺{proxy_limit}px）'
        self._running_method = method
        self._running_settings = {'input_folder':str(self.folder.resolve()),'method':method,'color':color,'scope':scope,
                                  'channel_index':channel+1 if channel is not None else None,'channel_count':self._tiff_pages,
                                  'reference_image_index':current+1 if scope=='Reference' else None,
                                  'reference_image_file':paths[current].name if scope=='Reference' else None,
                                  'algorithm':'ncc_fft_simplex_area_pyramid','proxy_max_side':proxy_limit,
                                  'input_preparation':'native_coverage_v2','reference_failure_policy':'retry_accepted_neighbors_then_unregistered',
                                  'calculation_resolution':resolution,'ncc_pixel_sampling':'all_valid_pixels',
                                  'started_at':datetime.now(timezone(timedelta(hours=9))).isoformat()}
        self._running_settings.update(original_image_indices=list(self._analysis_slice_indices),
            total_source_images=self._analysis_total_slices,
            data_sequence=[image.path.name if image is not None else None for image in self._analysis_sequence],
            source_data_sequence=[image.path.name if image is not None else None for image in self._analysis_source_sequence],
            data_image_indices=list(self._analysis_data_indices),ignore_excluded_images=self._analysis_ignore_excluded,
            reference_sequence_index=self._analysis_slice_indices[current] if scope=='Reference' and self._analysis_slice_indices else None,
            reference_original_image_index=self._analysis_data_indices[current] if scope=='Reference' and self._analysis_data_indices else None)
        if channel is not None: self._running_settings['color'] = None
        self.refresh_manual_controls()
        existing = self.alignment_matrices or None
        self._alignment_controls = [*self.alignment_buttons.values(), *self.scope_buttons.values(), *self.resolution_buttons.values(), self.resolution_input, self.color_combo,self.channel_combo,self.align_button]
        for widget in self._alignment_controls:
            widget.state(['disabled'])
        self.cancel_alignment_button.state(['!disabled'])
        self.alignment_status.set(f'計算中…（{resolution_label}）')
        self.open_alignment_progress()
        self.add_log(f'アライン開始: {ALIGNMENT_METHODS[method][0]} / '+(f'チャンネル {channel+1}' if channel is not None else color)+' / '
                     + ('現在の画像を基準' if scope=='Reference' else '全体最適化') + f' / {resolution_label}')
        events, cancel = self._alignment_events, self._alignment_cancel
        def worker():
            try:
                signatures = source_signatures(paths)
                result = align_files(paths,current,scope,color,method,existing,cancel,
                    lambda number,total,text:events.put(('progress',number,total,text)),proxy_limit=proxy_limit,**({'channel':channel} if channel is not None else {}))
                validate_sources(paths,{'source_signatures':signatures})
                self._running_settings['source_signatures'] = signatures
                events.put(('done',result))
            except Cancelled:
                events.put(('cancelled',None))
            except Exception as exc:
                events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
        self._alignment_poll_job = self.after(60,self.poll_alignment)

    def poll_alignment(self):
        self._alignment_poll_job = None
        try:
            while True:
                event = self._alignment_events.get_nowait()
                kind,value = event[0],event[1]
                if kind=='progress':
                    number,total,text = event[1:]
                    self.alignment_status.set(f'{number}/{total} · {text}')
                    self._alignment_dialog_text.set(text)
                    self._alignment_dialog_percent.set(f'{number} / {total}')
                    self._alignment_dialog_bar.stop()
                    if number>=total:
                        self._alignment_dialog_bar.configure(mode='indeterminate')
                        self._alignment_dialog_bar.start(15)
                    else:
                        self._alignment_dialog_bar.configure(mode='determinate',value=100*number/max(1,total))
                    continue
                self.close_alignment_progress()
                self._alignment_busy = False
                for widget in self._alignment_controls:
                    widget.state(['!disabled'])
                self.refresh_resolution_controls()
                self.cancel_alignment_button.state(['disabled'])
                if kind=='done':
                    matrices,records,canvas = value
                    previous = self.alignment_records or [None]*len(records)
                    self.alignment_matrices,self.alignment_canvas = matrices,canvas
                    self._automatic_matrices = [m.copy() if m is not None else None for m in matrices]
                    self._analysis_settings = copy.deepcopy(self._running_settings)
                    self.volume_panel.invalidate_aligned()
                    self._manual_events.clear()
                    self.stack_panel.invalidate_aligned()
                    self.save_button.state(['!disabled'])
                    self.save_status.set('保存内容を選択して「保存を実行」を押してください。')
                    self._result_method = self._running_method
                    self._manual_history.clear()
                    self._manual_modified.clear()
                    self.manual_message.set('移動・回転でアライン結果を補正できます。' if self._result_method=='rigid' else '移動でアライン結果を補正できます。')
                    self.alignment_records = [record if record is not None else previous[i] for i,record in enumerate(records)]
                    warnings = sum(bool(r and r['warning']) for r in records)
                    unregistered=sum(r.get('status')=='unregistered' for r in records if r)
                    self.alignment_status.set((f'完了 · 未成立 {unregistered} 枚（元位置・手動確認） · 要確認 {warnings} 枚' if unregistered else f'完了 · 要確認 {warnings} 枚'))
                    self.refresh_aligned_image()
                    self.request_preview_draw()
                elif kind=='cancelled':
                    self.alignment_status.set('中止しました。以前の結果を保持します。')
                else:
                    self.alignment_status.set(f'エラー: {value}')
                self.add_log(self.alignment_status.get())
                self.refresh_manual_controls()
        except queue.Empty:
            pass
        if self._alignment_busy:
            self._alignment_poll_job = self.after(60,self.poll_alignment)

    def open_alignment_progress(self):
        dialog = tk.Toplevel(self)
        self._alignment_dialog = dialog
        dialog.title('アライン実行中')
        dialog.resizable(False,False)
        dialog.transient(self)
        center_dialog(self,dialog,480,170)
        self._alignment_dialog_text = tk.StringVar(value='計算用画像を確認しています…')
        self._alignment_dialog_percent = tk.StringVar(value='')
        self._alignment_started_at = time.monotonic()
        self._alignment_elapsed = tk.StringVar(value='経過時間 00:00:00')
        ttk.Label(dialog,textvariable=self._alignment_dialog_text,wraplength=440).pack(fill='x',padx=18,pady=(18,10))
        self._alignment_dialog_bar = ttk.Progressbar(dialog,maximum=100,mode='indeterminate')
        self._alignment_dialog_bar.pack(fill='x',padx=18)
        self._alignment_dialog_bar.start(15)
        row = ttk.Frame(dialog)
        row.pack(fill='x',padx=18,pady=12)
        ttk.Label(row,textvariable=self._alignment_dialog_percent).pack(side='left')
        self._alignment_dialog_cancel = ttk.Button(row,text='中止',command=self.cancel_alignment)
        self._alignment_dialog_cancel.pack(side='right')
        ttk.Label(row,textvariable=self._alignment_elapsed).pack(side='right',padx=(8,16))
        dialog.protocol('WM_DELETE_WINDOW',self.cancel_alignment)
        dialog.grab_set()
        self.update_alignment_elapsed()

    def update_alignment_elapsed(self):
        if self._alignment_elapsed_job is not None:
            self.after_cancel(self._alignment_elapsed_job)
            self._alignment_elapsed_job = None
        if self._alignment_dialog is None:
            return
        seconds = max(0,int(time.monotonic()-self._alignment_started_at))
        hours,remainder = divmod(seconds,3600)
        minutes,seconds = divmod(remainder,60)
        self._alignment_elapsed.set(f'経過時間 {hours:02d}:{minutes:02d}:{seconds:02d}')
        self._alignment_elapsed_job = self.after(250,self.update_alignment_elapsed)

    def cancel_alignment(self):
        self._alignment_cancel.set()
        if self._alignment_dialog is not None:
            self._alignment_dialog_text.set('中止しています…')
            self._alignment_dialog_cancel.state(['disabled'])

    def close_alignment_progress(self):
        if self._alignment_elapsed_job is not None:
            self.after_cancel(self._alignment_elapsed_job)
            self._alignment_elapsed_job = None
        if self._alignment_dialog is not None:
            self._alignment_dialog_bar.stop()
            self._alignment_dialog.grab_release()
            self._alignment_dialog.destroy()
            self._alignment_dialog = None

    def show_preview(self, index):
        if not self.analysis_files or getattr(self,'_raw_busy',False):return
        self._preview_slot=max(0,min(self.preview_slot_count()-1,int(index)))
        positions=self._analysis_slice_indices or tuple(range(1,len(self.analysis_files)+1))
        self._preview_is_empty=self._preview_slot+1 not in positions
        if not self._preview_is_empty:self.preview_index=positions.index(self._preview_slot+1)
        self.clear_raw_images()
        if self._preview_original is not None:self._preview_original.close()
        if self._preview_display is not None:self._preview_display.close()
        self._preview_original=self._preview_display=self._preview_photo=None
        self._preview_error=None
        count=self.preview_slot_count()
        self.preview_number.set(self._preview_slot+1);self.preview_count.set(f'/ {count}')
        self.preview_spin.configure(to=count);self.preview_spin.state(['!disabled'])
        for widget in (self.first_button,self.previous_button):widget.state(['disabled' if self._preview_slot==0 else '!disabled'])
        for widget in (self.next_button,self.last_button):widget.state(['disabled' if self._preview_slot==count-1 else '!disabled'])
        if self._preview_is_empty:
            entry=self._analysis_sequence[self._preview_slot] if self._preview_slot<len(self._analysis_sequence) else None
            self.preview_filename.set(f'{self._preview_slot+1}: no image'+(f' · {entry.path.name}（解析除外）' if entry is not None else '（空行）'))
            self.clear_aligned_image();self.result_info.set('no image')
            self.refresh_manual_controls();self.request_preview_draw();return
        file=self.analysis_files[self.preview_index]
        try:
            thumbnail,(width,height)=self._preview_cache.get(self.preview_index);thumbnail.close()
            self._preview_display=self._preview_cache.render(self.preview_index,rigid(0,0,0,*self.display_canvas_size()),self.display_canvas_size())
            self._preview_original=Image.open(file.path)
            self.preview_filename.set(f'{self._preview_slot+1}: {file.path.name} · {width} × {height} px')
            if self.raw_display.get() and not self.large_raw_requested():self.read_raw_source()
        except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError) as exc:
            self._preview_error='画像を表示できません。別の画像に移動できます。'
            self.preview_filename.set(file.path.name);self.add_log(f'画像読み込みエラー: {file.path.name}: {exc}')
        self.refresh_aligned_image();self.refresh_manual_controls()
        if self.large_raw_requested():self.prepare_large_raw()
        self.request_preview_draw()

    def clear_raw_images(self):
        for image in (self._raw_original,self._raw_aligned):
            if image is not None:
                image.close()
        self._raw_original = self._raw_aligned = None

    def read_raw_source(self):
        if self._preview_is_empty or self._tiff_pages>1: return
        if is_tiff(self.analysis_files[self.preview_index].path):
            from alignment import read_rgb
            with read_rgb(self.analysis_files[self.preview_index].path) as source:
                raw = source.convert('RGBA')
            canvas = self.display_canvas_size()
            self._raw_original = Image.new('RGBA',canvas,'black')
            self._raw_original.paste(raw,((canvas[0]-raw.width)//2,(canvas[1]-raw.height)//2))
            raw.close(); return
        with Image.open(self.analysis_files[self.preview_index].path) as source:
            with ImageOps.exif_transpose(source) as oriented:
                raw = oriented.convert('RGBA')
        canvas = self.display_canvas_size()
        if raw.size!=canvas:
            self._raw_original = Image.new('RGBA',canvas,'white')
            self._raw_original.paste(raw,((canvas[0]-raw.width)//2,(canvas[1]-raw.height)//2),raw)
            raw.close()
        else:
            self._raw_original = raw

    def display_canvas_size(self):
        if self.alignment_canvas is not None:
            return tuple(self.alignment_canvas)
        sizes = [entry[0] for entry in self._preview_cache.entries.values() if entry[0] is not None] if self._preview_cache else []
        return (max(s[0] for s in sizes),max(s[1] for s in sizes)) if sizes else (1,1)

    def common_view_fit(self,image):
        width = max(1,min(self.preview_canvas.winfo_width(),self.result_canvas.winfo_width())-self.preview_sidebar()-16)
        height = max(1,min(self.preview_canvas.winfo_height(),self.result_canvas.winfo_height())-16)
        if self.tiff_view.active and self.tiff_view.focus_page is None and not self.tiff_view.merged:
            width=max(1,width/self._tiff_pages-12)
            height=max(1,height-104)
        return min(width/image.width,height/image.height)

    def view_origin(self,event,name):
        canvas=self.preview_canvas if name=='source' else self.result_canvas
        if self.tiff_view.active and self.tiff_view.focus_page is None and not self.tiff_view.merged:
            width=canvas.winfo_width()/self._tiff_pages
            slot=max(0,min(self._tiff_pages-1,int(event.x/max(1,width))))
            return (slot+.5)*width,(canvas.winfo_height()+98)/2
        sidebar=self.preview_sidebar()
        return sidebar+(canvas.winfo_width()-sidebar)/2,canvas.winfo_height()/2

    def sync_view_state(self,state):
        self._view_states = {name:{'zoom':state['zoom'],'center':tuple(state['center'])} for name in ('source','result')}

    def change_raw_display(self):
        if self._raw_busy:return
        self.clear_raw_images()
        if not self.raw_display.get():self.tiff_view.clear_raw_cache()
        if self.large_raw_requested():
            self.refresh_aligned_image();self.prepare_large_raw();self.request_preview_draw();return
        if self.raw_display.get() and self.analysis_files:
            self.configure(cursor='watch')
            self.update_idletasks()
            try:
                self.read_raw_source()
            except (OSError,ValueError,Image.DecompressionBombError) as exc:
                self.raw_display.set(False)
                self.add_log(f'生データを表示できません: {exc}')
            finally:
                self.configure(cursor='')
        self.refresh_aligned_image()
        self.request_preview_draw()

    def view_image(self,name):
        if self._raw_busy:return None
        if self._preview_is_empty:return None
        if self.tiff_view.active and self.analysis_files:
            return self.tiff_view.merged_image(name) if self.tiff_view.merged else self.tiff_view.image(name)
        if name=='source':
            return self._raw_original if self.raw_display.get() else self._preview_display
        return self._raw_aligned if self.raw_display.get() else self._aligned_display

    def zoom_image(self,event,name):
        image = self.view_image(name)
        if image is None:
            return 'break'
        canvas = self.preview_canvas if name=='source' else self.result_canvas
        state = self._view_states[name]
        sw,sh = image.size
        fit = self.common_view_fit(image)
        scale = fit*state['zoom']
        origin,origin_y=self.view_origin(event,name)
        px = state['center'][0]*sw+(event.x-origin)/scale
        py = state['center'][1]*sh+(event.y-origin_y)/scale
        new_zoom = max(.25,min(32.,state['zoom']*(1.2 if event.delta>0 else 1/1.2)))
        new_scale = fit*new_zoom
        state['center'] = ((px-(event.x-origin)/new_scale)/sw,
                           (py-(event.y-origin_y)/new_scale)/sh)
        state['zoom'] = new_zoom
        self.sync_view_state(state)
        self.request_preview_draw()
        return 'break'

    def start_image_pan(self,event,name):
        canvas = self.preview_canvas if name=='source' else self.result_canvas
        canvas.focus_set()
        self._pan_start = (name,event.x,event.y,self._view_states[name]['center'])

    def pan_image(self,event):
        if self._pan_start is None:
            return
        name,x,y,center = self._pan_start
        image = self.view_image(name)
        if image is None:
            return
        canvas = self.preview_canvas if name=='source' else self.result_canvas
        sw,sh = image.size
        scale = self.common_view_fit(image)*self._view_states[name]['zoom']
        self._view_states[name]['center'] = (center[0]-(event.x-x)/(scale*sw),center[1]-(event.y-y)/(scale*sh))
        self.sync_view_state(self._view_states[name])
        self.request_preview_draw()

    def reset_image_view(self,name):
        self.sync_view_state({'zoom':1.,'center':(.5,.5)})
        self._pan_start = None
        self.request_preview_draw()

    def double_click_image(self,event,name):
        if not self.tiff_view.toggle(event,name): self.reset_image_view(name)
        return 'break'

    def reset_all_image_views(self):
        self._view_states = {name: {'zoom':1.,'center':(.5,.5)} for name in ('source','result')}
        self._pan_start = None
        self.request_preview_draw()

    def choose_save_directory(self):
        directory = filedialog.askdirectory(parent=self,title='保存先ディレクトリを選択',mustexist=True,
                                            initialdir=self.save_directory.get() or str(self.folder or APP_ROOT))
        if directory:
            self.save_directory.set(str(Path(directory).resolve()))
            self.add_log(f'保存先ディレクトリを選択: {self.save_directory.get()}')

    def update_save_merge_controls(self):
        active = self._tiff_pages>1 and self.save_format.get() in ('PNG','JPEG')
        if active:
            self.save_merge_box.pack(fill='x',pady=6,before=self.save_movements_check)
        else: self.save_merge_box.pack_forget()
        enabled = active and self.save_images.get() and not self._saving_busy
        for widget in self.save_merge_radios: widget.state(['!disabled'] if enabled else ['disabled'])
        for widget in self.save_channel_checks:
            widget.state(['!disabled'] if enabled and self.save_merge_mode.get()=='selected' else ['disabled'])

    def start_saving(self):
        if self._saving_busy or self._loading_busy or self._alignment_busy or self._raw_busy:
            return
        if not self._analysis_settings or not self.alignment_matrices:
            self.save_status.set('アライン実行後に保存してください。')
            return
        paths = tuple(file.path for file in self.analysis_files)
        automatic = [m.copy() for m in self._automatic_matrices]
        final = [m.copy() for m in self.alignment_matrices]
        records = copy.deepcopy(self.alignment_records)
        settings = copy.deepcopy(self._analysis_settings)
        settings['_workspace']=self.workspace_snapshot()
        manual_events = copy.deepcopy(self._manual_events)
        canvas = tuple(self.alignment_canvas)
        directory,name = self.save_directory.get(),self.save_name.get()
        create_folder,images,table = self.save_create_folder.get(),self.save_images.get(),self.save_movements.get()
        image_format = self.save_format.get()
        if not directory.strip():
            self.save_status.set('保存先ディレクトリを選択してください。')
            return
        overwrite = False
        merge_mode = self.save_merge_mode.get() if self._tiff_pages>1 else 'separate'
        selected_channels = tuple(i for i,var in enumerate(self.save_channel_vars) if var.get())
        channel_display = None
        if images and image_format in ('PNG','JPEG') and self._tiff_pages>1:
            if merge_mode=='selected' and not selected_channels:
                self.save_status.set('合成するチャンネルを1つ以上選択してください。'); return
            self.tiff_view.ensure()
            for page in range(self._tiff_pages): self.tiff_view.image('source',page)
            channel_display = [{'color':s['color'].get(),'limits':tuple(s['limits'])} for s in self.tiff_view.channels]
        try:
            validate_name(name)
            destination = Path(directory).expanduser().resolve(strict=True)
            if not destination.is_dir(): raise ValueError('保存先ディレクトリを選択してください。')
            target = destination/name
            if create_folder and target.exists():
                if not target.is_dir() or target.is_symlink() or target.resolve().parent!=destination:
                    raise ValueError('保存先と同名のファイル、または使用できないフォルダがあります。')
                overwrite = messagebox.askyesno('上書き確認',f'同じ名前のフォルダが存在します。\n\n{target}\n\n同名の出力ファイルを上書きしますか？\nその他のファイルは残します。',parent=self,default='no')
                if not overwrite:
                    self.save_status.set('上書きをキャンセルしました。'); return
        except (OSError,ValueError) as error:
            self.save_status.set(f'保存エラー: {error}'); return
        self._saving_busy = True
        self._saving_cancel.clear()
        self._save_controls = [self.save_button,self.save_directory_entry,self.save_name_entry,self.save_browse_button,
                               self.save_create_folder_check,self.save_images_check,self.save_movements_check,self.save_format_combo,
                               *self.save_merge_radios,*self.save_channel_checks]
        for widget in self._save_controls: widget.state(['disabled'])
        dialog = tk.Toplevel(self)
        self._saving_dialog = dialog
        dialog.title('保存中')
        dialog.transient(self)
        dialog.resizable(False,False)
        center_dialog(self,dialog,480,170)
        self._saving_text = tk.StringVar(value='保存データを準備しています…')
        self._saving_percent = tk.StringVar(value='')
        ttk.Label(dialog,textvariable=self._saving_text,wraplength=440).pack(fill='x',padx=18,pady=(18,10))
        self._saving_bar = ttk.Progressbar(dialog,maximum=100,mode='indeterminate')
        self._saving_bar.pack(fill='x',padx=18)
        self._saving_bar.start(15)
        row = ttk.Frame(dialog)
        row.pack(fill='x',padx=18,pady=12)
        ttk.Label(row,textvariable=self._saving_percent).pack(side='left')
        ttk.Button(row,text='中止',command=self.cancel_saving).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW',self.cancel_saving)
        dialog.grab_set()
        events,cancel = self._saving_events,self._saving_cancel
        def worker():
            try:
                result = export_data(paths,automatic,final,records,canvas,settings,manual_events,directory,name,
                    create_folder,images,table,cancel,
                    lambda number,total,text:events.put(('progress',number,total,text)),image_format=image_format,
                    merge_mode=merge_mode,selected_channels=selected_channels,channel_display=channel_display,overwrite=overwrite)
                events.put(('done',str(result)))
            except Cancelled:
                events.put(('cancelled',None))
            except Exception as exc:
                events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
        self._saving_poll_job = self.after(50,self.poll_saving)

    def cancel_saving(self):
        self._saving_cancel.set()
        self._saving_text.set('中止しています…')

    def poll_saving(self):
        self._saving_poll_job = None
        try:
            while True:
                event = self._saving_events.get_nowait()
                kind = event[0]
                if kind=='progress':
                    number,total,text = event[1:]
                    self._saving_bar.stop()
                    self._saving_bar.configure(mode='determinate',value=100*number/max(1,total))
                    self._saving_text.set(text)
                    self._saving_percent.set(f'{number} / {total} 枚')
                    continue
                self._saving_bar.stop()
                self._saving_dialog.grab_release()
                self._saving_dialog.destroy()
                self._saving_dialog = None
                self._saving_busy = False
                for widget in self._save_controls: widget.state(['!disabled'])
                self.save_format_combo.state(['readonly'])
                self.update_save_merge_controls()
                if kind=='done': self.save_status.set(f'保存完了: {event[1]}')
                elif kind=='cancelled': self.save_status.set('保存を中止しました。')
                else: self.save_status.set(f'保存エラー: {event[1]}')
                self.add_log(self.save_status.get())
                if self._close_after_saving: self.close()
                return
        except queue.Empty:
            pass
        self._saving_poll_job = self.after(50,self.poll_saving)

    def preview_sidebar(self):
        if self.tiff_view.active and self.tiff_view.focus_page is not None and not self.tiff_view.merged:
            width=min(self.preview_canvas.winfo_width(),self.result_canvas.winfo_width())
            return min(340,max(0,width-140))
        return 0

    def highlight_reference(self):
        settings=self._analysis_settings or {}
        if not self._preview_is_empty and settings.get('scope')=='Reference' and settings.get('reference_image_index')==self.preview_index+1:
            canvas=self.result_canvas
            canvas.create_rectangle(2,2,max(3,canvas.winfo_width()-3),max(3,canvas.winfo_height()-3),outline='#ffff00',width=3,tags='reference-image')

    def draw_view(self,canvas,image,name,box=None):
        cw,ch = max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        sw,sh = image.size
        state = self._view_states[name]
        scale = self.common_view_fit(image)*state['zoom']
        if box is not None:
            bl,bt,br,bb=box
            x0,y0=(bl+br)/2-state['center'][0]*sw*scale,(bt+bb)/2-state['center'][1]*sh*scale
            left,top=max(math.ceil(bl),math.floor(x0)),max(math.ceil(bt),math.floor(y0))
            right,bottom=min(math.floor(br),math.ceil(x0+sw*scale)),min(math.floor(bb),math.ceil(y0+sh*scale))
            if right<=left or bottom<=top:return None
            with image.transform((right-left,bottom-top),Image.Transform.AFFINE,(1/scale,0,(left-x0)/scale,0,1/scale,(top-y0)/scale),Image.Resampling.BILINEAR) as tile:
                photo=ImageTk.PhotoImage(tile,master=self)
            canvas.create_image(left,top,image=photo,anchor='nw')
            spacing=xy_spacing(self)
            ppm=scale*sw/self.display_canvas_size()[0]/spacing[0] if spacing else None
            horizontal_bar(canvas,self.analysis_scale,ppm,(left,top,right,bottom))
            return photo
        sidebar=self.preview_sidebar()
        x0,y0 = sidebar+(cw-sidebar)/2-state['center'][0]*sw*scale,ch/2-state['center'][1]*sh*scale
        shared_w = max(1,min(self.preview_canvas.winfo_width(),self.result_canvas.winfo_width())-sidebar)
        shared_h = max(1,min(self.preview_canvas.winfo_height(),self.result_canvas.winfo_height()))
        left,top = max(math.ceil(sidebar+(cw-sidebar-shared_w)/2),math.floor(x0)),max(math.ceil((ch-shared_h)/2),math.floor(y0))
        right,bottom = min(math.floor(sidebar+(cw-sidebar+shared_w)/2),math.ceil(x0+sw*scale)),min(math.floor((ch+shared_h)/2),math.ceil(y0+sh*scale))
        if right<=left or bottom<=top:
            return None
        # Render only visible pixels, so large zooms never allocate huge images.
        resized = image.transform((right-left,bottom-top),Image.Transform.AFFINE,
                                  (1/scale,0,(left-x0)/scale,0,1/scale,(top-y0)/scale),Image.Resampling.BILINEAR)
        photo = ImageTk.PhotoImage(resized,master=self)
        resized.close()
        canvas.create_image(left,top,image=photo,anchor='nw')
        canvas.create_text(8,8,anchor='nw',text=f"{state['zoom']:.2f}×",fill='#56d8d2')
        spacing=xy_spacing(self)
        pixels_per_um=scale*image.width/self.display_canvas_size()[0]/spacing[0] if spacing else None
        horizontal_bar(canvas,self.analysis_scale,pixels_per_um,(left,top,right,bottom))
        return photo

    def jump_preview(self):
        if not self.analysis_files:
            return
        try:
            number = self.preview_number.get()
        except tk.TclError:
            self.preview_number.set(self._preview_slot+1)
            return
        self.show_preview(number-1)

    def preview_key(self, event, delta):
        if self._raw_busy:return 'break'
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, ttk.Radiobutton)):
            return
        if self.tabs.select()==str(self.stack_tab):
            self.stack_panel.show_page(self.stack_panel._display_slot+delta)
            return 'break'
        if self.tabs.select()!=str(self.analysis_tab):
            return
        target = self._preview_slot+delta
        if 0 <= target < self.preview_slot_count():
            self.show_preview(target)
        return 'break'

    def request_preview_draw(self):
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(16, self.draw_preview)

    def draw_preview(self):
        self._preview_job = None
        if self.large_raw_requested() and not self._raw_busy:self.prepare_large_raw()
        if self._preview_is_empty or getattr(self,'_raw_busy',False):
            for canvas in (self.preview_canvas,self.result_canvas):
                canvas.delete('all');canvas.create_text(max(1,canvas.winfo_width())/2,max(1,canvas.winfo_height())/2,text='no image' if self._preview_is_empty else '生データを準備しています…',fill='white')
            return
        if self.analysis_files and self.tiff_view.draw():
            self.highlight_reference();return
        for frame,label,name in ((self.preview_frame,'画像プレビュー','source'),
                                 (self.analysis_lower,'アライン後','result')):
            image = self.view_image(name)
            if image is None:
                detail = '表示用サイズ: —'
            else:
                mode = '元解像度' if self.raw_display.get() else '表示用に縮小'
                detail = f'{mode}: {image.width} × {image.height} px'
            frame.configure(text=f'{label}（{detail}）')
        self.result_canvas.delete('all')
        rw,rh = max(1,self.result_canvas.winfo_width()),max(1,self.result_canvas.winfo_height())
        if self.view_image('result') is not None:
            self._aligned_photo = self.draw_view(self.result_canvas,self.view_image('result'),'result')
        else:
            self.result_canvas.create_text(rw/2,rh/2,text='アライン結果がここに表示されます。',fill='white',width=max(1,rw-20))
        self.preview_canvas.delete('all')
        width = max(1, self.preview_canvas.winfo_width())
        height = max(1, self.preview_canvas.winfo_height())
        if self._preview_display is None:
            text = self._preview_error or 'dataタブで画像を選択し、Go analyze を押してください。'
            self.preview_canvas.create_text(width/2, height/2, text=text, fill='white', width=max(1,width-20), justify='center')
            return
        image = self.view_image('source') or self._preview_display
        self._preview_photo = self.draw_view(self.preview_canvas,image,'source')
        self.highlight_reference()

    def add_log(self, message: str, level=None):
        timestamp = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        error = level=='error' or (level is None and bool(re.search(r'エラー|失敗|できません|読み込めません|error|exception',message,re.IGNORECASE)))
        self.log_table.insert('', 0, values=('', timestamp, message),tags=('error',) if error else ())
        rows = self.log_table.get_children()
        for number, row in enumerate(rows[:1000], 1):
            self.log_table.set(row, 'number', number)
        for row in rows[1000:]:
            self.log_table.delete(row)
        self.log_table.yview_moveto(0)
        if self._log_handler is not None:
            (self._logger.error if error else self._logger.info)('%s  %s', timestamp, message)

    def callback_error(self, kind, value, traceback):
        message = f'操作エラー: {value}'
        self.status_text.set(message)
        self.add_log(message)

    def close(self):
        if self.stack_panel.busy:
            self.stack_panel._close_after = True
            self.stack_panel.cancel()
            return
        if self.volume_panel.busy:
            self.volume_panel._close_after = True
            self.volume_panel.cancel()
            return
        if self._saving_busy:
            self._close_after_saving = True
            self._saving_cancel.set()
            return
        if self._loading_busy:
            self._close_after_loading = True
            self._loading_cancel.set()
            return
        self._alignment_cancel.set()
        self.close_raw_preview()
        self.close_alignment_progress()
        self.clear_raw_images()
        if self._alignment_poll_job is not None:
            self.after_cancel(self._alignment_poll_job)
        self.clear_aligned_image()
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        if self._preview_original is not None:
            self._preview_original.close()
        if self._preview_display is not None:
            self._preview_display.close()
        if self._preview_cache is not None:
            self._preview_cache.close()
        self.volume_panel.close()
        self.stack_panel.close()
        self.appearance.close()
        for drop in self._folder_drops:drop.close()
        self.tiff_view.reset()
        self.add_log('BRAINを終了します。')
        if self._log_handler is not None:
            self._logger.removeHandler(self._log_handler)
            self._log_handler.close()
        self.destroy()

    def choose_folder(self):
        if self._alignment_busy or self._loading_busy or self._raw_busy:
            return
        folder = filedialog.askdirectory(parent=self, title='PNG / JPEG / TIFF画像のフォルダを選択', mustexist=True,
                                         initialdir=str(self.folder) if self.folder else str(APP_ROOT))
        if folder:
            self.select_folder(Path(folder))
        else:
            self.add_log('フォルダの選択をキャンセルしました。')

    def register_folder_drop(self,widget,callback):
        def deliver(path):
            if self.order_busy():
                self.status_text.set('処理が終わってからフォルダをドロップしてください。');return
            callback(path)
        try:self._folder_drops.append(FolderDrop(widget,deliver,lambda text:self.add_log('フォルダドロップ: '+text,level='error')))
        except OSError as error:
            # Keep folder selection available if Windows rejects a native hook.
            self.after_idle(lambda error=error:self.add_log('フォルダドロップを開始できません: '+str(error),level='error'))

    def order_busy(self):
        return any((self._loading_busy,self._alignment_busy,self._saving_busy,
                    getattr(self,'_raw_busy',False),getattr(getattr(self,'stack_panel',None),'busy',False),
                    getattr(getattr(self,'volume_panel',None),'busy',False)))

    def refresh_order_buttons(self):
        rows = self.file_list.get_children()
        selected = set(self.file_list.selection())
        up = any(row in selected and i>0 and rows[i-1] not in selected for i,row in enumerate(rows))
        down = any(row in selected and i+1<len(rows) and rows[i+1] not in selected for i,row in enumerate(rows))
        enabled = {'first':up,'up':up,'down':down,'last':down,'reverse':len(rows)>1,
                   'natural':bool(self.images) and self.images!=tuple(sorted(self.images,key=lambda item:natural_key(item.path.name)))}
        for action,button in self.order_buttons.items():
            button.state(['!disabled'] if enabled[action] and not self.order_busy() else ['disabled'])
        for button in getattr(self,'use_buttons',[]):button.state(['!disabled'] if selected and not self.order_busy() else ['disabled'])

    def data_sequence(self):return tuple(self._file_rows[row] for row in self.file_list.get_children())

    def change_exclusion_mode(self):
        if self.order_busy():
            self.ignore_excluded.set(self._pending_ignore_excluded);return
        self._pending_ignore_excluded=self.ignore_excluded.get()
        self.status_text.set('除外画像の扱いを変更しました。Go analyzeで反映します。')
        self.add_log('除外画像を完全に無視: '+('ON' if self.ignore_excluded.get() else 'OFF')+'。Go analyzeで確定します。')

    def active_images(self):return tuple(image for image in self.data_sequence() if image is not None and image.path not in self._excluded_paths)

    def add_blank(self):
        if self.order_busy():return
        selected=self.file_list.selection()
        row=self.file_list.focus() or (selected[0] if selected else '')
        if not row:
            self.status_text.set('空白を追加する位置の画像を選択してください。');return
        position=self.file_list.index(row)
        blank=self.file_list.insert('',position,values=('', 'no image（空行）','—','—'),tags=('excluded',))
        self._file_rows[blank]=None
        self.renumber_rows();self.refresh_usage_rows();self.refresh_order_buttons()
        self.status_text.set('選択画像の1つ上に空白を追加しました。Go analyzeで反映します。')
        self.add_log(self.status_text.get())

    def remove_blanks(self):
        if self.order_busy():return
        for row in self.file_list.selection():
            if self._file_rows[row] is None:self.file_list.delete(row);del self._file_rows[row]
        self.renumber_rows();self.refresh_usage_rows();self.refresh_order_buttons()

    def renumber_rows(self):
        for number,row in enumerate(self.file_list.get_children(),1):self.file_list.set(row,'number',number)

    def preview_slot_count(self):return self._analysis_total_slices or len(self.analysis_files)

    def refresh_usage_rows(self):
        for row,image in self._file_rows.items():
            excluded=image is None or image.path in self._excluded_paths
            self.file_list.set(row,'use','—' if image is None else '☐' if excluded else '☑')
            self.file_list.item(row,tags=('excluded',) if excluded else ())
        self.file_list.tag_configure('excluded',foreground='#888888')
        count=len(self.active_images())
        self.count_text.set(f'画像 {len(self.images)} 枚 · 解析に使用 {count} 枚')
        self.go_button.state(['!disabled'] if count>=2 and not self.order_busy() else ['disabled'])

    def use_selection(self,include,rows=None):
        if self.order_busy():return
        for row in (self.file_list.selection() if rows is None else rows):
            if self._file_rows[row] is None:continue
            path=self._file_rows[row].path
            if include:self._excluded_paths.discard(path)
            else:self._excluded_paths.add(path)
        self.refresh_usage_rows()
        self.status_text.set('解析への使用を変更しました。Go analyzeで反映します。')
        self.add_log(f'解析に使用: {len(self.active_images())} / {len(self.images)}枚。Go analyzeで確定します。')

    def apply_image_order(self,rows,selected=None):
        rows = tuple(rows)
        previous = self.file_list.get_children()
        if self.order_busy() or rows==previous: return
        if len(rows)!=len(previous) or set(rows)!=set(previous): raise ValueError('画像一覧の順序が不正です')
        selected = tuple(self.file_list.selection()) if selected is None else tuple(selected)
        self.images = tuple(self._file_rows[row] for row in rows if self._file_rows[row] is not None)
        for number,row in enumerate(rows,1):
            self.file_list.move(row,'',number-1)
            self.file_list.set(row,'number',number)
        self.file_list.selection_set(selected)
        if selected:
            self.file_list.focus(selected[0]); self.file_list.see(selected[0])
        self.refresh_order_buttons()
        self.status_text.set('順序を変更しました。Go analyzeで反映します。'+(' アラインはやり直しになります。' if self.analysis_files else ''))
        self.refresh_usage_rows()
        self.add_log(f'画像の順序を変更しました（{len(rows)}枚）。Go analyzeで確定します。')

    def reorder_images(self,action):
        if self.order_busy(): return 'break'
        rows = list(self.file_list.get_children()); selected = set(self.file_list.selection())
        if action=='reverse': rows.reverse()
        elif action=='natural':
            real=iter(sorted((row for row in rows if self._file_rows[row] is not None),key=lambda row:natural_key(self._file_rows[row].path.name)))
            rows=[row if self._file_rows[row] is None else next(real) for row in rows]
        elif action in ('first','last'):
            moving = [row for row in rows if row in selected]
            remaining = [row for row in rows if row not in selected]
            rows = moving+remaining if action=='first' else remaining+moving
        elif action in ('up','down'):
            positions = range(1,len(rows)) if action=='up' else range(len(rows)-2,-1,-1)
            step = -1 if action=='up' else 1
            for i in positions:
                if rows[i] in selected and rows[i+step] not in selected: rows[i],rows[i+step] = rows[i+step],rows[i]
        self.apply_image_order(rows)
        return 'break'

    def begin_file_drag(self,event):
        if self.file_list.identify_column(event.x)=='#1':
            row=self.file_list.identify_row(event.y)
            if row and self._file_rows[row] is not None:self.use_selection(self._file_rows[row].path in self._excluded_paths,(row,))
            return 'break'
        self._file_drag = None
        if self.order_busy(): return 'break'
        row = self.file_list.identify_row(event.y)
        if not row or event.state&5: return
        selected = self.file_list.selection()
        moving = tuple(item for item in self.file_list.get_children() if item in selected) if row in selected else (row,)
        self._file_drag = {'y':event.y,'rows':moving,'clicked':row,'dragged':False,'target':None}
        if row in selected and len(selected)>1:
            self.file_list.focus_set(); return 'break'

    def clear_drop_target(self):
        for row in self.file_list.tag_has('drop-target'):
            self.file_list.item(row,tags=tuple(tag for tag in self.file_list.item(row,'tags') if tag!='drop-target'))

    def move_file_drag(self,event):
        drag = self._file_drag
        if drag is None or self.order_busy(): return
        if not drag['dragged'] and abs(event.y-drag['y'])<4: return 'break'
        drag['dragged'] = True
        height = self.file_list.winfo_height()
        if event.y<30: self.file_list.yview_scroll(-1,'units')
        elif event.y>height-20: self.file_list.yview_scroll(1,'units')
        row = self.file_list.identify_row(event.y)
        if row:
            box = self.file_list.bbox(row)
            drag['target'] = (row,event.y>=box[1]+box[3]/2)
        else: drag['target'] = (None,event.y>=30)
        self.clear_drop_target()
        if row: self.file_list.item(row,tags=('drop-target',))
        return 'break'

    def end_file_drag(self,event):
        drag,self._file_drag = self._file_drag,None
        self.clear_drop_target()
        if drag is None: return
        if not drag['dragged']:
            self.file_list.selection_set(drag['clicked']); return
        if self.order_busy() or drag['target'] is None or not 0<=event.x<=self.file_list.winfo_width(): return 'break'
        rows = list(self.file_list.get_children()); moving = set(drag['rows'])
        target,after = drag['target']
        position = rows.index(target)+int(after) if target is not None else len(rows) if after else 0
        insert = sum(row not in moving for row in rows[:position])
        rest = [row for row in rows if row not in moving]
        self.apply_image_order(rest[:insert]+list(drag['rows'])+rest[insert:],drag['rows'])
        return 'break'

    def load_folder(self, folder: Path):
        if self._alignment_busy or self._loading_busy or self._raw_busy:
            return
        self._loading_busy = True
        self._loading_cancel.clear()
        dialog = tk.Toplevel(self)
        self._loading_dialog = dialog
        dialog.title('データ読み込み')
        dialog.resizable(False,False)
        dialog.transient(self)
        center_dialog(self,dialog,480,170)
        self._loading_text = tk.StringVar(value='画像ファイルを確認しています…')
        self._loading_percent = tk.StringVar(value='')
        ttk.Label(dialog,textvariable=self._loading_text,wraplength=440).pack(fill='x',padx=18,pady=(18,10))
        self._loading_bar = ttk.Progressbar(dialog,maximum=100,mode='indeterminate')
        self._loading_bar.pack(fill='x',padx=18)
        self._loading_bar.start(15)
        row = ttk.Frame(dialog)
        row.pack(fill='x',padx=18,pady=12)
        ttk.Label(row,textvariable=self._loading_percent).pack(side='left')
        self._loading_cancel_button = ttk.Button(row,text='中止',command=self.cancel_loading)
        self._loading_cancel_button.pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW',self.cancel_loading)
        dialog.grab_set()
        events, cancel = self._loading_events, self._loading_cancel
        cache_root = APP_ROOT
        requested_images = tuple(self.images) if self.folder is not None and folder.resolve()==self.folder else None
        requested_selected = self.active_images() if requested_images is not None else None
        self._loading_ignore_excluded=self.ignore_excluded.get()
        resume_plan=self._resume_plan
        self._loaded_resume=None
        sequence=self.data_sequence()
        filtered=tuple(image for image in sequence if not self._loading_ignore_excluded or image is None or image.path not in self._excluded_paths)
        positions={image.path:i+1 for i,image in enumerate(filtered) if image is not None}
        resume_slots=tuple(positions[image.path] for image in requested_selected) if requested_selected else ()
        def worker():
            cache = None
            try:
                images = scan_folder(folder)
                if self.folder is not None and folder.resolve()==self.folder:
                    images=tuple(image for image in images if image.path not in self._resume_ignored_paths)
                if requested_images is not None and (resume_plan or set(images)==set(requested_images)): images = requested_selected
                calibration = detect_calibration(folder,[image.path for image in images])
                if len(images)>=2:
                    cache = PreviewCache(cache_root,[image.path for image in images])
                    for number in range(len(images)):
                        if cancel.is_set():
                            raise Cancelled()
                        cache.prepare(number)
                        events.put(('progress',number+1,len(images),images[number].path.name))
                    for i,image in enumerate(images):
                        if is_tiff(image.path) and cache.entries[i][3]: raise ValueError(cache.entries[i][3])
                    counts = [cache.page_counts.get(i,1) for i in range(len(images))]
                    if max(counts)>1 and (len(set(counts))!=1 or not all(is_tiff(image.path) for image in images)):
                        raise ValueError('複数ページTIFFは、全画像のチャンネル数とページ順を統一してください。PNG/JPEGとの混在はできません。')
                if cancel.is_set():
                    raise Cancelled()
                if resume_plan:
                    self._loaded_resume=workspace_state.prepare(resume_plan,[image.path for image in images],cache,resume_slots,len(filtered),cancel,lambda i,n,text:events.put(('progress',i,n,text)))
                events.put(('done',folder,images,cache,calibration))
                cache = None
            except Cancelled:
                events.put(('cancelled',))
            except Exception as exc:
                events.put(('error',str(exc)))
            finally:
                if cache is not None:
                    cache.close()
        threading.Thread(target=worker,daemon=True).start()
        self._loading_poll_job = self.after(30,self.poll_loading)

    def cancel_loading(self):
        self._loading_cancel.set()
        self._loading_text.set('中止しています…')
        self._loading_cancel_button.state(['disabled'])

    def poll_loading(self):
        self._loading_poll_job = None
        try:
            while True:
                event = self._loading_events.get_nowait()
                if event[0]=='progress':
                    _,number,total,name = event
                    self._loading_bar.stop()
                    self._loading_bar.configure(mode='determinate',value=number/total*100)
                    self._loading_text.set(name if self._resume_plan else f'表示用画像を準備中: {name}')
                    self._loading_percent.set(f'{number} / {total} 枚  ({number/total*100:.0f}%)')
                    continue
                self._loading_bar.stop()
                self._loading_dialog.grab_release()
                self._loading_dialog.destroy()
                self._loading_dialog = None
                self._loading_busy = False
                if event[0]=='done':
                    _,folder,images,cache,calibration = event
                    if self._loading_cancel.is_set() or self._close_after_loading:
                        if cache is not None: cache.close()
                        self.add_log('データ読み込みを中止しました。')
                    elif self.folder is not None and folder.resolve()==self.folder and images==self.active_images() and len(images)>=2:
                        self.finish_analyze_loading(cache,calibration,images,self._loading_ignore_excluded)
                        if self._loaded_resume:
                            try:workspace_state.apply(self,self._loaded_resume)
                            except (OSError,ValueError,KeyError,TypeError) as error:
                                self.status_text.set('復元エラー: '+str(error));self.add_log(self.status_text.get(),level='error')
                            self._loaded_resume=None
                    else:
                        if cache is not None: cache.close()
                        self.select_folder(folder,images)
                        self.status_text.set('フォルダの内容が変わりました。一覧を再確認してください。')
                elif event[0]=='cancelled':
                    self.add_log('データ読み込みを中止しました。')
                else:
                    self.status_text.set(f'読み込みエラー: {event[1]}')
                    self.add_log(self.status_text.get())
                if self._loaded_resume and self._loaded_resume.get('stack') is not None:self._loaded_resume['stack'].close()
                self._loaded_resume=None;self._resume_plan=None
                if self._close_after_loading:
                    self.close()
                return
        except queue.Empty:
            pass
        self._loading_poll_job = self.after(30,self.poll_loading)

    def select_folder(self, folder: Path, prepared_images=None, prepared_cache=None) -> bool:
        if self._alignment_busy or self._loading_busy or self._raw_busy:
            return False
        self.add_log(f'フォルダを選択: {folder}')
        # Clear the previous choice before scanning so stale data cannot proceed.
        self._resume_plan = None
        self._loaded_resume = None
        self._resume_ignored_paths=frozenset()
        self.folder = None
        self.images = self.analysis_files = ()
        self._excluded_paths.clear();self._analysis_slice_indices=();self._analysis_total_slices=0
        self._analysis_sequence=();self._preview_slot=0;self._preview_is_empty=False
        self._analysis_source_sequence=();self._analysis_data_indices=();self._analysis_omitted_paths=frozenset();self._analysis_ignore_excluded=False
        self.volume_panel.set_calibration(None,reset=True)
        self.alignment_data_text.set('解析対象は未確定です。')
        self.clear_preview()
        self.reset_alignment()
        if self._preview_cache is not None:
            self._preview_cache.close()
        self._preview_cache = prepared_cache
        self.go_button.state(['disabled'])
        self.file_list.delete(*self.file_list.get_children())
        self._file_rows.clear(); self._file_drag = None
        self.refresh_order_buttons()
        self.folder_text.set(str(folder))
        self.count_text.set('画像 0 枚')
        try:
            images = scan_folder(folder) if prepared_images is None else prepared_images
        except (OSError, ValueError) as exc:
            self.status_text.set(f'フォルダを読み込めません: {exc}')
            self.add_log(self.status_text.get())
            return False
        self.folder = folder.resolve()
        self.images = images
        calibration = detect_calibration(self.folder,[image.path for image in images])
        self.volume_panel.set_calibration(calibration,reset=True)
        self.add_log(calibration['message'].replace('\n',' / '))
        self.folder_text.set(str(self.folder))
        self.count_text.set(f'画像 {len(images)} 枚')
        self.add_log(f'画像一覧を読み込みました: {len(images)} 枚（PNG / JPEG / TIFF）')
        for index, image in enumerate(images, 1):
            row = self.file_list.insert('', 'end', values=(index, image.path.name, f'{image.size/1024:,.1f} KB','☑'))
            self._file_rows[row] = image
        self.refresh_order_buttons()
        if len(images) < 2:
            self.status_text.set('PNG / JPEG / TIFF画像が2枚以上必要です。別のフォルダを選択してください。')
            self.add_log(self.status_text.get())
            return False
        self.status_text.set('フォルダと画像一覧を確認し、問題なければ Go analyze を押してください。')
        self.go_button.state(['!disabled'])
        return True

    def workspace_snapshot(self):
        return workspace_state.snapshot(self)

    def go_analyze(self):
        if self._alignment_busy or self._loading_busy or self._raw_busy:
            return
        if self.folder is None or len(self.active_images()) < 2:
            return
        # Re-scan at confirmation time in case files changed after selection.
        try:
            current = tuple(image for image in scan_folder(self.folder) if image.path not in self._resume_ignored_paths)
        except (OSError, ValueError):
            self.select_folder(self.folder)
            return
        if set(current) != set(self.images):
            self.select_folder(self.folder)
            self.status_text.set('フォルダの内容が変わりました。一覧を再確認してください。')
            self.add_log(self.status_text.get())
            return
        self._resume_plan=None
        try:
            candidates=workspace_state.discover(self.folder)
            if candidates and messagebox.askyesno('保存済み解析の読み込み','メタデータを読んで解析済みデータを読み込みますか？\n\nはい：保存した状態を復元します。\nいいえ：画像から解析しなおします。',parent=self):
                self._resume_plan=workspace_state.make_plan(candidates,self.images)
                workspace_state.install_sequence(self,self._resume_plan)
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.status_text.set('メタデータ読み込みエラー: '+str(error));self.add_log(self.status_text.get(),level='error');return
        self.load_folder(self.folder)

    def finish_analyze_loading(self, cache, calibration=None,images=None,ignore_excluded=None):
        self.clear_preview()
        self.reset_alignment()
        if self._preview_cache is not None:
            self._preview_cache.close()
        self._preview_cache = cache
        self.analysis_files = self.active_images() if images is None else tuple(images)
        self._analysis_source_sequence=self.data_sequence()
        self._analysis_omitted_paths=frozenset(self._excluded_paths)
        self._analysis_ignore_excluded=self.ignore_excluded.get() if ignore_excluded is None else bool(ignore_excluded)
        self._analysis_sequence=tuple(image for image in self._analysis_source_sequence
            if not self._analysis_ignore_excluded or image is None or image.path not in self._analysis_omitted_paths)
        data_positions={image.path:i+1 for i,image in enumerate(self._analysis_source_sequence) if image is not None}
        self._analysis_data_indices=tuple(data_positions[image.path] for image in self.analysis_files)
        positions={image.path:i+1 for i,image in enumerate(self._analysis_sequence) if image is not None}
        self._analysis_slice_indices=tuple(positions[image.path] for image in self.analysis_files)
        self._analysis_total_slices=len(self._analysis_sequence)
        self._input_has_tiff = any(is_tiff(image.path) for image in self.analysis_files)
        self._tiff_pages = max(cache.page_counts.values(),default=1)
        self.alignment_channel.set('1')
        self.channel_combo.configure(values=[str(i+1) for i in range(self._tiff_pages)])
        self.color_box.configure(text='アライン基準チャンネル' if self._tiff_pages>1 else '色')
        self.color_combo.pack_forget(); self.channel_combo.pack_forget()
        (self.channel_combo if self._tiff_pages>1 else self.color_combo).pack(fill='x')
        self.save_format.set('TIFF' if self._input_has_tiff else 'PNG')
        self.save_format_combo.state(['!disabled','readonly'])
        self.save_images_check.configure(text='アライン後の画像（選択した形式）')
        for widget in self.save_channel_checks: widget.destroy()
        self.save_channel_vars = [tk.BooleanVar(value=True) for _ in range(self._tiff_pages)]
        self.save_channel_checks = []
        for page,var in enumerate(self.save_channel_vars):
            widget = ChannelCheck(self.save_channels_frame,color=lambda p=page:self.tiff_view.channels[p]['color'].get() if p<len(self.tiff_view.channels) else 'Gray',text=f'Ch {page+1}',variable=var)
            widget.grid(row=page//8,column=page%8,sticky='w',padx=6,pady=3)
            self.save_channel_checks.append(widget)
        self.update_save_merge_controls()
        if self._tiff_pages>1:
            self.stack_panel.select_folder(self.folder)
        if calibration is None:
            calibration = detect_calibration(self.folder,[image.path for image in self.analysis_files])
        self.volume_panel.set_calibration(calibration)
        self.add_log(calibration['message'].replace('\n',' / '))
        self.save_directory.set(str(self.folder.resolve()))
        self.raw_button.state(['!disabled'])
        self.position_reset_button.state(['!disabled'])
        self.merged_button.state(['!disabled'] if self._tiff_pages>1 else ['disabled'])
        self.align_button.state(['!disabled'])
        self.alignment_data_text.set(f'{self.folder}\n画像 {len(self.analysis_files)} 枚')
        self.status_text.set('解析対象を確定しました。')
        self.add_log(f'Go analyze: 解析対象 {len(self.analysis_files)} 枚を確定しました。')
        self.tabs.select(self.analysis_tab)
        self.show_preview(0)
        self.stack_panel.sync_analysis(force=True)
        self.add_log('alignタブを開きました。')


if __name__ == '__main__':
    app = BRAIN()
    if _startup_ready is not None:
        app.after_idle(lambda:_startup_ready.write_text('ready',encoding='utf-8'))
    app.mainloop()

# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Interactive desktop workspace for a first serial-section 3D workflow."""
from __future__ import annotations
import copy
import json
import csv
import queue
import threading
import time
from pathlib import Path
from app_paths import APP_ROOT, FUNCTION_ROOT
from ui_controls import center_dialog
from scale_bars import ScaleBarControls,horizontal_bar
from slider_controls import ArrowScale
from channel_widgets import ChannelCheck
import tkinter as tk
from tkinter import ttk, filedialog
import numpy as np
from PIL import Image, ImageTk
from alignment import Cancelled
from image_sources import is_tiff
from tiff_display import display_pixels
from volume3d import build_volume, project_image_stack, stack_geometry, save_volume, display_channels, image_plane, pick_stack_point,physical_distance
from view_cube import ViewCube


AP_ORDERS = {'未指定（1枚目 → 最後）':'unspecified','前 → 後（A → P）':'A_to_P','後 → 前（P → A）':'P_to_A'}
STACK_DISPLAY_SPACING = 5.


class VolumePanel(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent)
        self.app = app
        self.volume = None
        self.busy = False
        self._cancel = threading.Event()
        self._events = queue.Queue()
        self._poll_job = self._draw_job = None
        self._dialog = None
        self._close_after = False
        self._photos = {}
        self._drag = None
        self._drag_moved = False
        self._picked = None
        self._slice_selected=False
        self._plane_regions = {}
        self.section_checks = []
        self.channel_checks = []
        self.channel_opacities=[];self.channel_opacity_labels=[]
        self.highlight_section=tk.BooleanVar(value=False)
        self.cut_count=tk.DoubleVar(value=0.)
        self.cut_text=tk.StringVar(value='0 / 0 枚を非表示')
        self.cut_side=tk.StringVar(value='A側から切片を隠す')
        self.selected_section_info=tk.StringVar(value='切片未選択')
        self.measure_mode=tk.BooleanVar(value=False)
        self.measure_text=tk.StringVar(value='測定をONにして、画像上の2点をクリックしてください。')
        self.measure_points=[]
        self.measurements=[]
        self._measurement_serial=0
        self._z_confirmed=False
        self.yaw,self.pitch,self.zoom = 55.,20.,1.
        self.pan = (0.,0.)
        self.use_raw = tk.BooleanVar(value=False)
        self.limit = tk.StringVar(value='256')
        self.shift_down = False
        self._shift_keys = set()
        self._shift_focus_job = None
        self._slice_wheel_remainder = {name:0. for name in ('XY','XZ','YZ')}
        self.spacing = [tk.DoubleVar(value=1.) for _ in range(3)]
        self.calibration = None
        self.calibration_info = tk.StringVar(value='画素サイズ情報は未確認。X・Y・Zの初期値1は仮値です。')
        self.ap_order = tk.StringVar(value='前 → 後（A → P）')
        self.opacity = tk.DoubleVar(value=15.)
        self.display_ap_spacing = tk.DoubleVar(value=1.)
        self.display_ap_spacing_text = tk.StringVar(value='1.00')
        self._display_ap_spacing = 1.
        self.scale_length=tk.DoubleVar(value=50.)
        self.scale_show_text=tk.BooleanVar(value=True)
        self._manual_xy_calibrated=False
        self._scale_manual=False
        self.black_transparent = tk.BooleanVar(value=True)
        self.opacity_text = tk.StringVar(value='15 %')
        self._render_cancel = threading.Event()
        self._render_events = queue.Queue()
        self._render_thread = None
        self._render_job = None
        self._render_key = None
        self._render_image = None
        self._render_view = None
        self.status = tk.StringVar(value='画像を読み込み、「3D構築」を押してください。')
        self.summary = tk.StringVar(value='ボリューム未作成')
        self.indices = {name:tk.DoubleVar(value=0.) for name in ('XY','XZ','YZ')}
        self.slice_labels = {name:tk.StringVar(value='—') for name in self.indices}
        self.columnconfigure(1,weight=1)
        self.rowconfigure(0,weight=1)
        settings = ttk.Frame(self)
        settings.grid(row=0,column=0,sticky='ns',padx=(0,12))
        self.settings_canvas = tk.Canvas(settings,width=305,height=1,highlightthickness=0)
        self.settings_canvas.pack(side='left',fill='both',expand=True)
        settings_scroll = ttk.Scrollbar(settings,orient='vertical',command=self.settings_canvas.yview)
        settings_scroll.pack(side='right',fill='y')
        self.settings_canvas.configure(yscrollcommand=settings_scroll.set)
        left = ttk.Frame(self.settings_canvas)
        settings_window = self.settings_canvas.create_window(0,0,window=left,anchor='nw',width=305)
        left.bind('<Configure>',lambda event:self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox('all')))
        self.settings_canvas.bind('<Configure>',lambda event:self.settings_canvas.itemconfigure(settings_window,width=event.width))
        ttk.Label(left,text='3D構築',style='Title.TLabel').pack(anchor='w',pady=(0,12))
        self._controls = []
        aligned = ttk.Checkbutton(left,text='アラインしていないデータを使用',variable=self.use_raw)
        aligned.pack(anchor='w',pady=6); self._controls.append(aligned)
        ttk.Label(left,text='計算用画像の最大辺').pack(anchor='w',pady=(8,3))
        limit = ttk.Combobox(left,textvariable=self.limit,values=['128','256','512'],state='readonly',width=23)
        limit.pack(fill='x'); self._controls.append(limit)
        ttk.Label(left,text='表示するチャンネルは3D画面内で選択します。',wraplength=245).pack(anchor='w',pady=8)
        spacing_box = ttk.LabelFrame(left,text='実際の間隔（µm）',padding=8)
        spacing_box.pack(fill='x',pady=12)
        self.spacing_entries = []
        for label,var in zip(['元画像1pxの幅 X','元画像1pxの高さ Y','AP切片間隔 Z'],self.spacing):
            row = ttk.Frame(spacing_box); row.pack(fill='x',pady=3)
            ttk.Label(row,text=label).pack(side='left')
            entry = ttk.Entry(row,textvariable=var,width=12)
            self.spacing_entries.append(entry)
            if len(self.spacing_entries)<=2:
                entry.bind('<Return>',self.manual_xy_calibration)
                entry.bind('<FocusOut>',self.manual_xy_calibration)
            else:
                entry.bind('<Return>',self.confirm_z_spacing)
                entry.bind('<FocusOut>',self.confirm_z_spacing)
            entry.pack(side='right'); self._controls.append(entry)
        ttk.Label(spacing_box,textvariable=self.calibration_info,wraplength=245).pack(anchor='w',pady=(8,0))
        self.build_button = ttk.Button(left,text='3D構築',command=self.start_build)
        self.build_button.pack(fill='x',pady=4); self._controls.append(self.build_button)
        display = ttk.LabelFrame(left,text='3Dプレビュー',padding=8)
        display.pack(fill='x',pady=12)
        ttk.Label(display,text='画像の並びと前後方向（AP）').pack(anchor='w')
        self.ap_combo = ttk.Combobox(display,textvariable=self.ap_order,values=list(AP_ORDERS),state='readonly',width=24)
        self.ap_combo.pack(fill='x',pady=3); self._controls.append(self.ap_combo)
        self.ap_combo.bind('<<ComboboxSelected>>',lambda event:self.change_ap_order())
        ttk.Label(display,text='AP方向の表示間隔（標準 = 1）').pack(anchor='w',pady=(8,0))
        spacing_row = ttk.Frame(display); spacing_row.pack(fill='x')
        self.display_ap_spacing_slider = ArrowScale(spacing_row,from_=.1,to=10.,
            variable=self.display_ap_spacing,command=self.set_display_ap_spacing,takefocus=True)
        self.display_ap_spacing_slider.pack(side='left',fill='x',expand=True)
        self._controls.append(self.display_ap_spacing_slider)
        self.display_ap_spacing_entry = ttk.Entry(spacing_row,textvariable=self.display_ap_spacing_text,width=6)
        self.display_ap_spacing_entry.pack(side='left',padx=4)
        self._controls.append(self.display_ap_spacing_entry)
        self.display_ap_spacing_entry.bind('<Return>',lambda event:self.set_display_ap_spacing())
        self.display_ap_spacing_entry.bind('<FocusOut>',lambda event:self.set_display_ap_spacing())
        self.display_ap_spacing_reset = ttk.Button(spacing_row,text='戻す',width=5,command=lambda:self.set_display_ap_spacing(1.))
        self.display_ap_spacing_reset.pack(side='left'); self._controls.append(self.display_ap_spacing_reset)
        for key in ('Left','Right','Up','Down'):
            self.display_ap_spacing_slider.bind('<KeyPress-'+key+'>',self.step_display_ap_spacing)
        self.display_ap_spacing_slider.bind('<MouseWheel>',self.step_display_ap_spacing)
        ttk.Label(display,text='左：狭く / 右：広く（実際のZは保持）').pack(anchor='w')
        ttk.Label(display,text='画像の不透明度').pack(anchor='w',pady=(8,0))
        slider = ArrowScale(display,from_=1,to=100,variable=self.opacity,command=lambda value:self.request_draw())
        slider.pack(fill='x'); self._controls.append(slider)
        ttk.Label(display,textvariable=self.opacity_text).pack(anchor='w')
        ttk.Label(display,text='チャンネル別の不透明度').pack(anchor='w',pady=(8,0))
        self.channel_opacity_box=ttk.Frame(display);self.channel_opacity_box.pack(fill='x')
        ttk.Checkbutton(display,text='選択切片を強調（ほかを薄く表示）',variable=self.highlight_section,command=self.request_draw).pack(anchor='w',pady=(8,0))
        ttk.Label(display,textvariable=self.selected_section_info,wraplength=275).pack(anchor='w',pady=3)
        ttk.Label(display,textvariable=self.cut_side).pack(anchor='w',pady=(8,0))
        self.cut_slider=ArrowScale(display,from_=0,to=1,variable=self.cut_count,command=lambda value:self.request_draw(),state='disabled',takefocus=True)
        self.cut_slider.pack(fill='x')
        cut_row=ttk.Frame(display);cut_row.pack(fill='x')
        ttk.Label(cut_row,textvariable=self.cut_text).pack(side='left')
        ttk.Button(cut_row,text='すべて戻す',command=self.reset_cut).pack(side='right')
        for key in ('Left','Right','Up','Down'):
            self.cut_slider.bind('<KeyPress-'+key+'>',self.step_cut)
        ttk.Label(display,text='スケールバー長（µm / ML・DV・AP）').pack(anchor='w',pady=(8,0))
        scale_row=ttk.Frame(display);scale_row.pack(fill='x',pady=3)
        self.scale_entry=ttk.Entry(scale_row,textvariable=self.scale_length,width=16)
        self.scale_entry.pack(side='left',fill='x',expand=True)
        ttk.Checkbutton(scale_row,text='文字',variable=self.scale_show_text,command=self.request_draw).pack(side='left',padx=4)
        self.scale_entry.bind('<Return>',self.set_scale_length)
        self.scale_entry.bind('<FocusOut>',self.set_scale_length)

        transparent = ttk.Checkbutton(display,text='黒背景を透過（濃淡に応じて透過）',variable=self.black_transparent,command=self.request_draw)
        transparent.pack(anchor='w',pady=3); self._controls.append(transparent)
        ttk.Button(display,text='3D表示をリセット',command=self.reset_view).pack(fill='x',pady=4)
        ttk.Label(display,text='AP = 前後軸（Z・切片を積む方向）\n初期表示: 左手前 A → 右奥 P\nドラッグ: 平面移動\nShift＋ドラッグ: 360°回転\nホイール: 拡大縮小',wraplength=245).pack(anchor='w',pady=(8,0))
        self.save_button = ttk.Button(left,text='3Dデータを保存（NPZ）',command=self.save_as,state='disabled')
        measure=ttk.LabelFrame(left,text='距離測定（実際の間隔で計算）',padding=8)
        measure.pack(fill='x',pady=(0,8))
        ttk.Checkbutton(measure,text='測定 ON（画像上の2点を指定）',variable=self.measure_mode,command=self.request_draw).pack(anchor='w')
        ttk.Label(measure,textvariable=self.measure_text,wraplength=275).pack(anchor='w',pady=5)
        ttk.Button(measure,text='測定中の点を消去',command=self.clear_measurement).pack(fill='x')
        self.measurement_list=ttk.Frame(measure);self.measurement_list.pack(fill='x',pady=5)
        ttk.Button(measure,text='距離一覧を保存（CSV / JSON）',command=self.save_measurements).pack(fill='x')
        ttk.Button(measure,text='距離一覧をすべて消去',command=self.reset_measurements).pack(fill='x',pady=3)
        ttk.Label(measure,text='3D・XY・X×AP・Y×APから指定可能。\n3点目を押すと新しい測定を開始します。',wraplength=275).pack(anchor='w',pady=4)
        self.save_button.pack(fill='x',pady=4); self._controls.append(self.save_button)
        ttk.Label(left,textvariable=self.summary,wraplength=245,justify='left').pack(fill='x',pady=10)
        ttk.Label(left,textvariable=self.status,wraplength=245,justify='left').pack(fill='x',pady=4)

        right = ttk.Frame(self)
        right.grid(row=0,column=1,sticky='nsew')
        for i in (0,1): right.columnconfigure(i,weight=1,uniform='volcols'); right.rowconfigure(i,weight=1,uniform='volrows')
        self.canvases,self.sliders = {},{}
        self.plane_scales={}
        for name,title,row,col in [('3D','3D画像スタック — 前後軸 AP（Z）',0,0),('XY','冠状断（XY）— AP位置を選択',0,1),('XZ','AP縦断（X × AP）',1,0),('YZ','AP縦断（Y × AP）',1,1)]:
            frame = ttk.LabelFrame(right,text=title,padding=4)
            frame.grid(row=row,column=col,sticky='nsew',padx=3,pady=3)
            frame.columnconfigure(0,weight=1); frame.rowconfigure(0,weight=1)
            if name!='3D':
                controls=ScaleBarControls(frame,self.request_draw,allow_color=False);controls.grid(row=0,column=0,sticky='e')
                self.plane_scales[name]=controls
                frame.rowconfigure(0,weight=0);frame.rowconfigure(1,weight=1)
            canvas = tk.Canvas(frame,background='#181e24',highlightthickness=0,width=200,height=140)
            canvas.image_view = True
            canvas.grid(row=1,column=0,sticky='nsew')
            if name=='3D':
                frame.rowconfigure(0,weight=0); frame.rowconfigure(1,weight=1)
                chooser = ttk.Frame(frame); chooser.grid(row=0,column=0,sticky='ew',pady=(0,4))
                chooser.columnconfigure(1,weight=1)
                ttk.Label(chooser,text='チャンネル').grid(row=0,column=0,sticky='w',padx=(0,5))
                self.channel_choices = ttk.Frame(chooser); self.channel_choices.grid(row=0,column=1,sticky='w')
                commands = ttk.Frame(chooser); commands.grid(row=1,column=0,sticky='nw')
                ttk.Label(commands,text='表示切片').pack(anchor='w')
                row_buttons = ttk.Frame(commands); row_buttons.pack()
                ttk.Button(row_buttons,text='全選択',width=6,command=lambda:self.select_sections(True)).pack(side='left')
                ttk.Button(row_buttons,text='解除',width=5,command=lambda:self.select_sections(False)).pack(side='left')
                section_area = ttk.Frame(chooser); section_area.grid(row=1,column=1,sticky='ew')
                section_area.columnconfigure(0,weight=1)
                self.section_canvas = tk.Canvas(section_area,height=25,highlightthickness=0)
                self.section_canvas.grid(row=0,column=0,sticky='ew')
                self.section_choices = ttk.Frame(self.section_canvas)
                self.section_canvas.create_window(0,0,window=self.section_choices,anchor='nw')
                self.section_choices.bind('<Configure>',lambda event:self.section_canvas.configure(scrollregion=self.section_canvas.bbox('all')))
                bar = ttk.Scrollbar(section_area,orient='horizontal',command=self.section_canvas.xview)
                bar.grid(row=1,column=0,sticky='ew'); self.section_canvas.configure(xscrollcommand=bar.set)
            canvas.bind('<Configure>',lambda event:self.request_draw())
            self.canvases[name] = canvas
            if name!='3D': canvas.bind('<ButtonPress-1>',lambda event,name=name:self.select_plane(event,name))
            if name!='3D':
                slider = ArrowScale(frame,from_=0,to=1,variable=self.indices[name],command=lambda value:self.select_slice_control(),state='disabled')
                slider.grid(row=2,column=0,sticky='ew')
                ttk.Label(frame,textvariable=self.slice_labels[name]).grid(row=3,column=0,sticky='w')
                self.sliders[name] = slider
                slider.configure(takefocus=True)
                slider.bind('<Button-1>',lambda event,widget=slider:widget.focus_set(),add='+')
                for key in ('Left','Right','Up','Down'):
                    slider.bind('<KeyPress-'+key+'>',lambda event,section=name:self.step_slice(event,section))
                slider.bind('<MouseWheel>',lambda event,section=name:self.step_slice(event,section,wheel=True))
                canvas.bind('<MouseWheel>',lambda event,section=name:self.step_slice(event,section,wheel=True))
                ttk.Label(frame,text='スライダーを選択: 矢印キー / ホイールで移動').grid(row=4,column=0,sticky='w')
        canvas = self.canvases['3D']
        canvas.bind('<ButtonPress-1>',self.press)
        canvas.bind('<ButtonRelease-1>',self.release)
        canvas.bind('<B1-Motion>',self.drag)
        canvas.bind('<MouseWheel>',self.wheel)
        canvas.bind('<Double-Button-1>',lambda event:self.reset_view())
        self.view_cube = ViewCube(canvas,self.set_view_angles)
        self.view_cube.place(relx=1.,x=-8,y=45,anchor='ne',width=150,height=150)
        self._shift_bindings = []
        for key in ('Shift_L','Shift_R'):
            self._shift_bindings.append(('<KeyPress-'+key+'>',self.app.bind('<KeyPress-'+key+'>',self.shift_pressed,add='+')))
            self._shift_bindings.append(('<KeyRelease-'+key+'>',self.app.bind('<KeyRelease-'+key+'>',self.shift_released,add='+')))
        self._shift_bindings.append(('<FocusOut>',self.app.bind('<FocusOut>',self.check_shift_focus,add='+')))
        self._tab_binding = self.app.tabs.bind('<<NotebookTabChanged>>',self.reset_shift_on_tab_change,add='+')
        def scroll_settings(event):
            if event.delta:self.settings_canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
            return 'break'
        def bind_settings_scroll(widget):
            if isinstance(widget,(ttk.Frame,ttk.Label,ttk.LabelFrame,tk.Canvas)):
                widget.bind('<MouseWheel>',scroll_settings,add='+')
            for child in widget.winfo_children():bind_settings_scroll(child)
        bind_settings_scroll(self.settings_canvas)
        self.request_draw()

    def step_slice(self,event,name,wheel=False):
        slider = self.sliders[name]
        if slider.instate(['disabled']) or self.volume is None: return 'break'
        if wheel and str(self.tk.call('focus'))!=str(slider): return 'break'
        if wheel:
            self._slice_wheel_remainder[name] += getattr(event,'delta',0)/120
            step = int(self._slice_wheel_remainder[name])
            self._slice_wheel_remainder[name] -= step
        else: step = 1 if event.keysym in ('Right','Up') else -1
        count = self.volume.data.shape[{'XY':0,'XZ':1,'YZ':2}[name]]
        self.indices[name].set(max(0,min(count-1,round(self.indices[name].get())+step)))
        self.select_slice_control()
        return 'break'

    def set_shift(self,pressed):
        if self.shift_down==pressed: return
        self.shift_down = pressed
        self.request_draw()

    def shift_pressed(self,event):
        if self.app.tabs.select()!=str(self.app.additional_tab): return
        self._shift_keys.add(event.keysym); self.set_shift(True)

    def shift_released(self,event):
        self._shift_keys.discard(event.keysym); self.set_shift(bool(self._shift_keys))

    def check_shift_focus(self,event):
        if self._shift_focus_job is not None: self.after_cancel(self._shift_focus_job)
        self._shift_focus_job = self.after_idle(self.reset_shift_if_unfocused)

    def reset_shift_if_unfocused(self):
        self._shift_focus_job = None
        focus = str(self.app.tk.call('focus'))
        if not focus or '.popdown' in focus:
            self._shift_keys.clear(); self.set_shift(False)

    def reset_shift_on_tab_change(self,event):
        if self.app.tabs.select()!=str(self.app.additional_tab):
            self._shift_keys.clear(); self.set_shift(False)

    def manual_xy_calibration(self,event=None):
        try:self._manual_xy_calibrated=all(np.isfinite(var.get()) and var.get()>0 for var in self.spacing[:2])
        except tk.TclError:self._manual_xy_calibrated=False
        self.app.request_preview_draw();self.app.stack_panel.request_draw()

    def confirm_z_spacing(self,event=None):
        try:self._z_confirmed=bool(np.isfinite(self.spacing[2].get()) and self.spacing[2].get()>0)
        except tk.TclError:self._z_confirmed=False

    def set_calibration(self,calibration,reset=False):
        if reset:self._manual_xy_calibrated=False
        self.calibration = copy.deepcopy(calibration)
        if reset:
            for var in self.spacing: var.set(1.)
            self._z_confirmed=False
        status = calibration['status'] if calibration else 'missing'
        for entry in self.spacing_entries[:2]:
            entry.state(['!disabled','!readonly'])
            if status in ('loaded','error'): entry.state(['readonly'])
        if status=='loaded':
            for var,value in zip(self.spacing,calibration['spacing_xy_um']): var.set(value)
        self.calibration_info.set(calibration['message'] if calibration else '画素サイズ情報は未確認。X・Y・Zの初期値1は仮値です。')

    def clear(self,message='画像を読み込み、「3D構築」を押してください。'):
        self.volume = None
        self.reset_measurements()
        self._plane_regions.clear()
        self._picked = None
        self._slice_selected=False
        self.configure_selection()
        self.reset_render()
        self._photos.clear()
        self.save_button.state(['disabled'])
        for slider in self.sliders.values(): slider.state(['disabled'])
        for text in self.slice_labels.values(): text.set('—')
        self.summary.set('ボリューム未作成')
        self.status.set(message)
        self.request_draw()

    def invalidate_aligned(self):
        if self.volume is not None and self.volume.params['aligned']:
            self.clear('アライン結果が変更されました。3D構築をやり直してください。')

    def start_build(self):
        if self.busy: return
        if self.app._raw_busy or self.app._alignment_busy or self.app._loading_busy or self.app._saving_busy or self.app.stack_panel.busy:
            self.status.set('現在の処理の完了を待ってください。'); return
        paths = tuple(file.path for file in self.app.analysis_files)
        if len(paths)<2:
            self.status.set('dataタブで画像を選び、Go analyzeを押してください。'); return
        try:
            if self.calibration and self.calibration['status']=='error':
                raise ValueError(self.calibration['message'])
            spacing = tuple(var.get() for var in self.spacing)
            if self.calibration and self.calibration['status']=='loaded':
                spacing = (*self.calibration['spacing_xy_um'],spacing[2])
            limit = int(self.limit.get())
            aligned = not self.use_raw.get()
            matrices = [m.copy() if m is not None else None for m in self.app.alignment_matrices] if aligned else None
            if aligned and (len(matrices)!=len(paths) or any(m is None for m in matrices)):
                raise ValueError('先にアラインを実行するか、「アラインしていないデータを使用」をONにしてください。')
            canvas = self.app.alignment_canvas if aligned else None
            provenance = {'input_folder':str(self.app.folder),
                          'alignment':copy.deepcopy(self.app._analysis_settings) if aligned else None,
                          'manual_events':copy.deepcopy(self.app._manual_events) if aligned else [],
                          'pixel_calibration':copy.deepcopy(self.calibration),
                          'measurement_calibration':{'xy_confirmed':bool(self._manual_xy_calibrated or (self.calibration or {}).get('status')=='loaded'),
                              'z_confirmed':bool(self._z_confirmed or spacing[2]!=1.)}}
            channels = None
            if all(is_tiff(path) for path in paths) and self.app._tiff_pages==1:
                channels = [{'index':0,'color':'Gray','limits':None}]
            if self.app._tiff_pages>1:
                self.app.tiff_view.ensure()
                channels = []
                for p,state in enumerate(self.app.tiff_view.channels):
                    limits = state['limits']
                    if not state['manual'] and state['index']!=self.app.preview_index:
                        limits = display_pixels(self.app._preview_cache.get_plane(self.app.preview_index,p))[1]
                    channels.append({'index':p,'color':state['color'].get(),'limits':tuple(limits)})
            ap_order = AP_ORDERS[self.ap_order.get()]
        except (ValueError,tk.TclError) as exc:
            self.status.set(str(exc)); return
        slots=tuple(self.app._analysis_slice_indices) or tuple(range(1,len(paths)+1))
        total=self.app._analysis_total_slices or len(paths)
        self.run_job('3D構築中',lambda cancel,progress:build_volume(paths,matrices,canvas,limit,spacing,'Gray',provenance,cancel,progress,ap_order=ap_order,channels=channels,slice_indices=slots,total_slices=total),self.built)

    def default_scale_length(self):
        if not self._scale_manual:
            try:self.scale_length.set(50.)
            except tk.TclError:pass

    def set_scale_length(self,event=None):
        try:
            value=self.scale_length.get()
            if not np.isfinite(value) or value<=0:raise ValueError()
            self._scale_manual=True
        except (ValueError,tk.TclError):
            self.scale_length.set(50.)
            self.status.set('スケールバーの長さには0より大きい値を入力してください。')
        self.request_draw()

    def change_ap_order(self):
        if self.volume is not None:
            self.volume.params['ap_order'] = AP_ORDERS[self.ap_order.get()]
            self.reset_render()
        self.reset_view()

    def built(self,volume):
        self.reset_render()
        self.volume = volume
        self.reset_measurements()
        if not self._scale_manual:self.scale_length.set(50.)
        self._picked = None
        self._slice_selected=True
        self.configure_selection()
        z,y,x = volume.data.shape
        for name,count in [('XY',z),('XZ',y),('YZ',x)]:
            self.sliders[name].configure(to=max(1,count-1))
            self.sliders[name].state(['!disabled'])
            self.indices[name].set(count//2)
        included=volume.params.get('included_slice_indices')
        if included:self.indices['XY'].set(included[len(included)//2]-1)
        self.save_button.state(['!disabled'])
        self.summary.set(f'画像スタック: AP {z}枚 × Y {y} × X {x}\nAP範囲（先頭から）: 0〜{(z-1)*volume.spacing_xyz[2]/1000:.4g} mm\n間隔 X/Y/AP: '+', '.join(f'{s:.4g}' for s in volume.spacing_xyz)+' µm')
        self.status.set('3D構築が完了しました。断面と3D表示を確認できます。')
        self.app.add_log(self.status.get())
        self.reset_view()

    def save_as(self):
        if self.busy or self.volume is None: return
        directory = self.app.save_directory.get() or str(self.app.folder or APP_ROOT)
        if not Path(directory).is_dir(): directory = str(APP_ROOT)
        path = filedialog.asksaveasfilename(parent=self.app,title='3Dデータを保存',initialdir=directory,
            initialfile=(self.app.save_name.get() or 'BRAIN')+'_3d.npz',defaultextension='.npz',filetypes=[('NumPy volume','*.npz')])
        if not path: return
        volume = self.volume
        volume.params['workspace']=self.app.workspace_snapshot()
        view=self.view_state()
        self.run_job('3Dデータ保存中',lambda cancel,progress:save_volume(volume,path,view,cancel),
                     lambda target:self.saved(target))

    def view_state(self):
        return {'mode':'image_stack','opacity':self.opacity.get()/100,'black_transparent':self.black_transparent.get(),'ap_order':AP_ORDERS[self.ap_order.get()],'display_ap_direction':'A_to_P','camera_depth':'positive_away','ap_spacing_scale':self.ap_spacing_scale(),'ap_spacing_relative_to_default':self._display_ap_spacing,'yaw':self.yaw,'pitch':self.pitch,'zoom':self.zoom,
                'visible_section_indices':[i+1 for i in self.visible_sections()],'visible_display_channels':[i+1 for i in self.visible_channels()],
                'channel_opacities':list(self.opacity_weights()),'highlight_selected_section':self.highlight_section.get(),
                'selected_section_index':self.selected_z()+1 if self._slice_selected else None,'hidden_from_anterior_count':self.cut_value(),
                'cut_reference':'first_image' if AP_ORDERS[self.ap_order.get()]=='unspecified' else 'A',
                'highlight_parameters':{'selected_opacity_minimum':.85,'other_sections_opacity_factor':.2},
                'checked_section_indices':[i+1 for i,var in enumerate(self.section_checks) if var.get()],
                'distance_measurement':self.measurement_metadata(),'distance_measurements':self.measurements_metadata(),
                'section_positions':{name:int(round(var.get())) for name,var in self.indices.items()},
                'pan_pixels':list(self.pan),'scale_bar_length_um':self.scale_length.get(),'scale_bar_show_text':self.scale_show_text.get(),'scale_bar_reference':'ML/DV/AP (AP display spacing accounted)','plane_scale_bars':{name:control.settings() for name,control in self.plane_scales.items()},'axes':{'X':'ML','Y':'DV (image up)','Z':'AP'}}

    def restore_saved(self,volume,view):
        self.built(volume)
        params=volume.params
        self.use_raw.set(not params.get('aligned',False))
        for var,value in zip(self.spacing,(*params.get('input_pixel_size_xy',volume.spacing_xyz[:2]),volume.spacing_xyz[2])):var.set(value)
        self.limit.set(str(max(volume.data.shape[1:])))
        order=view.get('ap_order',params.get('ap_order','A_to_P'))
        self.ap_order.set(next((name for name,value in AP_ORDERS.items() if value==order),list(AP_ORDERS)[1]))
        for key,low,high,default in [('yaw',-1e9,1e9,55),('pitch',-1e9,1e9,20),('zoom',.01,8,1)]:
            value=float(view.get(key,default));setattr(self,key,max(low,min(high,value)))
        self.pan=tuple(view.get('pan_pixels',[0.,0.]))
        self.opacity.set(max(1,min(100,float(view.get('opacity',.15))*100)))
        self.black_transparent.set(bool(view.get('black_transparent',True)))
        self.set_display_ap_spacing(view.get('ap_spacing_relative_to_default',1.))
        self.scale_length.set(view.get('scale_bar_length_um',50.));self.scale_show_text.set(bool(view.get('scale_bar_show_text',True)))
        self.highlight_section.set(bool(view.get('highlight_selected_section',False)))
        self.cut_count.set(view.get('hidden_from_anterior_count',0))
        for name,var in self.indices.items():
            maximum={'XY':volume.data.shape[0],'XZ':volume.data.shape[1],'YZ':volume.data.shape[2]}[name]
            var.set(max(0,min(maximum-1,int(view.get('section_positions',{}).get(name,maximum//2)))))
        self._slice_selected=view.get('selected_section_index') is not None
        if self._slice_selected:self.indices['XY'].set(max(0,min(volume.data.shape[0]-1,int(view['selected_section_index'])-1)))
        for i,var in enumerate(self.section_checks,1):var.set(i in view.get('checked_section_indices',view.get('visible_section_indices',list(range(1,len(self.section_checks)+1)))))
        for i,var in enumerate(self.channel_checks,1):var.set(i in view.get('visible_display_channels',list(range(1,len(self.channel_checks)+1))))
        for var,value in zip(self.channel_opacities,view.get('channel_opacities',[])):var.set(max(0,min(100,float(value)*100)))
        for info in view.get('distance_measurements',[]):
            measured=physical_distance(volume,*info['points_zyx'])
            self.append_measurement({**info,**measured})
        if not view.get('distance_measurements') and view.get('distance_measurement',{}):
            old=view['distance_measurement']
            if old.get('complete'):self.append_measurement({'name':'距離1','visible':True,**old})
        self._measurement_serial=len(self.measurements)
        confirmation=params.get('analysis',{}).get('measurement_calibration',{})
        self._z_confirmed=bool(confirmation.get('z_confirmed',False))
        self._manual_xy_calibrated=bool(confirmation.get('xy_confirmed',False))
        for name,options in view.get('plane_scale_bars',{}).items():
            if name in self.plane_scales:
                control=self.plane_scales[name];control.enabled.set(bool(options.get('visible',True)))
                control.value.set(str(options.get('length_um') or 50));control.show_text.set(bool(options.get('show_text',True)))
        self.status.set('保存済み3Dデータ・表示条件・距離一覧を復元しました。');self.request_draw()

    def saved(self,target):
        self.status.set(f'保存完了: {target}')
        self.app.add_log(self.status.get())

    def run_job(self,title,work,done):
        self.busy = True
        self._cancel.clear()
        self._done = done
        self._old_states = [(widget,widget.state()) for widget in self._controls]
        for widget,state in self._old_states: widget.state(['disabled'])
        dialog = tk.Toplevel(self.app)
        self._dialog = dialog
        dialog.title(title); dialog.transient(self.app); dialog.resizable(False,False)
        center_dialog(self.app,dialog,480,170)
        self._progress_text = tk.StringVar(value=title)
        self._started_at = time.monotonic()
        self.elapsed_text = tk.StringVar(value='経過時間 00:00:00')
        ttk.Label(dialog,textvariable=self._progress_text,wraplength=440).pack(fill='x',padx=18,pady=(18,10))
        self._bar = ttk.Progressbar(dialog,maximum=100,mode='indeterminate')
        self._bar.pack(fill='x',padx=18); self._bar.start(15)
        row = ttk.Frame(dialog)
        row.pack(fill='x',padx=18,pady=12)
        ttk.Label(row,textvariable=self.elapsed_text).pack(side='left')
        ttk.Button(row,text='中止',command=self.cancel).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW',self.cancel)
        dialog.grab_set()
        events,cancel = self._events,self._cancel
        def worker():
            try:
                result = work(cancel,lambda i,n,text:events.put(('progress',i,n,text)))
                events.put(('done',result))
            except Cancelled: events.put(('cancelled',None))
            except Exception as exc: events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
        self._poll_job = self.after(50,self.poll)

    def cancel(self):
        self._cancel.set()
        self._progress_text.set('中止しています…')

    def update_elapsed(self):
        seconds = max(0,int(time.monotonic()-self._started_at))
        hours,remainder = divmod(seconds,3600)
        minutes,seconds = divmod(remainder,60)
        self.elapsed_text.set(f'経過時間 {hours:02d}:{minutes:02d}:{seconds:02d}')

    def poll(self):
        self._poll_job = None
        self.update_elapsed()
        try:
            while True:
                event = self._events.get_nowait()
                kind = event[0]
                if kind=='progress':
                    i,n,text = event[1:]
                    self._bar.stop(); self._bar.configure(mode='determinate',value=100*i/max(1,n))
                    self._progress_text.set(f'{i}/{n} · {text}')
                    continue
                self._bar.stop(); self._dialog.grab_release(); self._dialog.destroy()
                self._dialog = None
                self.busy = False
                for widget,state in self._old_states:
                    widget.state(['!disabled']); widget.state(state)
                if kind=='done': self._done(event[1])
                elif kind=='cancelled': self.status.set('処理を中止しました。')
                else: self.status.set(f'3D処理エラー: {event[1]}'); self.app.add_log(self.status.get())
                if self._close_after: self.app.close()
                return
        except queue.Empty: pass
        self._poll_job = self.after(50,self.poll)

    def request_draw(self):
        if hasattr(self,'view_cube'):self.view_cube.set_view(self.yaw,self.pitch)
        if self._draw_job is not None: self.after_cancel(self._draw_job)
        self._draw_job = self.after(35,self.draw)

    def set_view_angles(self,yaw,pitch):
        self.yaw,self.pitch = float(yaw),float(pitch)
        self._drag = None
        self.request_draw()

    def reset_view(self):
        # AP position mapping handles acquisition order without viewing image backs.
        self.yaw,self.pitch,self.zoom = 55.,20.,1.
        self.pan = (0.,0.)
        self._drag = None
        self.request_draw()

    def ap_spacing_scale(self):
        return STACK_DISPLAY_SPACING*self._display_ap_spacing

    def set_display_ap_spacing(self,value=None):
        try:
            factor = float(self.display_ap_spacing_text.get() if value is None else value)
            if not np.isfinite(factor) or not .1<=factor<=10.: raise ValueError()
        except (ValueError,TypeError,tk.TclError):
            self.display_ap_spacing_text.set(f'{self._display_ap_spacing:.2f}')
            self.status.set('AP方向の表示間隔は0.1〜10の範囲で入力してください。実際のZ間隔は変更しません。')
            return
        self._display_ap_spacing = factor
        self.display_ap_spacing.set(factor)
        self.display_ap_spacing_text.set(f'{factor:.2f}')
        self.request_draw()

    def step_display_ap_spacing(self,event):
        if self.display_ap_spacing_slider.instate(['disabled']): return 'break'
        delta = getattr(event,'delta',0)
        if delta:
            if self.focus_get()!=self.display_ap_spacing_slider: return 'break'
            step = .1 if delta>0 else -.1
        else: step = .1 if event.keysym in ('Right','Up') else -.1
        self.set_display_ap_spacing(round(max(.1,min(10.,self._display_ap_spacing+step)),2))
        return 'break'

    def press(self,event):
        self.canvases['3D'].focus_set()
        if getattr(event,'state',0)&1: self.set_shift(True)
        self._drag = (event.x,event.y,self.yaw,self.pitch,self.pan)
        self._drag_moved = False

    def drag(self,event):
        if self._drag:
            x,y,yaw,pitch,pan = self._drag
            if abs(event.x-x)+abs(event.y-y)<4: return
            self._drag_moved = True
            shifted = self.shift_down or bool(getattr(event,'state',0)&1)
            if not shifted:
                self.pan = (pan[0]+event.x-x,pan[1]+event.y-y)
                self.request_draw();return
            if shifted: self.set_shift(True)
            self.yaw = (yaw-(event.x-x)*.5+180)%360-180
            angle = pitch+(event.y-y)*.5
            self.pitch = (angle+180)%360-180 if shifted else max(-89,min(89,angle))
            self.request_draw()

    def release(self,event):
        clicked = self._drag is not None and not self._drag_moved and not (self.shift_down or getattr(event,'state',0)&1)
        self._drag = None
        if not clicked or self.volume is None or self._render_view is None: return
        key,rotation = self._render_view
        if key[0]!=id(self.volume): return
        point = pick_stack_point(self.volume,event.x,event.y,*key[1:6],opacity=key[6],black_transparent=key[7],
                                 ap_spacing_scale=key[8],sections=key[10],channels=key[11],pan=key[12],channel_opacities=key[13],highlight_section=key[14])
        if point is None:
            self.clear_slice_selection();return
        self._picked = point
        self._slice_selected=True
        self.add_measure_point(point)
        for name,value in zip(('XY','XZ','YZ'),point): self.indices[name].set(value)
        self.request_draw()

    def select_xy(self,event):
        self.select_plane(event,'XY')

    def select_plane(self,event,name):
        if self.volume is None or name not in self._plane_regions: return
        left,top,width,height = self._plane_regions[name]
        if not left<=event.x<left+width or not top<=event.y<top+height: return
        nz,ny,nx=self.volume.data.shape
        horizontal,vertical={'XY':(nx,ny),'XZ':(nx,nz),'YZ':(ny,nz)}[name]
        u=min(horizontal-1,int((event.x-left)*horizontal/width));v=min(vertical-1,int((event.y-top)*vertical/height))
        z,y,x=(max(0,min(count-1,round(self.indices[axis].get()))) for axis,count in [('XY',nz),('XZ',ny),('YZ',nx)])
        if name=='XY':x,y=u,v
        elif name=='XZ':x,z=u,v
        else:y,z=u,v
        if not self.volume.valid[z,y,x]: return
        for axis,value in zip(('XY','XZ','YZ'),(z,y,x)):self.indices[axis].set(value)
        self._picked = (z,y,x)
        self._slice_selected=True
        self.add_measure_point(self._picked)
        self.section_checks[z].set(True)
        self.request_draw()

    def configure_selection(self):
        for parent in (self.channel_choices,self.section_choices,self.channel_opacity_box):
            for child in parent.winfo_children(): child.destroy()
        self.channel_checks.clear(); self.section_checks.clear();self.channel_opacities.clear();self.channel_opacity_labels.clear()
        self.cut_count.set(0.)
        self.cut_slider.state(['disabled'] if self.volume is None else ['!disabled'])
        if self.volume is None:
            self.cut_text.set('0 / 0 枚を非表示');self.selected_section_info.set('切片未選択');return
        for label,color in display_channels(self.volume):
            variable = tk.BooleanVar(value=True); self.channel_checks.append(variable)
            ChannelCheck(self.channel_choices,color=color,text=label,variable=variable,command=self.request_draw).pack(side='left',padx=3)
            opacity=tk.DoubleVar(value=100.);self.channel_opacities.append(opacity)
            row=ttk.Frame(self.channel_opacity_box);row.pack(fill='x',pady=2)
            ttk.Label(row,text=label,width=13).pack(side='left')
            ArrowScale(row,from_=0,to=100,variable=opacity,command=lambda value:self.request_draw()).pack(side='left',fill='x',expand=True)
            text=tk.StringVar(value='100 %');self.channel_opacity_labels.append(text)
            entry=ttk.Entry(row,textvariable=text,width=5);entry.pack(side='right')
            text.set('100')
            entry.bind('<Return>',lambda event,var=opacity,text=text:self.edit_opacity(var,text))
            entry.bind('<FocusOut>',lambda event,var=opacity,text=text:self.edit_opacity(var,text))
            entry.opacity_text=text
        for i in range(self.volume.data.shape[0]):
            included=i+1 in self.volume.params.get('included_slice_indices',range(1,self.volume.data.shape[0]+1))
            variable = tk.BooleanVar(value=included); self.section_checks.append(variable)
            widget=ttk.Checkbutton(self.section_choices,text=str(i+1),variable=variable,command=self.request_draw)
            widget.pack(side='left',padx=2)
            if not included:widget.state(['disabled'])
        self.section_canvas.xview_moveto(0)
        self.cut_slider.configure(to=max(1,len(self.anatomical_sections())))

    def anatomical_sections(self):
        if self.volume is None:return ()
        included=set(self.volume.params.get('included_slice_indices',range(1,self.volume.data.shape[0]+1)))
        sections=tuple(i for i in range(self.volume.data.shape[0]) if i+1 in included)
        return sections[::-1] if AP_ORDERS[self.ap_order.get()]=='P_to_A' else sections

    def cut_value(self):return max(0,min(len(self.anatomical_sections()),round(self.cut_count.get())))

    def visible_sections(self):
        hidden=set(self.anatomical_sections()[:self.cut_value()])
        return tuple(i for i,variable in enumerate(self.section_checks) if variable.get() and i not in hidden)

    def reset_cut(self):self.cut_count.set(0.);self.request_draw()

    def step_cut(self,event):
        if not self.cut_slider.instate(['disabled']):
            step=1 if event.keysym in ('Right','Up') else -1
            self.cut_count.set(max(0,min(len(self.anatomical_sections()),self.cut_value()+step)));self.request_draw()
        return 'break'

    def opacity_weights(self):return tuple(max(0.,min(1.,var.get()/100)) for var in self.channel_opacities)

    def selected_z(self):
        return max(0,min(self.volume.data.shape[0]-1,round(self.indices['XY'].get()))) if self.volume is not None else 0

    def select_slice_control(self):
        self._slice_selected=self.volume is not None
        self.request_draw()

    def clear_slice_selection(self):
        self._picked=None;self._slice_selected=False
        if self.volume is not None:self.draw_stack_view()
        self.request_draw()

    def clear_measurement(self):
        self.measure_points=[]
        self.measure_text.set('測定をONにして、画像上の2点をクリックしてください。')
        self.request_draw()

    def measurement_metadata(self):
        if self.volume is None or not self.measure_points:return None
        result=physical_distance(self.volume,self.measure_points[0],self.measure_points[-1])
        result['complete']=len(self.measure_points)==2
        analysis=self.volume.params.get('analysis',{})
        result['calibration_confirmation']=copy.deepcopy(analysis.get('measurement_calibration',
            {'xy_confirmed':analysis.get('pixel_calibration',{}).get('status')=='loaded' if analysis.get('pixel_calibration') else False,'z_confirmed':False}))
        return result

    def add_measure_point(self,point):
        if not self.measure_mode.get():return
        if not any(weight>0 for i,weight in enumerate(self.opacity_weights()) if i in self.visible_channels()):return
        if len(self.measure_points)==2:self.measure_points=[]
        self.measure_points.append(tuple(map(int,point)))
        if len(self.measure_points)==1:
            z,y,x=self.measure_points[0];self.measure_text.set(f'A点：切片 {z+1} / X {x+1}, Y {y+1}\n次にB点をクリックしてください。')
        else:
            info=self.measurement_metadata();parts=info['components_um']
            confirmed=all(info['calibration_confirmation'].values())
            notice='' if confirmed else '\n間隔未確認：設定値による参考値です。'
            self.measure_text.set(f'A–B：{info["distance_um"]:.4g} µm\nML {parts["ML"]:.4g} / DV {parts["DV"]:.4g} / AP {parts["AP"]:.4g} µm'+notice)
            self.app.add_log('3D距離測定: '+self.measure_text.get().replace('\n',' · '))
            self._measurement_serial+=1
            self.append_measurement({'name':f'距離{self._measurement_serial}','visible':True,**info})
        self.request_draw()

    def update_section_info(self):
        if not self._slice_selected:
            self.selected_section_info.set('切片未選択（背景クリックで解除済み）');return
        z=self.selected_z();number=z+1
        included=list(self.volume.params.get('included_slice_indices',range(1,self.volume.data.shape[0]+1)))
        files=self.volume.params.get('input_files',[])
        name=Path(files[included.index(number)]).name if number in included and included.index(number)<len(files) else 'no image' if number not in included else 'ファイル情報なし'
        visible='' if z in self.visible_sections() else '\n3Dでは現在非表示'
        self.selected_section_info.set(f'切片 {number} / 先頭からAP {z*self.volume.spacing_xyz[2]:.4g} µm\n{name}'+visible)

    def paint_measurement(self,canvas,points,info=None):
        if len(points)==2 and all(point is not None for point in points):
            a,b=points
            canvas.create_line(*a,*b,fill='#ff80ff',width=2,dash=(4,2),tags='measurement')
            info=info or self.measurement_metadata()
            canvas.create_text((a[0]+b[0])/2,(a[1]+b[1])/2-12,text=f'{info.get("name","")} {info["distance_um"]:.4g} µm'.strip(),fill='white',tags='measurement')
        for i,point in enumerate(points):
            if point is None:continue
            x,y=point;color=('#00e5ff','#ff80ff')[i]
            canvas.create_oval(x-5,y-5,x+5,y+5,outline=color,width=2,tags='measurement')
            canvas.create_text(x+8,y-8,text=('A','B')[i],anchor='sw',fill=color,tags='measurement')

    def edit_opacity(self,var,text):
        try:
            value=float(text.get().strip().rstrip('%'))
            if not np.isfinite(value) or not 0<=value<=100:raise ValueError()
            var.set(value)
        except (ValueError,tk.TclError):self.status.set('不透明度は0〜100の数値を入力してください。')
        text.set(f'{var.get():g}');self.request_draw()

    def append_measurement(self,info):
        record=copy.deepcopy(info)
        record['name_var']=tk.StringVar(value=record.pop('name','距離'))
        record['visible_var']=tk.BooleanVar(value=record.pop('visible',True))
        row=ttk.Frame(self.measurement_list);row.pack(fill='x',pady=2);record['row']=row
        ttk.Checkbutton(row,variable=record['visible_var'],command=self.request_draw).pack(side='left')
        entry=ttk.Entry(row,textvariable=record['name_var'],width=11);entry.pack(side='left',fill='x',expand=True)
        entry.bind('<Return>',lambda event:self.request_draw());entry.bind('<FocusOut>',lambda event:self.request_draw())
        ttk.Label(row,text=f'{record["distance_um"]:.4g} µm').pack(side='left',padx=3)
        ttk.Button(row,text='×',width=2,command=lambda:self.remove_measurement(record)).pack(side='right')
        self.measurements.append(record)

    def remove_measurement(self,record):
        record['row'].destroy();self.measurements.remove(record);self.request_draw()

    def reset_measurements(self):
        for record in self.measurements:record['row'].destroy()
        self.measurements.clear();self._measurement_serial=0;self.clear_measurement()

    def measurements_metadata(self):
        return [{**{key:copy.deepcopy(value) for key,value in item.items() if key not in ('name_var','visible_var','row')},
                 'name':item['name_var'].get().strip() or f'距離{i+1}','visible':item['visible_var'].get()}
                for i,item in enumerate(self.measurements)]

    def save_measurements(self):
        if not self.measurements:return
        path=filedialog.asksaveasfilename(parent=self.app,title='距離一覧を保存',
            initialdir=self.app.save_directory.get() or str(self.app.folder or APP_ROOT),
            initialfile='BRAIN_distances.json',defaultextension='.json',filetypes=[('JSON','*.json'),('CSV','*.csv')])
        if not path:return
        try:
            records=self.measurements_metadata()
            from export_data import csv_filename
            if Path(path).suffix.lower()=='.csv':
                with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(['name','visible','distance_um','ML_um','DV_um','AP_um','A_z','A_y','A_x','B_z','B_y','B_x'])
                    for item in records:writer.writerow([csv_filename(item['name']),item['visible'],item['distance_um'],*[item['components_um'][a] for a in ('ML','DV','AP')],*item['points_zyx'][0],*item['points_zyx'][1]])
            else:Path(path).write_text(json.dumps({'format':'BRAIN distances','measurements':records},ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            self.app.add_log('距離一覧を保存: '+path)
        except (OSError,ValueError) as error:self.status.set('距離保存エラー: '+str(error))

    def displayed_measurements(self):
        records=[item for item in self.measurements_metadata() if item['visible']]
        if len(self.measure_points)==1:records.append({'points_zyx':self.measure_points})
        return records

    def draw_plane_measurement(self,name,indices):
        left,top,width,height=self._plane_regions[name]
        nz,ny,nx=self.volume.data.shape
        for info in self.displayed_measurements():
            points=[]
            for z,y,x in info['points_zyx']:
                fixed,u,v,nu,nv={'XY':(z,x,y,nx,ny),'XZ':(y,x,z,nx,nz),'YZ':(x,y,z,ny,nz)}[name]
                points.append((left+(u+.5)*width/nu,top+(v+.5)*height/nv) if fixed==indices[name] else None)
            self.paint_measurement(self.canvases[name],points,info)

    def draw_3d_measurement(self,key,rotation,scale,origins):
        basis=rotation[:2,:2]@np.diag(self.volume.spacing_xyz[:2])*scale
        for info in self.displayed_measurements():
            points=[tuple(basis@[x+.5,y+.5]+origins[int(z),:2]*scale+[key[1]/2,key[2]/2]) if z in key[10] else None for z,y,x in info['points_zyx']]
            self.paint_measurement(self.canvases['3D'],points,info)

    def visible_channels(self): return tuple(i for i,variable in enumerate(self.channel_checks) if variable.get())

    def select_sections(self,selected):
        allowed=set(self.volume.params.get('included_slice_indices',range(1,len(self.section_checks)+1))) if self.volume is not None else set()
        for i,variable in enumerate(self.section_checks,1): variable.set(selected and i in allowed)
        self.request_draw()

    def wheel(self,event):
        self.zoom = max(.01,min(8,self.zoom*(1.15 if event.delta>0 else 1/1.15)))
        self.request_draw()
        return 'break'

    def draw(self):
        if self._draw_job is not None: self.after_cancel(self._draw_job)
        self._draw_job = None
        for canvas in self.canvases.values(): canvas.delete('all')
        if self.volume is None:
            for name,canvas in self.canvases.items():
                canvas.create_text(max(1,canvas.winfo_width())/2,max(1,canvas.winfo_height())/2,text=name+' · ボリューム未作成',fill='white')
            return
        volume = self.volume
        z,y,x = volume.data.shape
        indices = {name:max(0,min(count-1,round(self.indices[name].get()))) for name,count in [('XY',z),('XZ',y),('YZ',x)]}
        if self._picked is not None: self._picked = tuple(indices[name] for name in ('XY','XZ','YZ'))
        sx,sy,sz = volume.spacing_xyz
        channels = self.visible_channels()
        weights=self.opacity_weights()
        planes = {'XY':(image_plane(volume,'XY',indices['XY'],channels,weights),volume.valid[indices['XY']],sx,sy,'AP',z,sz),
                  'XZ':(image_plane(volume,'XZ',indices['XZ'],channels,weights),volume.valid[:,indices['XZ'],:],sx,sz,'Y',y,sy),
                  'YZ':(image_plane(volume,'YZ',indices['YZ'],channels,weights),volume.valid[:,:,indices['YZ']],sy,sz,'X',x,sx)}
        for name,(pixels,mask,dx,dy,axis,count,step) in planes.items():
            canvas = self.canvases[name]
            width,height = max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
            physical_w,physical_h = pixels.shape[1]*dx,pixels.shape[0]*dy
            scale = min(max(1,width-12)/physical_w,max(1,height-12)/physical_h)
            with Image.fromarray(np.where(mask[...,None],pixels,0).astype(np.uint8)) as image:
                resized = image.resize((max(1,round(physical_w*scale)),max(1,round(physical_h*scale))),Image.Resampling.BILINEAR)
            self._photos[name] = ImageTk.PhotoImage(resized,master=self)
            resized.close()
            canvas.create_image(width/2,height/2,image=self._photos[name])
            iw,ih=self._photos[name].width(),self._photos[name].height()
            horizontal_bar(canvas,self.plane_scales[name],iw/physical_w,(8,8,width-8,height-8))
            points = {'XY':(indices['YZ'],indices['XZ'],x,y),'XZ':(indices['YZ'],indices['XY'],x,z),
                      'YZ':(indices['XZ'],indices['XY'],y,z)}
            cx,cy,nx,ny = points[name]
            iw,ih = self._photos[name].width(),self._photos[name].height()
            left,top = (width-iw)/2,(height-ih)/2
            self._plane_regions[name] = (left,top,iw,ih)
            px,py = left+(cx+.5)*iw/nx,top+(cy+.5)*ih/ny
            canvas.create_line(px,top,px,top+ih,fill='#ffca28',dash=(3,3),tags='linked-crosshair')
            canvas.create_line(left,py,left+iw,py,fill='#ffca28',dash=(3,3),tags='linked-crosshair')
            position = indices[name]*step if axis=='AP' else (indices[name]+.5)*step
            self.slice_labels[name].set(f'AP切片: {indices[name]+1} / {count} · 先頭から {position/1000:.4g} mm' if axis=='AP' else f'{axis}位置: {indices[name]+1} / {count} · {position:.4g} µm')
            if name!='XY':
                first,last = self.ap_end_labels()
                canvas.create_text(8,8,anchor='nw',text=f'縦方向: AP · 上={first} / 下={last}',fill='#ffca28')
            self.draw_plane_measurement(name,indices)
        self.opacity_text.set(f'{self.opacity.get():.0f} %')
        focused=self.focus_get()
        for value,text in zip(self.channel_opacities,self.channel_opacity_labels):
            if getattr(focused,'opacity_text',None) is not text:text.set(f'{value.get():.0f}')
        self.cut_side.set('先頭から切片を隠す（AP未指定）' if AP_ORDERS[self.ap_order.get()]=='unspecified' else 'A側から切片を隠す')
        self.cut_text.set(f'{self.cut_value()} / {len(self.anatomical_sections())} 枚を非表示')
        self.update_section_info()
        self.request_render()
        self.draw_stack_view()

    def ap_end_labels(self):
        order = AP_ORDERS[self.ap_order.get()]
        return ('A（前）','P（後）') if order=='A_to_P' else ('P（後）','A（前）') if order=='P_to_A' else ('1枚目','最後')

    def reset_render(self):
        self._render_cancel.set()
        if self._render_job is not None: self.after_cancel(self._render_job)
        self._render_job = None
        self._render_key = self._render_view = None
        if self._render_image is not None: self._render_image.close()
        self._render_image = None
        while True:
            try:
                image = self._render_events.get_nowait()[1]
                if image is not None: image.close()
            except queue.Empty: break

    def request_render(self):
        canvas = self.canvases['3D']
        width,height = max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        key = (id(self.volume),width,height,self.yaw,self.pitch,self.zoom,self.opacity.get()/100,self.black_transparent.get(),self.ap_spacing_scale(),self.volume.params.get('ap_order'),self.visible_sections(),self.visible_channels(),self.pan,
               self.opacity_weights(),self.selected_z() if self.highlight_section.get() and self._slice_selected else None)
        if key==self._render_key: return
        self._render_cancel.set()
        cancel = self._render_cancel = threading.Event()
        self._render_key = key
        volume,events = self.volume,self._render_events
        def worker():
            try:
                image,rotation = project_image_stack(volume,*key[1:9],cancel=cancel,sections=key[10],channels=key[11],pan=key[12],channel_opacities=key[13],highlight_section=key[14])
                if cancel.is_set(): image.close()
                else: events.put((key,image,rotation))
            except Cancelled: pass
            except Exception as error: events.put((key,None,str(error)))
        self._render_thread = threading.Thread(target=worker,daemon=True)
        self._render_thread.start()
        if self._render_job is None: self._render_job = self.after(30,self.poll_render)

    def poll_render(self):
        self._render_job = None
        while True:
            try: key,image,rotation = self._render_events.get_nowait()
            except queue.Empty: break
            if key!=self._render_key:
                if image is not None: image.close()
                continue
            if image is None:
                self.status.set(f'3D表示エラー: {rotation}'); continue
            if self._render_image is not None: self._render_image.close()
            self._render_image,self._render_view = image,(key,rotation)
            self._photos['3D'] = ImageTk.PhotoImage(image,master=self)
            self.draw_stack_view()
        if self._render_thread is not None and self._render_thread.is_alive():
            self._render_job = self.after(30,self.poll_render)

    def draw_stack_view(self):
        canvas = self.canvases['3D']; canvas.delete('all')
        width,height = max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        if '3D' in self._photos: canvas.create_image(0,0,image=self._photos['3D'],anchor='nw')
        if self._render_image is not None:self.view_cube.set_background(self._render_image)
        if self._render_view is None:
            canvas.create_text(width/2,height/2,text='3D画像を準備しています…',fill='white'); return
        key,rotation = self._render_view
        first,last = self.ap_end_labels()
        rotation,extent,scale,origins,centers = stack_geometry(self.volume,*key[1:6],ap_spacing_scale=key[8],pan=key[12])
        start,end = centers[0],centers[-1]
        canvas.create_line(*start,*end,fill='#ffca28',width=2,dash=(4,3),arrow='both',tags='ap-axis')
        first_text = first+' / 1枚目' if AP_ORDERS[self.ap_order.get()]!='unspecified' else '1枚目（AP始点）'
        last_text = last+f' / {len(centers)}枚目' if AP_ORDERS[self.ap_order.get()]!='unspecified' else f'{len(centers)}枚目（AP終点）'
        for point,text,tag in [(start,first_text,'ap-first'),(end,last_text,'ap-last')]:
            xx,yy = point
            canvas.create_text(max(70,min(width-70,xx)),max(48,min(height-22,yy-18)),text=text,fill='#ffca28',tags=tag)
        canvas.create_text(10,10,anchor='nw',text=f'前後軸 AP（Z） · 表示 {len(key[10])} / {len(centers)} 枚 · 不透明度 {key[6]*100:.0f} %',fill='#ffca28')
        if self._slice_selected and key[14] in key[10]:
            zi=key[14]
            basis=rotation[:2,:2]@np.diag(self.volume.spacing_xyz[:2])*scale
            corners=np.array([[0,0],[self.volume.data.shape[2],0],[self.volume.data.shape[2],self.volume.data.shape[1]],[0,self.volume.data.shape[1]]])@basis.T+origins[zi,:2]*scale+[key[1]/2,key[2]/2]
            canvas.create_polygon(*corners.ravel(),fill='',outline='#ffff00',width=3,tags='highlighted-section')
        if self._slice_selected:
            selected=self.selected_z()
            canvas.create_text(10,48,anchor='nw',text=f'選択切片 {selected+1} / 先頭からAP {selected*self.volume.spacing_xyz[2]:.4g} µm'+('（現在非表示）' if selected not in key[10] else ''),fill='#ffff00',tags='section-info')
        if self._picked is not None and self._picked[0] in key[10] and key[11]:
            zi,yi,xi = self._picked
            basis = rotation[:2,:2] @ np.diag(self.volume.spacing_xyz[:2])*scale
            corners = np.array([[0,0],[self.volume.data.shape[2],0],[self.volume.data.shape[2],self.volume.data.shape[1]],[0,self.volume.data.shape[1]]]) @ basis.T+origins[zi,:2]*scale+[key[1]/2,key[2]/2]
            canvas.create_polygon(*corners.ravel(),fill='',outline='#ffb74d',width=2,tags='selected-section')
            point = basis @ [xi+.5,yi+.5]+origins[zi,:2]*scale+[key[1]/2,key[2]/2]
            px,py = point
            canvas.create_oval(px-5,py-5,px+5,py+5,outline='#ffb74d',width=2,tags='picked-point')
        self.draw_3d_measurement(key,rotation,scale,origins)
        if self._render_key!=key: canvas.create_text(10,30,anchor='nw',text='表示を更新中…',fill='white')
        for i,(axis,color) in enumerate([('ML（X）','#ff7777'),('DV（Y）','#77dd88'),('AP（Z）','#ffca28')]):
            direction = rotation[:,i]*(-1 if i==1 else 1); length = 55 if i==2 else 30
            xx,yy = 65+direction[0]*length,height-48+direction[1]*length
            canvas.create_line(65,height-48,xx,yy,fill=color,width=3 if i==2 else 2,arrow='last',tags=('axis-ml','axis-dv','axis-ap')[i])
            canvas.create_text(xx,yy,text=axis,fill=color,anchor='sw')
        try:length=self.scale_length.get()
        except tk.TclError:return
        if np.isfinite(length) and length>0:
            vectors=[rotation[:2,i]*length*scale*(-1 if i==1 else key[8] if i==2 else 1) for i in range(3)]
            points=np.vstack(([0.,0.],*vectors))
            lo,hi=points.min(axis=0),points.max(axis=0)
            if np.any(hi-lo>[width-153,height-80]):
                canvas.create_text(width-12,height-12,anchor='se',text='スケールバーの長さを短くするか、表示を縮小してください。',fill='white',tags='scale-bar')
            else:
                origin=np.array([width-133,height-28])-hi
                for i,(label,color,delta) in enumerate(zip(('ML','DV','AP'),('#ff7777','#77dd88','#ffca28'),vectors)):
                    end=origin+delta
                    tags=('scale-bar','scale-bar-'+label.lower())
                    canvas.create_line(*origin,*end,fill=color,width=3,tags=tags)
                    if np.linalg.norm(delta)<2:
                        canvas.create_oval(end[0]-3,end[1]-3,end[0]+3,end[1]+3,outline=color,tags=tags)
                    else:
                        normal=np.array([-delta[1],delta[0]])/np.linalg.norm(delta)*4
                        canvas.create_line(*(end-normal),*(end+normal),fill=color,width=2,tags=tags)
                    if self.scale_show_text.get():canvas.create_text(width-14,height-64+i*18,text=f'{label} {length:g} µm',fill=color,anchor='e',tags=tags)

    def close(self):
        self._cancel.set()
        self.reset_render()
        if self._shift_focus_job is not None: self.after_cancel(self._shift_focus_job)
        for event,binding in self._shift_bindings: self.app.unbind(event,binding)
        self.app.tabs.unbind('<<NotebookTabChanged>>',self._tab_binding)
        if self._draw_job is not None: self.after_cancel(self._draw_job)
        if self._poll_job is not None: self.after_cancel(self._poll_job)
        self.volume = None
        self._photos.clear()

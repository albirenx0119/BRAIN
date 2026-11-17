# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""GUI for applying final alignment to loaded images or matching TIFF files."""
import copy
import queue
import threading
import time
from pathlib import Path
from app_paths import APP_ROOT, FUNCTION_ROOT
from ui_controls import center_dialog
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import numpy as np
from PIL import Image,ImageTk
from alignment import Cancelled
from stack_images import build_stack,inspect_tiffs,AlignedTiffStack
from image_sources import is_tiff
from color_picker import ColorPicker


from tiff_display import TiffViews, page_layout, display_pixels, color_pixels, parse_ranges, channel_projection


class StackPanel(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=16)
        self.app = app
        self.stack = None
        self.busy = False
        self._close_after = False
        self._cancel = threading.Event()
        self._events = queue.Queue()
        self._poll_job = self._draw_job = None
        self._photo = None
        self._photos = []
        self.channel_stacks = {}
        self._analysis_revision = self._analysis_display_signature = None
        self._external_source = False
        self.reference_channel = None
        self.page_regions = []
        self.auto_brightness = tk.BooleanVar(value=True)
        self._approved_report = None
        self.folder = tk.StringVar()
        self.status = tk.StringVar(value='alignタブでアラインを実行してからスタックしてください。')
        self.info = tk.StringVar(value='スタック未作成')
        self.index = tk.IntVar(value=1)
        self.page_count = tk.StringVar(value='/ 0')
        self._current_page = 0
        self._display_slot=0;self._display_slots=();self._empty_display=False
        self.columnconfigure(0,weight=1); self.rowconfigure(5,weight=1)
        ttk.Label(self,text='stack images',style='Title.TLabel').grid(row=0,column=0,sticky='w',pady=(0,12))
        choices = ttk.Frame(self)
        choices.grid(row=1,column=0,sticky='ew')
        ttk.Label(choices,text='読み込んだ画像と同じ名前のTIFFを読み込んでマージ').pack(anchor='w',pady=(0,8))
        row = ttk.Frame(choices); row.pack(fill='x',pady=8)
        self.folder_entry = ttk.Entry(row,textvariable=self.folder,state='readonly')
        self.folder_entry.pack(side='left',fill='x',expand=True)
        self.app.register_folder_drop(self.folder_entry,self.select_folder)
        self.browse = ttk.Button(row,text='フォルダを選択',command=self.choose_folder)
        self.browse.pack(side='left',padx=(8,0))
        ttk.Label(choices,text='TIFFは拡張子を除く同名で照合します。画像順は解析対象の順です。\n自動アライン＋手動補正の最終結果を元解像度に適用します。').pack(anchor='w')
        actions = ttk.Frame(self); actions.grid(row=2,column=0,sticky='ew',pady=10)
        self.build_button = ttk.Button(actions,text='TIFFを読み込み',command=self.start_build,state='disabled')
        self.build_button.pack(side='left')
        ttk.Label(actions,text='構築結果は起動中のみ保持します。').pack(side='left',padx=12)
        ttk.Label(self,textvariable=self.status,wraplength=1100).grid(row=3,column=0,sticky='w',pady=4)
        nav = self.navigation = ttk.Frame(self); nav.grid(row=4,column=0,sticky='ew',pady=6)
        self.first_button = ttk.Button(nav,text='最初',command=lambda:self.show_page(0),state='disabled')
        self.previous_button = ttk.Button(nav,text='◀ 前へ',command=lambda:self.show_page(self._display_slot-1),state='disabled')
        self.next_button = ttk.Button(nav,text='次へ ▶',command=lambda:self.show_page(self._display_slot+1),state='disabled')
        self.last_button = ttk.Button(nav,text='最後',command=lambda:self.show_page(self.total_pages()-1),state='disabled')
        for button in (self.first_button,self.previous_button): button.pack(side='left',padx=(0,4))
        self.index_input = ttk.Spinbox(nav,from_=1,to=1,textvariable=self.index,width=6,state='disabled',command=self.jump_page)
        self.index_input.pack(side='left',padx=(6,4)); self.index_input.bind('<Return>',lambda e:self.jump_page())
        ttk.Label(nav,textvariable=self.page_count).pack(side='left',padx=(0,10))
        for button in (self.next_button,self.last_button): button.pack(side='left',padx=(0,4))
        self.views = TiffViews(self,self)
        self.views.grid(row=5,column=0,sticky='nsew')
        self.canvas = self.views.canvases['raw']
        ttk.Label(self,textvariable=self.info,anchor='w').grid(row=6,column=0,sticky='ew',pady=(4,0))
        self.request_draw()

    def total_pages(self):
        return len(self._display_slots) if self.stack is not None and self._display_slots else int(self.stack.array.shape[0]) if self.stack is not None else 0

    def set_display_slots(self,positions,total):
        mapping={int(position)-1:i for i,position in enumerate(positions)}
        self._display_slots=tuple(mapping.get(i) for i in range(total))

    def stack_indices(self,state):
        slots=tuple(range(self.total_pages())) if state['mode'].get()=='all' else parse_ranges(state['range'].get(),self.total_pages())
        mapping=self._display_slots or tuple(range(self.total_pages()))
        selected=tuple(mapping[i] for i in slots if mapping[i] is not None)
        if not selected:raise ValueError('指定範囲に使用できる画像がありません。no imageを除いた範囲を指定してください。')
        return selected

    def show_page(self,number):
        if self.stack is None: return
        self._display_slot = max(0,min(self.total_pages()-1,int(number)))
        mapping=self._display_slots or tuple(range(self.total_pages()))
        self._empty_display=mapping[self._display_slot] is None
        if not self._empty_display:self._current_page=mapping[self._display_slot]
        self.index.set(self._display_slot+1)
        for button in (self.first_button,self.previous_button):
            button.state(['disabled'] if self._display_slot==0 else ['!disabled'])
        for button in (self.next_button,self.last_button):
            button.state(['disabled'] if self._display_slot==self.total_pages()-1 else ['!disabled'])
        self.request_draw()

    def jump_page(self):
        try: number = self.index.get()-1
        except tk.TclError:
            self.index.set(self._display_slot+1); return
        self.show_page(number)

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.app,title='対応するTIFFのフォルダを選択',mustexist=True,
            initialdir=self.folder.get() or str(self.app.folder or APP_ROOT))
        if folder:
            if self.select_folder(folder): self._external_source = Path(folder).resolve()!=self.app.folder

    def sync_analysis(self,force=False):
        app = self.app
        paths = tuple(file.path for file in app.analysis_files)
        if not paths or not all(is_tiff(path) for path in paths) or (self._external_source and not force): return False
        if self.busy: return False
        if force: self._external_source = False
        matrices = app.alignment_matrices or [np.eye(3) for _ in paths]
        if len(matrices)!=len(paths) or any(m is None for m in matrices): return False
        canvas = app.display_canvas_size()
        revision = (paths,tuple(canvas),tuple(tuple(m.ravel()) for m in matrices),str(app._preview_cache.directory),
                    repr(app._analysis_settings),len(app._manual_events))
        if revision!=self._analysis_revision or not isinstance(self.stack,AlignedTiffStack):
            if self.stack is not None: self.stack.close()
            self.stack = AlignedTiffStack(paths,matrices,canvas,app._preview_cache,
                {'alignment':copy.deepcopy(app._analysis_settings),'manual_events':copy.deepcopy(app._manual_events)})
            self.channel_stacks.clear(); self.views.raw_cache.clear()
            self.views.merge_ready = False; self.views.merge_result = None
            self._analysis_revision = revision
        self.folder.set(str(app.folder)); self.build_button.state(['!disabled'])
        self.views.ensure(app._tiff_pages)
        self.reference_channel = int(app.alignment_channel.get())-1 if app._tiff_pages>1 else 0
        settings = []
        if app._tiff_pages>1:
            app.tiff_view.ensure()
            for page in range(app._tiff_pages):
                state = app.tiff_view.channels[page]
                if not state['manual'] and state['index']!=app.preview_index:
                    from tiff_display import display_pixels
                    limits=display_pixels(app._preview_cache.get_plane(app.preview_index,page))[1]
                    state.update(limits=limits,index=app.preview_index);state['low'].set(limits[0]);state['high'].set(limits[1])
                settings.append((state['color'].get(),tuple(state['limits'])))
        else:
            from tiff_display import display_pixels
            settings = [(app.alignment_color.get(),display_pixels(app._preview_cache.get_plane(app.preview_index,0))[1])]
        signature = (self.reference_channel,tuple(settings))
        if signature!=self._analysis_display_signature:
            for state,(color,limits) in zip(self.views.channels,settings):
                state['color'].set(color); state['low'].set(limits[0]); state['high'].set(limits[1])
                state.update(manual=True,limits=limits)
            self._analysis_display_signature = signature
        self.set_display_slots(app._analysis_slice_indices or range(1,len(paths)+1),app._analysis_total_slices or len(paths))
        self.index_input.configure(to=self.total_pages()); self.index_input.state(['!disabled'])
        self.page_count.set(f'/ {self.total_pages()}'); self.show_page(app._preview_slot)
        self.status.set('alignタブのアライン結果・チャンネル設定を反映しています。' if app._analysis_settings else 'TIFFを表示しています。アライン実行後に結果を自動反映します。')
        self.request_draw()
        return True

    def confirm_folder(self,folder,paths):
        report = inspect_tiffs(paths,folder)
        excluded={path.stem.casefold() for path in (self.app._analysis_omitted_paths if self.app.analysis_files else self.app._excluded_paths)}
        ignored=[name for name in report['extra'] if Path(name).stem.casefold() in excluded]
        if ignored:
            report['ignored_excluded_files']=ignored
            report['tiff_count_total']=report['tiff_count']
            report['tiff_count']-=len(ignored)
            report['extra']=[name for name in report['extra'] if name not in ignored]
            report['consistent']=report['tiff_count']==report['expected_count'] and not any(report[key] for key in ('missing','extra','duplicates'))
        if not report['consistent'] and report!=self._approved_report:
            lines = [f"解析対象: {report['expected_count']}枚 / TIFF: {report['tiff_count']}枚",
                     f"名前が一意に一致: {len(report['matched_indices'])}枚"]
            for key,label in [('missing','対応TIFFがない画像'),('extra','解析対象にないTIFF'),('duplicates','名前が重複して対応不明')]:
                if report[key]:
                    lines.append(label+f"（{len(report[key])}件）:\n"+'\n'.join(report[key][:12]))
                    if len(report[key])>12: lines.append('ほか '+str(len(report[key])-12)+'件')
            lines.append('名前・枚数が一致しません。このフォルダを使用しますか？\n一致するTIFFだけを解析対象の順に読み込みます。\n不足・余分・重複した画像は除外し、名前の違う画像へ変換を割り当てません。')
            if len(report['matched_indices'])<2: lines.append('※ 一致が2枚未満のため、この状態ではスタックできません。')
            if not messagebox.askokcancel('TIFFの対応を確認', '\n\n'.join(lines),parent=self.app): return None
        self._approved_report = report
        return report

    def select_folder(self,folder):
        paths = tuple(file.path for file in (self.app.analysis_files or self.app.images))
        if not paths:
            self.status.set('先にdataタブで画像フォルダを選択してください。'); return False
        try: report = self.confirm_folder(folder,paths)
        except (OSError,ValueError) as exc:
            self.status.set(f'TIFFフォルダ確認エラー: {exc}')
            self.app.add_log(self.status.get(),level='error'); return False
        if report is None: return False
        self.folder.set(report['folder'])
        self.build_button.state(['!disabled'])
        self.status.set(('すべて一致しています。' if report['consistent'] else '不一致を確認して選択しました。')+
                        f" 解析対象 {report['expected_count']}枚 / TIFF {report['tiff_count']}枚 / 読み込み対象 {len(report['matched_indices'])}枚")
        return True

    def invalidate_aligned(self):
        if self.sync_analysis(): return
        if self.stack is not None: self.clear('アライン結果が変わりました。スタックを作り直してください。')

    def clear(self,message='alignタブでアラインを実行してからスタックしてください。'):
        self.clear_channels()
        self._analysis_revision = self._analysis_display_signature = None
        self.reference_channel = None
        if self.stack is not None: self.stack.close()
        self.stack = None; self._photo = None; self._photos.clear(); self.page_regions = []
        self.info.set('スタック未作成'); self.status.set(message)
        self.index.set(1); self.page_count.set('/ 0'); self._current_page = 0
        self._display_slot=0;self._display_slots=();self._empty_display=False
        for widget in (self.index_input,self.first_button,self.previous_button,self.next_button,self.last_button): widget.state(['disabled'])
        self.request_draw()

    def start_build(self):
        if self.busy: return
        if self.app._raw_busy or self.app._alignment_busy or self.app._loading_busy or self.app._saving_busy or self.app.volume_panel.busy:
            self.status.set('現在の処理の完了を待ってください。'); return
        paths = tuple(file.path for file in self.app.analysis_files)
        matrices = self.app.alignment_matrices
        if len(paths)<2 or len(matrices)!=len(paths) or any(m is None for m in matrices):
            self.status.set('先にalignタブで全画像をアラインしてください。'); return
        if not self._external_source and self.sync_analysis(): return
        if not self.folder.get().strip():
            self.status.set('TIFFのフォルダを選択してください。'); return
        folder = self.folder.get()
        try: report = self.confirm_folder(folder,paths)
        except (OSError,ValueError) as exc:
            self.status.set(f'TIFFフォルダ確認エラー: {exc}')
            self.app.add_log(self.status.get(),level='error'); return
        if report is None: return
        indices = report['matched_indices']
        if len(indices)<2:
            self.status.set('名前が一意に一致するTIFFが2枚以上必要です。フォルダを選び直してください。'); return
        paths = tuple(paths[i] for i in indices)
        matrices = [matrices[i].copy() for i in indices]
        canvas = tuple(self.app.alignment_canvas)
        provenance = {'alignment':copy.deepcopy(self.app._analysis_settings),'manual_events':copy.deepcopy(self.app._manual_events),
                      'pixel_calibration':copy.deepcopy(self.app.volume_panel.calibration),
                      'tiff_matching':copy.deepcopy(report),'original_image_indices':[(self.app._analysis_slice_indices[i] if self.app._analysis_slice_indices else i+1) for i in indices],
                      'total_source_slots':self.app._analysis_total_slices or len(self.app.analysis_files)}
        self.start_progress('TIFF読み込み中')
        events,cancel = self._events,self._cancel
        root = APP_ROOT
        def worker():
            try:
                result = build_stack(paths,matrices,canvas,root,folder,provenance,cancel,
                    lambda i,n,text:events.put(('progress',i,n,text)))
                events.put(('done',result))
            except Cancelled: events.put(('cancelled',None))
            except Exception as exc: events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
        self._poll_job = self.after(50,self.poll)

    def start_progress(self,title):
        self.busy = True; self._cancel.clear()
        controls = (self.folder_entry,self.browse,self.build_button)
        self._states = [(w,w.state()) for w in controls]
        for widget in controls: widget.state(['disabled'])
        dialog = tk.Toplevel(self.app); self._dialog = dialog
        dialog.title(title); dialog.transient(self.app); dialog.resizable(False,False)
        center_dialog(self.app,dialog,520,190)
        self.progress_text = tk.StringVar(value='対応する画像を確認しています…')
        self.elapsed = tk.StringVar(value='経過時間 00:00:00'); self._started_at = time.monotonic()
        ttk.Label(dialog,textvariable=self.progress_text,wraplength=480).pack(fill='x',padx=18,pady=14)
        self.bar = ttk.Progressbar(dialog,maximum=100); self.bar.pack(fill='x',padx=18)
        ttk.Label(dialog,textvariable=self.elapsed).pack(pady=8)
        ttk.Button(dialog,text='中止',command=self.cancel).pack()
        dialog.protocol('WM_DELETE_WINDOW',self.cancel); dialog.grab_set()

    def cancel(self):
        self._cancel.set(); self.progress_text.set('中止しています…')

    def poll(self):
        self._poll_job = None
        seconds = max(0,int(time.monotonic()-self._started_at))
        self.elapsed.set(f'経過時間 {seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}')
        try:
            while True:
                event = self._events.get_nowait()
                if event[0]=='progress':
                    _,i,n,text = event
                    self.progress_text.set(text); self.bar.configure(value=100*i/max(1,n)); continue
                self._dialog.grab_release(); self._dialog.destroy(); self.busy = False
                for widget,state in self._states: widget.state(['!disabled']); widget.state(state)
                if event[0]=='done':
                    self.clear()
                    self.stack = event[1]
                    n,p = self.stack.array.shape[:2]
                    info=self.stack.params.get('analysis',{})
                    self.set_display_slots(info.get('original_image_indices',range(1,n+1)),info.get('total_source_slots',n))
                    self.index_input.configure(to=self.total_pages()); self.index_input.state(['!disabled'])
                    self.page_count.set(f'/ {self.total_pages()}')
                    self.views.notebook.select(self.views.frames['raw'])
                    self.show_page(0)
                    self.status.set(f'TIFF読み込み完了: {n}枚 × {p}ページ · {self.stack.array.dtype} · 元解像度')
                    self.app.add_log(self.status.get()); self.request_draw()
                elif event[0]=='channel_done':
                    _,page,projection,indices,display_limits = event
                    self.channel_stacks[page] = {'projection':projection,'indices':indices,'display_limits':display_limits,
                        'selection':copy.deepcopy(self._channel_plans[page])}
                    self.views.merge_ready = False; self.views.merge_result = None
                    self.status.set(f'チャンネル {page+1}のstack完了: {len(indices)}枚（最大値投影を表示）')
                    self.app.add_log(self.status.get()); self.request_draw()
                elif event[0]=='all_channels_done':
                    self.channel_stacks=event[1]
                    for page,record in self.channel_stacks.items():record['selection']=copy.deepcopy(self._channel_plans[page])
                    self.views.merge_ready=True
                    self.views.notebook.select(self.views.frames['merge'])
                    self.status.set('全チャンネルのstack完了。mergeを表示します。')
                    self.app.add_log(self.status.get());self.request_draw()
                elif event[0]=='channel_cancelled': self.status.set('stackを中止しました。以前の結果を保持します。')
                elif event[0]=='channel_error':
                    self.status.set('stackエラー: '+event[1]); self.app.add_log(self.status.get(),level='error')
                elif event[0]=='export_done':
                    self.status.set('stack / merge保存完了: '+event[1]);self.views.save_panel.info.set(self.status.get());self.app.add_log(self.status.get())
                elif event[0]=='export_cancelled':
                    self.views.save_panel.info.set('保存を中止しました。')
                elif event[0]=='export_error':
                    self.status.set('stack / merge保存エラー: '+event[1]);self.views.save_panel.info.set(self.status.get());self.app.add_log(self.status.get(),level='error')
                elif event[0]=='cancelled': self.status.set('TIFF読み込みを中止しました。以前の結果を保持します。')
                else: self.status.set('TIFF読み込みエラー: '+event[1]); self.app.add_log(self.status.get())
                if self._close_after: self.app.close()
                return
        except queue.Empty: pass
        self._poll_job = self.after(50,self.poll)

    def request_draw(self):
        if self._draw_job is not None: self.after_cancel(self._draw_job)
        self._draw_job = self.after(30,self.draw)

    def clear_channels(self):
        self.channel_stacks.clear()
        self.views.reset()

    def stack_channel(self,page):
        if self.stack is None or self.busy: return
        if isinstance(self.stack,AlignedTiffStack) and not self.app._analysis_settings:
            self.status.set('先にalignタブでアラインを実行してください。'); return
        state = self.views.channels[page]
        if not state['manual']: self.views.rendered(page,self.views.raw_pixels(self._current_page,page))
        display_limits = tuple(state['limits'])
        try:
            indices = self.stack_indices(state)
        except ValueError as exc:
            self.status.set(f'stack範囲エラー: {exc}')
            self.app.add_log(self.status.get(),level='error'); return
        self._channel_plans={page:self.selection_snapshot(page,indices)}
        self.start_progress('stack実行中')
        array,events,cancel = self.stack.array,self._events,self._cancel
        def worker():
            try:
                result = channel_projection(array,page,indices,cancel,lambda i,n,text:events.put(('progress',i,n,text)))
                events.put(('channel_done',page,result,indices,display_limits))
            except Cancelled: events.put(('channel_cancelled',None))
            except Exception as exc: events.put(('channel_error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
        self._poll_job = self.after(50,self.poll)

    def stack_all(self):
        if self.stack is None or self.busy:return
        if isinstance(self.stack,AlignedTiffStack) and not self.app._analysis_settings:
            self.status.set('先にalignタブでアラインを実行してください。');return
        self.views.ensure(self.stack.array.shape[1])
        plans=[]
        try:
            for page,state in enumerate(self.views.channels):
                if not state['manual']:self.views.rendered(page,self.views.raw_pixels(self._current_page,page))
                indices=self.stack_indices(state)
                plans.append((page,indices,tuple(state['limits'])))
        except ValueError as error:
            self.status.set(f'stack範囲エラー: {error}');self.app.add_log(self.status.get(),level='error');return
        self._channel_plans={page:self.selection_snapshot(page,indices) for page,indices,limits in plans}
        self.start_progress('全チャンネルstack実行中')
        array,events,cancel=self.stack.array,self._events,self._cancel
        def worker():
            try:
                results={};offset=0;total=sum(len(indices) for _,indices,_ in plans)
                for page,indices,limits in plans:
                    result=channel_projection(array,page,indices,cancel,lambda i,n,text:events.put(('progress',offset+i,total,text)))
                    results[page]={'projection':result,'indices':indices,'display_limits':limits}
                    offset+=len(indices)
                events.put(('all_channels_done',results))
            except Cancelled:events.put(('channel_cancelled',None))
            except Exception as error:events.put(('channel_error',str(error)))
        threading.Thread(target=worker,daemon=True).start()
        self._poll_job=self.after(50,self.poll)

    def selection_snapshot(self,page,indices):
        state=self.views.channels[page]
        mapping=self._display_slots or tuple(range(self.stack.array.shape[0]))
        return {'mode':state['mode'].get(),'requested_range':state['range'].get(),
            'source_slice_numbers':[i+1 for i,index in enumerate(mapping) if index in indices],
            'total_source_slots':len(mapping),'color_at_stack':state['color'].get()}

    def draw(self):
        if self._draw_job is not None: self.after_cancel(self._draw_job)
        self._draw_job = None
        try: self.views.draw()
        except (OSError,ValueError) as exc:
            self.status.set(f'TIFF表示エラー: {exc}')
            self.app.add_log(self.status.get(),level='error')
        self._photos = self.views.photos
        self._photo = self._photos[0] if self._photos else None
        self.page_regions = self.views.regions

    def close(self):
        self._cancel.set()
        self.clear_channels()
        if self._poll_job is not None: self.after_cancel(self._poll_job)
        if self._draw_job is not None: self.after_cancel(self._draw_job)
        if self.stack is not None: self.stack.close()
        self.stack = None; self._photo = None; self._photos.clear()

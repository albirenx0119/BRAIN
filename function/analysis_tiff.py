# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Horizontal multichannel analysis display with synchronized single-channel focus."""
from collections import OrderedDict
from slider_controls import ArrowScale
from channel_widgets import ChannelCheck
import tkinter as tk
from tkinter import ttk
import numpy as np
from PIL import Image
from color_picker import ColorPicker
from image_sources import render_native
from tiff_display import display_pixels,color_pixels
from intensity_controls import bind_intensity_keys
from raw_preview import colored_native
from scale_bars import horizontal_bar,xy_spacing


class AnalysisTiffView:
    def __init__(self,app):
        self.app = app
        self.channels = []
        self.focus_page = None
        self.regions = {}
        self.photos = []
        self.cache = OrderedDict()
        self.merged = False
        self.merge_cache = {}

    @property
    def active(self): return self.app._tiff_pages>1

    def reset(self):
        for state in self.channels:
            state['bar'].destroy(); state['check'].destroy(); state['color'].trace_remove('write',state['trace'])
        self.channels.clear(); self.focus_page = None; self.regions.clear(); self.photos.clear()
        for image in self.cache.values(): image.close()
        self.cache.clear()
        for key,image in self.merge_cache.values(): image.close()
        self.merge_cache.clear(); self.merged = False
        if hasattr(self.app,'channel_colors'): self.app.channel_colors.refresh()
        if hasattr(self.app,'merged_button'): self.app.merged_button.configure(style='TButton')
        if hasattr(self.app,'merged_channels'): self.app.merged_channels.pack_forget()

    def ensure(self):
        if len(self.channels)==self.app._tiff_pages: return
        self.reset()
        for p in range(self.app._tiff_pages):
            state = {'color':tk.StringVar(value='Gray'),'low':tk.DoubleVar(value=0.),'high':tk.DoubleVar(value=255.),
                     'manual':False,'limits':(0.,255.),'index':None,'bounds_index':None,'scales':[],'entries':[],
                     'include':tk.BooleanVar(value=True)}
            state['check'] = ChannelCheck(self.app.merged_channels,color=state['color'],text=f'Ch {p+1}',variable=state['include'],command=self.app.request_preview_draw)
            state['check'].pack(side='left',padx=3)
            bar = ttk.Frame(self.app.preview_canvas,padding=3); state['bar'] = bar
            row = ttk.Frame(bar); row.pack(fill='x')
            ttk.Label(row,text=f'Ch {p+1}').pack(side='left')
            picker = ColorPicker(row,state['color']); picker.button.configure(width=6); picker.pack(side='left',padx=3)
            ttk.Button(row,text='自動',command=lambda page=p:self.auto(page)).pack(side='left')
            for key,label in [('low','下限'),('high','上限')]:
                row = ttk.Frame(bar); row.pack(fill='x')
                ttk.Label(row,text=label).pack(side='left')
                scale = ArrowScale(row,variable=state[key],from_=0,to=65535,length=90,
                                  command=lambda value,page=p:self.manual(page))
                scale.pack(side='left',fill='x',expand=True); state['scales'].append(scale)
                bind_intensity_keys(scale,state[key],lambda page=p:self.manual(page))
                entry = ttk.Entry(row,textvariable=state[key],width=7); entry.pack(side='left')
                state['entries'].append(entry)
                bind_intensity_keys(entry,state[key],lambda page=p:self.manual(page))
                entry.bind('<Return>',lambda e,page=p:self.manual(page))
            state['trace'] = state['color'].trace_add('write',lambda *args:self.color_changed())
            self.channels.append(state)
        self.app.channel_colors.refresh()

    def color_changed(self):
        for state in self.channels:state['check'].update_color()
        for widget in getattr(self.app,'save_channel_checks',[]):
            if hasattr(widget,'update_color'):widget.update_color()
        self.app.channel_colors.refresh()
        self.app.request_preview_draw()

    def clear_raw_cache(self):
        for key in list(self.cache):
            if key[5]:self.cache.pop(key).close()
        for name,(key,image) in list(self.merge_cache.items()):
            if key[1]:image.close();del self.merge_cache[name]

    def auto(self,page):
        self.channels[page]['manual'] = False; self.channels[page]['index'] = None
        self.app.request_preview_draw()

    def manual(self,page):
        state = self.channels[page]
        try:
            limits = (state['low'].get(),state['high'].get())
            if not all(np.isfinite(limits)) or limits[0]>=limits[1]: raise ValueError()
        except (ValueError,tk.TclError):
            state['low'].set(state['limits'][0]); state['high'].set(state['limits'][1]); return
        state.update(manual=True,limits=limits); self.app.request_preview_draw()

    def image(self,name,page=None):
        self.ensure()
        page = self.focus_page if page is None and self.focus_page is not None else (page or 0)
        app = self.app; index = app.preview_index
        if name=='result' and (not app.alignment_matrices or app.alignment_matrices[index] is None): return None
        pixels = app._preview_cache.get_plane(index,page)
        state = self.channels[page]
        if state['index']!=index and not state['manual']:
            limits = display_pixels(pixels)[1]
            state.update(limits=limits,index=index); state['low'].set(limits[0]); state['high'].set(limits[1])
        if state['bounds_index']!=index:
            lo,hi = app._preview_cache.page_ranges[index,page]
            for scale in state['scales']: scale.configure(from_=lo,to=hi)
            state['bounds_index'] = index
        canvas = app.display_canvas_size()
        matrix = np.eye(3) if name=='source' else app.alignment_matrices[index]
        raw = app.raw_display.get()
        key = (index,page,name,tuple(matrix.ravel()),tuple(canvas),raw,state['limits'],state['color'].get())
        if key not in self.cache:
            if raw:
                self.cache[key]=colored_native(app.analysis_files[index].path,page,matrix,canvas,state['limits'],state['color'].get())
                while len(self.cache)>max(12,2*len(self.channels)):self.cache.popitem(last=False)[1].close()
                return self.cache[key]
            else:
                sw,sh = app._preview_cache.entries[index][0]
                scale = min(1.,768/max(canvas))
                pad = np.array([[1.,0.,(canvas[0]-sw)//2],[0.,1.,(canvas[1]-sh)//2],[0.,0.,1.]])
                source_scale = np.diag([sw/pixels.shape[1],sh/pixels.shape[0],1.])
                transform = np.diag([scale,scale,1.]) @ matrix @ pad @ source_scale
                inv = np.linalg.inv(transform)
                source = Image.fromarray(pixels)
                working = source.convert('I') if source.mode.startswith('I;16') else source
                image = working.transform((int(np.ceil(canvas[0]*scale)),int(np.ceil(canvas[1]*scale))),
                    Image.Transform.AFFINE,tuple(inv[:2].ravel()),Image.Resampling.BILINEAR,fillcolor=0)
                if working is not source: working.close()
                source.close()
            try: colored = color_pixels(display_pixels(np.asarray(image),limits=state['limits'])[0],state['color'].get())
            finally: image.close()
            self.cache[key] = Image.fromarray(colored)
            while len(self.cache)>(2 if raw else 12): self.cache.popitem(last=False)[1].close()
        self.cache.move_to_end(key)
        return self.cache[key]

    def toggle(self,event,name):
        if not self.active: return False
        if self.merged:
            self.app.reset_all_image_views(); return True
        if self.focus_page is not None: self.focus_page = None
        else:
            for page,left,right in self.regions.get(name,[]):
                if left<=event.x<right: self.focus_page = page; break
        self.app.reset_all_image_views(); self.app.request_preview_draw()
        return True

    def toggle_merged(self):
        if not self.active: return
        self.ensure()
        self.merged = not self.merged; self.focus_page = None
        self.app.merged_button.configure(style='MergedOn.TButton' if self.merged else 'TButton')
        if self.merged: self.app.merged_channels.pack(side='left',padx=(8,0))
        else: self.app.merged_channels.pack_forget()
        self.app.reset_all_image_views()

    def merged_image(self,name):
        self.ensure()
        app = self.app
        if name=='result' and (not app.alignment_matrices or app.alignment_matrices[app.preview_index] is None): return None
        for p,state in enumerate(self.channels):
            if state['include'].get() and not state['manual'] and state['index']!=app.preview_index: self.image('source',p)
        matrix = np.eye(3) if name=='source' else app.alignment_matrices[app.preview_index]
        key = (app.preview_index,app.raw_display.get(),tuple(app.display_canvas_size()),tuple(matrix.ravel()),
               tuple((s['limits'],s['color'].get(),s['include'].get()) for s in self.channels))
        if name not in self.merge_cache or self.merge_cache[name][0]!=key:
            merged = None
            for page in range(len(self.channels)):
                if not self.channels[page]['include'].get(): continue
                image = self.image(name,page)
                with image.convert('RGB') as rgb: values = np.asarray(rgb)
                if merged is None: merged = values.astype(np.uint16)
                else:
                    merged += values
                    np.minimum(merged,255,out=merged)
            if merged is None:
                w,h = app.display_canvas_size()
                scale = 1. if app.raw_display.get() else min(1.,768/max(w,h))
                result = Image.new('RGB',(int(np.ceil(w*scale)),int(np.ceil(h*scale))),'black')
            else: result = Image.fromarray(merged.astype(np.uint8))
            if name in self.merge_cache: self.merge_cache[name][1].close()
            self.merge_cache[name] = (key,result)
        return self.merge_cache[name][1]

    def draw(self):
        if not self.active: return False
        app = self.app; self.ensure(); self.photos.clear()
        try: reference = int(app.alignment_channel.get())-1
        except ValueError: reference = 0
        for p,state in enumerate(self.channels):
            state['check'].configure(text=f'Ch {p+1}'+('（基準）' if p==reference else ''))
        if self.merged:
            self.regions = {}
            for name,canvas in [('source',app.preview_canvas),('result',app.result_canvas)]:
                canvas.delete('all')
                image = self.merged_image(name)
                if image is None:
                    canvas.create_text(canvas.winfo_width()/2,canvas.winfo_height()/2,text='アライン未実行',fill='white'); continue
                photo = app.draw_view(canvas,image,name)
                if photo is not None: self.photos.append(photo)
            source = self.merged_image('source')
            count = sum(s['include'].get() for s in self.channels)
            app.preview_frame.configure(text=f'画像プレビュー（merged · {count}/{len(self.channels)}チャンネル · {source.width} × {source.height} px）')
            app.analysis_lower.configure(text=f'アライン後（merged · {count}/{len(self.channels)}チャンネル）')
            return True
        pages = list(range(app._tiff_pages)) if self.focus_page is None else [self.focus_page]
        for name,canvas in [('source',app.preview_canvas),('result',app.result_canvas)]:
            canvas.delete('all'); width,height = max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
            self.regions[name] = []
            for slot,page in enumerate(pages):
                left,right = slot*width/len(pages),(slot+1)*width/len(pages)
                self.regions[name].append((page,left,right))
                image = self.image(name,page)
                if image is None:
                    canvas.create_rectangle(left+1,1,right-1,height-1,outline='#e53935' if page==reference else '#697580',width=3 if page==reference else 1,tags=(f'channel-frame-{page}',))
                    canvas.create_text((left+right)/2,height/2,text=f'Ch {page+1} · アライン未実行',fill='white'); continue
                if self.focus_page is not None:
                    photo = app.draw_view(canvas,image,name)
                    if photo is not None: self.photos.append(photo)
                else:
                    photo=app.draw_view(canvas,image,name,box=(left+6,104,right-6,height-6))
                    if photo is not None:self.photos.append(photo)
                canvas.create_rectangle(left+1,1,right-1,height-1,outline='#e53935' if page==reference else '#697580',width=3 if page==reference else 1,tags=(f'channel-frame-{page}',))
                if name=='source':
                    if self.focus_page is not None:canvas.create_window(6,28,window=self.channels[page]['bar'],anchor='nw',width=max(1,app.preview_sidebar()-12))
                    else:canvas.create_window((left+right)/2,4,window=self.channels[page]['bar'],anchor='n')
                else: canvas.create_text(left+8,8,text=f'Ch {page+1}',anchor='nw',fill='white')
        detail = f'Ch {self.focus_page+1} 単独表示' if self.focus_page is not None else f'{app._tiff_pages}チャンネル横並び'
        sample = self.image('source',pages[0])
        size = f'{sample.width} × {sample.height} px'
        mode = '元解像度' if app.raw_display.get() else '表示用に縮小'
        app.preview_frame.configure(text=f'画像プレビュー（{detail}・{mode}: {size}）')
        app.analysis_lower.configure(text=f'アライン後（{detail}）')
        return True

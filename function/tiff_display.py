# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""TIFF inspection, selected channel stacks, and display-only color merging."""
import re
from collections import OrderedDict
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import numpy as np
from PIL import Image,ImageTk
from alignment import check
from color_picker import ColorPicker
from intensity_controls import bind_intensity_keys
from image_sources import image_range
from scale_bars import ScaleBarControls,horizontal_bar,xy_spacing
from colors import color_rgb


def page_layout(count,width,height):
    return [(i*width/count,0,(i+1)*width/count,height) for i in range(count)]


def parse_ranges(text,count):
    text = re.sub(r'\s+','',text).replace('〜','~').replace('～','~').replace('，',',')
    selected = set()
    if not text: raise ValueError('枚目を入力してください（例: 1~5,8,10）')
    for item in text.split(','):
        if not re.fullmatch(r'\d+(~\d+)?',item): raise ValueError('範囲は1~5,8,10の形式で入力してください')
        pair = list(map(int,item.split('~')))
        start,end = pair[0],pair[-1]
        if not 1<=start<=end<=count: raise ValueError(f'枚目は1〜{count}の昇順の範囲で指定してください')
        selected.update(range(start-1,end))
    return tuple(sorted(selected))


def display_pixels(pixels,auto=True,limits=None):
    colors = pixels[...,:3] if pixels.ndim==3 and pixels.shape[-1]==4 else pixels
    finite = colors[np.isfinite(colors)]
    if limits is not None: low,high = limits
    elif auto and finite.size:
        low,high = np.percentile(finite,[1,99.5])
        if high<=low: low,high = float(finite.min()),float(finite.max())
        if high<=low: low,high = 0.,max(float(high),1.)
    elif np.issubdtype(pixels.dtype,np.integer):
        bounds = np.iinfo(pixels.dtype); low,high = float(bounds.min),float(bounds.max)
    elif finite.size: low,high = float(finite.min()),float(finite.max())
    else: low,high = 0.,1.
    values = np.nan_to_num((colors.astype(float)-low)*255/max(float(high-low),1e-12),nan=0,posinf=255,neginf=0)
    converted = np.clip(values,0,255).astype(np.uint8)
    if pixels.ndim==3 and pixels.shape[-1]==4:
        alpha = pixels[...,3]
        if pixels.dtype!=np.uint8: alpha = np.clip(alpha.astype(float)*255/np.iinfo(pixels.dtype).max,0,255)
        converted = np.dstack((converted,alpha.astype(np.uint8)))
    return converted,(float(low),float(high))


def color_pixels(pixels,color):
    gray = pixels if pixels.ndim==2 else np.rint(pixels[...,:3].astype(float) @ np.array([.2126,.7152,.0722])).astype(np.uint8)
    if color=='Gray': return gray
    return ((gray.astype(np.uint16)[...,None]*np.array(color_rgb(color),np.uint16)+127)//255).astype(np.uint8)


def channel_projection(array,page,indices,cancel=None,progress=None):
    if not indices: raise ValueError('スタックする画像がありません')
    stride = max(1,int(np.ceil(max(array.shape[2:4])/768)))
    projection = None
    for i,index in enumerate(indices):
        check(cancel)
        small = np.array(array[index,page,::stride,::stride],copy=True)
        if projection is None: projection = small
        else: np.fmax(projection,small,out=projection)
        if progress: progress(i+1,len(indices),f'チャンネル {page+1}: {index+1}枚目')
    check(cancel)
    return projection


def merge_colors(images):
    if not images: raise ValueError('先にstackを実行してください')
    shape = images[0].shape[:2]
    merged = np.zeros((*shape,3),np.float32)
    for image in images:
        if image.shape[:2]!=shape: raise ValueError('スタック画像のサイズが一致しません')
        merged += image[...,None] if image.ndim==2 else image[...,:3]
    return np.clip(merged,0,255).astype(np.uint8)


class TiffViews(ttk.Frame):
    def __init__(self,parent,panel):
        super().__init__(parent)
        self.panel = panel
        self.channels = []
        self.bars = {'raw':[],'stack':[]}
        self.photos = []; self.regions = []
        self.raw_cache = OrderedDict()
        self.merge_ready = False
        self.merge_result = None
        self.merge_checks=[]
        self.notebook = ttk.Notebook(self); self.notebook.pack(fill='both',expand=True)
        self.frames,self.canvases = {},{}
        self.scale_controls={}
        for name,label in [('raw','raw tiff'),('stack','stack'),('merge','merge')]:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame,text=label); self.frames[name] = frame
            if name=='merge':
                row=ttk.Frame(frame);row.pack(fill='x',padx=6,pady=6)
                ttk.Button(row,text='merge',command=self.merge).pack(side='left')
                self.merge_choices=ttk.Frame(row);self.merge_choices.pack(side='left',padx=10)
            if name=='stack':
                self.stack_all_button=ttk.Button(frame,text='全チャンネルstack',command=self.panel.stack_all)
                self.stack_all_button.pack(anchor='w',padx=6,pady=6)
            scale=ScaleBarControls(frame,self.panel.request_draw)
            scale.pack(anchor='e',padx=6,pady=2);self.scale_controls[name]=scale
            canvas = tk.Canvas(frame,background='#181e24',highlightthickness=0)
            canvas.image_view = True
            canvas.pack(fill='both',expand=True)
            canvas.bind('<Configure>',lambda e:self.panel.request_draw())
            self.canvases[name] = canvas
        self.notebook.bind('<<NotebookTabChanged>>',lambda e:self.changed_tab())
        from stack_save_panel import StackSavePanel
        self.save_panel=StackSavePanel(self.notebook,panel)
        self.notebook.add(self.save_panel,text='save');self.frames['save']=self.save_panel

    def active(self):
        selected = self.notebook.select()
        return next((k for k,v in self.frames.items() if str(v)==selected),'raw')

    def changed_tab(self):
        if self.active()=='save':self.save_panel.sync_default()
        if self.active()=='raw': self.panel.navigation.grid()
        else: self.panel.navigation.grid_remove()
        self.panel.request_draw()

    def reset(self):
        for widget in self.merge_checks:widget.destroy()
        self.merge_checks.clear()
        for bars in self.bars.values():
            for bar in bars: bar.destroy()
            bars.clear()
        for state in self.channels: state['color'].trace_remove('write',state['trace'])
        self.channels.clear(); self.raw_cache.clear(); self.photos.clear(); self.regions = []
        self.merge_ready = False; self.merge_result = None
        for canvas in self.canvases.values(): canvas.delete('all')

    def ensure(self,count):
        if len(self.channels)==count: return
        self.reset()
        for p in range(count):
            state = {'color':tk.StringVar(value='Gray'),'low':tk.DoubleVar(value=0.),'high':tk.DoubleVar(value=255.),
                     'manual':False,'limits':(0.,255.),'scales':[],'scale_sets':{'raw':[],'stack':[]}, 'mode':tk.StringVar(value='all'),
                     'range':tk.StringVar(value=''),'range_entries':[]}
            state['include']=tk.BooleanVar(value=True)
            check=ttk.Checkbutton(self.merge_choices,text=f'Ch {p+1}',variable=state['include'],command=self.merge)
            check.pack(side='left',padx=4);self.merge_checks.append(check)
            state['trace'] = state['color'].trace_add('write',lambda *args:self.panel.request_draw())
            self.channels.append(state)
            for name in ('raw','stack'):
                bar = ttk.Frame(self.canvases[name],padding=4)
                head = ttk.Frame(bar); head.pack(fill='x')
                ttk.Label(head,text=f'チャンネル {p+1}').pack(side='left',padx=3)
                picker = ColorPicker(head,state['color']); picker.button.configure(width=7); picker.pack(side='left',padx=3)
                ttk.Button(head,text='自動',command=lambda page=p:self.auto_range(page)).pack(side='left',padx=3)
                for key,label in [('low','下限'),('high','上限')]:
                    row = ttk.Frame(bar); row.pack(fill='x')
                    ttk.Label(row,text=label).pack(side='left')
                    scale = ttk.Scale(row,from_=0,to=65535,variable=state[key],length=130,
                                      command=lambda value,page=p:self.manual_range(page))
                    scale.pack(side='left',fill='x',expand=True,padx=4); state['scales'].append(scale)
                    state['scale_sets'][name].append(scale)
                    bind_intensity_keys(scale,state[key],lambda page=p:self.manual_range(page))
                    entry = ttk.Entry(row,textvariable=state[key],width=9); entry.pack(side='left')
                    bind_intensity_keys(entry,state[key],lambda page=p:self.manual_range(page))
                    entry.bind('<Return>',lambda e,page=p:self.manual_range(page))
                if name=='stack':
                    row = ttk.Frame(bar); row.pack(fill='x',pady=(3,0))
                    for mode in ('all','select'):
                        ttk.Radiobutton(row,text=mode,value=mode,variable=state['mode'],command=lambda page=p:self.range_mode(page)).pack(side='left')
                    entry = ttk.Entry(row,textvariable=state['range'],width=16,state='disabled')
                    entry.pack(side='left',padx=4); state['range_entries'].append(entry)
                    ttk.Button(row,text='stack',command=lambda page=p:self.panel.stack_channel(page)).pack(side='left')
                self.bars[name].append(bar)
            self.set_image_bounds(p,np.zeros((1,1),np.uint8),'raw')

    def range_mode(self,p):
        for entry in self.channels[p]['range_entries']:
            entry.state(['!disabled'] if self.channels[p]['mode'].get()=='select' else ['disabled'])

    def manual_range(self,p):
        state = self.channels[p]
        try:
            low,high = state['low'].get(),state['high'].get()
            if not np.isfinite(low) or not np.isfinite(high) or low>=high: raise ValueError()
        except (ValueError,tk.TclError):
            low,high = state['limits']; state['low'].set(low); state['high'].set(high)
            self.panel.status.set('表示範囲は、下限より上限を大きくしてください。'); return
        state.update(manual=True,limits=(low,high))
        self.panel.request_draw()

    def auto_range(self,p):
        self.channels[p]['manual'] = False; self.panel.request_draw()

    def raw_pixels(self,n,p):
        key = (n,p)
        if key not in self.raw_cache:
            if hasattr(self.panel.stack,'preview'):
                self.raw_cache[key] = self.panel.stack.preview(n,p)
                while len(self.raw_cache)>12: self.raw_cache.popitem(last=False)
                return self.raw_cache[key]
            prepared = Path(self.panel.stack.directory.name)/f'raw_{n}_{p}.npy'
            if prepared.exists(): self.raw_cache[key] = np.load(prepared,allow_pickle=False)
            else:
                path = self.panel.stack.params['images'][n]['stack_image']
                with Image.open(path) as source:
                    source.seek(p)
                    stride = max(1,int(np.ceil(max(source.size)/768)))
                    self.raw_cache[key] = np.array(np.asarray(source)[::stride,::stride],copy=True)
            while len(self.raw_cache)>12: self.raw_cache.popitem(last=False)
        self.raw_cache.move_to_end(key)
        return self.raw_cache[key]

    def set_image_bounds(self,p,pixels,kind):
        if kind=='raw':
            n = self.panel._current_page
            if hasattr(self.panel.stack,'page_range'): bounds = self.panel.stack.page_range(n,p)
            else:
                ranges = self.panel.stack.params['images'][n].get('page_ranges')
                bounds = ranges[p] if ranges else image_range(pixels)
        else: bounds = image_range(pixels)
        state = self.channels[p]
        scales = list(state['scale_sets'][kind])
        if kind=='raw' and p not in self.panel.channel_stacks: scales += state['scale_sets']['stack']
        for scale in scales: scale.configure(from_=bounds[0],to=bounds[1])
        return tuple(bounds)

    def rendered(self,p,pixels):
        state = self.channels[p]
        converted,limits = display_pixels(pixels,True,state['limits'] if state['manual'] else None)
        if not state['manual']:
            state['limits'] = limits; state['low'].set(limits[0]); state['high'].set(limits[1])
        self.set_image_bounds(p,pixels,'raw')
        return color_pixels(converted,state['color'].get()),limits

    def merge(self):
        selected=[p for p,state in enumerate(self.channels) if state['include'].get()]
        if self.panel.stack is None or any(p not in self.panel.channel_stacks for p in selected):
            self.merge_ready = False; self.merge_result = None
            self.panel.status.set('先にstackタブで選択チャンネルのstackを実行してください。')
        else: self.merge_ready = True
        self.panel.request_draw()

    def rendered_stack(self,p):
        record = self.panel.channel_stacks[p]
        self.set_image_bounds(p,record['projection'],'stack')
        pixels,_ = display_pixels(record['projection'],limits=record['display_limits'])
        return color_pixels(pixels,self.channels[p]['color'].get())

    def put_image(self,canvas,pixels,box,header):
        left,top,right,bottom = box
        with Image.fromarray(pixels) as image:
            image.thumbnail((max(1,int(right-left)-16),max(1,int(bottom-top)-header-10)))
            photo = ImageTk.PhotoImage(image,master=self)
        self.photos.append(photo)
        cx,cy=(left+right)/2,(top+bottom+header)/2
        canvas.create_image(cx,cy,image=photo)
        spacing=xy_spacing(self.panel.app)
        canvas_width=self.panel.stack.params['canvas_xy'][0]
        ppm=photo.width()/canvas_width/spacing[0] if spacing else None
        horizontal_bar(canvas,self.scale_controls[self.active()],ppm,(cx-photo.width()/2,cy-photo.height()/2,cx+photo.width()/2,cy+photo.height()/2))

    def draw(self):
        name = self.active()
        if name=='save':return
        canvas = self.canvases[name]
        canvas.delete('all'); self.photos.clear()
        if self.panel.stack is None: return
        count = self.panel.stack.array.shape[1]; self.ensure(count)
        width,height = max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        if name=='raw' and self.panel._empty_display:
            canvas.create_text(width/2,height/2,text='no image',fill='white')
            self.panel.info.set(f'画像 {self.panel._display_slot+1}/{self.panel.total_pages()} · no image');return
        if name=='merge':
            if not self.merge_ready:
                canvas.create_text(width/2,height/2,text='先にstackタブで各チャンネルのstackを実行し、mergeを押してください。',fill='white',width=max(1,width-20))
                return
            images = [self.rendered_stack(p) for p in range(count) if self.channels[p]['include'].get()]
            if not images:
                self.merge_result=None
                canvas.create_text(width/2,height/2,text='表示するチャンネルを選択してください。',fill='white');return
            self.merge_result = merge_colors(images)
            self.put_image(canvas,self.merge_result,(0,0,width,height),0)
            self.panel.info.set('merge · チャンネル別stackの最大値投影を加算合成（表示用）')
            return
        self.regions = page_layout(count,width,height)
        for p,box in enumerate(self.regions):
            left,top,right,bottom = box
            bar = self.bars[name][p]
            header = 140 if name=='stack' else 110
            canvas.create_rectangle(left+2,top+2,right-2,bottom-2,outline='#697580')
            canvas.create_window((left+right)/2,top+4,window=bar,anchor='n')
            if p==self.panel.reference_channel:
                canvas.create_rectangle(left+2,top+2,right-2,bottom-2,outline='#e53935',width=3)
            if name=='raw': pixels = self.raw_pixels(self.panel._current_page,p)
            elif p in self.panel.channel_stacks: pixels = self.panel.channel_stacks[p]['projection']
            else:
                canvas.create_text((left+right)/2,(top+bottom+header)/2,text='stackを実行してください。\nselectの例: 1~5,8,10',fill='white')
                continue
            if name=='raw': pixels,limits = self.rendered(p,pixels)
            else: pixels = self.rendered_stack(p)
            self.put_image(canvas,pixels,box,header)
            if name=='stack':
                indices = self.panel.channel_stacks[p]['indices']
                lo,hi = self.panel.channel_stacks[p]['display_limits']
                canvas.create_text(left+8,bottom-10,anchor='sw',fill='white',text=f'{len(indices)}枚 · 最大値投影 · stack時の範囲 {lo:.5g}〜{hi:.5g}')
        if name=='raw':
            n = self.panel._current_page
            file = Path(self.panel.stack.params['images'][n]['stack_image']).name
            aligned = self.panel.stack.params.get('aligned',False)
            detail = 'アライン結果を反映' if aligned else '元TIFF'
            reference = f' · 基準Ch {self.panel.reference_channel+1}' if self.panel.reference_channel is not None else ''
            self.panel.info.set(f'{file} · 画像 {self.panel._display_slot+1}/{self.panel.total_pages()} · 全{count}ページを同時表示（{detail}）{reference}')
        else: self.panel.info.set('stack · 読み込み順の枚目を指定 · アライン後の画像を使用')

# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Physical display overlays and per-view scale-bar controls."""
import math
import tkinter as tk
from tkinter import ttk,colorchooser
from PIL import Image,ImageDraw,ImageFont


def xy_spacing(app):
    panel=app.volume_panel
    calibration=panel.calibration or {}
    if calibration.get('status')=='loaded':return tuple(calibration['spacing_xy_um'])
    if calibration.get('status')=='error':return None
    if getattr(panel,'_manual_xy_calibrated',False):
        try:
            values=tuple(var.get() for var in panel.spacing[:2])
            if all(math.isfinite(v) and v>0 for v in values):return values
        except tk.TclError:pass
    return None


class ScaleBarControls(ttk.Frame):
    def __init__(self,parent,command,allow_color=True):
        super().__init__(parent)
        self.enabled=tk.BooleanVar(value=True)
        self.value=tk.StringVar(value='50')
        self.command=command
        self.check=ttk.Checkbutton(self,text='スケールバー',variable=self.enabled,command=self.changed)
        self.check.pack(side='left')
        self.entry=ttk.Entry(self,textvariable=self.value,width=8)
        self.entry.pack(side='left',padx=4)
        ttk.Label(self,text='µm').pack(side='left')
        self.show_text=tk.BooleanVar(value=True)
        self.color=tk.StringVar(value='#ffffff')
        self.text_check=ttk.Checkbutton(self,text='文字',variable=self.show_text,command=self.changed)
        self.text_check.pack(side='left',padx=(4,0))
        if allow_color:
            self.color_button=tk.Button(self,text='色',width=3,background='white',foreground='black',command=self.choose_color)
            self.color_button.pack(side='left',padx=3)
        self.entry.bind('<Return>',self.changed)
        self.entry.bind('<FocusOut>',self.changed)

    def choose_color(self):
        _,color=colorchooser.askcolor(color=self.color.get(),parent=self.winfo_toplevel(),title='スケールバーの色')
        if color:
            self.color.set(color)
            light=sum(int(color[i:i+2],16)*weight for i,weight in zip((1,3,5),(.2126,.7152,.0722)))
            self.color_button.configure(background=color,foreground='black' if light>150 else 'white')
            self.changed()

    def settings(self):
        return {'visible':self.enabled.get(),'length_um':self.length(),'show_text':self.show_text.get(),'color':self.color.get()}

    def changed(self,event=None):
        self.entry.state(['!disabled'] if self.enabled.get() else ['disabled'])
        self.command()

    def length(self):
        try:
            value=float(self.value.get())
            return value if self.enabled.get() and math.isfinite(value) and value>0 else None
        except ValueError:return None


def horizontal_bar(canvas,controls,pixels_per_um,box):
    length=controls.length()
    if length is None:return
    left,top,right,bottom=box
    if right-left<30 or bottom-top<30:return
    if pixels_per_um is None:
        canvas.create_text(right-8,bottom-8,anchor='se',text='画素サイズ未設定',fill='white',tags='scale-bar')
        return
    pixels=length*pixels_per_um
    if pixels>right-left-20:
        canvas.create_text(right-8,bottom-8,anchor='se',text='スケールバーの長さを短くしてください',fill='white',tags='scale-bar')
        return
    x,y=right-10,bottom-12
    color=controls.color.get()
    rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
    outline='white' if sum(rgb)/3<100 else 'black'
    for color,width in [(outline,5),(color,3)]:
        canvas.create_line(x-pixels,y,x,y,fill=color,width=width,tags='scale-bar')
    if controls.show_text.get():canvas.create_text(x-pixels/2,y-10,text=f'{length:g} µm',fill=controls.color.get(),tags='scale-bar')


def burn_scale_bar(image,settings,spacing_xy):
    """Return an RGB copy with a calibrated horizontal bar; never change source."""
    result=image.convert('RGB')
    if not settings['visible']:return result
    if not spacing_xy or not all(math.isfinite(v) and v>0 for v in spacing_xy):
        result.close();raise ValueError('スケールバーの保存にはX/Yの画素サイズを設定してください。')
    length=settings['length_um']
    if length is None or not math.isfinite(length) or length<=0:
        result.close();raise ValueError('スケールバーの長さを正の数で指定してください。')
    pixels=length/spacing_xy[0];margin=max(10,round(min(result.size)*.025))
    if pixels>result.width-2*margin:
        result.close();raise ValueError('スケールバーの長さが画像幅を超えます。短くしてください。')
    draw=ImageDraw.Draw(result);x,y=result.width-margin,result.height-margin
    width=max(2,round(min(result.size)/250));color=settings['color']
    draw.line((x-pixels,y,x,y),fill=color,width=width)
    if settings['show_text']:
        size=max(12,min(96,round(min(result.size)/35)))
        try:font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',size)
        except OSError:font=ImageFont.load_default()
        draw.text((x-pixels/2,y-width-4),f'{length:g} µm',fill=color,font=font,anchor='mb',stroke_width=max(1,width//3),stroke_fill='black' if color.lower()!='#000000' else 'white')
    return result

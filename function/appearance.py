# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Persistent monochrome appearance with Windows application-theme tracking."""
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from app_paths import APP_ROOT
from slider_controls import bind_slider

MODES = {'dark':'黒基調','light':'白基調','system':'PCの環境設定に合わせる'}


def system_is_dark():
    if os.name=='nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return winreg.QueryValueEx(key,'AppsUseLightTheme')[0]==0
        except OSError: pass
    return False


class Appearance:
    def __init__(self,root,path=None):
        self.root = root
        self.path = Path(path) if path else APP_ROOT/'.local/appearance.json'
        try: mode = json.loads(self.path.read_text(encoding='utf-8'))['mode']
        except (OSError,ValueError,KeyError,TypeError): mode = 'dark'
        self.mode = tk.StringVar(master=root,value=mode if mode in MODES else 'dark')
        self.dark = None
        self.job = None
        root.bind_all('<Map>',self.mapped,add='+')

    def set_mode(self):
        if self.mode.get() not in MODES: self.mode.set('dark')
        self.apply()
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'mode':self.mode.get()}),encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            self.root.add_log('表示テーマの設定を保存できません。',level='error')

    def mapped(self,event):
        if self.dark is not None: self.paint_widget(event.widget)

    def paint_widget(self,widget):
        try:
            bind_slider(widget)
            if isinstance(widget,(tk.Tk,tk.Toplevel,tk.Frame)):
                widget.configure(background=self.bg)
            elif isinstance(widget,tk.Text):
                widget.configure(background=self.field,foreground=self.fg,insertbackground=self.fg,
                    selectbackground=self.selected,selectforeground=self.fg,highlightbackground=self.border)
            elif isinstance(widget,tk.Canvas): widget.configure(background='#101010' if getattr(widget,'image_view',False) else self.field,highlightbackground=self.border)
            elif isinstance(widget,tk.Listbox): widget.configure(background=self.field,foreground=self.fg,selectbackground=self.selected,selectforeground=self.fg)
            elif isinstance(widget,ttk.Label): widget.configure(foreground=self.fg)
            if hasattr(widget,'update_color'): widget.update_color()
        except (tk.TclError,AttributeError): pass

    def apply(self):
        self.dark = self.mode.get()=='dark' or (self.mode.get()=='system' and system_is_dark())
        self.bg,self.field,self.fg,self.border,self.selected,self.disabled = (
            ('#181818','#101010','#eeeeee','#505050','#484848','#858585') if self.dark else
            ('#f4f4f4','#ffffff','#161616','#b5b5b5','#d0d0d0','#777777'))
        panel = '#292929' if self.dark else '#e6e6e6'
        inverse_bg,inverse_fg = ('#ffffff','#000000') if self.dark else ('#000000','#ffffff')
        style = ttk.Style(self.root)
        style.configure('.',background=self.bg,foreground=self.fg,fieldbackground=self.field,
                        bordercolor=self.border,lightcolor=self.border,darkcolor=self.border,troughcolor=panel,
                        selectbackground=self.selected,selectforeground=self.fg,arrowcolor=self.fg)
        style.map('.',foreground=[('disabled',self.disabled)])
        for name in ('TButton','TMenubutton'):
            style.configure(name,background=panel,foreground=self.fg)
            style.map(name,background=[('disabled',self.bg),('pressed',inverse_bg),('selected',inverse_bg),('active',self.selected)],foreground=[('disabled',self.disabled),('pressed',inverse_fg),('selected',inverse_fg),('!disabled',self.fg)])
        style.configure('Go.TButton',background=panel,foreground=self.fg)
        style.map('Go.TButton',background=[('disabled',self.bg),('pressed',inverse_bg),('active',self.selected)],foreground=[('disabled',self.disabled),('pressed',inverse_fg),('!disabled',self.fg)])
        style.configure('MergedOn.TButton',background=inverse_bg,foreground=inverse_fg)
        style.map('MergedOn.TButton',background=[('disabled',self.bg),('!disabled',inverse_bg)],foreground=[('disabled',self.disabled),('!disabled',inverse_fg)])
        style.configure('TNotebook.Tab',background=panel,foreground=self.fg)
        style.map('TNotebook.Tab',background=[('selected',inverse_bg),('active',self.selected)],foreground=[('disabled',self.disabled),('selected',inverse_fg),('!disabled',self.fg)])
        for name in ('TEntry','TCombobox','TSpinbox'):
            style.configure(name,fieldbackground=self.field,foreground=self.fg,insertcolor=self.fg,background=panel)
            style.map(name,fieldbackground=[('disabled',panel),('readonly',self.field)],foreground=[('disabled',self.disabled),('!disabled',self.fg)],selectbackground=[('!disabled',self.selected)],selectforeground=[('!disabled',self.fg)])
        style.configure('Treeview',background=self.field,fieldbackground=self.field,foreground=self.fg)
        style.configure('Treeview.Heading',background=panel,foreground=self.fg)
        style.map('Treeview',background=[('selected',self.selected)],foreground=[])
        for name in ('TCheckbutton','TRadiobutton'):
            style.configure(name,background=self.bg,foreground=self.fg,indicatorbackground=self.field,indicatorforeground=self.fg)
            style.map(name,background=[('disabled',self.bg),('selected',inverse_bg),('pressed',inverse_bg),('active',panel)],foreground=[('disabled',self.disabled),('selected',inverse_fg),('pressed',inverse_fg),('!disabled',self.fg)],indicatorbackground=[('disabled',panel),('selected',inverse_bg)],indicatorforeground=[('selected',inverse_fg)])
        style.configure('Horizontal.TProgressbar',background=self.fg,troughcolor=panel)
        for name in ('Horizontal.TScale','Vertical.TScale'):
            style.configure(name,background=self.fg,troughcolor=panel)
            style.map(name,troughcolor=[('disabled',panel),('focus',inverse_bg),('!focus',panel)],
                background=[('disabled',self.disabled),('focus',inverse_fg),('!focus',self.fg)],
                bordercolor=[('focus',inverse_fg),('!focus',self.border)],
                lightcolor=[('focus',inverse_fg),('!focus',self.border)],
                darkcolor=[('focus',inverse_fg),('!focus',self.border)])
        self.root.option_add('*TCombobox*Listbox.background',self.field)
        self.root.option_add('*TCombobox*Listbox.foreground',self.fg)
        self.root.option_add('*TCombobox*Listbox.selectBackground',self.selected)
        self.root.option_add('*TCombobox*Listbox.selectForeground',self.fg)
        pending = [self.root]
        while pending:
            widget = pending.pop(); self.paint_widget(widget)
            pending.extend(widget.winfo_children())
        if hasattr(self.root,'file_list'): self.root.file_list.tag_configure('drop-target',background=self.selected)
        if hasattr(self.root,'channel_colors'): self.root.channel_colors.refresh()
        self.title_bar()

    def title_bar(self):
        if os.name!='nt': return
        try:
            import ctypes
            from ctypes import wintypes
            user = ctypes.windll.user32
            user.GetParent.argtypes=[wintypes.HWND];user.GetParent.restype=wintypes.HWND
            handle=user.GetParent(self.root.winfo_id())
            value=ctypes.c_int(int(self.dark))
            api=ctypes.windll.dwmapi.DwmSetWindowAttribute
            api.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
            api(handle,20,ctypes.byref(value),ctypes.sizeof(value))
        except (AttributeError,OSError): pass

    def watch(self):
        self.job = None
        if self.mode.get()=='system' and self.dark!=system_is_dark(): self.apply()
        self.job = self.root.after(2000,self.watch)

    def close(self):
        if self.job is not None:self.root.after_cancel(self.job);self.job=None

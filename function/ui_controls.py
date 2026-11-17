# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Shared dialog placement and mouse-held repeat controls."""
from tkinter import ttk


def center_dialog(root,dialog,width,height):
    root.update_idletasks()
    x=root.winfo_rootx()+(root.winfo_width()-width)//2
    y=root.winfo_rooty()+(root.winfo_height()-height)//2
    dialog.geometry(f'{width}x{height}{x:+d}{y:+d}')
    dialog.update_idletasks()
    # Windows geometry positions include window borders, while root coordinates
    # describe the GUI client area. Correct that decoration offset as well.
    outer_x=x+(x-dialog.winfo_rootx())
    outer_y=y+(y-dialog.winfo_rooty())
    dialog.geometry(f'{width}x{height}{outer_x:+d}{outer_y:+d}')


class RepeatButton(ttk.Button):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.job=None
        self.bind('<ButtonPress-1>',self.press)
        self.bind('<ButtonRelease-1>',self.stop)
        self.bind('<Leave>',self.stop)
        self.bind('<FocusOut>',self.stop)

    def press(self,event=None):
        self.stop()
        if self.instate(['disabled']):return 'break'
        self.focus_set();self.state(['pressed']);self.invoke()
        self.job=self.after(350,self.repeat)
        return 'break'

    def repeat(self):
        self.job=None
        if self.instate(['disabled']) or not self.instate(['pressed']):return
        self.invoke();self.job=self.after(80,self.repeat)

    def stop(self,event=None):
        if self.job is not None:self.after_cancel(self.job);self.job=None
        self.state(['!pressed'])
        return 'break'

    def destroy(self):
        self.stop();super().destroy()

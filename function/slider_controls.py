# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Consistent focus and arrow navigation for every ttk slider."""
import math
import tkinter as tk
from tkinter import ttk


def step_slider(event):
    widget=event.widget
    if widget.instate(['disabled']):return 'break'
    try:
        low,high=sorted((float(widget.cget('from')),float(widget.cget('to'))))
        amount=.01 if high-low<=1 else 1.
        if getattr(event,'state',0)&1:amount*=100 if event.state&4 else 10
        value=widget.get()+(amount if event.keysym in ('Right','Up') else -amount)
        if math.isfinite(value):widget.set(max(low,min(high,value)))
    except (ValueError,tk.TclError):pass
    return 'break'


def bind_slider(widget):
    if not isinstance(widget,ttk.Scale) or getattr(widget,'_consistent_slider',False):return
    widget._consistent_slider=True
    widget.configure(takefocus=True)
    widget.bind('<ButtonPress-1>',lambda event:widget.focus_set(),add='+')
    for key in ('Left','Right','Up','Down'):
        # Keep specialized controls' existing 1/10/100 or slice-step behavior.
        if not widget.bind('<KeyPress-'+key+'>'):widget.bind('<KeyPress-'+key+'>',step_slider)


class ArrowScale(ttk.Scale):
    """A normal ttk scale with one-unit buttons at both ends."""
    def __init__(self,parent,**kwargs):
        self.container=ttk.Frame(parent)
        super().__init__(self.container,**kwargs)
        self.decrease=ttk.Button(self.container,text='◀',width=2,command=lambda:self.nudge(-1))
        self.increase=ttk.Button(self.container,text='▶',width=2,command=lambda:self.nudge(1))
        self.decrease.pack(side='left')
        super().pack(side='left',fill='x',expand=True)
        self.increase.pack(side='right')
        self._sync_buttons()

    def nudge(self,direction):
        if self.instate(['disabled']):return
        self.focus_set()
        low,high=sorted((float(self.cget('from')),float(self.cget('to'))))
        self.set(max(low,min(high,self.get()+direction)))

    def _sync_buttons(self):
        if hasattr(self,'decrease'):
            state=['disabled'] if self.instate(['disabled']) else ['!disabled']
            self.decrease.state(state);self.increase.state(state)

    def state(self,statespec=None):
        result=super().state(statespec)
        self._sync_buttons() if statespec is not None else None
        return result

    def configure(self,cnf=None,**kwargs):
        result=super().configure(cnf,**kwargs)
        self._sync_buttons()
        return result
    config=configure
    def pack(self,*args,**kwargs):return self.container.pack(*args,**kwargs)
    def grid(self,*args,**kwargs):return self.container.grid(*args,**kwargs)
    def place(self,*args,**kwargs):return self.container.place(*args,**kwargs)
    def pack_forget(self):return self.container.pack_forget()
    def grid_remove(self):return self.container.grid_remove()
    def grid_forget(self):return self.container.grid_forget()

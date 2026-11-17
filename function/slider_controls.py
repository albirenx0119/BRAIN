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

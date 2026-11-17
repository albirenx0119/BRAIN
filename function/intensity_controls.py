# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Exact keyboard increments for intensity sliders and numeric fields."""
import math
import tkinter as tk


def step_intensity(event,variable,on_change):
    if event.widget.instate(['disabled']) or event.widget.instate(['readonly']): return 'break'
    amount = 100 if event.state & 1 and event.state & 4 else 10 if event.state & 1 else 1
    direction = 1 if event.keysym in ('Right','Up') else -1
    try:
        value = variable.get()
        if not math.isfinite(value): return 'break'
        variable.set(value+direction*amount)
        on_change()
    except (ValueError,tk.TclError): pass
    return 'break'


def bind_intensity_keys(widget,variable,on_change):
    widget.configure(takefocus=True)
    for key in ('Left','Right','Up','Down'):
        widget.bind('<'+key+'>',lambda event:step_intensity(event,variable,on_change))
    widget.bind('<Button-1>',lambda event:widget.focus_set(),add='+')

# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Channel selection controls whose selected background matches the channel."""
from tkinter import ttk
from colors import color_hex,text_color

class ChannelCheck(ttk.Checkbutton):
    def __init__(self,parent,color,**kwargs):
        self.channel_color=color
        super().__init__(parent,**kwargs)
        self._channel_style='ChannelCheck'+str(self).replace('.','_')+'.TCheckbutton'
        self.configure(style=self._channel_style)
        self.update_color()
        self._color_trace=None
        if hasattr(color,'trace_add'):
            self._color_trace=color.trace_add('write',lambda *args:self.update_color())
            self.bind('<Destroy>',self._destroy_trace,add='+')
    def update_color(self):
        color=self.channel_color() if callable(self.channel_color) else self.channel_color
        if hasattr(color,'get'):color=color.get()
        bg=color_hex(color);fg=text_color(color)
        style=ttk.Style(self)
        style.map(self._channel_style,background=[('selected',bg)],foreground=[('selected',fg)],
                  indicatorbackground=[('selected',bg)],indicatorforeground=[('selected',fg)])

    def _destroy_trace(self,event):
        if event.widget is self and self._color_trace is not None:
            self.channel_color.trace_remove('write',self._color_trace);self._color_trace=None

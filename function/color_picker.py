# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Standard read-only combobox with colored, white-text options."""
import tkinter as tk
from tkinter import ttk,colorchooser
from colors import COLORS,color_rgb,color_hex,valid_color,text_color

DISABLED_BACKGROUND = '#d9dde1'
DISABLED_FOREGROUND = '#888888'


class ChannelColors:
    """Paint a channel combobox using its live per-channel display colors."""
    def __init__(self,combo,variable,colors):
        self.combo,self.variable,self.colors = combo,variable,colors
        self.style = ttk.Style(combo)
        self.name = 'Channel'+str(combo).replace('.','_')+'.TCombobox'
        self.job = None
        combo.configure(style=self.name,postcommand=self.schedule)
        self.trace = variable.trace_add('write',lambda *args:self.refresh())
        combo.bind('<Destroy>',self.destroy,add='+')
        self.refresh()

    def refresh(self):
        colors = self.colors()
        color = colors.get(self.variable.get(),COLORS['Gray'])
        foreground = text_color(color)
        self.style.configure(self.name,fieldbackground=color,background=color,foreground=foreground,arrowcolor=foreground)
        self.style.map(self.name,fieldbackground=[('readonly',color)],foreground=[('!disabled',foreground)],
                       selectbackground=[('readonly',color)],selectforeground=[('readonly',foreground)])

    def schedule(self):
        if self.job is not None: self.combo.after_cancel(self.job)
        self.job = self.combo.after_idle(self.paint)

    def paint(self):
        self.job = None
        pending = [str(self.combo.tk.call('ttk::combobox::PopdownWindow',str(self.combo)))]
        while pending:
            path = pending.pop()
            if self.combo.tk.call('winfo','class',path)=='Listbox':
                colors = self.colors()
                for index,value in enumerate(self.combo.cget('values')):
                    color = colors.get(str(value),COLORS['Gray'])
                    foreground = text_color(color)
                    self.combo.tk.call(path,'itemconfigure',index,'-background',color,'-foreground',foreground,
                                       '-selectbackground',color,'-selectforeground',foreground)
                break
            pending.extend(self.combo.tk.splitlist(self.combo.tk.call('winfo','children',path)))

    def destroy(self,event):
        if event.widget is not self.combo: return
        if self.job is not None: self.combo.after_cancel(self.job)
        self.variable.trace_remove('write',self.trace)


class ColorPicker(ttk.Frame):
    def __init__(self,parent,variable):
        super().__init__(parent)
        self.variable = variable
        self._paint_job = None
        self._style_name = 'Color'+str(self).replace('.','_')+'.TCombobox'
        self._style = ttk.Style(self)
        self.button = ttk.Combobox(self,textvariable=variable,values=list(COLORS)+['color pick'],state='readonly',
                                   style=self._style_name,postcommand=self.schedule_paint)
        self.button.pack(side='left',fill='x',expand=True)
        self.rgb_text = tk.StringVar()
        self.rgb_entry = ttk.Entry(self,textvariable=self.rgb_text,width=14)
        self.rgb_entry.pack(side='left',padx=(4,0))
        self.rgb_entry.bind('<Return>',self.edit_rgb)
        self.rgb_entry.bind('<FocusOut>',self.edit_rgb)
        self.button.bind('<<ComboboxSelected>>',self.selected)
        self._last_color = variable.get() if valid_color(variable.get()) else 'Gray'
        self.listbox = None
        self._trace = variable.trace_add('write',lambda *args:self.update_color())
        self.update_color()

    @property
    def popup(self):
        path = str(self.button)+'.popdown'
        if self.tk.getboolean(self.tk.call('winfo','exists',path)) and self.tk.call('wm','state',path)!='withdrawn':
            return path
        return None

    def update_color(self):
        value = self.variable.get()
        if not valid_color(value): return
        self._last_color = value
        self.rgb_text.set(', '.join(map(str,color_rgb(value))))
        values = list(COLORS)+([] if value in COLORS else [value])+['color pick']
        self.button.configure(values=values)
        color = color_hex(value)
        foreground = text_color(value)
        self._style.configure(self._style_name,fieldbackground=color,background=color,
                              foreground=foreground,arrowcolor=foreground)
        self._style.map(self._style_name,fieldbackground=[('disabled',DISABLED_BACKGROUND),('readonly',color)],
                        background=[('disabled',DISABLED_BACKGROUND),('!disabled',color)],
                        foreground=[('disabled',DISABLED_FOREGROUND),('!disabled',foreground)],
                        arrowcolor=[('disabled',DISABLED_FOREGROUND),('!disabled',foreground)],
                        selectbackground=[('disabled',DISABLED_BACKGROUND),('readonly',color)],
                        selectforeground=[('disabled',DISABLED_FOREGROUND),('readonly',foreground)])

    def state(self,statespec=None):
        result = super().state(statespec)
        if hasattr(self,'button'):
            self.button.state(['disabled'] if self.instate(['disabled']) else ['!disabled','readonly'])
            self.rgb_entry.state(['disabled'] if self.instate(['disabled']) else ['!disabled'])
            if self.instate(['disabled']): self.close_popup()
        return result

    def schedule_paint(self):
        # Native Post configures its listbox, then flushes idle work before mapping.
        if self._paint_job is not None:
            self.after_cancel(self._paint_job)
        self._paint_job = self.after_idle(self.paint_options)

    def paint_options(self):
        self._paint_job = None
        popdown = str(self.tk.call('ttk::combobox::PopdownWindow',str(self.button)))
        pending = [popdown]
        self.listbox = None
        while pending:
            path = pending.pop()
            if self.tk.call('winfo','class',path)=='Listbox':
                self.listbox = path
                break
            pending.extend(self.tk.splitlist(self.tk.call('winfo','children',path)))
        if self.listbox is not None:
            for index,value in enumerate(self.button.cget('values')):
                color = color_hex(value) if valid_color(value) else '#444444'
                foreground = text_color(color)
                self.tk.call(self.listbox,'itemconfigure',index,'-background',color,'-foreground',foreground,
                             '-selectbackground',color,'-selectforeground',foreground)

    def open_popup(self):
        if not self.instate(['disabled']):
            self.tk.call('ttk::combobox::Post',str(self.button))

    def choose(self,value):
        if (valid_color(value) or value=='color pick') and not self.instate(['disabled']):
            self.variable.set(value)
            self.button.event_generate('<<ComboboxSelected>>')
        self.close_popup()

    def selected(self,event=None):
        if self.variable.get()!='color pick': return
        previous = self._last_color
        self.variable.set(previous)
        self.close_popup()
        result = colorchooser.askcolor(color='#%02X%02X%02X'%color_rgb(previous),parent=self,title='表示色を選択')
        if result[1]: self.variable.set(result[1].upper())

    def edit_rgb(self,event=None):
        if self.instate(['disabled']): return
        try:
            text = self.rgb_text.get().strip()
            if valid_color(text) and text.startswith('#'): value = text.upper()
            else:
                parts = text.replace(',',' ').split()
                values = tuple(int(part) for part in parts)
                if len(values)!=3 or any(v<0 or v>255 for v in values): raise ValueError()
                value = '#%02X%02X%02X'%values
            self.variable.set(value)
        except (ValueError,tk.TclError): self.update_color()
        return 'break' if event is not None and event.type==tk.EventType.KeyPress else None

    def close_popup(self):
        self.tk.call('ttk::combobox::Unpost',str(self.button))

    def destroy(self):
        if self._paint_job is not None:
            self.after_cancel(self._paint_job)
        self.close_popup()
        self.variable.trace_remove('write',self._trace)
        super().destroy()

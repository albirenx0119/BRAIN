# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Save options for native-resolution channel stacks and selected-channel merge."""
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from export_data import validate_name
from scale_bars import ScaleBarControls,xy_spacing
from stack_export import export_plan,export_stack
from alignment import Cancelled


class StackSavePanel(ttk.Frame):
    def __init__(self,parent,panel):
        super().__init__(parent,padding=16)
        self.panel=panel;self.app=panel.app
        self.directory=tk.StringVar();self.folder_name=tk.StringVar(value='BRAIN_stack')
        self.name=tk.StringVar(value='BRAIN_stack');self.create_folder=tk.BooleanVar(value=True)
        self.save_stacks=tk.BooleanVar(value=True);self.save_merge=tk.BooleanVar(value=True)
        self.format=tk.StringVar(value='PNG');self.info=tk.StringVar(value='stackを実行した結果を元解像度で保存します。')
        self._last_default=None
        self.columnconfigure(1,weight=1)
        ttk.Label(self,text='stack / merge 保存',style='Title.TLabel').grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,16))
        ttk.Label(self,text='保存先ディレクトリ').grid(row=1,column=0,sticky='w',padx=(0,12),pady=6)
        self.folder_entry=ttk.Entry(self,textvariable=self.directory);self.folder_entry.grid(row=1,column=1,sticky='ew')
        self.app.register_folder_drop(self.folder_entry,lambda path:self.directory.set(str(path)))
        ttk.Button(self,text='フォルダを選択',command=self.choose).grid(row=1,column=2,padx=(8,0))
        ttk.Label(self,text='作成するフォルダ名').grid(row=2,column=0,sticky='w',pady=6)
        ttk.Entry(self,textvariable=self.folder_name).grid(row=2,column=1,sticky='ew')
        ttk.Checkbutton(self,text='フォルダを作成',variable=self.create_folder).grid(row=2,column=2,padx=8)
        ttk.Label(self,text='保存する名前').grid(row=3,column=0,sticky='w',pady=6)
        ttk.Entry(self,textvariable=self.name).grid(row=3,column=1,sticky='ew')
        ttk.Combobox(self,textvariable=self.format,values=['PNG','TIFF','JPEG'],state='readonly',width=12).grid(row=3,column=2,padx=8)
        contents=ttk.LabelFrame(self,text='保存するもの',padding=10);contents.grid(row=4,column=0,columnspan=3,sticky='ew',pady=12)
        ttk.Checkbutton(contents,text='stack画像（作成済みチャンネルすべて）',variable=self.save_stacks).pack(anchor='w',pady=3)
        ttk.Checkbutton(contents,text='merge画像（mergeタブでチェックしたチャンネル）',variable=self.save_merge).pack(anchor='w',pady=3)
        self.scale=ScaleBarControls(contents,lambda:None);self.scale.pack(anchor='w',pady=8)
        ttk.Label(contents,text='元解像度のRGB 8-bit画像。表示色とstack実行時のインテンシティを反映します。',wraplength=900).pack(anchor='w',pady=4)
        ttk.Label(contents,text='名前の後にチャンネル名/色、アライン有無、使用切片範囲を付けます。\nチャンネルごとの条件は「保存名_stack_params.json」に必ず保存します。',wraplength=900).pack(anchor='w',pady=4)
        ttk.Button(self,text='保存を実行',command=self.start).grid(row=5,column=1,sticky='w',pady=8)
        ttk.Label(self,textvariable=self.info,wraplength=1100).grid(row=6,column=0,columnspan=3,sticky='w',pady=8)

    def sync_default(self):
        default=self.app.save_directory.get() or str(self.app.folder or '')
        if not self.directory.get() or self.directory.get()==self._last_default:self.directory.set(default)
        self._last_default=default

    def choose(self):
        path=filedialog.askdirectory(parent=self.app,title='stack / mergeの保存先',initialdir=self.directory.get() or None,mustexist=True)
        if path:self.directory.set(path)

    def start(self):
        panel=self.panel
        if panel.busy or self.app.order_busy():return
        try:
            if panel.stack is None:raise ValueError('先にTIFFを読み込み、stackを実行してください。')
            base=self.name.get().strip();validate_name(base)
            target=Path(self.directory.get()).expanduser().resolve(strict=True)
            if not target.is_dir():raise ValueError('保存先ディレクトリを選択してください。')
            overwrite=False
            if self.create_folder.get():
                folder=self.folder_name.get().strip();validate_name(folder)
                target=target/folder
                if target.exists():
                    if not target.is_dir() or target.is_symlink():raise ValueError('同名の通常フォルダ以外が存在します。')
                    if not messagebox.askyesno('上書き確認',f'同名のフォルダがあります。今回の同名出力を上書きしますか？\n{target}',parent=self.app):return
                    overwrite=True
            states=[{'color':state['color'].get(),'include':state['include'].get()} for state in panel.views.channels]
            mapping=panel._display_slots or tuple(range(panel.stack.array.shape[0]))
            plan=export_plan(panel.stack,panel.channel_stacks,states,mapping,base,self.save_stacks.get(),self.save_merge.get(),self.format.get())
            if not overwrite and any((target/name).exists() for name in plan['outputs']):
                if not messagebox.askyesno('上書き確認','同名の出力があります。上書きしますか？',parent=self.app):return
                overwrite=True
            settings=self.scale.settings();spacing=xy_spacing(self.app)
            if settings['visible'] and (not spacing or settings['length_um'] is None):raise ValueError('スケールバーの長さとX/Y画素サイズを設定するか、スケールバーをOFFにしてください。')
        except (ValueError,OSError,tk.TclError) as error:
            self.info.set('保存エラー: '+str(error));self.app.add_log(self.info.get(),level='error');return
        plan['workspace']=self.app.workspace_snapshot()
        panel.start_progress('stack / merge画像を保存中')
        events,cancel=panel._events,panel._cancel;stack=panel.stack
        def worker():
            try:
                result=export_stack(stack,plan,target,settings,spacing,overwrite,cancel,
                    lambda i,n,text:events.put(('progress',i,n,text)))
                events.put(('export_done',str(result)))
            except Cancelled:events.put(('export_cancelled',None))
            except Exception as error:events.put(('export_error',str(error)))
        threading.Thread(target=worker,daemon=True).start();panel._poll_job=panel.after(50,panel.poll)

# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Cancellable full-resolution display preparation, without Tk calls in workers."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import numpy as np
from PIL import Image
from alignment import Cancelled,check,read_rgb,render_result
from image_sources import render_native,display_array,is_tiff
from tiff_display import color_pixels,display_pixels
from ui_controls import center_dialog

RAW_PROGRESS_THRESHOLD=8000


def colored_native(path,page,matrix,canvas,limits,color,cancel=None,progress=None):
    check(cancel)
    with render_native(path,page,matrix,canvas) as source:
        result=Image.new('L' if color=='Gray' else 'RGB',canvas)
        try:
            pixels=np.asarray(source)
            for y in range(0,canvas[1],64):
                check(cancel)
                values=color_pixels(display_array(pixels[y:y+64],limits),color)
                with Image.fromarray(values) as row:result.paste(row,(0,y))
                if progress and (y%512==0 or y+64>=canvas[1]):progress(min(y+64,canvas[1])/canvas[1])
            return result
        except BaseException:result.close();raise


class RawPreviewWorkflow:
    def init_raw_preview(self):
        self._raw_busy=False;self._raw_dialog=None;self._raw_poll_job=None
        self._raw_events=queue.Queue();self._raw_cancel=threading.Event()

    def large_raw_requested(self):
        if not self.raw_display.get() or self._preview_is_empty or not self.analysis_files or self._preview_cache is None:return False
        size=self._preview_cache.entries[self.preview_index][0]
        return size is not None and max(size)>RAW_PROGRESS_THRESHOLD

    def raw_tiff_jobs(self):
        self.tiff_view.ensure();view=self.tiff_view;index=self.preview_index;canvas=self.display_canvas_size()
        pages=[view.focus_page] if view.focus_page is not None and not view.merged else [p for p,s in enumerate(view.channels) if not view.merged or s['include'].get()]
        jobs=[]
        for page in pages:
            state=view.channels[page]
            if not state['manual'] and state['index']!=index:
                limits=display_pixels(self._preview_cache.get_plane(index,page))[1]
                state.update(limits=limits,index=index);state['low'].set(limits[0]);state['high'].set(limits[1])
            for name in ('source','result'):
                matrix=np.eye(3) if name=='source' else self.alignment_matrices[index] if index<len(self.alignment_matrices) else None
                if matrix is None:continue
                key=(index,page,name,tuple(matrix.ravel()),tuple(canvas),True,state['limits'],state['color'].get())
                jobs.append((key,page,matrix.copy(),tuple(state['limits']),state['color'].get()))
        return jobs

    def prepare_large_raw(self):
        if not self.large_raw_requested():return False
        if self._raw_busy:return True
        jobs=self.raw_tiff_jobs() if self._tiff_pages>1 else None
        if jobs is not None:
            keep={job[0] for job in jobs}
            for key in list(self.tiff_view.cache):
                if key[5] and key not in keep:self.tiff_view.cache.pop(key).close()
            jobs=[job for job in jobs if job[0] not in self.tiff_view.cache]
            if not jobs:return False
        elif self._raw_original is not None and (self.preview_index>=len(self.alignment_matrices) or self.alignment_matrices[self.preview_index] is None or self._raw_aligned is not None):return False
        path=self.analysis_files[self.preview_index].path;canvas=tuple(self.display_canvas_size())
        matrix=self.alignment_matrices[self.preview_index].copy() if self.preview_index<len(self.alignment_matrices) and self.alignment_matrices[self.preview_index] is not None else None
        self._raw_busy=True;self._raw_cancel=threading.Event();self.refresh_manual_controls()
        dialog=self._raw_dialog=tk.Toplevel(self);dialog.title('生画像表示の準備');dialog.transient(self);dialog.resizable(False,False)
        center_dialog(self,dialog,500,190)
        self._raw_text=tk.StringVar(value='元解像度の画像を読み込んでいます…')
        self._raw_elapsed=tk.StringVar(value='経過時間 00:00:00');self._raw_started=time.monotonic()
        ttk.Label(dialog,textvariable=self._raw_text,wraplength=460).pack(fill='x',padx=18,pady=(18,10))
        self._raw_bar=ttk.Progressbar(dialog,maximum=100,mode='indeterminate');self._raw_bar.pack(fill='x',padx=18);self._raw_bar.start(15)
        ttk.Label(dialog,textvariable=self._raw_elapsed).pack(pady=8)
        ttk.Button(dialog,text='中止',command=self.cancel_raw_preview).pack()
        dialog.protocol('WM_DELETE_WINDOW',self.cancel_raw_preview);dialog.grab_set()
        events,cancel=self._raw_events,self._raw_cancel
        def worker():
            results=[];published=False
            try:
                if jobs is not None:
                    for n,(key,page,transform,limits,color) in enumerate(jobs):
                        check(cancel);text=f'Ch {page+1} · '+('元画像' if key[2]=='source' else 'アライン後')
                        events.put(('progress',n,len(jobs),text))
                        image=colored_native(path,page,transform,canvas,limits,color,cancel,
                            lambda fraction:events.put(('progress',n+fraction,len(jobs),text)))
                        results.append((key,image))
                else:
                    check(cancel)
                    with read_rgb(path) as source:raw=source.convert('RGBA')
                    if raw.size!=canvas:
                        padded=Image.new('RGBA',canvas,'black' if is_tiff(path) else 'white')
                        padded.paste(raw,((canvas[0]-raw.width)//2,(canvas[1]-raw.height)//2),raw);raw.close();raw=padded
                    results.append(('source',raw));check(cancel)
                    events.put(('progress',1,2 if matrix is not None else 1,'アライン後の生画像を準備しています…'))
                    if matrix is not None:results.append(('result',render_result(path,matrix,canvas)))
                check(cancel);events.put(('done',results));published=True
            except Cancelled:events.put(('cancelled',None))
            except Exception as error:events.put(('error',str(error)))
            finally:
                if not published:
                    for key,image in results:image.close()
        threading.Thread(target=worker,daemon=True).start()
        self._raw_poll_job=self.after(30,self.poll_raw_preview)
        return True

    def cancel_raw_preview(self):
        self._raw_cancel.set();self._raw_text.set('中止しています…')

    def poll_raw_preview(self):
        self._raw_poll_job=None
        seconds=int(time.monotonic()-self._raw_started)
        self._raw_elapsed.set(f'経過時間 {seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}')
        try:
            while True:
                kind,data,*rest=self._raw_events.get_nowait()
                if kind=='progress':
                    total,text=rest;self._raw_bar.stop();self._raw_bar.configure(mode='determinate',value=100*data/max(1,total));self._raw_text.set(text);continue
                cancelled=self._raw_cancel.is_set()
                if kind=='done' and not cancelled:
                    for key,image in data:
                        if key=='source':self._raw_original=image
                        elif key=='result':self._raw_aligned=image
                        else:self.tiff_view.cache[key]=image
                else:
                    if kind=='done':
                        for key,image in data:image.close()
                    self.raw_display.set(False)
                    self.tiff_view.clear_raw_cache()
                    if kind=='error':self.add_log('生画像表示エラー: '+data,level='error')
                self._raw_busy=False;self._raw_bar.stop();self._raw_dialog.grab_release();self._raw_dialog.destroy();self._raw_dialog=None
                self.refresh_manual_controls()
                self.request_preview_draw();return
        except queue.Empty:pass
        self._raw_poll_job=self.after(30,self.poll_raw_preview)

    def close_raw_preview(self):
        self._raw_cancel.set()
        if self._raw_poll_job is not None:self.after_cancel(self._raw_poll_job);self._raw_poll_job=None
        if self._raw_dialog is not None:self._raw_dialog.destroy();self._raw_dialog=None
        self._raw_busy=False
        while True:
            try:
                event=self._raw_events.get_nowait()
                if event[0]=='done':
                    for key,image in event[1]:image.close()
            except queue.Empty:break

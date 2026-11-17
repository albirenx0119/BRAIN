# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Windows folder drops and encoded image clipboard formats, without extra packages."""
import base64
import ctypes
from ctypes import wintypes
import io
import os
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image,ImageDraw,ImageFont


class FolderDrop:
    def __init__(self,widget,callback,on_error):
        self.widget,self.callback,self.on_error=widget,callback,on_error
        self.closed=False;self.original=None
        if os.name!='nt':return
        user=ctypes.WinDLL('user32',use_last_error=True);shell=ctypes.WinDLL('shell32',use_last_error=True)
        self.user,self.shell=user,shell
        self.handle=widget.winfo_id()
        self.proc_type=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
        self.setter=getattr(user,'SetWindowLongPtrW',user.SetWindowLongW)
        self.setter.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_void_p];self.setter.restype=ctypes.c_void_p
        user.CallWindowProcW.argtypes=[ctypes.c_void_p,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        user.CallWindowProcW.restype=ctypes.c_ssize_t
        shell.DragAcceptFiles.argtypes=[wintypes.HWND,wintypes.BOOL]
        shell.DragQueryFileW.argtypes=[wintypes.HANDLE,wintypes.UINT,wintypes.LPWSTR,wintypes.UINT]
        shell.DragQueryFileW.restype=wintypes.UINT
        shell.DragFinish.argtypes=[wintypes.HANDLE]
        def procedure(hwnd,message,wparam,lparam):
            if message==0x0233:
                try:
                    count=shell.DragQueryFileW(wparam,0xffffffff,None,0)
                    paths=[]
                    for i in range(count):
                        length=shell.DragQueryFileW(wparam,i,None,0)
                        value=ctypes.create_unicode_buffer(length+1)
                        shell.DragQueryFileW(wparam,i,value,length+1);paths.append(value.value)
                    widget.after_idle(lambda:self.deliver(paths))
                finally:shell.DragFinish(wparam)
                return 0
            return user.CallWindowProcW(self.original,hwnd,message,wparam,lparam)
        self.procedure=self.proc_type(procedure)
        ctypes.set_last_error(0)
        self.original=self.setter(self.handle,-4,ctypes.cast(self.procedure,ctypes.c_void_p))
        if not self.original:raise ctypes.WinError(ctypes.get_last_error())
        shell.DragAcceptFiles(self.handle,True)
        widget.bind('<Destroy>',lambda event:self.close() if event.widget==widget else None,add='+')

    def deliver(self,paths):
        if self.closed:return
        try:
            if len(paths)!=1 or not Path(paths[0]).is_dir():raise ValueError('フォルダを1つドロップしてください。')
            self.callback(Path(paths[0]).resolve())
        except Exception as error:self.on_error(str(error))

    def close(self):
        if self.closed:return
        self.closed=True
        if self.original:
            self.shell.DragAcceptFiles(self.handle,False)
            self.setter(self.handle,-4,self.original)
            self.original=None


def copy_image(image,format,owner=0):
    if os.name!='nt':raise OSError('画像コピーはWindowsで利用できます。')
    format=format.upper()
    if format not in ('PNG','JPEG','TIFF','BMP'):raise ValueError('未対応のコピー形式です。')
    with image.convert('RGB') as rgb:
        encoded=io.BytesIO();rgb.save(encoded,format=format,**({'quality':95} if format=='JPEG' else {}))
        dib=io.BytesIO();rgb.save(dib,format='BMP')
    user=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user.OpenClipboard.argtypes=[wintypes.HWND];user.OpenClipboard.restype=wintypes.BOOL
    user.SetClipboardData.argtypes=[wintypes.UINT,wintypes.HANDLE];user.SetClipboardData.restype=wintypes.HANDLE
    user.RegisterClipboardFormatW.argtypes=[wintypes.LPCWSTR];user.RegisterClipboardFormatW.restype=wintypes.UINT
    kernel.GlobalAlloc.argtypes=[wintypes.UINT,ctypes.c_size_t];kernel.GlobalAlloc.restype=wintypes.HANDLE
    kernel.GlobalLock.argtypes=[wintypes.HANDLE];kernel.GlobalLock.restype=ctypes.c_void_p
    kernel.GlobalUnlock.argtypes=[wintypes.HANDLE]
    kernel.GlobalFree.argtypes=[wintypes.HANDLE]
    if not user.OpenClipboard(owner):raise OSError('クリップボードが使用中です。もう一度お試しください。')
    try:
        user.EmptyClipboard()
        entries=[(user.RegisterClipboardFormatW({'PNG':'PNG','JPEG':'JFIF','TIFF':'TIFF','BMP':'image/bmp'}[format]),encoded.getvalue()),(8,dib.getvalue()[14:])]
        for kind,data in entries:
            handle=kernel.GlobalAlloc(0x0002,len(data))
            if not handle:raise MemoryError('画像コピー用メモリを確保できません。')
            pointer=kernel.GlobalLock(handle)
            if not pointer:kernel.GlobalFree(handle);raise MemoryError('クリップボードメモリにアクセスできません。')
            ctypes.memmove(pointer,data,len(data));kernel.GlobalUnlock(handle)
            if not user.SetClipboardData(kind,handle):kernel.GlobalFree(handle);raise ctypes.WinError(ctypes.get_last_error())
    finally:user.CloseClipboard()


def canvas_image_at(canvas,x,y):
    for item in reversed(canvas.find_all()):
        if canvas.type(item)!='image':continue
        box=canvas.bbox(item)
        if not box or not box[0]<=x<box[2] or not box[1]<=y<box[3]:continue
        name=canvas.itemcget(item,'image')
        data=canvas.tk.call(name,'data','-format','png')
        if isinstance(data,str):data=base64.b64decode(data)
        with Image.open(io.BytesIO(data)) as image:result=image.convert('RGB')
        drawing=ImageDraw.Draw(result)
        anchors={'center':'mm','n':'mt','ne':'rt','e':'rm','se':'rb','s':'mb','sw':'lb','w':'lm','nw':'lt'}
        overlays=set(canvas.find_withtag('scale-bar'))|set(canvas.find_withtag('measurement'))
        for overlay in (item for item in canvas.find_all() if item in overlays):
            coords=canvas.coords(overlay)
            if not coords:continue
            shifted=[value-(box[0] if i%2==0 else box[1]) for i,value in enumerate(coords)]
            kind=canvas.type(overlay);color=canvas.itemcget(overlay,'fill')
            if kind=='line':drawing.line(shifted,fill=color,width=max(1,round(float(canvas.itemcget(overlay,'width')))))
            elif kind=='text':
                try:
                    font=tkfont.Font(root=canvas,font=canvas.itemcget(overlay,'font'))
                    size=font.actual('size');size=abs(size) if size<0 else round(size*canvas.winfo_fpixels('1p'))
                    face=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',max(1,size))
                except (OSError,tk.TclError):face=ImageFont.load_default()
                drawing.text(shifted,canvas.itemcget(overlay,'text'),fill=color,font=face,
                    anchor=anchors.get(canvas.itemcget(overlay,'anchor'),'mm'))
            elif kind=='oval':drawing.ellipse(shifted,outline=canvas.itemcget(overlay,'outline'),width=2)
        return result
    return None


class ImageCopyMenus:
    def __init__(self,app):
        self.app=app;self.bound=set();self.current_menu=None
        app.bind_all('<Map>',lambda event:self.bind(event.widget),add='+')
        self.scan(app)

    def scan(self,widget):
        self.bind(widget)
        for child in widget.winfo_children():self.scan(child)

    def bind(self,widget):
        if not isinstance(widget,tk.Canvas) or not getattr(widget,'image_view',False) or str(widget) in self.bound:return
        self.bound.add(str(widget))
        widget.bind('<Button-3>',lambda event:self.menu(widget,event),add='+')

    def menu(self,canvas,event):
        if not any(canvas.type(i)=='image' and canvas.bbox(i) and canvas.bbox(i)[0]<=event.x<canvas.bbox(i)[2] and canvas.bbox(i)[1]<=event.y<canvas.bbox(i)[3] for i in canvas.find_all()):return
        if self.current_menu is not None:
            try:self.current_menu.destroy()
            except tk.TclError:pass
        menu=tk.Menu(canvas,tearoff=False);self.current_menu=menu
        for format in ('PNG','JPEG','TIFF','BMP'):
            menu.add_command(label=f'{format}形式で画像をコピー',command=lambda format=format:self.copy(canvas,event.x,event.y,format))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()
        return 'break'

    def copy(self,canvas,x,y,format):
        image=canvas_image_at(canvas,x,y)
        if image is None:return
        try:
            copy_image(image,format,self.app.winfo_id())
            self.app.add_log(f'表示画像を{format}形式でコピーしました。')
        except Exception as error:
            self.app.add_log('画像コピーエラー: '+str(error),level='error')
            from tkinter import messagebox
            messagebox.showerror('画像コピー',str(error),parent=self.app)
        finally:image.close()

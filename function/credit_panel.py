# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Application information and native update-check entry point."""
import json
import os
import subprocess
import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser
from importlib.metadata import version
from app_paths import APP_ROOT,APP_VERSION,FULL_NAME,COPYRIGHT
from feedback import FeedbackSender, FeedbackError
from appearance import MODES
from ui_controls import center_dialog
from cv2 import __version__ as opencv_version

REPOSITORY = 'https://github.com/albirenx0119/BRAIN'


class CreditPanel(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,padding=24)
        self.app = app
        self.process = None
        self.dialog = None
        self.job = None
        try:
            info = json.loads((APP_ROOT/'version.json').read_text(encoding='utf-8-sig'))
        except (OSError,ValueError): info = {}
        ttk.Label(self,text='BRAIN',style='Title.TLabel').pack(anchor='w',pady=(0,16))
        rows = [('正式名称',FULL_NAME),('読み方','ブレイン'),('作成者','Ren Y Sato'),('Copyright',COPYRIGHT),('バージョン',info.get('version',APP_VERSION)),
                ('更新日時',info.get('updated_at','不明')),
                ('リリース',info.get('release_tag','不明')),('公開日',info.get('release_date','不明')),
                ('対応環境','Windows 10 / 11（64-bit）'),
                ('開発協力','OpenAI Codexの支援を受けて、実装・修正・テストを行っています。'),
                ('処理','ローカルでの画像アライン・TIFFスタック・AP軸への画像3D構築'),
                ('登録エンジン','NCC最大化：画素数による縮小段階・FFT移動候補・移動と回転の同時調整'),
                ('ライセンス表示','function/THIRD_PARTY_NOTICES.txt / function/licenses/Apache-2.0.txt'),
                ('使用ライブラリ',f'Pillow {version("Pillow")} / NumPy {version("numpy")} / OpenCV {opencv_version} / Tkinter {tk.TkVersion}')]
        table = ttk.Frame(self); table.pack(anchor='w',fill='x')
        for row,(label,text) in enumerate(rows):
            ttk.Label(table,text=label,width=18).grid(row=row,column=0,sticky='nw',pady=6)
            ttk.Label(table,text=text,wraplength=800).grid(row=row,column=1,sticky='nw',pady=6)
        ttk.Label(self,text='GitHub').pack(anchor='w',pady=(18,4))
        link = ttk.Label(self,text=REPOSITORY,foreground='#1565c0',cursor='hand2')
        link.pack(anchor='w'); link.bind('<Button-1>',lambda event:webbrowser.open(REPOSITORY))
        self.button = ttk.Button(self,text='アップデートを確認',command=self.check_updates)
        self.button.pack(anchor='w',pady=(24,8))
        self.status = tk.StringVar(value='更新確認はGitHubへ接続します。画像データは送信しません。')
        ttk.Label(self,textvariable=self.status,wraplength=900).pack(anchor='w')
        appearance = ttk.LabelFrame(self,text='表示テーマ',padding=8)
        appearance.pack(fill='x',pady=(14,0))
        for value,label in MODES.items():
            ttk.Radiobutton(appearance,text=label,variable=app.appearance.mode,value=value,
                            command=app.appearance.set_mode).pack(side='left',padx=(0,18))
        feedback = self.feedback_frame = ttk.LabelFrame(self,text='debug/function',padding=12)
        feedback.pack(side='bottom',fill='x',pady=(20,0))
        ttk.Label(feedback,text='不具合報告・機能の要望を入力してください。Sendで入力したメッセージを開発者に送信します。',wraplength=1100).pack(anchor='w')
        self.feedback_text = tk.Text(feedback,height=5,wrap='word',undo=True)
        self.feedback_text.pack(fill='x',pady=8)
        row = ttk.Frame(feedback); row.pack(fill='x')
        self.send_button = ttk.Button(row,text='Send',command=self.send_feedback)
        self.send_button.pack(side='right')
        self.feedback_status = tk.StringVar(value='')
        ttk.Label(row,textvariable=self.feedback_status,wraplength=950).pack(side='left')
        self.feedback_sender = FeedbackSender()
        self._feedback_events = queue.Queue()
        self._feedback_busy = False
        self._feedback_job = self.after(100,self.poll_feedback)

    def send_feedback(self):
        if self._feedback_busy: return
        remaining = self.feedback_sender.remaining()
        if remaining:
            self.feedback_status.set(f'連投を防ぐため、あと{remaining}秒お待ちください。')
            self.app.add_log('debug/function: '+self.feedback_status.get()); return
        text = self.feedback_text.get('1.0','end-1c')
        if not text.strip():
            self.feedback_status.set('メッセージを入力してください。'); return
        self._feedback_busy = True
        self.send_button.state(['disabled'])
        self.feedback_status.set('送信中…')
        self.app.add_log(f'debug/function: 開発者への送信を開始しました（{len(text.strip())}文字）。')
        sender,events = self.feedback_sender,self._feedback_events
        def worker():
            try: sender.send(text); events.put((True,text,'送信しました。'))
            except FeedbackError as error: events.put((False,text,str(error)))
            except Exception: events.put((False,text,'送信できませんでした。メッセージは残しています。'))
        threading.Thread(target=worker,daemon=True).start()

    def poll_feedback(self):
        self._feedback_job = None
        try:
            while True:
                sent,text,status = self._feedback_events.get_nowait()
                self._feedback_busy = False
                if sent and self.feedback_text.get('1.0','end-1c')==text: self.feedback_text.delete('1.0','end')
                self.feedback_status.set(status)
                self.app.add_log('debug/function: '+('開発者へ送信しました。' if sent else '送信エラー: '+status),level='info' if sent else 'error')
        except queue.Empty: pass
        remaining = self.feedback_sender.remaining()
        configured = self.feedback_sender.configured()
        self.send_button.state(['disabled'] if self._feedback_busy or remaining or not configured else ['!disabled'])
        if remaining and not self._feedback_busy: self.feedback_status.set(f'次の送信まで {remaining} 秒お待ちください。')
        elif not configured: self.feedback_status.set('送信先の設定を確認してください。')
        elif self.feedback_status.get().startswith('次の送信まで '): self.feedback_status.set('送信できます。')
        self._feedback_job = self.after(300 if self._feedback_busy else 1000,self.poll_feedback)

    def check_updates(self):
        if self.process is not None: return
        if any((self.app._raw_busy,self.app._loading_busy,self.app._alignment_busy,self.app._saving_busy,
                self.app.stack_panel.busy,self.app.volume_panel.busy)):
            self.status.set('現在の処理が完了してから、アップデートを確認してください。'); return
        try:
            self.process = subprocess.Popen([str(APP_ROOT/'BRAIN.exe'),'--check-updates',str(os.getpid())],cwd=APP_ROOT)
        except OSError as error:
            self.status.set(f'更新の確認を開始できません: {error}'); return
        self.button.state(['disabled'])
        self.dialog = tk.Toplevel(self.app)
        self.dialog.title('アップデートを確認')
        self.dialog.transient(self.app); self.dialog.resizable(False,False)
        ttk.Label(self.dialog,text='更新確認ウィンドウで操作してください。\n更新する場合、未保存の解析結果は失われます。',padding=20).pack()
        self.dialog.protocol('WM_DELETE_WINDOW',lambda:None)
        center_dialog(self.app,self.dialog,480,140)
        self.dialog.grab_set()
        self.job = self.after(100,self.poll)

    def poll(self):
        self.job = None
        if self.process.poll() is None:
            self.job = self.after(100,self.poll); return
        code = self.process.returncode
        self.process = None
        if self.dialog is not None:
            self.dialog.grab_release(); self.dialog.destroy(); self.dialog = None
        self.button.state(['!disabled'])
        self.status.set('更新の確認が完了しました。' if code==0 else '更新確認を完了できませんでした。現在のバージョンを引き続き使えます。')

    def destroy(self):
        if self.job is not None: self.after_cancel(self.job)
        if self._feedback_job is not None: self.after_cancel(self._feedback_job)
        super().destroy()

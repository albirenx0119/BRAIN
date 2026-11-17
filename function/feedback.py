# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Public feedback relay and persistent, rate-limited feedback."""
import base64
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from app_paths import APP_ROOT

LOCAL = APP_ROOT/'.local'
FEEDBACK_RELAY = 'https://bralign-feedback.ren-bralign.workers.dev/feedback'


def delivery_url(local=LOCAL):
    # Direct delivery is an explicit developer override, never a bundled secret.
    if os.environ.get('BRAIN_DISCORD_WEBHOOK','').strip():
        return load_webhook(local)+'?wait=true'
    return FEEDBACK_RELAY


class FeedbackError(Exception): pass


class CooldownError(FeedbackError):
    def __init__(self,seconds):
        self.seconds = max(1,math.ceil(seconds))
        super().__init__(f'連投を防ぐため、あと{self.seconds}秒お待ちください。')


def _protect(data,decrypt=False):
    if os.name!='nt': raise FeedbackError('暗号化設定の保存にはWindowsが必要です。')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    api.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    api.restype = wintypes.BOOL
    if not api(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):
        raise FeedbackError('送信先の暗号化設定を読み書きできません。現在のWindowsユーザーで設定してください。')
    try: return ctypes.string_at(result.data,result.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data,ctypes.c_void_p))


def _atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,prefix='.feedback-',delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data,stream,ensure_ascii=False)
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()


def validate_webhook(url):
    if not isinstance(url,str) or not re.fullmatch(r'https://discord(?:app)?\.com/api/webhooks/\d{10,24}/[A-Za-z0-9_-]{32,}',url):
        raise FeedbackError('DiscordのWebhook設定が正しくありません。')
    return url


def save_webhook(url,local=LOCAL):
    url = validate_webhook(url.strip())
    _atomic_json(Path(local)/'feedback.json',{'webhook_dpapi':base64.b64encode(_protect(url.encode('utf-8'))).decode('ascii')})


def load_webhook(local=LOCAL):
    value = os.environ.get('BRAIN_DISCORD_WEBHOOK','').strip()
    if value: return validate_webhook(value)
    try:
        data = json.loads((Path(local)/'feedback.json').read_text(encoding='utf-8'))
        return validate_webhook(_protect(base64.b64decode(data['webhook_dpapi']),True).decode('utf-8'))
    except FileNotFoundError: raise FeedbackError('送信先が未設定です。ローカルのWebhook設定が必要です。') from None
    except FeedbackError: raise
    except Exception: raise FeedbackError('送信先のローカル設定を読み込めません。') from None


class FeedbackSender:
    def __init__(self,local=LOCAL,clock=time.time,opener=None):
        self.local = Path(local)
        self.clock = clock
        self.opener = opener or urllib.request.urlopen
        self._mutex = threading.Lock()

    def configured(self):
        try: delivery_url(self.local); return True
        except FeedbackError: return False

    def _state(self):
        try:
            state = json.loads((self.local/'feedback_state.json').read_text(encoding='utf-8'))
            if not isinstance(state,dict): raise ValueError()
            for key in ('blocked_until','next_allowed','last_at'):
                if not isinstance(state.get(key,0),(int,float)) or not math.isfinite(state.get(key,0)): raise ValueError()
            for key in ('attempts','posts'):
                if not isinstance(state.get(key,[]),list) or any(not isinstance(t,(int,float)) or not math.isfinite(t) for t in state.get(key,[])): raise ValueError()
            return state
        except FileNotFoundError: return {}
        except Exception: raise FeedbackError('送信制限の記録を読み込めません。') from None

    def remaining(self):
        try:
            state = self._state()
            return max(0,math.ceil(max(state.get('blocked_until',0),state.get('next_allowed',0))-self.clock()))
        except FeedbackError: return 300

    @contextmanager
    def _locked(self):
        if not self._mutex.acquire(False): raise FeedbackError('送信中です。完了までお待ちください。')
        stream = None
        try:
            self.local.mkdir(parents=True,exist_ok=True)
            stream = open(self.local/'feedback.lock','a+b')
            stream.seek(0,2)
            if stream.tell()==0: stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: raise FeedbackError('別の送信処理が進行中です。') from None
            yield
        finally:
            if stream is not None: stream.close()
            self._mutex.release()

    def send(self,text):
        text = text.strip()
        if not text: raise FeedbackError('メッセージを入力してください。')
        if len(text.encode('utf-16-le'))//2>2000: raise FeedbackError('メッセージは2000文字以内にしてください。')
        url = delivery_url(self.local)
        # Keep the process lock throughout the request; no parallel sends or retries.
        with self._locked():
            now = self.clock(); state = self._state()
            attempts = [t for t in state.get('attempts',[]) if now-t<30]+[now]
            posts = [t for t in state.get('posts',[]) if now-t<60]
            state['attempts'] = attempts[-10:]
            if len(attempts)>=5: state['blocked_until'] = max(state.get('blocked_until',0),now+300)
            wait = max(state.get('blocked_until',0),state.get('next_allowed',0))-now
            if wait>0:
                _atomic_json(self.local/'feedback_state.json',state); raise CooldownError(wait)
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if len(posts)>=3 or (digest==state.get('last_hash') and now-state.get('last_at',0)<300):
                state['blocked_until'] = now+300
                _atomic_json(self.local/'feedback_state.json',state); raise CooldownError(300)
            state.update(posts=posts+[now],next_allowed=now+10,last_hash=digest,last_at=now)
            _atomic_json(self.local/'feedback_state.json',state)
            payload = json.dumps({'content':text,'allowed_mentions':{'parse':[]}},ensure_ascii=False).encode('utf-8')
            request = urllib.request.Request(url,data=payload,method='POST',headers={
                'Content-Type':'application/json; charset=utf-8','User-Agent':'BRAIN-Feedback/1.0.1'})
            try:
                with self.opener(request,timeout=15) as response:
                    body = response.read(65536)
                    if response.headers.get('X-RateLimit-Remaining')=='0':
                        delay = self._delay(response.headers.get('X-RateLimit-Reset-After'))
                        state['next_allowed'] = max(state['next_allowed'],self.clock()+delay)
                    _atomic_json(self.local/'feedback_state.json',state)
                    if response.status==204 and url!=FEEDBACK_RELAY: return {'sent':True}
                    data = json.loads(body)
                    if (response.status!=200 or not isinstance(data,dict) or not data.get('id')
                            or (url==FEEDBACK_RELAY and data.get('sent') is not True)):
                        raise FeedbackError('送信結果を確認できません。重複を避けるため自動再送はしません。')
                    return {'sent':True,'message_id':str(data['id'])}
            except urllib.error.HTTPError as error:
                if error.code==429:
                    try: retry = json.loads(error.read(65536)).get('retry_after')
                    except Exception: retry = None
                    delay = max(1,self._delay(retry),self._delay(error.headers.get('Retry-After')))
                    state['blocked_until'] = self.clock()+delay
                    state.pop('last_hash',None)
                    _atomic_json(self.local/'feedback_state.json',state)
                    raise CooldownError(delay) from None
                raise FeedbackError(f'Discordへ送信できませんでした（HTTP {error.code}）。メッセージは残しています。') from None
            except FeedbackError: raise
            except Exception:
                raise FeedbackError('送信結果を確認できません。ネット接続を確認してください。重複を避けるため自動再送はしません。') from None

    @staticmethod
    def _delay(value):
        try:
            value = float(value)
            return min(86400,max(0,value)) if math.isfinite(value) else 0
        except (TypeError,ValueError): return 0

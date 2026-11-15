"""Local-only native-messaging connection to explicitly installed browser helpers."""
import json
import os
import queue
import secrets
import socket
import struct
import threading
import time
from pathlib import Path

from desktop_tools import DesktopError

_broker=None
_lock=threading.Lock()


def socket_path():
    return Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/'jarvis-browser'/'control.sock'


class Broker:
    def __init__(self):
        self.clients={};self.pending={};self.lock=threading.RLock();self.server=None

    def start(self):
        with self.lock:
            if self.server:return
            path=socket_path();path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.parent.chmod(0o700)
            # Never replace an active broker owned by another Jarvis process.
            if path.exists():
                probe=socket.socket(socket.AF_UNIX);probe.settimeout(.2)
                try:probe.connect(str(path))
                except OSError:path.unlink()
                else:raise DesktopError('Another Jarvis browser bridge is already active.')
                finally:probe.close()
            server=socket.socket(socket.AF_UNIX);server.bind(str(path));path.chmod(0o600);server.listen(8)
            self.server=server
            threading.Thread(target=self._accept,daemon=True).start()

    def _accept(self):
        while self.server:
            try:connection,_=self.server.accept()
            except OSError:return
            if struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=os.getuid():
                connection.close();continue
            threading.Thread(target=self._client,args=(connection,),daemon=True).start()

    def _client(self,connection):
        identity=None
        try:
            connection.settimeout(10);file=connection.makefile('rb')
            raw=file.readline(16385)
            if len(raw)>16384:return
            hello=json.loads(raw)
            if hello.get('browser') not in {'chrome','chromium'}:return
            identity=secrets.token_hex(16)
            client=dict(id=identity,browser=hello['browser'],connection=connection,send=threading.Lock())
            with self.lock:self.clients[identity]=client
            connection.settimeout(None)
            while True:
                raw=file.readline(2*1024*1024+1)
                if not raw or len(raw)>2*1024*1024:return
                message=json.loads(raw)
                with self.lock:waiter=self.pending.get((identity,message.get('id')))
                if waiter:waiter.put_nowait(message)
        except (OSError,ValueError,queue.Full):pass
        finally:
            with self.lock:self.clients.pop(identity,None)
            connection.close()

    def _one(self,client,action,args,check):
        key=secrets.token_hex(16);waiter=queue.Queue(maxsize=1)
        with self.lock:self.pending[(client['id'],key)]=waiter
        try:
            with client['send']:client['connection'].sendall((json.dumps(dict(id=key,action=action,expires=time.time()+5,**args))+'\n').encode())
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                check()
                try:message=waiter.get(timeout=.1)
                except queue.Empty:continue
                if not message.get('ok'):raise DesktopError(message.get('error','Browser operation failed.'))
                return message.get('result')
            raise DesktopError('Browser connection timed out. Nothing will be retried automatically.')
        except OSError as exc:raise DesktopError('Browser connection closed.') from exc
        finally:
            with self.lock:self.pending.pop((client['id'],key),None)
            try:
                with client['send']:client['connection'].sendall((json.dumps({'action':'cancel','cancel':key})+'\n').encode())
            except OSError:pass

    def request(self,browser,action,args,check):
        self.start()
        with self.lock:clients=[c for c in self.clients.values() if c['browser']==browser]
        if not clients:raise DesktopError('Chrome tab access needs the Jarvis Browser Controls extension. Run scripts/install-browser-controls, then load browser-extension in Chrome extensions. No browser windows were closed.')
        results=[]
        for client in clients:
            payload=args
            if action=='close':
                payload={'tabs':[t for t in args['tabs'] if t.get('connection')==client['id']]}
                if not payload['tabs']:continue
            result=self._one(client,action,payload,check)
            if action=='list':
                results.extend(dict(t,connection=client['id']) for t in result)
            else:results.append(result)
        return results


def get_broker():
    global _broker
    with _lock:
        if _broker is None:_broker=Broker()
    return _broker

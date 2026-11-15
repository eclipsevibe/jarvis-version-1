"""Orange J.A.R.V.I.S. desktop HUD and command overlay."""
from __future__ import annotations
import glob, math, os, re, sys, threading, traceback, urllib.request, sqlite3, logging
import json, subprocess, time
from datetime import datetime
from pathlib import Path
import codex_bridge
import desktop_state
from PyQt6.QtCore import QObject, QEvent, QThread, QTimer, QSize, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QShortcut, QKeySequence, QActionGroup
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget
from router import Router, Result, LOCAL_SAFETY_LOCK, RequestCancelled, HandoffRequested
from voice import VoiceEngine, SentenceBuffer
from voice_profiles import PROFILES, read_profile

SOCKET = "jarvis-hud-" + str(os.getuid())
ORANGE = "#FF8C00"


def action_icon(button, name, label):
    button.setText('')
    button.setIcon(QIcon(str(Path(__file__).parent/'assets/icons'/(name+'.svg'))))
    button.setIconSize(QSize(22,22))
    button.setMinimumWidth(44)
    button.setAccessibleName(label)
    button.setToolTip(label)

class Reactor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent); self.phase=0.; self.mode="idle"; self.level=0.; self.setFixedSize(180,180)
        self.timer=QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(30)
    def _tick(self): self.phase=(self.phase+(.22 if self.mode=="thinking" else .07))%360; self.update()
    def set_mode(self, mode): self.mode=mode; self.update()
    def set_audio_level(self, value): self.level=max(0.,min(1.,value)); self.update()
    def paintEvent(self, event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.translate(self.width()/2,self.height()/2)
        pulse=1+self.level*.18; strength=int(140+75*self.level+25*math.sin(self.phase/24)); orange=QColor(255,140,0,strength)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255,140,0,12+int(self.level*35))); p.drawEllipse(int(-65*pulse),int(-65*pulse),int(130*pulse),int(130*pulse))
        for r,w,a in ((73,1,.3),(63,2,.8),(51,1,.55),(34,2,.7)):
            p.setPen(QPen(QColor(255,179,0,int(strength*a)),w)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(int(-r*pulse),int(-r*pulse),int(2*r*pulse),int(2*r*pulse))
        p.save(); p.rotate(self.phase*(2.8 if self.mode=="thinking" else 1)); p.setPen(QPen(orange,3)); p.drawArc(-59,-59,118,118,18*16,(105+int(self.level*70))*16); p.drawArc(-59,-59,118,118,198*16,62*16); p.restore()
        p.setPen(QPen(QColor(255,179,0,180),1))
        for i in range(48):
            p.save(); p.rotate(i*7.5); length=7+int(self.level*8 if i%3==0 else 0); p.drawLine(0,-(78 if i%3==0 else 80),0,-(78+length if i%3==0 else 84)); p.restore()
        p.setPen(QPen(orange,1)); p.drawLine(-15,0,15,0); p.drawLine(0,-15,0,15); p.setBrush(QColor(255,140,0,90)); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(-6,-6,12,12)

class Worker(QObject):
    result=pyqtSignal(int,str,str); status=pyqtSignal(int,str); token=pyqtSignal(int,str)
    def __init__(self,router,text,rid): super().__init__(); self.router=router; self.text=text; self.rid=rid; self.started=time.time()
    def finish(self,tier,answer):
        try:
            self.router.memory.record(self.text,answer,'failed' if tier=='ERROR' else 'cancelled' if tier=='STOPPED' else 'completed',
                                      'desktop',started=self.started)
        except (OSError, sqlite3.Error):
            logging.warning('Interaction could not be saved to learning memory')
        self.result.emit(self.rid,tier,answer)

    def run(self):
        timer=None
        monitor_stop=threading.Event()
        history=list(self.router.history)
        try:
            self.router.status=lambda text:self.status.emit(self.rid,text); self.router.stream=lambda text:self.token.emit(self.rid,text)
            remaining=max(.01,self.router.deadline-time.monotonic()) if self.router.deadline else self.router.budget.handoff_seconds
            timer=threading.Timer(remaining,self.router.request_handoff); timer.daemon=True; timer.start()
            if self.router.local_mode:
                def monitor():
                    while not monitor_stop.wait(1):
                        if self.router.local_generating:
                            try:self.router.check_local_resources()
                            except RequestCancelled:return
                threading.Thread(target=monitor,daemon=True).start()
            try:
                try:
                    ans=self.router.handle(self.text)
                    self.router._check_cancel()
                except Exception:
                    self.router._check_cancel()
                    raise
            except HandoffRequested:
                timer.cancel()
                if self.router.local_mode:raise RequestCancelled('Local mode stopped. Codex fallback is disabled.')
                if self.router.cancel_event.is_set():raise RequestCancelled("Request stopped.")
                self.status.emit(self.rid,"CODEX: Answering with Codex…")
                answer=codex_bridge.answer(self.text,history,self.router.request_actions,self.router.cancel_event)
                if self.router.cancel_event.is_set():raise RequestCancelled("Request stopped.")
                self.router.history=history+[{"role":"user","content":self.text},{"role":"assistant","content":answer}]
                ans=Result("CODEX",answer)
            self.finish(ans.tier,ans.text)
        except codex_bridge.CodexCancelled as exc:
            self.finish("STOPPED",str(exc))
        except codex_bridge.CodexError as exc:
            self.finish("STOPPED" if self.router.cancel_event.is_set() else "ERROR",str(exc))
        except RequestCancelled as exc:
            self.finish("STOPPED",str(exc))
        except Exception as exc:
            if self.router.cancel_event.is_set():
                self.finish("STOPPED","Request stopped. Actions already completed are not undone.")
                return
            traceback.print_exc(file=sys.stderr)
            self.finish("ERROR",f"Request failed: {exc}")
        finally:
            monitor_stop.set()
            if timer is not None:timer.cancel()

class Confirmation(QObject):
    requested=pyqtSignal(str,object)
    def __init__(self,parent): super().__init__(parent); self.requested.connect(self._ask)
    def check(self,description):
        if QApplication.instance().thread()==QThread.currentThread(): return self._direct(description)
        event=threading.Event(); answer={"ok":False}; self.requested.emit(description,(event,answer))
        while not event.wait(.1):
            if self.parent().router.interrupted():return False
        return answer["ok"]
    def _ask(self,desc,pair):
        event,answer=pair
        if not self.parent().router.interrupted():answer["ok"]=self._direct(desc)
        event.set()
    def _direct(self,desc):
        box=QMessageBox(QMessageBox.Icon.Question,"Jarvis confirmation",desc,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,self.parent())
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        timer=QTimer(box); timer.timeout.connect(lambda:box.reject() if self.parent().router.interrupted() else None); timer.start(100)
        return box.exec()==QMessageBox.StandardButton.Yes

class Hud(QWidget):
    engine_probe=pyqtSignal(bool)
    def __init__(self,app):
        super().__init__(); self.app=app; self.pending=[]; self.busy=False; self.request_id=0; self.streamed=False; self.muted=False
        self._drag_offset=None; self._user_positioned=False; self._using_codex=False
        self._speech_buffer=SentenceBuffer(); self._speech_streamed=False
        self.setWindowTitle("J.A.R.V.I.S."); self.setWindowFlags(Qt.WindowType.Tool|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.FramelessWindowHint); self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("""
          QWidget#panel{background:rgba(10,8,5,238);color:#ffe2b5;border:1px solid #FF8C00;border-radius:18px}
          QLabel{color:#d0aa75;font-family:'DejaVu Sans Mono'} QLineEdit{background:rgba(255,140,0,15);border:1px solid #9a5400;border-radius:10px;color:#fff0d8;padding:11px;font:14px 'DejaVu Sans Mono'}
          QLineEdit:focus{border:1px solid #FFB300} QPushButton{background:#392106;border:1px solid #a65b00;border-radius:9px;padding:9px 13px;color:#ffd08b;font:bold 11px 'DejaVu Sans Mono'}
          QPushButton:hover{background:#593208} QPushButton:checked{background:#70420d;border-color:#ffb300} QPlainTextEdit{background:transparent;border:0;color:#ffe2b5;font:12px 'DejaVu Sans Mono'}
          QLabel#title{color:#FFB300;font:bold 15px 'DejaVu Sans Mono'} QLabel#subtitle{color:#FF8C00;font:9px 'DejaVu Sans Mono'} QLabel#telemetry{color:#c08842;font:9px 'DejaVu Sans Mono'}
        """)
        outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); panel=QWidget(objectName="panel"); outer.addWidget(panel); layout=QVBoxLayout(panel); layout.setContentsMargins(18,14,18,14); layout.setSpacing(8)
        self._panel_layout=layout
        self.drag_header=QWidget(); header=QHBoxLayout(self.drag_header); header.setContentsMargins(0,0,0,0)
        title=QVBoxLayout(); title.addWidget(QLabel("J.A.R.V.I.S.",objectName="title")); title.addWidget(QLabel("// OS INTERFACE",objectName="subtitle")); header.addLayout(title); header.addStretch(); self.telemetry=QLabel("CPU --%   RAM --%   BAT --%",objectName="telemetry"); header.addWidget(self.telemetry); self.tier=QLabel("[READY]"); header.addWidget(self.tier); layout.addWidget(self.drag_header)
        self._drag_widgets=[self.drag_header]+self.drag_header.findChildren(QLabel)
        for widget in self._drag_widgets:
            widget.installEventFilter(self); widget.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_header.setToolTip("Drag here to move Jarvis")
        body=QHBoxLayout(); self.reactor=Reactor(); body.addWidget(self.reactor); self.output=QPlainTextEdit(); self.output.setReadOnly(True); self.output.setMaximumHeight(180); self.output.setPlaceholderText("SYSTEM READY  //  AWAITING INPUT"); body.addWidget(self.output,1); layout.addLayout(body)
        row=QHBoxLayout()
        self.drag_grip=QLabel("⠿"); self.drag_grip.setToolTip("Drag to move; reopening centres the input below Jarvis")
        self.drag_grip.setCursor(Qt.CursorShape.OpenHandCursor); self.drag_grip.installEventFilter(self)
        self._drag_widgets.append(self.drag_grip); row.addWidget(self.drag_grip); self.drag_grip.hide()
        self.input=QLineEdit(); self.input.setPlaceholderText("Transmit a request to Jarvis…"); self.input.returnPressed.connect(self.submit); self.input.installEventFilter(self); row.addWidget(self.input,1)
        buttons=QGridLayout(); buttons.setContentsMargins(0,0,0,0); buttons.setSpacing(6)
        self.send=QPushButton(); self.send.clicked.connect(self.submit)
        self.mode=QPushButton("MODE: AUTO"); self.mode.setCheckable(True); self.mode.setToolTip('Click to cycle Auto → Codex → Local. Local never uses Codex or cloud AI and stops at safety limits.'); self.mode.clicked.connect(self.cycle_mode)
        self.mic=QPushButton(); self.mic.clicked.connect(self.toggle_mic)
        self.mute=QPushButton(); self.mute.clicked.connect(self.toggle_mute)
        self.controls_button=QPushButton(); self.controls_button.clicked.connect(self.toggle_control_panel)
        action_icon(self.send,'send','Send request')
        action_icon(self.mic,'mic','Start microphone')
        action_icon(self.mute,'mute','Mute speech')
        action_icon(self.controls_button,'settings','Open or close controls (Super+J)')
        self.mode.ensurePolished()
        mode_width=max(self.mode.fontMetrics().horizontalAdvance('MODE: '+name) for name in ('AUTO','CODEX','LOCAL'))+30
        for button in (self.send,self.mode,self.mic,self.mute,self.controls_button):button.setFixedHeight(40)
        self.send.setFixedWidth(mode_width); self.mode.setFixedWidth(mode_width)
        self.mic.setFixedWidth(44); self.mute.setFixedWidth(44); self.controls_button.setFixedWidth(94)
        buttons.addWidget(self.send,0,0); buttons.addWidget(self.mode,1,0)
        buttons.addWidget(self.mic,0,1); buttons.addWidget(self.mute,0,2)
        buttons.addWidget(self.controls_button,1,1,1,2)
        row.addLayout(buttons); layout.addLayout(row)
        self.mic.setCheckable(True); self.mute.setCheckable(True)
        self.input.setFixedHeight(86)
        self.escape=QShortcut(QKeySequence("Escape"),self); self.escape.activated.connect(self.hide)
        self.confirmation=Confirmation(self); self.router=Router(confirm=self.confirmation.check); self.voice=VoiceEngine(); self.voice.audio_level.connect(self._audio_level); self.voice.transcript.connect(self._voice_prompt); self.voice.status.connect(self._voice_status)
        self.app.aboutToQuit.connect(self.voice.shutdown)
        try:
            preferences=json.loads((Path.home()/'.config/jarvis/ui-preferences.json').read_text())
            self.router.set_mode(preferences.get('mode') if preferences.get('mode') in {'auto','codex','local'} else 'codex' if preferences.get('codex_mode') is True else 'auto')
        except (OSError,ValueError):pass
        self.mode.setChecked(self.router.codex_mode); self.mode.setText('MODE: '+self.router.mode.upper())
        self.server=QLocalServer(self); QLocalServer.removeServer(SOCKET); self.server.listen(SOCKET); self.server.newConnection.connect(self._ipc)
        self.tray=QSystemTrayIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon),self); menu=QMenu(); menu.addAction("Open command bar",self.toggle); menu.addAction('Control panel (Super+J)',self.toggle_control_panel); menu.addAction("Conversation and controls",self.show_controls); menu.addAction("Restart Local Engine",self.restart_engine)
        voice_menu=menu.addMenu("Voice")
        self.voice_actions=QActionGroup(self); self.voice_actions.setExclusive(True)
        for name,profile in PROFILES.items():
            action=voice_menu.addAction(profile['label']); action.setCheckable(True)
            action.setChecked(name==read_profile()); self.voice_actions.addAction(action)
            action.triggered.connect(lambda checked,n=name:self._select_voice(n))
        voice_menu.addSeparator()
        voice_menu.addAction("Preview selected voice",lambda:self.voice.speak("Good evening. All systems are ready. I have your next task queued whenever you are ready."))
        menu.addSeparator(); menu.addAction("Quit",self.app.quit); self.tray.setContextMenu(menu); self.tray.show()
        self._watchdog=QTimer(self); self._watchdog.setSingleShot(True); self._watchdog.timeout.connect(self._request_timeout)
        self.engine_probe.connect(self._engine_status); self.probe_timer=QTimer(self); self.probe_timer.timeout.connect(self._probe_engine); self.probe_timer.start(10000); self.telemetry_timer=QTimer(self); self.telemetry_timer.timeout.connect(self._update_telemetry); self.telemetry_timer.start(2000); self._update_telemetry(); self._probe_engine(); self.resize(810,320); self.hide()
        self._desktop_bridge=desktop_state.DesktopState(); self._desktop_compact=False
        self.desktop_timer=QTimer(self); self.desktop_timer.timeout.connect(self._desktop_tick); self.desktop_timer.start(40)
        self._desktop_tick()
    def _audio_level(self,level):
        self.reactor.set_audio_level(level)
    def _desktop_tick(self):
        wanted=desktop_state.requested()
        compact=wanted and desktop_state.renderer_ready() and not getattr(self,"_controls_open",False)
        if compact!=self._desktop_compact:
            self._desktop_compact=compact
            self.setWindowTitle("J.A.R.V.I.S. — Command Bar" if compact else "J.A.R.V.I.S.")
            self.drag_grip.setVisible(compact)
            self.drag_header.setVisible(not compact); self.reactor.setVisible(not compact)
            self.output.setVisible(not compact)
            for control in (self.mode,self.mic,self.mute):control.show()
            self._panel_layout.setContentsMargins(12,10,12,10) if compact else self._panel_layout.setContentsMargins(18,14,18,14)
            self.setMinimumSize(min(650,self.app.primaryScreen().availableGeometry().width()),0) if compact else self.setMinimumSize(0,0)
            self.resize(650,110) if compact else self.resize(810,320)
            if self.isVisible():self._position()
        if wanted:
            self._desktop_bridge.publish(self.reactor.mode,self.reactor.level,self.telemetry.text())
    def _position(self):
        g=self.app.primaryScreen().availableGeometry()
        self._panel_layout.invalidate(); self._panel_layout.activate()
        self.layout().invalidate(); self.layout().activate(); self.adjustSize()
        if self._desktop_compact:
            m=self.app.primaryScreen().geometry(); scale=min(m.width()/1440,m.height()/900)
            x=m.x()+(m.width()-self.width())//2
            y=round(m.y()+m.height()*.47+280*scale)
            self.move(max(g.left(),min(x,g.right()-self.width()+1)),max(g.top(),min(y,g.bottom()-self.height()+1)))
        elif not self._user_positioned:
            self.move(g.center().x()-self.width()//2,g.bottom()-self.height()-55)
    def eventFilter(self,obj,event):
        if obj in self._drag_widgets:
            if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                handle=self.windowHandle()
                if handle is not None and handle.startSystemMove():
                    self._user_positioned=True; return True
                self._drag_offset=event.globalPosition().toPoint()-self.frameGeometry().topLeft(); return True
            if event.type()==QEvent.Type.MouseMove and self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint()-self._drag_offset); self._user_positioned=True; return True
            if event.type()==QEvent.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton:
                self._drag_offset=None; return True
        if obj is getattr(self,"input",None) and event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape: self.hide(); return True
        return super().eventFilter(obj,event)
    def toggle_mode(self,checked):
        self.set_mode('codex' if checked else 'auto')
    def cycle_mode(self):
        modes=['auto','codex','local']
        self.set_mode(modes[(modes.index(self.router.mode)+1)%len(modes)])
    def set_mode(self,mode):
        self.router.set_mode(mode)
        self.mode.setChecked(self.router.codex_mode)
        self.mode.setText('MODE: '+mode.upper())
        try:
            path=Path.home()/'.config/jarvis/ui-preferences.json'; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({'mode':mode,'codex_mode':self.router.codex_mode}))
        except OSError:
            self.output.appendPlainText("JARVIS  ›  Mode changed for this session; the preference could not be saved.")
    def _probe_engine(self):
        def run():
            base=os.getenv("JARVIS_LOCAL_URL","http://127.0.0.1:8080/v1/chat/completions").split("/v1/")[0]
            health_path = "/api/tags" if ":11434" in base else "/health"
            try:
                with urllib.request.urlopen(base+health_path,timeout=1) as r: self.engine_probe.emit(r.status==200)
            except Exception:self.engine_probe.emit(False)
        threading.Thread(target=run,daemon=True).start()
    def _engine_status(self,ok): self.tray.setToolTip("Jarvis — "+("Ready" if ok else "Offline"))
    def _update_telemetry(self):
        try:
            cpu=os.getloadavg()[0]/max(1,os.cpu_count() or 1)*100
            with open('/proc/meminfo') as f: mem={x[0].rstrip(':'):int(x[1]) for l in f if len(x:=l.split())>1}
            ram=(mem['MemTotal']-mem['MemAvailable'])/mem['MemTotal']*100; battery='--'
            for path in glob.glob('/sys/class/power_supply/BAT*/capacity'):
                try: battery=open(path).read().strip(); break
                except OSError: pass
            fans=[]; temperature='--'; fan_auto=False
            for base in glob.glob('/sys/class/hwmon/hwmon*'):
                try:
                    chip=open(base+'/name').read().strip()
                    if chip=='k10temp':
                        for path in glob.glob(base+'/temp*_input'):
                            value=int(open(path).read().strip())
                            if value>0: temperature=f"{value/1000:.0f}"; break
                    for path in glob.glob(base+'/fan*_input'):
                        rpm=int(open(path).read().strip())
                        if rpm>0: fans.append(rpm)
                    for path in glob.glob(base+'/pwm*_enable'):
                        if int(open(path).read().strip())>=2: fan_auto=True
                except (OSError,ValueError): pass
            fan=f"{max(fans)} RPM" if fans else ("AUTO" if fan_auto else "N/A")
            temp=f"{temperature}°C" if temperature!='--' else "N/A"
            text=f"CPU {cpu:02.0f}%   TEMP {temp}   RAM {ram:02.0f}%   BAT {battery}%   FAN {fan}"; self.telemetry.setText(text)
        except (OSError,KeyError,ValueError):pass
    def _ipc(self):
        sock=self.server.nextPendingConnection()
        if sock:sock.readyRead.connect(lambda s=sock:self._ipc_command(s))
    def _ipc_command(self,sock):
        command=bytes(sock.readAll()).strip()
        if command==b'control-panel':self.toggle_control_panel()
        elif command==b'resources':self.show_resource_panel()
        else:self.toggle()
        sock.disconnectFromServer()
    def toggle_control_panel(self):
        if not getattr(self,'control_panel',None):
            from control_panel import ControlPanel
            self.control_panel=ControlPanel(self)
        self.control_panel.toggle()
    def show_resource_panel(self):
        if not getattr(self,'resource_panel',None):
            from resource_panel import ResourcePanel
            self.resource_panel=ResourcePanel(self)
        self.resource_panel.show();self.resource_panel.raise_();self.resource_panel.activateWindow()
    def toggle(self):
        if self.isVisible(): self.hide(); return
        self._desktop_tick()
        self._position()
        self.show(); self.raise_(); self.activateWindow(); QTimer.singleShot(0,self.input.setFocus)
    def show_controls(self):
        # Keep transcript and voice/mode controls available from the tray.
        self._controls_open=True; self._desktop_compact=False
        self.setWindowTitle("J.A.R.V.I.S."); self.drag_grip.hide()
        for widget in (self.drag_header,self.reactor,self.output,self.mode,self.mic,self.mute):widget.show()
        self.resize(810,320)
        if not self.isVisible():self.toggle()
        self.raise_(); self.activateWindow()
    def hideEvent(self,event):
        super().hideEvent(event)
        self._controls_open=False
    def _select_voice(self,name):
        try:
            self.voice.set_profile(name)
            self._voice_status("Selected "+PROFILES[name]["label"])
        except (OSError,ValueError) as exc:
            self._voice_status("Could not save voice preference: "+str(exc))
        for action,profile_name in zip(self.voice_actions.actions(),PROFILES):
            action.setChecked(profile_name==read_profile())

    def toggle_mute(self): self.muted=not self.muted; action_icon(self.mute,'volume' if self.muted else 'mute','Unmute speech' if self.muted else 'Mute speech'); self.mute.setChecked(self.muted); self.voice.set_muted(self.muted)
    def toggle_mic(self):
        if self.voice.listening:self.voice.stop_listening(); action_icon(self.mic,'mic','Start microphone'); self.mic.setChecked(False)
        else:
            try:self.voice.start_listening(); action_icon(self.mic,'mic','Stop microphone'); self.mic.setChecked(True)
            except Exception as e:self.mic.setChecked(False); self._voice_status(f"Microphone unavailable: {e}")
    def _voice_status(self,text): self.tier.setText("[VOICE]"); self.tray.setToolTip("Jarvis — "+text); self.output.appendPlainText("VOICE  •  "+text)
    def _voice_prompt(self,text): self.output.appendPlainText(f"YOU (VOICE)  ›  {text}"); self._submit_text(text)
    def submit(self):
        if self.busy:
            self.cancel_request()
            return
        text=self.input.text().strip()
        if text:self.input.clear(); self.output.appendPlainText(f"YOU  ›  {text}"); self._submit_text(text)
    def _submit_text(self,text):
        if self.busy:
            self.output.appendPlainText("JARVIS  ›  Still working. Press STOP before sending another request.")
            return
        requested_mode=re.fullmatch(r"(?:enable|activate|use|switch to|turn on) (codex|auto|local)(?: mode)?[.!?]*",text.strip(),re.I)
        if requested_mode:
            self.set_mode(requested_mode[1].lower())
            self.output.appendPlainText('JARVIS  ›  '+requested_mode[1].title()+' mode enabled.')
            return
        self.router.prepare_request(text)
        self.voice.stop_speaking(); self._speech_buffer=SentenceBuffer(); self._speech_streamed=False
        self._using_codex=False; self.mode.setEnabled(False)
        self.busy=True; self.request_id+=1; rid=self.request_id; self.tier.setText("[PROCESSING]"); self.reactor.set_mode("thinking"); action_icon(self.send,'stop','Stop request'); self.send.setEnabled(True); self.streamed=False
        thread=QThread(self); worker=Worker(self.router,text,rid); worker.moveToThread(thread)
        thread.started.connect(worker.run,Qt.ConnectionType.QueuedConnection)
        worker.status.connect(self._worker_status,Qt.ConnectionType.QueuedConnection)
        worker.token.connect(self._token,Qt.ConnectionType.QueuedConnection)
        worker.result.connect(self._finish_request,Qt.ConnectionType.QueuedConnection)
        worker.result.connect(thread.quit,Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(worker.deleteLater); thread.finished.connect(thread.deleteLater)
        self.pending.append((thread,worker)); thread.finished.connect(lambda t=thread:self._release_worker(t)); self._watchdog.start(self.router.budget.handoff_seconds*1000); thread.start()
    def _request_timeout(self):
        if not self.busy or self._using_codex:return
        budget=self.router.budget
        reason=f"The {budget.level} response exceeded {budget.handoff_seconds} seconds (3 × the {budget.expected_seconds}-second estimate)."
        self.router.request_handoff(); self._watchdog.stop(); self.tier.setText("[PROCESSING]")
        threading.Thread(target=self._save_timeout_diagnostics,args=(reason,),daemon=True).start()
    @staticmethod
    def _save_timeout_diagnostics(reason):
        report={"time":datetime.now().astimezone().isoformat(),"reason":reason,"model":os.getenv("JARVIS_MODEL","local-model")}
        try:
            result=subprocess.run(["journalctl","-b","0","-u","ollama","--no-pager","-n","50"],capture_output=True,text=True,timeout=3)
            report["ollama_log"]=result.stdout[-16000:]
            directory=Path.home()/"jarvis/poweroff-diagnostics"
            directory.mkdir(parents=True,exist_ok=True)
            telemetry=directory/"telemetry.jsonl"
            if telemetry.exists():
                with telemetry.open('rb') as source:
                    source.seek(0,2); source.seek(max(0,source.tell()-8192))
                    lines=source.read().decode('utf-8','replace').splitlines()
                if lines:report["telemetry"]=json.loads(lines[-1])
            target=directory/"response-timeout.json"
            temp=target.with_suffix('.tmp'); temp.write_text(json.dumps(report,indent=2)); temp.chmod(0o600); temp.replace(target)
        except Exception:
            traceback.print_exc(file=sys.stderr)
    def cancel_request(self):
        if not self.busy:return
        self.voice.stop_speaking()
        self.router.cancel(); self._watchdog.stop(); self.send.setEnabled(False); self.tier.setText("[STOPPING]")
        self.output.appendPlainText("JARVIS  ›  Stopping this request. Completed actions are not undone.")
    @pyqtSlot(int,str)
    def _worker_status(self,rid,text):
        if rid==self.request_id and self.busy and not self.router.cancel_event.is_set():
            if text.startswith("CODEX:"):
                self._using_codex=True; self._watchdog.stop(); self.tier.setText("[PROCESSING]")
                self.voice.stop_speaking(); self._speech_buffer=SentenceBuffer(); self._speech_streamed=False
            else:self.tier.setText("[EXECUTING]")
            self.tray.setToolTip("Jarvis — preparing your answer" if self._using_codex else "Jarvis — "+text)
    def _release_worker(self,thread):
        self.pending=[pair for pair in self.pending if pair[0] is not thread]
    @pyqtSlot(int,str,str)
    def _finish_request(self,rid,tier,answer):
        if rid!=self.request_id or not self.busy:return
        self._watchdog.stop(); self._done(tier,answer)
    def _done(self,tier,answer):
        if not self.busy and tier=="ERROR":return
        self.busy=False; self.mode.setEnabled(True); self._using_codex=False; action_icon(self.send,'send','Send request'); self.send.setEnabled(True); self.reactor.set_mode("idle"); self.tier.setText("["+("READY" if tier=="CODEX" else tier)+"]")
        if not self.streamed or tier in {"ERROR","STOPPED","CODEX"}:self.output.appendPlainText("JARVIS  ›  "+answer)
        self.output.appendPlainText("")
        if tier in {"ERROR","STOPPED"}:
            self.voice.stop_speaking(); self._speech_buffer=SentenceBuffer()
            if tier=="ERROR":self.voice.speak(answer)
        else:
            if not self._speech_streamed:
                for chunk in self._speech_buffer.feed(answer):self.voice.speak(chunk)
            for chunk in self._speech_buffer.finish():self.voice.speak(chunk)
    def _token(self,rid,token):
        if rid!=self.request_id or not self.busy or self.router.interrupted():return
        if not token.strip() and not self.streamed:return
        if not self.streamed:
            self.output.appendPlainText("JARVIS  ›  "); self.streamed=True
        self._speech_streamed=True
        for chunk in self._speech_buffer.feed(token):self.voice.speak(chunk)
        cur=self.output.textCursor(); cur.movePosition(cur.MoveOperation.End); cur.insertText(token); self.output.setTextCursor(cur); self.output.ensureCursorVisible()
    def restart_engine(self):
        if LOCAL_SAFETY_LOCK.exists():
            self.output.appendPlainText("JARVIS  ›  Local AI remains disabled while the power-offs are investigated.")
            return
        import subprocess
        if os.getenv("JARVIS_LOCAL_URL", "").startswith("http://127.0.0.1:11434/"):
            subprocess.Popen(["ollama", "stop", os.getenv("JARVIS_MODEL", "jarvis-gemma4-12b")])
        else:
            subprocess.Popen(["systemctl", "--user", "restart", "jarvis-llm.service"])

def toggle_existing(command=b"toggle"):
    sock=QLocalSocket(); sock.connectToServer(SOCKET)
    if not sock.waitForConnected(250):return False
    sock.write(command); sock.flush(); sock.waitForBytesWritten(250); sock.disconnectFromServer(); return True
def main():
    from voice_profiles import apply_pending_profile
    try:apply_pending_profile()
    except (OSError,ValueError) as exc:print('Could not activate staged voice: '+str(exc),file=sys.stderr)
    app=QApplication(sys.argv); app.setQuitOnLastWindowClosed(False)
    if '--resources' in sys.argv and toggle_existing(b'resources'):return 0
    if '--control-panel' in sys.argv and toggle_existing(b'control-panel'):return 0
    if "--toggle" in sys.argv and toggle_existing():return 0
    popup="--popup" in sys.argv
    hud=Hud(app)
    try:
        from browser_control import get_broker,DesktopError
        get_broker().start()
    except (OSError,RuntimeError,DesktopError) as exc:print('Browser controls unavailable: '+str(exc),file=sys.stderr)
    from resource_limits import get_manager
    get_manager()  # Restore/reapply explicitly configured budgets after login.
    if '--resources' in sys.argv:hud.show_resource_panel()
    if '--control-panel' in sys.argv:hud.toggle_control_panel()
    if "--show" in sys.argv or "--toggle" in sys.argv or popup:hud.toggle()
    return app.exec()
if __name__=="__main__":raise SystemExit(main())

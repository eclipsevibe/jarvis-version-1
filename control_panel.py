"""Local Jarvis settings window, independent of the compact command bar."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                            QPushButton, QVBoxLayout)
from voice_profiles import PROFILES, read_profile


class ControlPanel(QDialog):
    def __init__(self, hud):
        super().__init__(hud, Qt.WindowType.Window)
        self.hud = hud
        self.setWindowTitle('Jarvis Control Panel')
        self.setMinimumWidth(480)
        self.setStyleSheet('''
            QDialog {background:#140f09;color:#ffe2b5}
            QLabel {color:#e0c3a0;font-size:13px}
            QLabel#heading {color:#ffb438;font:bold 22px 'DejaVu Sans Mono'}
            QPushButton,QComboBox {background:#302111;color:#ffe2b5;
                border:1px solid #966023;border-radius:8px;padding:10px}
            QPushButton:hover {background:#493018}
            QPushButton:disabled {color:#887562;border-color:#554737}
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        layout.addWidget(QLabel('JARVIS CONTROL PANEL', objectName='heading'))
        layout.addWidget(QLabel('Super+J to open or close · Escape to close'))
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)

        row = QHBoxLayout()
        self.mode = QPushButton(); self.mode.clicked.connect(self.change_mode); row.addWidget(self.mode)
        self.mic = QPushButton(); self.mic.clicked.connect(self.change_mic); row.addWidget(self.mic)
        self.mute = QPushButton(); self.mute.clicked.connect(self.change_mute); row.addWidget(self.mute)
        layout.addLayout(row)
        resources=QPushButton('CPU / RAM budgets, app exemptions and fan speed…')
        resources.clicked.connect(hud.show_resource_panel);layout.addWidget(resources)
        hardware=QHBoxLayout()
        for title,panel in [('Bluetooth','bluetooth'),('Wi-Fi','wifi'),('Sound','sound')]:
            button=QPushButton(title+' settings')
            button.clicked.connect(lambda _,p=panel:self.open_settings(p));hardware.addWidget(button)
        layout.addLayout(hardware)

        memory=QPushButton('Learning memory — review, pause or forget…')
        memory.clicked.connect(self.open_memory);layout.addWidget(memory)

        layout.addWidget(QLabel('Laptop voice'))
        self.voice = QComboBox()
        for name, profile in PROFILES.items(): self.voice.addItem(profile['label'], name)
        self.voice.activated.connect(self.change_voice)
        layout.addWidget(self.voice)
        preview = QPushButton('Preview selected voice')
        preview.clicked.connect(lambda: hud.voice.speak('At your service. All systems are ready.'))
        layout.addWidget(preview)

        row = QHBoxLayout()
        conversation = QPushButton('Open conversation')
        conversation.clicked.connect(hud.show_controls); row.addWidget(conversation)
        command = QPushButton('Open text box')
        command.clicked.connect(self.open_command); row.addWidget(command)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.stop = QPushButton('Stop current request')
        self.stop.clicked.connect(hud.cancel_request); row.addWidget(self.stop)
        restart = QPushButton('Restart local engine')
        restart.clicked.connect(hud.restart_engine); row.addWidget(restart)
        layout.addLayout(row)
        close = QPushButton('Close control panel'); close.clicked.connect(self.hide); layout.addWidget(close)
        self.timer = QTimer(self); self.timer.setInterval(500); self.timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self):
        hud = self.hud
        self.status.setText(('Processing a request' if hud.busy else 'Ready')+' · '+hud.telemetry.text())
        self.mode.setText('Mode: '+hud.router.mode.title())
        self.mode.setEnabled(hud.mode.isEnabled())
        self.mic.setText('Microphone: On' if hud.voice.listening else 'Microphone: Off')
        self.mute.setText('Unmute speech' if hud.muted else 'Mute speech')
        self.stop.setEnabled(hud.busy)
        self.voice.setCurrentIndex(max(0, self.voice.findData(read_profile())))

    def change_mode(self):
        self.hud.mode.click(); self.refresh()

    def open_memory(self):
        from memory_panel import MemoryPanel
        if not hasattr(self,'memory_panel'):self.memory_panel=MemoryPanel(self.hud)
        self.memory_panel.refresh();self.memory_panel.show();self.memory_panel.raise_();self.memory_panel.activateWindow()

    def open_settings(self,panel):
        from desktop_tools import open_settings,DesktopError
        try:open_settings(panel)
        except (DesktopError,OSError) as exc:self.status.setText(str(exc))

    def change_mic(self):
        self.hud.toggle_mic(); self.refresh()

    def change_mute(self):
        self.hud.toggle_mute(); self.refresh()

    def change_voice(self, index):
        self.hud._select_voice(self.voice.itemData(index)); self.refresh()

    def open_command(self):
        self.hud.hide()
        self.hud.toggle()

    def toggle(self):
        if self.isVisible(): self.hide()
        else:
            self.refresh(); self.show(); self.raise_(); self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event); self.timer.start()

    def hideEvent(self, event):
        self.timer.stop(); super().hideEvent(event)

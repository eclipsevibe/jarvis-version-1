"""Local process exemptions and verified user-app resource budgets."""
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
                            QSpinBox,QDoubleSpinBox,QCheckBox,QListWidget,QMessageBox)
from resource_limits import get_manager


class ResourcePanel(QDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.manager=get_manager()
        self.setWindowTitle('Jarvis — CPU / RAM budgets and fan readings')
        self.resize(720,600)
        layout=QVBoxLayout(self)
        label=QLabel('Combined budgets for managed user apps. Kernel/system services, desktop infrastructure and recovery terminals are excluded. Exemptions apply to the selected app’s process group and expire when it closes.')
        label.setWordWrap(True);layout.addWidget(label)
        row=QHBoxLayout();row.addWidget(QLabel('CPU % of machine:'))
        self.cpu=QSpinBox();self.cpu.setRange(25,95);self.cpu.setValue(self.manager.config['cpu_percent']);row.addWidget(self.cpu)
        self.ram_on=QCheckBox('Soft RAM budget (GiB):');self.ram_on.setChecked(bool(self.manager.config['ram_gib']));row.addWidget(self.ram_on)
        self.ram=QDoubleSpinBox();self.ram.setRange(2,512);self.ram.setSingleStep(.5);self.ram.setValue(self.manager.config['ram_gib'] or 8);row.addWidget(self.ram)
        layout.addLayout(row)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.fan=QLabel();self.fan.setWordWrap(True);layout.addWidget(self.fan)
        self.apps=QListWidget();layout.addWidget(self.apps)
        row=QHBoxLayout()
        for text,callback in [('Apply budgets',self.apply),('Disable / restore',self.disable),('Refresh apps',self.reload)]:
            b=QPushButton(text);b.clicked.connect(callback);row.addWidget(b)
        layout.addLayout(row)
        row=QHBoxLayout()
        for text,action in [('Exempt selected app','exempt'),('Remove exemption','unexempt')]:
            b=QPushButton(text);b.clicked.connect(lambda _,a=action:self.exempt(a));row.addWidget(b)
        layout.addLayout(row)
        close=QPushButton('Close');close.clicked.connect(self.hide);layout.addWidget(close)
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.refresh)
        self.signature=None;self.refresh()

    def confirm(self,text):
        return QMessageBox.question(self,'Apply resource budgets',text)==QMessageBox.StandardButton.Yes

    def apply(self):
        try:
            result=self.manager.command(dict(action='set',cpu_percent=self.cpu.value(),ram_gib=self.ram.value() if self.ram_on.isChecked() else None),self.confirm)
            self.status.setText(result)
        except (OSError,ValueError) as exc:QMessageBox.warning(self,'Resource limits',str(exc))

    def disable(self):
        self.manager.command({'action':'disable'},lambda _:True);self.refresh()

    def exempt(self,action):
        index=self.apps.currentRow()
        if 0<=index<len(self.rows) and self.rows[index]['pids']:
            self.manager.command({'action':action,'pid':self.rows[index]['pids'][0]},lambda _:True)
            self.signature=None

    def reload(self):
        self.manager.refresh();self.refresh()

    def refresh(self):
        state=self.manager.snapshot()
        self.status.setText(state['message'])
        self.fan.setText('Fan speed: '+state['fan']+'\nFan control is not enabled.')
        signature=str((state['apps'],state['exemptions']))
        if signature!=self.signature:
            selected=self.apps.currentRow();self.apps.clear();self.rows=state['apps']
            for row in self.rows:
                self.apps.addItem(('EXEMPT — ' if row['unit'] in state['exemptions'] else '')+row['description']+
                    f"\nPIDs: {', '.join(map(str,row['pids'][:12]))} · {row['memory_mib']} MiB\n{row['unit']}")
            self.apps.setCurrentRow(selected);self.signature=signature

    def showEvent(self,event):
        super().showEvent(event);self.timer.start();self.reload()

    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)

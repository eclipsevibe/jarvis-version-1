"""Review and control Jarvis's local learning memory."""
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QLineEdit,QListWidget,QPlainTextEdit,QPushButton,QMessageBox)


class MemoryPanel(QDialog):
    def __init__(self,hud):
        super().__init__(hud)
        self.store=hud.router.memory
        self.setWindowTitle('Jarvis — Learning memory');self.resize(700,560)
        layout=QVBoxLayout(self)
        self.enabled=QCheckBox('Learn from interactions and recall relevant memories');layout.addWidget(self.enabled)
        self.enabled.toggled.connect(self.set_enabled)
        info=QLabel('Saved on this laptop. Preferences and relevant past conversations inform future replies on this desktop. This does not retrain the model. Relevant snippets go to your selected AI backend; extraction itself makes no AI calls. Obvious credentials are redacted.');info.setWordWrap(True);layout.addWidget(info)
        self.status=QLabel();layout.addWidget(self.status)
        self.search=QLineEdit();self.search.setPlaceholderText('Search saved preferences and interactions…');layout.addWidget(self.search)
        self.search.textChanged.connect(self.refresh)
        self.rows=QListWidget();layout.addWidget(self.rows);self.rows.currentRowChanged.connect(self.selected)
        self.detail=QPlainTextEdit();self.detail.setReadOnly(True);layout.addWidget(self.detail)
        row=QHBoxLayout()
        for title,action in [('Refresh',self.refresh),('Forget selected',self.forget_selected),('Forget everything',self.forget_all)]:
            button=QPushButton(title);button.clicked.connect(action);row.addWidget(button)
        layout.addLayout(row);self.refresh()

    def refresh(self,*_):
        try:
            state=self.store.state();self.enabled.blockSignals(True);self.enabled.setChecked(state['enabled']);self.enabled.blockSignals(False)
            self.status.setText(f"{state['preferences']} preferences · {state['interactions']} interactions · newest 50 matches shown")
            self.items=self.store.entries(self.search.text());self.rows.clear();self.detail.clear()
            for item in self.items:self.rows.addItem(('Preference: ' if item['kind']=='preference' else 'Conversation: ')+item['text'][:120].replace('\n',' '))
        except Exception:self.status.setText('Memory could not be read. Check laptop storage permissions.')

    def selected(self,index):
        if 0<=index<len(self.items):
            item=self.items[index];self.detail.setPlainText(item['text']+('\n\nJARVIS:\n'+item['answer'] if item['answer'] else ''))

    def set_enabled(self,value):
        self.store.enable(value);self.refresh()

    def forget_selected(self):
        index=self.rows.currentRow()
        if index<0:return
        item=self.items[index];self.store.delete(item['kind'],item['id']);self.refresh()

    def forget_all(self):
        if QMessageBox.question(self,'Forget all learning memory','Delete every saved preference and interaction?')==QMessageBox.StandardButton.Yes:
            self.store.forget();self.refresh()

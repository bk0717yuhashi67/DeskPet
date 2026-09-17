"""提醒设置界面：左边列表，右边表单。编辑只改内存，Apply/OK 才写库。"""
from __future__ import annotations

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ui.style import DIALOG_QSS, center_on_screen
from remind.model import (
    ACTION_LABELS,
    KIND_DAILY,
    KIND_ONCE,
    KIND_WEEKLY,
    WEEKDAY_LABELS,
    Reminder,
)
from remind.store import ReminderStore

KIND_ITEMS = [(KIND_DAILY, "每天"), (KIND_WEEKLY, "每周指定几天"), (KIND_ONCE, "只提醒一次")]


class ReminderDialog(QDialog):
    test_requested = Signal(object)

    def __init__(self, store: ReminderStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.items: list[Reminder] = []
        self.current: Reminder | None = None
        self._loading = False

        self.setWindowTitle("提醒设置")
        self.setStyleSheet(DIALOG_QSS)
        self.resize(660, 462)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        head = QLabel("提醒")
        head.setObjectName("H1")
        root.addWidget(head)
        hint = QLabel("预设了 12:00 / 18:00 吃饭、22:00 休息三条，可以直接改或删。")
        hint.setObjectName("Hint")
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ---- 左：列表 ----
        left = QVBoxLayout()
        left.setSpacing(7)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.list, 1)

        tools = QHBoxLayout()
        tools.setSpacing(7)
        self.btn_add = QPushButton("新增")
        self.btn_add.clicked.connect(self._on_add)
        self.btn_del = QPushButton("删除")
        self.btn_del.clicked.connect(self._on_delete)
        self.btn_restore = QPushButton("恢复预设")
        self.btn_restore.clicked.connect(self._on_restore)
        tools.addWidget(self.btn_add)
        tools.addWidget(self.btn_del)
        tools.addWidget(self.btn_restore)
        left.addLayout(tools)
        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setFixedWidth(252)
        body.addWidget(left_wrap)

        # ---- 右：表单 ----
        right = QVBoxLayout()
        right.setSpacing(9)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(9)

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        form.addRow("时间", self.time_edit)

        self.kind_combo = QComboBox()
        for key, label in KIND_ITEMS:
            self.kind_combo.addItem(label, key)
        self.kind_combo.currentIndexChanged.connect(self._sync_kind)
        form.addRow("重复", self.kind_combo)

        self.week_row = QWidget()
        wk = QHBoxLayout(self.week_row)
        wk.setContentsMargins(0, 0, 0, 0)
        wk.setSpacing(4)
        self.day_boxes: list[QCheckBox] = []
        for label in WEEKDAY_LABELS:
            cb = QCheckBox(label)
            self.day_boxes.append(cb)
            wk.addWidget(cb)
        wk.addStretch(1)
        form.addRow("星期", self.week_row)

        self.msg_edit = QLineEdit()
        self.msg_edit.setPlaceholderText("到点想让我说什么？")
        form.addRow("提醒内容", self.msg_edit)

        self.action_combo = QComboBox()
        for key, label in ACTION_LABELS:
            self.action_combo.addItem(label, key)
        form.addRow("企鹅动作", self.action_combo)

        self.snooze_spin = QSpinBox()
        self.snooze_spin.setRange(1, 120)
        self.snooze_spin.setSuffix(" 分钟")
        form.addRow("「稍后」间隔", self.snooze_spin)

        row2 = QHBoxLayout()
        self.enabled_check = QCheckBox("启用")
        self.urgent_check = QCheckBox("紧急（可穿透安静模式）")
        row2.addWidget(self.enabled_check)
        row2.addWidget(self.urgent_check)
        row2.addStretch(1)
        wrap2 = QWidget()
        wrap2.setLayout(row2)
        form.addRow("", wrap2)

        right.addLayout(form)

        ops = QHBoxLayout()
        self.btn_test = QPushButton("测试提醒")
        self.btn_test.clicked.connect(self._on_test)
        self.btn_apply = QPushButton("应用修改")
        self.btn_apply.clicked.connect(self._on_apply)
        ops.addWidget(self.btn_test)
        ops.addStretch(1)
        ops.addWidget(self.btn_apply)
        right.addLayout(ops)
        right.addStretch(1)
        body.addLayout(right, 1)

        root.addLayout(body, 1)

        # ---- 底部 ----
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("确定")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._sync_kind()
        self.reload()
        center_on_screen(self)

    # ------------------------------------------------------------ 数据
    def reload(self) -> None:
        self.items = self.store.all()
        self._refresh_list()

    def _refresh_list(self, keep_row: int | None = None) -> None:
        row = keep_row if keep_row is not None else self.list.currentRow()
        self._loading = True
        self.list.clear()
        for r in self.items:
            item = QListWidgetItem(r.summary())
            if r.builtin:
                item.setToolTip("预设提醒")
            self.list.addItem(item)
        self._loading = False
        if self.items:
            row = max(0, min(row, len(self.items) - 1))
            self.list.setCurrentRow(row)
        else:
            self.current = None
            self._fill_form(None)

    def _on_select(self, row: int) -> None:
        if self._loading or row < 0 or row >= len(self.items):
            return
        self.current = self.items[row]
        self._fill_form(self.current)

    # ------------------------------------------------------------ 表单
    def _fill_form(self, r: Reminder | None) -> None:
        self._loading = True
        enabled = r is not None
        for w in (
            self.time_edit, self.kind_combo, self.msg_edit, self.action_combo,
            self.snooze_spin, self.enabled_check, self.urgent_check,
            self.btn_test, self.btn_apply,
        ):
            w.setEnabled(enabled)
        for cb in self.day_boxes:
            cb.setEnabled(enabled)

        if r is None:
            self._loading = False
            self._sync_kind()
            return

        try:
            hh, mm = (int(x) for x in r.time_hhmm.split(":"))
        except Exception:
            hh, mm = 12, 0
        self.time_edit.setTime(QTime(hh, mm))

        idx = self.kind_combo.findData(r.kind)
        self.kind_combo.setCurrentIndex(max(0, idx))

        for i, cb in enumerate(self.day_boxes):
            cb.setChecked(bool(r.weekdays >> i & 1))

        self.msg_edit.setText(r.message)
        aidx = self.action_combo.findData(r.action)
        self.action_combo.setCurrentIndex(max(0, aidx))
        self.snooze_spin.setValue(max(1, min(120, r.snooze_min)))
        self.enabled_check.setChecked(r.enabled)
        self.urgent_check.setChecked(r.urgent)

        self._loading = False
        self._sync_kind()

    def _sync_kind(self) -> None:
        kind = self.kind_combo.currentData()
        weekly = kind == KIND_WEEKLY
        self.week_row.setVisible(weekly)
        if not weekly:
            for cb in self.day_boxes:
                cb.setEnabled(False)
        elif self.current is not None and not self._loading:
            for cb in self.day_boxes:
                cb.setEnabled(True)

    def _collect_form(self, base: Reminder) -> Reminder:
        t = self.time_edit.time()
        r = Reminder(**{**base.__dict__})
        r.time_hhmm = f"{t.hour():02d}:{t.minute():02d}"
        r.kind = self.kind_combo.currentData() or KIND_DAILY
        wd = 0
        for i, cb in enumerate(self.day_boxes):
            if cb.isChecked():
                wd |= 1 << i
        r.weekdays = wd or 0b1111111
        r.message = self.msg_edit.text().strip()
        r.action = self.action_combo.currentData() or "auto"
        r.snooze_min = int(self.snooze_spin.value())
        r.enabled = self.enabled_check.isChecked()
        r.urgent = self.urgent_check.isChecked()
        r.clamp_time()
        return r

    # ------------------------------------------------------------ 操作
    def _on_add(self) -> None:
        r = self.store.new_draft()
        r.id = None
        self.items.append(r)
        self._refresh_list(keep_row=len(self.items) - 1)
        self.msg_edit.setFocus()

    def _on_delete(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.items):
            return
        r = self.items[row]
        if r.id and r.id > 0:
            self.store.delete(r.id, mark_removed=bool(r.builtin))
        self.items.pop(row)
        self._refresh_list(keep_row=max(0, row - 1))

    def _on_restore(self) -> None:
        self.store.restore_builtins()
        self.reload()

    def _on_apply(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.items):
            return
        base = self.items[row]
        updated = self._collect_form(base)
        if updated.id and updated.id > 0:
            self.store.update(updated)
        else:
            updated.id = self.store.add(updated)
        self.items[row] = updated
        self.current = updated
        self._refresh_list(keep_row=row)

    def _on_test(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.items):
            return
        updated = self._collect_form(self.items[row])
        self.test_requested.emit(updated)

    def _on_ok(self) -> None:
        self._on_apply()
        self.accept()

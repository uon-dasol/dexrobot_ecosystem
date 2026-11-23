"""Desktop entry point. SPDX-License-Identifier: Apache-2.0."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .backend import Connection, Worker
from .core import COLORS, FINGERS, FINGER_NAMES, JOINTS, STALE_SECONDS, Snapshot
from .widgets import ForceCard, HandMap, HistoryPlot, number

STYLE = """
QWidget { background: #0c1421; color: #e7eef8; font-family: 'Sans Serif'; font-size: 12px; }
QMainWindow { background: #0c1421; }
QLabel#title { font-size: 25px; font-weight: 700; }
QLabel#muted { color: #91a1b7; }
QLabel#badge { background: #203d36; color: #7ce7ba; border-radius: 5px; padding: 6px 12px; font-weight: 700; }
QFrame#card, QGroupBox { background: #121d2d; border: 1px solid #29374d; border-radius: 9px; }
QFrame#card QLabel, QGroupBox QLabel, QGroupBox QCheckBox { background: transparent; }
QGroupBox { margin-top: 12px; padding: 16px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #9babc1; }
QPushButton { background: #203149; border: 1px solid #344861; padding: 9px 14px; border-radius: 5px; }
QPushButton:hover { background: #2c4461; }
QPushButton:disabled { color: #65738a; background: #172235; }
QPushButton#primary { background: #65dcb0; color: #10251d; border: none; font-weight: 700; }
QPushButton#primary:disabled { background: #254839; color: #708a7e; }
QComboBox, QSpinBox, QDoubleSpinBox { background: #1b293c; padding: 6px; border: 1px solid #34445c; border-radius: 4px; }
QComboBox QAbstractItemView { background: #1b293c; selection-background-color: #355570; }
QTabWidget::pane { border: 1px solid #29374d; border-radius: 6px; }
QTabBar::tab { background: #111e30; padding: 12px 18px; color: #94a5bd; }
QTabBar::tab:selected { background: #22364d; color: #83e9c2; }
QTableWidget { background: #121d2d; alternate-background-color: #162235; gridline-color: #26364c; border: none; }
QHeaderView::section { background: #1b2a3e; color: #a8b8cc; border: none; padding: 8px; }
QPlainTextEdit { background: #101b2a; color: #b6c6d9; border: none; font-family: monospace; font-size: 11px; }
QSplitter::handle { background: #243248; }
QToolTip { background: #203149; color: #e7eef8; border: 1px solid #456; }
"""


def table(headers, rows):
    result = QTableWidget(rows, len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.verticalHeader().setVisible(False)
    result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    result.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    result.setAlternatingRowColors(True)
    result.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    return result


class MonitorWindow(QMainWindow):
    def __init__(self, config: Connection):
        super().__init__()
        self.setWindowTitle("DexHand Monitor · tactile & motion")
        self.resize(1440, 940)
        self.worker = None
        self.connected = False
        self.closing = False
        self.latest = None
        self.synced = set()
        self.record_path = ""
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 12)
        layout.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("DexHand Monitor")
        title.setObjectName("title")
        titles.addWidget(title)
        subtitle = QLabel("Tactile sensing / motor feedback / explicit joint control")
        subtitle.setObjectName("muted")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.badge = QLabel("DEMO" if config.demo else "HARDWARE")
        self.badge.setObjectName("badge")
        header.addWidget(self.badge)
        about = QPushButton("About / licenses")
        about.clicked.connect(self.about)
        header.addWidget(about)
        layout.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._sidebar(config))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._overview(), "Tactile overview")
        self.tabs.addTab(self._motors(), "Motor feedback")
        self.tabs.addTab(self._controls(), "Joint control")
        self.traffic_log = QPlainTextEdit()
        self.traffic_log.setReadOnly(True)
        self.traffic_log.setMaximumBlockCount(3000)
        self.tabs.addTab(self.traffic_log, "CAN traffic")
        self.session_log = QPlainTextEdit()
        self.session_log.setReadOnly(True)
        self.session_log.setMaximumBlockCount(1000)
        self.tabs.addTab(self.session_log, "Session log")
        body.addWidget(self.tabs, 1)
        layout.addLayout(body, 1)
        self.footer = QLabel("Disconnected · connect in demo mode to explore the monitor")
        self.footer.setObjectName("muted")
        layout.addWidget(self.footer)
        self._log("Ready. Connecting never sends a joint position command.")

    def _sidebar(self, config):
        panel = QWidget()
        panel.setFixedWidth(252)
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        group = QGroupBox("CONNECTION")
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.mode = QComboBox()
        self.mode.addItems(["Demo", "Hardware"])
        self.mode.setCurrentIndex(0 if config.demo else 1)
        form.addRow("Source", self.mode)
        self.hand = QComboBox()
        self.hand.addItems(["right", "left"])
        self.hand.setCurrentText(config.hand)
        form.addRow("Hand", self.hand)
        self.device = QComboBox()
        self.device.addItem("USBCANFD-100U", "ZCAN_USBCANFD_100U")
        self.device.addItem("USBCANFD-200U", "ZCAN_USBCANFD_200U")
        self.device.setCurrentIndex(self.device.findData(config.device_type))
        form.addRow("Adapter", self.device)
        self.channel = QSpinBox()
        self.channel.setRange(0, 1)
        self.channel.setValue(config.channel)
        form.addRow("Channel", self.channel)
        self.index = QSpinBox()
        self.index.setRange(0, 15)
        self.index.setValue(config.device_index)
        form.addRow("Device index", self.index)
        self.period = QSpinBox()
        self.period.setRange(10, 200)
        self.period.setSingleStep(10)
        self.period.setSuffix(" ms")
        self.period.setValue(config.period_ms)
        form.addRow("Feedback", self.period)
        self.termination = QCheckBox("Adapter termination")
        self.termination.setChecked(config.termination)
        form.addRow(self.termination)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self.toggle_connection)
        form.addRow(self.connect_button)
        self.connection_fields = [self.mode, self.hand, self.device, self.channel,
                                  self.index, self.period, self.termination]
        box.addWidget(group)
        stream = QGroupBox("STREAM")
        stream_box = QVBoxLayout(stream)
        self.status = QLabel("Disconnected")
        self.status.setWordWrap(True)
        stream_box.addWidget(self.status)
        self.rate = QLabel("0 frames/s")
        self.rate.setObjectName("muted")
        stream_box.addWidget(self.rate)
        self.frames_count = QLabel("0 received")
        self.frames_count.setObjectName("muted")
        stream_box.addWidget(self.frames_count)
        box.addWidget(stream)
        recording = QGroupBox("RECORDING")
        recbox = QVBoxLayout(recording)
        self.record_button = QPushButton("Record CSV…")
        self.record_button.setEnabled(False)
        self.record_button.clicked.connect(self.toggle_recording)
        recbox.addWidget(self.record_button)
        self.record_status = QLabel("Not recording")
        self.record_status.setObjectName("muted")
        self.record_status.setWordWrap(True)
        recbox.addWidget(self.record_status)
        box.addWidget(recording)
        self.clear_button = QPushButton("Clear motor errors")
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(lambda: self.worker.submit("clear") if self.worker else None)
        box.addWidget(self.clear_button)
        hint = QLabel("Start with tactile feedback. Joint commands are enabled separately in the control tab.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        box.addWidget(hint)
        box.addStretch()
        return panel

    def _overview(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(14)
        cards = QHBoxLayout()
        self.cards = []
        for name, color in zip(FINGER_NAMES, COLORS):
            card = ForceCard(name, color)
            cards.addWidget(card)
            self.cards.append(card)
        box.addLayout(cards)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        hand_group = QGroupBox("CONTACT MAP")
        hand_box = QVBoxLayout(hand_group)
        self.hand_map = HandMap()
        hand_box.addWidget(self.hand_map, 1)
        legend = QLabel("5 fingertip sensors · normal force (N)\nCircle size saturates at 5 N; numeric values stay unscaled.")
        legend.setObjectName("muted")
        legend.setWordWrap(True)
        hand_box.addWidget(legend)
        splitter.addWidget(hand_group)
        graph_group = QGroupBox("FORCE HISTORY")
        graph_box = QVBoxLayout(graph_group)
        options = QHBoxLayout()
        self.metric_combo = QComboBox()
        self.metric_combo.addItems(["Normal force", "Tangential force"])
        self.metric_combo.currentIndexChanged.connect(self._plot_options)
        options.addWidget(self.metric_combo)
        self.plot_finger = QComboBox()
        self.plot_finger.addItems(["All fingers", *FINGER_NAMES])
        self.plot_finger.currentIndexChanged.connect(self._plot_options)
        options.addWidget(self.plot_finger)
        self.window_combo = QComboBox()
        self.window_combo.addItems(["10 s", "30 s", "60 s"])
        self.window_combo.currentIndexChanged.connect(self._plot_options)
        options.addWidget(self.window_combo)
        self.pause_plot = QCheckBox("Pause plot")
        self.pause_plot.toggled.connect(self._plot_options)
        options.addWidget(self.pause_plot)
        graph_box.addLayout(options)
        self.plot = HistoryPlot()
        graph_box.addWidget(self.plot, 1)
        colors = QHBoxLayout()
        for name, color in zip(FINGER_NAMES, COLORS):
            label = QLabel("● " + name)
            label.setStyleSheet(f"color: {color}; background: transparent;")
            colors.addWidget(label)
        graph_box.addLayout(colors)
        splitter.addWidget(graph_group)
        splitter.setSizes([330, 650])
        box.addWidget(splitter, 1)
        self.sensors = table(["Finger", "Normal (N)", "Shear (N)", "Direction (°)",
                              "Proximity", "Temp (°C)", "Age (ms)", "Status"], 5)
        self.sensors.setMaximumHeight(210)
        for row, name in enumerate(FINGER_NAMES):
            self.sensors.setItem(row, 0, QTableWidgetItem(name))
        box.addWidget(self.sensors)
        return page

    def _motors(self):
        self.motors = table(["Joint", "Angle (°)", "Current (mA)", "Speed (rpm)",
                             "Error", "Age (ms)", "Status"], 12)
        for row, (name, _, _) in enumerate(JOINTS):
            self.motors.setItem(row, 0, QTableWidgetItem(name))
        return self.motors

    def _controls(self):
        page = QWidget()
        box = QVBoxLayout(page)
        note = QLabel("Stage targets, select joints, then apply once. Connecting and changing fields do not move the hand.")
        note.setWordWrap(True)
        box.addWidget(note)
        settings = QHBoxLayout()
        self.control_mode = QComboBox()
        self.control_mode.addItems(["IMPEDANCE_GRASP", "CASCADED_PID", "PROTECT_HALL_POSITION"])
        settings.addWidget(QLabel("Mode"))
        settings.addWidget(self.control_mode)
        self.current = QSpinBox()
        self.current.setRange(10, 255)
        self.current.setValue(20)
        self.current.setSuffix(" mA")
        settings.addWidget(QLabel("Current limit"))
        settings.addWidget(self.current)
        self.speed = QSpinBox()
        self.speed.setRange(0, 32767)
        self.speed.setValue(5000)
        self.speed.setToolTip("SDK command speed in hardware units; not degrees per second.")
        settings.addWidget(QLabel("Speed (raw)"))
        settings.addWidget(self.speed)
        settings.addStretch()
        box.addLayout(settings)
        grid = QGridLayout()
        self.targets = {}
        self.selected = {}
        for row, (name, label, limit) in enumerate(JOINTS):
            select = QCheckBox(label)
            value = QDoubleSpinBox()
            value.setRange(0, limit)
            value.setDecimals(1)
            value.setSingleStep(1)
            value.setSuffix(" °")
            grid.addWidget(select, row % 6, (row // 6) * 2)
            grid.addWidget(value, row % 6, (row // 6) * 2 + 1)
            self.selected[name] = select
            self.targets[name] = value
        box.addLayout(grid)
        presets = QHBoxLayout()
        sync = QPushButton("Copy measured angles")
        sync.clicked.connect(self.copy_measured)
        presets.addWidget(sync)
        for label, curl in [("Stage open", 0), ("Stage half curl", 30)]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, angle=curl: self.stage_preset(angle))
            presets.addWidget(btn)
        presets.addStretch()
        box.addLayout(presets)
        self.arm = QCheckBox("Enable joint commands for this session")
        self.arm.setEnabled(False)
        self.arm.toggled.connect(self._armed)
        box.addWidget(self.arm)
        self.send = QPushButton("Apply selected joint targets")
        self.send.setObjectName("primary")
        self.send.setEnabled(False)
        self.send.clicked.connect(self.apply_targets)
        box.addWidget(self.send)
        status = QLabel("Fresh motor feedback is required. Disconnect closes CAN; it is not a hardware emergency stop.")
        status.setObjectName("muted")
        status.setWordWrap(True)
        box.addWidget(status)
        box.addStretch()
        return page

    def _plot_options(self, *_):
        if not hasattr(self, "plot"):
            return
        self.plot.metric_name = "normal" if self.metric_combo.currentIndex() == 0 else "tangential"
        self.plot.finger = None if self.plot_finger.currentIndex() == 0 else self.plot_finger.currentIndex() - 1
        self.plot.window_seconds = (10, 30, 60)[self.window_combo.currentIndex()]
        self.plot.paused = self.pause_plot.isChecked()
        self.plot.update()

    def toggle_connection(self):
        if self.worker:
            self.arm.setChecked(False)
            self.connect_button.setEnabled(False)
            self.connect_button.setText("Disconnecting…")
            self.worker.stop()
            return
        config = Connection(self.mode.currentIndex() == 0, self.hand.currentText(),
                            self.channel.value(), self.index.value(), self.device.currentData(),
                            self.period.value(), self.termination.isChecked())
        self.plot.clear()
        self.synced.clear()
        self.latest = None
        self.traffic_log.clear()
        self.arm.setChecked(False)
        for selected in self.selected.values():
            selected.setChecked(False)
        self.badge.setText("DEMO" if config.demo else "HARDWARE")
        self.worker = Worker(config, self)
        self.worker.sample.connect(self.on_sample)
        self.worker.state.connect(self.on_state)
        self.worker.message.connect(self._log)
        self.worker.frames.connect(self.on_frames)
        self.worker.recording.connect(self.on_recording)
        self.worker.finished.connect(self.on_finished)
        for field in self.connection_fields:
            field.setEnabled(False)
        self.connect_button.setText("Disconnect")
        self.worker.start()

    def on_state(self, text, connected):
        self.connected = connected
        self.status.setText(text)
        self.record_button.setEnabled(connected)
        self.clear_button.setEnabled(connected)
        self.arm.setEnabled(connected)
        if not connected:
            self.arm.setChecked(False)
            if self.latest:
                stale = replace(self.latest,
                                sensors={k: replace(v, age=float("inf"), fresh=False)
                                         for k, v in self.latest.sensors.items()},
                                motors={k: replace(v, age=float("inf")) for k, v in self.latest.motors.items()},
                                rx_hz=0)
                self.on_sample(stale, plot=False)

    def on_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        for field in self.connection_fields:
            field.setEnabled(True)
        self.connect_button.setEnabled(True)
        self.connect_button.setText("Connect")
        if self.closing:
            QTimer.singleShot(0, self.close)

    def on_sample(self, sample: Snapshot, plot=True):
        self.latest = sample
        for i, name in enumerate(FINGERS):
            sensor = sample.sensors.get(name)
            self.cards[i].update_sensor(sensor)
            live = sensor is not None and sensor.age <= STALE_SECONDS
            values = ([number(sensor.normal), number(sensor.tangential), number(sensor.direction, 0),
                       number(sensor.proximity, 0), number(sensor.temperature, 1), number(sensor.age * 1000, 0),
                       "LIVE" if sensor.normal is not None else "INVALID"] if live else
                      ["—"] * 5 + [number(sensor.age * 1000, 0) if sensor else "—", "STALE" if sensor else "WAITING"])
            for col, value in enumerate(values, 1):
                self.sensors.setItem(i, col, QTableWidgetItem(value))
        self.hand_map.set_sample(sample)
        if plot:
            self.plot.add(sample)
        for row, (name, _, _) in enumerate(JOINTS):
            motor = sample.motors.get(name)
            live = motor is not None and motor.age <= STALE_SECONDS
            if live and motor.angle is not None and name not in self.synced:
                self.targets[name].setValue(motor.angle)
                self.synced.add(name)
            values = ([number(motor.angle, 1), number(motor.current, 0), number(motor.velocity, 1),
                       "unknown" if motor.error is None else str(motor.error), number(motor.age * 1000, 0),
                       "ERROR" if motor.error else "LIVE"] if live else ["—"] * 5 + ["STALE" if motor else "WAITING"])
            for col, value in enumerate(values, 1):
                self.motors.setItem(row, col, QTableWidgetItem(value))
        self.rate.setText(f"{sample.rx_hz:.0f} frames/s" + (" · simulated" if sample.demo else ""))
        self.frames_count.setText(f"{sample.frames:,} received")
        live_count = sum(sensor.age <= STALE_SECONDS and sensor.normal is not None for sensor in sample.sensors.values())
        self.footer.setText(f"{'DEMO · ' if sample.demo else ''}{sample.hand.upper()} hand · "
                            f"{live_count}/5 tactile sensors live · session {sample.elapsed:.1f}s · "
                            "normal/shear in N · missing samples shown as —")

    def on_frames(self, frames):
        lines = []
        for wall_time, direction, can_id, data in frames:
            stamp = dt.datetime.fromtimestamp(wall_time).strftime("%H:%M:%S.%f")[:-3]
            lines.append(f"{stamp}  {direction:9s}  {can_id:03X}  [{len(data):02d}]  {data.hex(' ').upper()}")
        self.traffic_log.appendPlainText("\n".join(lines))

    def _log(self, message):
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.session_log.appendPlainText(f"{stamp}  {message}")
        self.statusBar().showMessage(message, 10000)

    def _armed(self, enabled):
        self.send.setEnabled(enabled and self.connected)
        if self.worker:
            self.worker.submit("arm", enabled)

    def copy_measured(self):
        if not self.latest:
            return
        for name, motor in self.latest.motors.items():
            if motor.angle is not None and motor.age <= STALE_SECONDS:
                self.targets[name].setValue(motor.angle)

    def stage_preset(self, angle):
        for name, _, _ in JOINTS:
            self.targets[name].setValue(angle if name.endswith(("mcp", "dip")) else 0)
            self.selected[name].setChecked(True)
        self._log("Preset staged. Apply selected targets to send a command.")

    def apply_targets(self):
        if self.worker and self.connected and self.arm.isChecked():
            targets = {name: spin.value() for name, spin in self.targets.items()
                       if self.selected[name].isChecked()}
            self.worker.submit("move", (targets, self.control_mode.currentText(),
                                        self.current.value(), self.speed.value()))

    def toggle_recording(self):
        if not self.worker:
            return
        if self.record_path:
            self.worker.submit("record_stop")
            return
        default = "dexhand_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
        path, _ = QFileDialog.getSaveFileName(self, "Record sensor samples", default, "CSV (*.csv)")
        if path:
            self.worker.submit("record", path)

    def on_recording(self, path, rows):
        self.record_path = path
        self.record_button.setText("Stop recording" if path else "Record CSV…")
        self.record_status.setText(f"{rows:,} samples\n{Path(path).name}" if path else f"Not recording · {rows:,} samples")

    def about(self):
        QMessageBox.about(self, "About DexHand Monitor",
                         "Independent DexHand monitor · Apache-2.0\n\n"
                         "Uses the Apache-2.0 pyzlg_dexhand SDK.\n"
                         "Qt for Python / PySide6 Essentials is used under LGPLv3.\n"
                         "Qt license texts: https://doc.qt.io/qtforpython-6/licenses.html\n\n"
                         "Original vector graphics; no vendor executable, images or decompiled code are included.\n"
                         "This application is not an official DexRobot release.")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.closing = True
            self.arm.setChecked(False)
            self.worker.stop()
            event.ignore()
        else:
            event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description="DexHand tactile monitor; defaults to a hardware-free demo.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--demo", action="store_true", help="Auto-connect to simulated data (default)")
    source.add_argument("--hardware", action="store_true", help="Select hardware mode; press Connect in the window")
    parser.add_argument("--hand", choices=["left", "right"], default="right")
    parser.add_argument("--channel", type=int, choices=[0, 1], default=0)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--device-type", choices=["ZCAN_USBCANFD_100U", "ZCAN_USBCANFD_200U"], default="ZCAN_USBCANFD_100U")
    parser.add_argument("--no-termination", action="store_true")
    parser.add_argument("--screenshot", type=Path, help="Save a window screenshot after two seconds")
    parser.add_argument("--quit-after", type=float, help="Close the window after this many seconds")
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    config = Connection(not args.hardware, args.hand, args.channel, args.device_index,
                        args.device_type, termination=not args.no_termination)
    window = MonitorWindow(config)
    window.show()
    if not args.hardware:
        QTimer.singleShot(0, window.toggle_connection)
    if args.screenshot:
        def save_screenshot():
            if not window.grab().save(str(args.screenshot)):
                print(f"Could not save screenshot: {args.screenshot}", file=sys.stderr)
        QTimer.singleShot(2000, save_screenshot)
    if args.quit_after is not None:
        QTimer.singleShot(max(1, int(args.quit_after * 1000)), window.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""Original vector graphics and rolling plots; no vendor image assets.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from .core import COLORS, FINGERS, STALE_SECONDS, Sensor, Snapshot


def number(value, precision=2):
    return "—" if value is None or not math.isfinite(value) else f"{value:.{precision}f}"


class ForceCard(QFrame):
    def __init__(self, name: str, color: str):
        super().__init__()
        self.setObjectName("card")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        title = QLabel(name.upper())
        title.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 700;")
        box.addWidget(title)
        self.value = QLabel("— N")
        self.value.setStyleSheet("font-size: 27px; font-weight: 600;")
        box.addWidget(self.value)
        self.detail = QLabel("Waiting for sensor")
        self.detail.setObjectName("muted")
        box.addWidget(self.detail)

    def update_sensor(self, sensor: Sensor | None):
        live = sensor is not None and sensor.age <= STALE_SECONDS
        self.value.setText(f"{number(sensor.normal if live else None)} N")
        self.detail.setText(
            f"Shear {number(sensor.tangential)} N" if live else
            "STALE · no recent packet" if sensor else "Waiting for sensor"
        )


class HandMap(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(280, 290)
        self.snapshot = None
        self.scale = 5.0

    def set_sample(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.width() / 2 - 140, self.height() / 2 - 145)
        p.setPen(QColor("#8d9eb4"))
        p.setFont(QFont("Sans Serif", 9))
        p.drawText(QRectF(0, 0, 280, 24), Qt.AlignmentFlag.AlignCenter,
                   f"{self.snapshot.hand.upper() if self.snapshot else 'RIGHT'} HAND · FINGERTIP FORCE")
        p.translate(0, 24)
        # The schematic is intentionally not a geometric or pressure-array model.
        mirror = self.snapshot is not None and self.snapshot.hand == "left"
        if mirror:
            p.translate(280, 0)
            p.scale(-1, 1)
        p.setPen(QPen(QColor("#43536a"), 1.4))
        p.setBrush(QColor("#1d2a3d"))
        p.drawRoundedRect(QRectF(74, 105, 145, 127), 32, 32)
        p.drawRoundedRect(QRectF(102, 218, 91, 22), 8, 8)
        rects = [QRectF(24, 126, 39, 89), QRectF(78, 30, 31, 106),
                 QRectF(115, 12, 31, 120), QRectF(152, 26, 31, 110),
                 QRectF(189, 57, 29, 89)]
        for i, rect in enumerate(rects):
            p.setPen(QPen(QColor("#43536a"), 1.4))
            p.setBrush(QColor("#1d2a3d"))
            p.drawRoundedRect(rect, 14, 14)
            center = QPointF(rect.center().x(), rect.top() + 19)
            sensor = self.snapshot.sensors.get(FINGERS[i]) if self.snapshot else None
            live = sensor is not None and sensor.age <= STALE_SECONDS and sensor.normal is not None
            force = sensor.normal if live else 0
            color = QColor(COLORS[i]) if live else QColor("#627086")
            glow = QColor(color)
            glow.setAlpha(30 + int(110 * min(1, max(0, force / self.scale))))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(center, 19, 19)
            p.setBrush(color)
            p.drawEllipse(center, 5 + 6 * min(1, max(0, force / self.scale)),
                          5 + 6 * min(1, max(0, force / self.scale)))
            p.save()
            if mirror:
                p.translate(center.x() * 2, 0)
                p.scale(-1, 1)
            p.setPen(QColor("#e8edf5"))
            p.setFont(QFont("Sans Serif", 8))
            p.drawText(QRectF(center.x() - 24, center.y() + 23, 48, 20),
                       Qt.AlignmentFlag.AlignCenter, number(force if live else None))
            p.restore()
        p.end()


class HistoryPlot(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(430, 270)
        self.points = deque(maxlen=4000)
        self.window_seconds = 10
        self.metric_name = "normal"
        self.finger = None
        self.paused = False

    def add(self, snapshot: Snapshot):
        if self.paused:
            return
        normal, tangential = [], []
        for name in FINGERS:
            sensor = snapshot.sensors.get(name)
            usable = sensor is not None and sensor.age <= STALE_SECONDS
            # A missing packet creates a gap once stale, never an invented zero.
            normal.append(sensor.normal if usable else None)
            tangential.append(sensor.tangential if usable else None)
        self.points.append((snapshot.elapsed, normal, tangential))
        self.update()

    def clear(self):
        self.points.clear()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#121d2d"))
        area = QRectF(54, 24, max(10, self.width() - 72), max(10, self.height() - 58))
        end = self.points[-1][0] if self.points else self.window_seconds
        end = max(end, self.window_seconds)
        start = end - self.window_seconds
        series = 1 if self.metric_name == "normal" else 2
        visible = [point for point in self.points if point[0] >= start]
        indexes = list(range(5)) if self.finger is None else [self.finger]
        values = [point[series][i] for point in visible for i in indexes
                  if point[series][i] is not None]
        low = min(0.0, min(values, default=0))
        high = max(1.0, max(values, default=1) * 1.15)
        span = max(1e-9, high - low)
        p.setFont(QFont("Sans Serif", 8))
        for i in range(5):
            y = area.top() + area.height() * i / 4
            p.setPen(QColor("#263349"))
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            p.setPen(QColor("#8394ad"))
            p.drawText(QRectF(0, y - 9, 45, 18), Qt.AlignmentFlag.AlignRight,
                       f"{high - span * i / 4:.1f}")
        for i in range(6):
            x = area.left() + area.width() * i / 5
            p.setPen(QColor("#263349"))
            p.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            p.setPen(QColor("#8394ad"))
            p.drawText(QRectF(x - 25, area.bottom() + 8, 50, 20),
                       Qt.AlignmentFlag.AlignCenter, f"{start + self.window_seconds * i / 5:.0f}s")
        p.drawText(QPointF(10, 16), "N")
        p.save()
        p.setClipRect(area)
        for i in indexes:
            path = QPainterPath()
            continuing = False
            last_time = None
            for point in visible:
                if last_time is not None and point[0] - last_time > STALE_SECONDS:
                    continuing = False
                last_time = point[0]
                value = point[series][i]
                if value is None:
                    continuing = False
                    continue
                xy = QPointF(area.left() + (point[0] - start) / self.window_seconds * area.width(),
                             area.bottom() - (value - low) / span * area.height())
                if continuing:
                    path.lineTo(xy)
                else:
                    path.moveTo(xy)
                continuing = True
            p.setPen(QPen(QColor(COLORS[i]), 2))
            p.drawPath(path)
        p.restore()
        if not visible:
            p.setPen(QColor("#8394ad"))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Connect to start streaming")
        p.end()

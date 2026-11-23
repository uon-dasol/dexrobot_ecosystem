"""Transport independent telemetry models and recording.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

FINGERS = ("th", "ff", "mf", "rf", "lf")
FINGER_NAMES = ("Thumb", "Index", "Middle", "Ring", "Little")
COLORS = ("#68e0b3", "#72b7ff", "#c5a0ff", "#ffca7b", "#ff8fa8")
JOINTS = (
    ("th_rot", "Thumb rotation", 150.0),
    ("th_mcp", "Thumb MCP", 90.0),
    ("th_dip", "Thumb DIP", 90.0),
    ("ff_spr", "Finger spread", 30.0),
    ("ff_mcp", "Index MCP", 90.0),
    ("ff_dip", "Index DIP", 90.0),
    ("mf_mcp", "Middle MCP", 90.0),
    ("mf_dip", "Middle DIP", 90.0),
    ("rf_mcp", "Ring MCP", 90.0),
    ("rf_dip", "Ring DIP", 90.0),
    ("lf_mcp", "Little MCP", 90.0),
    ("lf_dip", "Little DIP", 90.0),
)
JOINT_BOARD = {
    "th_dip": 0, "th_mcp": 0, "th_rot": 1, "ff_spr": 1,
    "ff_dip": 2, "ff_mcp": 2, "mf_dip": 3, "mf_mcp": 3,
    "rf_dip": 4, "rf_mcp": 4, "lf_dip": 5, "lf_mcp": 5,
}
FINGER_BOARD = dict(zip(FINGERS, (0, 2, 3, 4, 5)))
STALE_SECONDS = 0.5


def finite(value: object) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class Sensor:
    normal: float | None
    tangential: float | None
    direction: float | None = None
    proximity: float | None = None
    temperature: float | None = None
    age: float = 0.0
    fresh: bool = True


@dataclass(frozen=True)
class Motor:
    angle: float | None
    current: float | None = None
    velocity: float | None = None
    error: int | None = None
    age: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    elapsed: float
    wall_time: float
    hand: str
    sensors: dict[str, Sensor] = field(default_factory=dict)
    motors: dict[str, Motor] = field(default_factory=dict)
    frames: int = 0
    rx_hz: float = 0.0
    demo: bool = False


def validate_targets(targets: dict[str, float], motors: dict[str, Motor]) -> None:
    """Validate the entire request before any motor command is transmitted."""
    limits = {name: limit for name, _, limit in JOINTS}
    if not targets:
        raise ValueError("Select at least one joint.")
    for name, target in targets.items():
        if name not in limits or finite(target) is None or not 0 <= target <= limits[name]:
            raise ValueError(f"Invalid target for {name}: {target}")
        motor = motors.get(name)
        if motor is None or motor.angle is None or motor.age > STALE_SECONDS:
            raise ValueError(f"{name}: waiting for fresh motor feedback.")
        if motor.error not in (None, 0):
            raise ValueError(f"{name}: motor error {motor.error}; inspect and clear errors first.")


class CsvRecorder:
    """Write new sensor samples only; cached values must not become new samples."""

    HEADER = ("unix_time_s", "session_s", "hand", "finger", "source",
              "normal_force_N", "tangential_force_N", "direction_deg",
              "proximity_raw", "temperature_C", "age_s")

    def __init__(self) -> None:
        self.file: TextIO | None = None
        self.writer = None
        self.rows = 0

    def start(self, path: str | Path) -> None:
        self.close()
        self.file = Path(path).open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.writer.writerow(self.HEADER)
        self.rows = 0

    def write(self, snapshot: Snapshot) -> None:
        if self.writer is None:
            return
        for finger, sensor in snapshot.sensors.items():
            if not sensor.fresh or sensor.age > STALE_SECONDS:
                continue
            self.writer.writerow((snapshot.wall_time, snapshot.elapsed,
                                  snapshot.hand, finger, "demo" if snapshot.demo else "hardware",
                                  sensor.normal, sensor.tangential, sensor.direction,
                                  sensor.proximity, sensor.temperature, sensor.age))
            self.rows += 1
        if self.file:
            self.file.flush()

    def close(self) -> None:
        if self.file:
            self.file.close()
        self.file = None
        self.writer = None

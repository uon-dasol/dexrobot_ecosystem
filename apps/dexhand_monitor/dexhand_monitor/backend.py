"""One worker owns CAN I/O, recording, and all hardware commands.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from .core import (FINGERS, FINGER_BOARD, JOINTS, JOINT_BOARD, CsvRecorder,
                   Motor, Sensor, Snapshot, finite, validate_targets)


@dataclass(frozen=True)
class Connection:
    demo: bool = True
    hand: str = "right"
    channel: int = 0
    device_index: int = 0
    device_type: str = "ZCAN_USBCANFD_100U"
    period_ms: int = 20
    termination: bool = True


class DemoSource:
    """Deterministic demo data, independent of USB libraries and hardware."""

    def __init__(self, config: Connection):
        self.config = config
        self.started = time.monotonic()
        self.targets = {name: 0.0 for name, _, _ in JOINTS}
        self.angles = dict(self.targets)
        self.count = 0
        self.last = self.started

    def open(self) -> None:
        pass

    def poll(self) -> Snapshot:
        now = time.monotonic()
        t = now - self.started
        dt = now - self.last
        self.last = now
        self.count += 6
        sensors = {}
        for i, name in enumerate(FINGERS):
            force = max(0.0, (0.9 + 0.4 * math.sin(t * 0.9 + i)) * (1 + i * 0.28))
            sensors[name] = Sensor(force, force * (0.16 + 0.05 * math.sin(t + i)),
                                   (t * 25 + i * 35) % 360, 140 + i * 11,
                                   26 + 0.5 * math.sin(t * 0.2 + i))
        motors = {}
        for name, _, _ in JOINTS:
            delta = self.targets[name] - self.angles[name]
            step = max(-45 * dt, min(45 * dt, delta))
            self.angles[name] += step
            motors[name] = Motor(self.angles[name], 18 + abs(step) * 2,
                                 step / max(dt, 0.001) / 6, 0)
        return Snapshot(t, time.time(), self.config.hand, sensors, motors,
                        self.count, 6000 / self.config.period_ms, True)

    def move(self, targets, mode, current, speed) -> None:
        self.targets.update(targets)

    def clear_errors(self) -> None:
        pass

    def traffic(self) -> list:
        return []

    def close(self) -> None:
        pass


class HardwareSource:
    def __init__(self, config: Connection):
        self.config = config
        self.started = time.monotonic()
        self.hand = None
        self.transport = None
        self.enabled_feedback = False
        self.seen: dict[int, int] = {}
        self.motor_sample: dict[str, Motor] = {}

    def open(self) -> None:
        # Import lazily: demo mode never loads the vendor driver.
        from pyzlg_dexhand import LeftDexHand, RightDexHand
        from pyzlg_dexhand.zcan import ZCANDeviceType
        from pyzlg_dexhand.zcan_wrapper import ZCANWrapper
        from pyzlg_dexhand.dexhand_protocol import MessageType, get_message_type
        from pyzlg_dexhand.dexhand_protocol.commands import FeedbackMode
        from pyzlg_dexhand.dexhand_protocol.messages import process_message

        base = 1 if self.config.hand == "left" else 7
        termination = self.config.termination

        class TrackingTransport(ZCANWrapper):
            def __init__(self):
                super().__init__()
                self.received = deque(maxlen=2000)
                self.rx_times = deque(maxlen=5000)
                self.last_rx = {}
                self.sequences = {}
                self.count = 0

            def configure_channel(self, channel, **kwargs):
                kwargs["enable_resistance"] = termination
                return super().configure_channel(channel, **kwargs)

            def send_fd_message(self, channel, id, data, flags=None):
                ok = super().send_fd_message(channel, id, data, flags)
                self.received.append((time.time(), "TX" if ok else "TX FAILED", id, bytes(data)))
                return ok

            def receive_fd_messages(self, channel, max_messages=1000, timeout_ms=0):
                frames = super().receive_fd_messages(channel, max_messages, timeout_ms)
                selected = []
                for can_id, data, stamp in frames:
                    now = time.monotonic()
                    self.count += 1
                    self.rx_times.append(now)
                    self.received.append((time.time(), "RX", can_id, bytes(data)))
                    kind = get_message_type(can_id)
                    if kind not in (MessageType.MOTION_FEEDBACK, MessageType.ERROR_MESSAGE,
                                    MessageType.CONFIG_RESPONSE):
                        continue
                    board = can_id - int(kind) - base
                    # Another hand may share this physical channel. Never give its
                    # board IDs to the SDK instance for the selected hand.
                    if not 0 <= board < 6:
                        continue
                    if kind == MessageType.MOTION_FEEDBACK:
                        try:
                            decoded = process_message(can_id, data)
                            if decoded.feedback is None:
                                continue
                        except (ValueError, IndexError):
                            continue
                        self.last_rx[board] = now
                        self.sequences[board] = self.sequences.get(board, 0) + 1
                    selected.append((can_id, data, stamp))
                return selected

        self.transport = TrackingTransport()
        if not self.transport.open(ZCANDeviceType[self.config.device_type], self.config.device_index):
            raise RuntimeError("Cannot open the USB-CAN adapter. Check device type, index and USB permissions.")
        cls = LeftDexHand if self.config.hand == "left" else RightDexHand
        self.hand = cls(zcan=self.transport, auto_init=False)
        self.hand.config.channel = self.config.channel
        if not self.hand.init():
            raise RuntimeError(f"Cannot configure CAN channel {self.config.channel}.")
        if not self.hand.set_feedback_mode(FeedbackMode.PERIODIC, self.config.period_ms, True):
            raise RuntimeError("Could not request periodic feedback.")
        self.enabled_feedback = True

    def poll(self) -> Snapshot:
        feedback = self.hand.get_feedback()
        now = time.monotonic()
        sensors = {}
        for name, value in feedback.touch.items():
            board = FINGER_BOARD[name]
            age = max(0.0, now - self.transport.last_rx.get(board, -math.inf))
            sequence = self.transport.sequences.get(board, 0)
            fresh = sequence != self.seen.get(board, 0)
            direction = finite(value.direction)
            sensors[name] = Sensor(finite(value.normal_force), finite(value.tangential_force),
                                   direction if direction is not None and 0 <= direction < 360 else None,
                                   finite(value.proximity), finite(value.temperature), age, fresh)
            self.seen[board] = sequence
        motors = {}
        for name, value in feedback.joints.items():
            board = JOINT_BOARD[name]
            age = max(0.0, now - self.transport.last_rx.get(board, -math.inf))
            error = value.error_code
            state = self.hand.board_states[board]
            if state.error_info is not None:
                error = int(state.error_info.error_type) or 1
            motors[name] = Motor(finite(value.angle), finite(value.current),
                                  finite(value.velocity), error, age)
        self.motor_sample = motors
        while self.transport.rx_times and now - self.transport.rx_times[0] > 1:
            self.transport.rx_times.popleft()
        return Snapshot(now - self.started, time.time(), self.config.hand, sensors,
                        motors, self.transport.count, float(len(self.transport.rx_times)))

    def move(self, targets, mode, current, speed) -> None:
        from pyzlg_dexhand import JointCommand, ControlMode
        validate_targets(targets, self.motor_sample)
        success = self.hand.move_joints(
            **{name: JointCommand(value, current=current, velocity=speed)
               for name, value in targets.items()},
            control_mode=ControlMode[mode], use_broadcast=False,
        )
        if not success:
            raise RuntimeError("One or more joint command transmissions failed; inspect CAN traffic and motor feedback.")

    def clear_errors(self) -> None:
        if not self.hand.clear_errors(use_broadcast=False):
            raise RuntimeError("Error-clear command transmission failed.")

    def traffic(self) -> list:
        result = list(self.transport.received)
        self.transport.received.clear()
        return result

    def close(self) -> None:
        try:
            if self.hand and self.enabled_feedback:
                from pyzlg_dexhand.dexhand_protocol.commands import FeedbackMode
                self.hand.set_feedback_mode(FeedbackMode.PERIODIC, self.config.period_ms, False)
        finally:
            if self.transport:
                # The hand borrows this transport; the owner closes it exactly once.
                self.transport.close()


class Worker(QThread):
    sample = Signal(object)
    state = Signal(str, bool)
    message = Signal(str)
    frames = Signal(object)
    recording = Signal(str, int)

    def __init__(self, config: Connection, parent=None, source_factory=None):
        super().__init__(parent)
        self.config = config
        self.source_factory = source_factory
        self.commands = queue.Queue()
        self.stopping = threading.Event()

    def submit(self, action: str, payload=None) -> None:
        self.commands.put((action, payload))

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> None:
        source = None
        recorder = CsvRecorder()
        armed = False
        last_snapshot = None
        handler = _LogForwarder(self.message.emit)
        sdk_logger = logging.getLogger("pyzlg_dexhand")
        sdk_logger.addHandler(handler)
        try:
            factory = self.source_factory or (DemoSource if self.config.demo else HardwareSource)
            source = factory(self.config)
            self.state.emit("Connecting…", False)
            source.open()
            self.message.emit("Demo session opened." if self.config.demo else
                              f"CAN open: {self.config.hand} hand, channel {self.config.channel}. Waiting for feedback.")
            self.state.emit("Demo connected" if self.config.demo else "CAN connected", True)
            while not self.stopping.is_set():
                tick = time.monotonic()
                # Poll before commands so validation uses the latest received state.
                last_snapshot = source.poll()
                recorder.write(last_snapshot)
                self.sample.emit(last_snapshot)
                frames = source.traffic()
                if frames:
                    self.frames.emit(frames)
                for _ in range(20):
                    if self.stopping.is_set():
                        break
                    try:
                        action, payload = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if action == "arm":
                            armed = bool(payload)
                            self.message.emit("Joint commands enabled." if armed else "Joint commands disabled.")
                        elif action == "move":
                            if not armed:
                                raise ValueError("Enable joint commands before applying targets.")
                            targets, mode, current, speed = payload
                            validate_targets(targets, last_snapshot.motors)
                            source.move(targets, mode, current, speed)
                            self.message.emit(f"Requested {mode} targets: {targets}")
                        elif action == "clear":
                            source.clear_errors()
                            self.message.emit("Requested error clear (per-board commands).")
                        elif action == "record":
                            recorder.start(payload)
                            self.recording.emit(str(payload), 0)
                            self.message.emit(f"Recording: {payload}")
                        elif action == "record_stop":
                            recorder.close()
                            self.recording.emit("", recorder.rows)
                            self.message.emit(f"Recording stopped: {recorder.rows} samples.")
                    except Exception as exc:
                        self.message.emit(f"Request rejected: {exc}")
                if recorder.file:
                    self.recording.emit(str(recorder.file.name), recorder.rows)
                self.stopping.wait(max(0, self.config.period_ms / 1000 - (time.monotonic() - tick)))
        except Exception as exc:
            self.message.emit(f"Connection error: {exc}")
            self.state.emit(str(exc), False)
        finally:
            recorder.close()
            if source:
                try:
                    source.close()
                except Exception as exc:
                    self.message.emit(f"Disconnect error: {exc}")
            sdk_logger.removeHandler(handler)
            self.recording.emit("", recorder.rows)
            self.state.emit("Disconnected", False)


class _LogForwarder(logging.Handler):
    def __init__(self, callback):
        super().__init__(logging.WARNING)
        self.callback = callback

    def emit(self, record):
        self.callback(f"SDK {record.levelname}: {record.getMessage()}")

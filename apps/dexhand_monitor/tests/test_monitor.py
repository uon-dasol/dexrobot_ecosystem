"""Hardware-free regression tests for telemetry and motor command boundaries."""

import csv
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pyzlg_dexhand"))
sys.path.insert(0, str(ROOT / "apps" / "dexhand_monitor"))

from PySide6.QtWidgets import QApplication

from dexhand_monitor.backend import Connection, DemoSource, HardwareSource, Worker
from dexhand_monitor.core import (FINGERS, JOINTS, CsvRecorder, Motor, Sensor,
                                  Snapshot, finite, validate_targets)


class FakeTransport:
    """No native driver is constructed: patched in before HardwareSource.open."""
    def __init__(self):
        self.pending = []
        self.sent = []
        self.closed = 0
        self.configured = []

    def open(self, *args):
        return True

    def configure_channel(self, channel, **kwargs):
        self.configured.append((channel, kwargs))
        return True

    def send_fd_message(self, channel, id, data, flags=None):
        self.sent.append((channel, id, data))
        return True

    def receive_fd_messages(self, channel, max_messages=1000, timeout_ms=0):
        self.assert_nonblocking = timeout_ms == 0
        pending, self.pending = self.pending, []
        return pending

    def close(self):
        self.closed += 1
        return True


def feedback(normal=1.25):
    return (struct.pack("<hhhhhhhh", 20, 10, 100, 25, 12, 200, 1200, 2300)
            + struct.pack("<fIfIHII", normal, 0, 0.25, 0, 90, 123, 27))


class CoreTests(unittest.TestCase):
    def test_nonfinite_values_are_missing(self):
        for value in (None, math.nan, math.inf, "bad"):
            self.assertIsNone(finite(value))
        self.assertEqual(finite(1.2), 1.2)

    def test_entire_command_is_validated(self):
        motors = {"th_mcp": Motor(10), "ff_mcp": Motor(20, age=0.7)}
        validate_targets({"th_mcp": 30}, motors)
        for targets in ({}, {"bogus": 1}, {"th_mcp": 91}, {"th_mcp": math.nan},
                        {"th_mcp": 30, "ff_mcp": 30}):
            with self.assertRaises(ValueError):
                validate_targets(targets, motors)
        with self.assertRaises(ValueError):
            validate_targets({"th_mcp": 20}, {"th_mcp": Motor(10, error=3)})

    def test_csv_never_relabels_cached_or_stale_values_as_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "samples.csv"
            recorder = CsvRecorder()
            recorder.start(path)
            sensors = {"th": Sensor(1.2, 0.1), "ff": Sensor(2, 0, fresh=False),
                       "mf": Sensor(3, 0, age=0.8)}
            recorder.write(Snapshot(1, 100, "right", sensors, demo=True))
            recorder.close()
            with path.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["finger"], "th")
            self.assertEqual(rows[0]["source"], "demo")
            self.assertEqual(rows[0]["normal_force_N"], "1.2")

    def test_demo_has_all_sensors_and_joints(self):
        source = DemoSource(Connection())
        source.open()
        sample = source.poll()
        self.assertEqual(set(sample.sensors), set(FINGERS))
        self.assertEqual(set(sample.motors), {j[0] for j in JOINTS})
        self.assertTrue(sample.demo)
        source.close()


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("pyzlg_dexhand.zcan_wrapper.ZCANWrapper", FakeTransport)
        self.patcher.start()
        self.source = HardwareSource(Connection(demo=False, hand="right", channel=0, termination=False))
        self.source.open()

    def tearDown(self):
        self.source.close()
        self.patcher.stop()

    def test_open_only_requests_feedback_and_honors_channel(self):
        tx = self.source.transport.sent
        self.assertEqual(len(tx), 6)
        self.assertEqual({item[0] for item in tx}, {0})
        self.assertEqual({item[1] for item in tx}, set(range(7, 13)))
        self.assertFalse(self.source.transport.configured[0][1]["enable_resistance"])

    def test_filters_other_hand_and_preserves_real_freshness(self):
        transport = self.source.transport
        transport.pending = [(0x181, feedback(9), 1), (0x187, feedback(1.25), 2)]
        sample = self.source.poll()
        self.assertEqual(sample.sensors["th"].normal, 1.25)
        self.assertTrue(sample.sensors["th"].fresh)
        self.assertTrue(transport.assert_nonblocking)
        second = self.source.poll()
        self.assertFalse(second.sensors["th"].fresh)
        with patch("dexhand_monitor.backend.time.monotonic", return_value=time.monotonic() + 2):
            stale = self.source.poll()
        self.assertGreater(stale.sensors["th"].age, 0.5)
        self.assertFalse(stale.sensors["th"].fresh)

    def test_invalid_feedback_does_not_refresh_sensor_clock(self):
        self.source.transport.pending = [(0x187, b"short", 1)]
        sample = self.source.poll()
        self.assertFalse(sample.sensors)
        self.assertFalse(self.source.transport.last_rx)

    def test_missing_feedback_blocks_motion(self):
        before = len(self.source.transport.sent)
        with self.assertRaises(ValueError):
            self.source.move({"th_mcp": 20}, "CASCADED_PID", 20, 5000)
        self.assertEqual(len(self.source.transport.sent), before)

    def test_selected_joint_uses_only_its_board_and_propagates_send_failure(self):
        transport = self.source.transport
        transport.pending = [(0x187, feedback(), 1)]
        self.source.poll()
        before = len(transport.sent)
        self.source.move({"th_mcp": 20}, "CASCADED_PID", 20, 5000)
        commands = transport.sent[before:]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0:2], (0, 0x107))
        self.assertEqual(commands[0][2][0:2], bytes([0x44, 0x02]))
        with patch.object(transport, "send_fd_message", return_value=False):
            with self.assertRaises(RuntimeError):
                self.source.move({"th_mcp": 20}, "CASCADED_PID", 20, 5000)


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for desktop event")

    def test_demo_window_renders_disconnects_and_reconnects(self):
        from dexhand_monitor.app import MonitorWindow
        window = MonitorWindow(Connection())
        window.show()
        window.toggle_connection()
        try:
            self.wait_until(lambda: window.latest is not None)
            self.assertIn("N", window.cards[0].value.text())
            window.metric_combo.setCurrentIndex(1)
            window.plot_finger.setCurrentIndex(2)
            window.stage_preset(30)
            self.assertFalse(window.arm.isChecked())
            self.assertFalse(window.send.isEnabled())
            self.assertFalse(window.grab().isNull())
            window.toggle_connection()
            self.wait_until(lambda: window.worker is None)
            self.assertEqual(window.sensors.item(0, 7).text(), "STALE")
            window.toggle_connection()
            self.wait_until(lambda: window.latest is not None and window.connected)
        finally:
            if window.worker:
                window.worker.stop()
                self.wait_until(lambda: window.worker is None)
            window.close()

    def test_worker_rejects_motion_until_armed(self):
        class TracedDemo(DemoSource):
            moves = []

            def move(self, *args):
                self.moves.append(args)
                super().move(*args)

        worker = Worker(Connection(), source_factory=TracedDemo)
        messages = []
        samples = []
        worker.message.connect(messages.append)
        worker.sample.connect(samples.append)
        worker.start()
        try:
            self.wait_until(lambda: bool(samples))
            request = ({"th_mcp": 20}, "CASCADED_PID", 20, 5000)
            worker.submit("move", request)
            self.wait_until(lambda: any("rejected" in msg for msg in messages))
            self.assertFalse(TracedDemo.moves)
            worker.submit("arm", True)
            worker.submit("move", request)
            self.wait_until(lambda: len(TracedDemo.moves) == 1)
            worker.submit("arm", False)
            worker.submit("move", request)
            self.wait_until(lambda: sum("rejected" in msg for msg in messages) == 2)
            self.assertEqual(len(TracedDemo.moves), 1)
        finally:
            worker.stop()
            worker.wait(3000)


if __name__ == "__main__":
    unittest.main()

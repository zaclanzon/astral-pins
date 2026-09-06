"""GTK integration checks; run with a display, e.g. GDK_BACKEND=broadway."""
import unittest
from unittest.mock import patch
import cairo
import astral_pins as monitor


@unittest.skipUnless(monitor.Gdk.Display.get_default(), "GTK display required")
class MonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = monitor.App(None, None, demo=True)
        cls.app.register(None)
        cls.app.activate()
        for timer in cls.app.timers:
            monitor.GLib.source_remove(timer)
        cls.app.timers.clear()

    @classmethod
    def tearDownClass(cls):
        cls.app.get_active_window().destroy()
        cls.app.quit()

    def setUp(self):
        self.app.graph_pointer = None
        self.app.over_since = [None] * 6
        self.app.last_sample = None
        for samples in self.app.history:
            samples.clear()
        self.pins = [(12.0, a) for a in (5.1, 5.2, 5.0, 5.3, 5.15, 5.25)]

    def test_normal_totals_and_pin_precision(self):
        self.app.update_pins(self.pins, 0)
        self.assertEqual(self.app.power.get_label(), "372.0 W")
        self.assertEqual(self.app.current.get_label(), "31.00 A")
        self.assertEqual(self.app.rows[0].value.get_label(), "5.100 A")
        self.assertIn("Within thresholds", self.app.status.get_label())
        self.assertFalse(self.app.banner.get_visible())

    def test_warning_and_exact_alarm_hold_boundary(self):
        self.pins[2] = (12.0, 8.0)
        self.app.update_pins(self.pins, 0)
        self.assertIn("Elevated", self.app.status.get_label())
        self.pins[2] = (12.0, 9.2)
        for timestamp in range(1, 6):
            self.app.update_pins(self.pins, timestamp)
            self.assertIsNone(self.app.alert_msg)
            self.assertIn("Confirming", self.app.status.get_label())
        self.app.update_pins(self.pins, 6)
        self.assertIn("PIN 2", self.app.alert_msg)
        self.assertIn("Overcurrent", self.app.rows[2].status.get_label())
        self.pins[2] = (12.0, 5.0)
        self.app.update_pins(self.pins, 7)
        self.assertIsNone(self.app.alert_msg)
        self.assertIsNone(self.app.over_since[2])

    def test_dropout_requires_both_strict_boundaries(self):
        self.pins[4] = (12.0, 0.2)
        self.app.update_pins(self.pins, 0)
        self.assertIsNone(self.app.alert_msg)
        self.pins[4] = (12.0, 0.08)
        self.app.update_pins(self.pins, 1)
        self.assertIn("Possible dropout", self.app.alert_msg)
        self.assertIn("Possible dropout", self.app.rows[4].status.get_label())
        low_load = [(12.0, 2.0)] * 5 + [(12.0, 0.0)]
        self.app.update_pins(low_load, 2)
        self.assertIsNone(self.app.alert_msg)

    def test_read_failure_clears_live_values_and_resets_hold(self):
        self.pins[2] = (12.0, 9.6)
        for timestamp in range(5):
            self.app.update_pins(self.pins, timestamp)
        self.app.demo = False
        try:
            with patch.object(monitor, "read_raw", side_effect=OSError("disconnected")), patch.object(monitor.time, "monotonic", return_value=5):
                self.app.poll_pins()
        finally:
            self.app.demo = True
        self.assertEqual(self.app.power.get_label(), "— W")
        self.assertTrue(self.app.stale)
        self.assertIn("Historical", self.app.chart_note.get_label())
        self.assertTrue(all(row.value.get_label() == "— A" for row in self.app.rows))
        self.app.update_pins(self.pins, 6)
        self.assertIsNone(self.app.alert_msg)
        self.assertFalse(self.app.stale)
        self.assertEqual(self.app.over_since[2], 6)

    def test_long_sampling_gap_does_not_confirm_alarm(self):
        self.pins[0] = (12.0, 9.6)
        self.app.update_pins(self.pins, 0)
        self.app.update_pins(self.pins, 20)
        self.assertIsNone(self.app.alert_msg)
        self.assertEqual(self.app.over_since[0], 20)

    def test_history_expires_and_chart_handles_overrange(self):
        self.pins[0] = (12.0, 12.5)
        self.app.update_pins(self.pins, 0)
        self.app.update_pins(self.pins, 121)
        self.assertEqual(len(self.app.history[0]), 1)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 230)
        self.app.draw_graph(None, cairo.Context(surface), 800, 230)
        self.app.show_stale(242)
        self.assertFalse(self.app.history[0])
        self.app.draw_graph(None, cairo.Context(surface), 800, 230)

    def test_missing_gpu_does_not_hide_coolant(self):
        self.app.demo = False
        try:
            self.app.telemetry = (None, 34.5)
            self.app.refresh_smi_label()
            self.assertEqual(self.app.metrics[0].value.get_label(), "—")
            self.assertEqual(self.app.metrics[4].value.get_label(), "34.5 °C")
            self.app.telemetry = (None, None)
            self.app.refresh_smi_label()
            self.assertEqual(self.app.metrics[4].value.get_label(), "Not detected")
        finally:
            self.app.demo = True

    def test_hover_level_and_visible_peaks(self):
        self.app.history[0].extend([(-21, 30), (-20, 6), (90, 8), (101, 20)])
        self.app.history[1].append((95, 5))
        self.app.graph_pointer = (200, 116)
        level, peaks, y_max = self.app.graph_hover_data(800, 230, 100)
        self.assertEqual(y_max, 10)
        self.assertEqual(level, 5)
        self.assertEqual(peaks[:3], [(90, 8), (95, 5), None])
        self.app.graph_pointer = (200, 30)
        self.assertEqual(self.app.graph_hover_data(800, 230, 100)[0], 10)
        self.app.graph_pointer = (200, 202)
        self.assertEqual(self.app.graph_hover_data(800, 230, 100)[0], 0)
        self.app.graph_pointer = (20, 116)
        self.assertIsNone(self.app.graph_hover_data(800, 230, 100)[0])
        self.assertIsNone(self.app.graph_hover_data(30, 20, 100)[0])

    def test_hover_summary_and_leave(self):
        self.app.history[0].append((100, 8))
        self.app.history[1].append((100, 5))
        self.app.history[2].append((100, 4))
        with patch.object(self.app.graph, "get_width", return_value=800), patch.object(self.app.graph, "get_height", return_value=230), patch.object(monitor.time, "monotonic", return_value=100):
            self.app.on_graph_motion(None, 200, 116)
            self.assertEqual(self.app.peak_labels[0].get_label(), "PIN 0: 8.000 A")
            self.assertEqual(self.app.peak_labels[1].get_label(), "PIN 1: 5.000 A")
            self.assertEqual(self.app.peak_labels[2].get_label(), "PIN 2: Below line")
            self.assertEqual(self.app.peak_labels[3].get_label(), "PIN 3: No data")
            self.app.stale = True
            self.app.refresh_graph_hover()
            self.assertIn("Historical", self.app.hover_note.get_label())
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 230)
            self.app.draw_graph(None, cairo.Context(surface), 800, 230)
            self.app.on_graph_leave(None)
            self.assertIsNone(self.app.graph_pointer)
            self.assertEqual(self.app.peak_labels[0].get_label(), "PIN 0: —")

    def test_demo_does_not_access_hardware(self):
        with patch.object(monitor, "read_raw", side_effect=AssertionError("hardware read")), patch.object(monitor.subprocess, "run", side_effect=AssertionError("subprocess")), patch.object(monitor, "coolant_temp", side_effect=AssertionError("hwmon read")):
            self.app.poll_pins()
            self.app.refresh_smi_label()


if __name__ == "__main__":
    unittest.main()

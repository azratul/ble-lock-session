import os
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import ble_lock_session as bls


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class BluetoothctlTest(unittest.TestCase):
    def test_strips_ansi_and_readline_codes(self):
        raw = "\x01\x1b[0;94m\x02[bluetooth]\x01\x1b[0m\x02 Device AA:BB:CC:DD:EE:FF Watch\n"
        with mock.patch.object(bls.subprocess, "run", return_value=completed(stdout=raw)):
            output = bls.bluetoothctl(["devices"], 5)
        self.assertEqual(output, "[bluetooth] Device AA:BB:CC:DD:EE:FF Watch\n")

    def test_missing_binary_raises(self):
        with mock.patch.object(bls.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(bls.BluetoothUnavailableError):
                bls.bluetoothctl(["devices"], 5)

    def test_no_adapter_raises(self):
        with mock.patch.object(bls.subprocess, "run", return_value=completed(stderr="No default controller available")):
            with self.assertRaises(bls.BluetoothUnavailableError):
                bls.bluetoothctl(["devices"], 5)


class DefaultCommandsTest(unittest.TestCase):
    def test_sway(self):
        self.assertEqual(bls.get_default_lock_command("SWAY"), "swaylock")
        self.assertEqual(bls.get_default_unlock_command("SWAY"), "pkill -USR1 swaylock")

    def test_gnome_uses_loginctl(self):
        # gnome-screensaver-command no longer exists on modern GNOME
        self.assertEqual(bls.get_default_lock_command("GNOME"), "loginctl lock-session")
        self.assertEqual(bls.get_default_unlock_command("GNOME"), "loginctl unlock-session")

    def test_fallback(self):
        self.assertEqual(bls.get_default_lock_command("KDE"), "loginctl lock-session")
        self.assertEqual(bls.get_default_unlock_command(""), "loginctl unlock-session")


class PositiveIntTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(bls.get_positive_int({"sleep_time": "3"}, "sleep_time"), 3)

    def test_zero_exits(self):
        with self.assertRaises(SystemExit):
            bls.get_positive_int({"sleep_time": "0"}, "sleep_time")

    def test_garbage_exits(self):
        with self.assertRaises(SystemExit):
            bls.get_positive_int({"sleep_time": "fast"}, "sleep_time")


class LoadConfigTest(unittest.TestCase):
    def run_load_config(self, initial=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ble-lock-session", "config.ini")
            if initial is not None:
                os.makedirs(os.path.dirname(path))
                with open(path, "w") as f:
                    f.write(initial)
            with mock.patch.object(bls, "CONFIG_FILE", path):
                config = bls.load_config()
                with open(path) as f:
                    persisted = f.read()
            return config, persisted

    def test_creates_file_with_defaults(self):
        config, persisted = self.run_load_config()
        self.assertIn("SETTINGS", config)
        self.assertEqual(config["SETTINGS"]["target_address"], "")
        self.assertIn("fail_checks", persisted)

    def test_merges_missing_keys_and_keeps_existing(self):
        config, persisted = self.run_load_config("[SETTINGS]\nsleep_time = 9\n")
        self.assertEqual(config["SETTINGS"]["sleep_time"], "9")
        self.assertEqual(config["SETTINGS"]["fail_checks"], "3")
        self.assertIn("discover_time", persisted)

    def test_percent_in_commands_survives(self):
        config, _ = self.run_load_config("[SETTINGS]\nlock_cmd = date +%s\n")
        self.assertEqual(config["SETTINGS"]["lock_cmd"], "date +%s")


class ScanDeviceTest(unittest.TestCase):
    def test_finds_device_with_spaces_in_name(self):
        def fake(args, timeout):
            return "Device AA:BB:CC:DD:EE:FF Mi Smart Band 5\n" if args[0] == "devices" else ""
        with mock.patch.object(bls, "bluetoothctl", side_effect=fake):
            self.assertEqual(bls.scan_device("Mi Smart Band 5", 10), "AA:BB:CC:DD:EE:FF")

    def test_returns_none_after_deadline(self):
        with mock.patch.object(bls, "bluetoothctl", return_value=""):
            self.assertIsNone(bls.scan_device("Nope", 0))

    def test_exact_name_match_only(self):
        def fake(args, timeout):
            return "Device AA:BB:CC:DD:EE:FF Mi Smart Band 5\n" if args[0] == "devices" else ""
        with mock.patch.object(bls, "bluetoothctl", side_effect=fake):
            self.assertIsNone(bls.scan_device("Mi Smart Band", 0))


class ClassicPresenceMonitorTest(unittest.TestCase):
    MAC = "AA:BB:CC:DD:EE:FF"

    def bluetooth_socket(self, fake_sock):
        # setup-python builds may omit Linux Bluetooth socket constants.
        # Provide the complete socket API here so these tests exercise the
        # connection logic independently of how the interpreter was built.
        return mock.patch.multiple(
            bls.socket,
            AF_BLUETOOTH=mock.sentinel.af_bluetooth,
            SOCK_SEQPACKET=mock.sentinel.sock_seqpacket,
            BTPROTO_L2CAP=mock.sentinel.btproto_l2cap,
            socket=mock.Mock(return_value=fake_sock),
            create=True,
        )

    def connect(self, connect_effect):
        monitor = bls.ClassicPresenceMonitor()
        fake_sock = mock.MagicMock()
        fake_sock.connect.side_effect = connect_effect
        with self.bluetooth_socket(fake_sock):
            result = monitor.connect(self.MAC, 5)
        return monitor, fake_sock, result

    def test_accepted_connection_is_present_and_held(self):
        monitor, fake_sock, result = self.connect(None)
        self.assertTrue(result)
        self.assertIs(monitor.sock, fake_sock)
        fake_sock.setblocking.assert_called_once_with(False)

    def test_rejected_connection_proves_presence_but_is_not_held(self):
        monitor, fake_sock, result = self.connect(ConnectionRefusedError())
        self.assertTrue(result)
        self.assertIsNone(monitor.sock)
        fake_sock.close.assert_called_once()

    def test_timeout_is_absent(self):
        monitor, fake_sock, result = self.connect(socket.timeout())
        self.assertFalse(result)
        self.assertIsNone(monitor.sock)
        fake_sock.close.assert_called_once()

    def test_host_down_is_absent(self):
        monitor, _, result = self.connect(OSError(112, "Host is down"))
        self.assertFalse(result)

    def test_python_without_bluetooth_support(self):
        monitor = bls.ClassicPresenceMonitor()
        with mock.patch.object(bls, "socket", mock.MagicMock(spec=[])):
            self.assertFalse(monitor.connect(self.MAC, 5))

    def test_python_with_incomplete_bluetooth_support(self):
        required = ("AF_BLUETOOTH", "SOCK_SEQPACKET", "BTPROTO_L2CAP")
        for missing in required:
            available = [name for name in required if name != missing]
            incomplete_socket = mock.MagicMock(spec=available + ["socket"])
            with self.subTest(missing=missing), \
                    mock.patch.object(bls, "socket", incomplete_socket):
                self.assertFalse(bls.ClassicPresenceMonitor().connect(self.MAC, 5))
            incomplete_socket.socket.assert_not_called()

    def test_still_present_without_held_channel(self):
        self.assertFalse(bls.ClassicPresenceMonitor().still_present(self.MAC))

    def test_still_present_while_channel_quiet(self):
        monitor = bls.ClassicPresenceMonitor()
        monitor.sock = mock.MagicMock()
        monitor.last_keepalive = time.time()
        with mock.patch.object(bls.select, "select", return_value=([], [], [])):
            self.assertTrue(monitor.still_present(self.MAC))
        self.assertIsNotNone(monitor.sock)
        monitor.sock.send.assert_not_called()

    def test_keepalive_sent_when_due(self):
        monitor = bls.ClassicPresenceMonitor()
        monitor.sock = mock.MagicMock()
        monitor.last_keepalive = time.time() - bls.KEEPALIVE_INTERVAL
        with mock.patch.object(bls.select, "select", return_value=([], [], [])):
            self.assertTrue(monitor.still_present(self.MAC))
        sent = monitor.sock.send.call_args[0][0]
        self.assertEqual(sent[0], 0x02)
        self.assertGreater(monitor.last_keepalive, time.time() - 1)

    def test_failed_keepalive_triggers_reconnect(self):
        monitor = bls.ClassicPresenceMonitor()
        dead_sock = mock.MagicMock()
        dead_sock.send.side_effect = OSError(107, "not connected")
        monitor.sock = dead_sock
        monitor.last_keepalive = time.time() - bls.KEEPALIVE_INTERVAL
        new_sock = mock.MagicMock()
        with mock.patch.object(bls.select, "select", return_value=([], [], [])), \
                self.bluetooth_socket(new_sock):
            self.assertTrue(monitor.still_present(self.MAC))
        dead_sock.close.assert_called_once()
        self.assertIs(monitor.sock, new_sock)

    def test_idle_close_reconnects_immediately(self):
        monitor = bls.ClassicPresenceMonitor()
        old_sock = mock.MagicMock()
        old_sock.recv.return_value = b""
        monitor.sock = old_sock
        new_sock = mock.MagicMock()
        with mock.patch.object(bls.select, "select", return_value=([old_sock], [], [])), \
                self.bluetooth_socket(new_sock):
            self.assertTrue(monitor.still_present(self.MAC))
        old_sock.close.assert_called_once()
        self.assertIs(monitor.sock, new_sock)
        new_sock.connect.assert_called_once_with((self.MAC, bls.SDP_PSM))

    def test_wait_sleeps_normally_without_held_channel(self):
        with mock.patch.object(bls.time, "sleep") as sleep, \
                mock.patch.object(bls.select, "select") as sel:
            bls.ClassicPresenceMonitor().wait(3)
        sleep.assert_called_once_with(3)
        sel.assert_not_called()

    def test_wait_blocks_on_quiet_channel(self):
        monitor = bls.ClassicPresenceMonitor()
        monitor.sock = mock.MagicMock()
        with mock.patch.object(bls.time, "sleep") as sleep, \
                mock.patch.object(bls.select, "select", return_value=([], [], [])) as sel:
            monitor.wait(3)
        self.assertEqual(sel.call_args[0][3], 3)
        sleep.assert_not_called()

    def test_wait_wakes_early_on_channel_event(self):
        monitor = bls.ClassicPresenceMonitor()
        monitor.sock = mock.MagicMock()
        with mock.patch.object(bls.time, "sleep") as sleep, \
                mock.patch.object(bls.select, "select", return_value=([monitor.sock], [], [])):
            monitor.wait(3)
        sleep.assert_called_once_with(0.5)

    def test_departed_device_is_absent(self):
        monitor = bls.ClassicPresenceMonitor()
        dead_sock = mock.MagicMock()
        dead_sock.recv.side_effect = OSError()
        monitor.sock = dead_sock
        gone_sock = mock.MagicMock()
        gone_sock.connect.side_effect = socket.timeout()
        with mock.patch.object(bls.select, "select", return_value=([dead_sock], [], [])), \
                self.bluetooth_socket(gone_sock):
            self.assertFalse(monitor.still_present(self.MAC))
        self.assertIsNone(monitor.sock)


class DevicePresentTest(unittest.TestCase):
    MAC = "aa:bb:cc:dd:ee:ff"

    def absent_monitor(self):
        monitor = mock.MagicMock(spec=bls.ClassicPresenceMonitor)
        monitor.still_present.return_value = False
        monitor.connect.return_value = False
        return monitor

    def test_held_channel_short_circuits(self):
        monitor = self.absent_monitor()
        monitor.still_present.return_value = True
        with mock.patch.object(bls, "bluetoothctl") as bt:
            self.assertTrue(bls.device_present(monitor, self.MAC, 5))
        bt.assert_not_called()

    def test_connected_short_circuits(self):
        with mock.patch.object(bls, "bluetoothctl", return_value="Connected: yes\n") as bt:
            self.assertTrue(bls.device_present(self.absent_monitor(), self.MAC, 5))
        bt.assert_called_once()

    def test_seen_in_discovery_scan(self):
        def fake(args, timeout):
            if args[0] == "info":
                return "Connected: no\n"
            return "[NEW] Device AA:BB:CC:DD:EE:FF Watch\n"
        with mock.patch.object(bls, "bluetoothctl", side_effect=fake):
            self.assertTrue(bls.device_present(self.absent_monitor(), self.MAC, 5))

    def test_absent(self):
        def fake(args, timeout):
            if args[0] == "info":
                return "Connected: no\n"
            return "[NEW] Device 11:22:33:44:55:66 Other\n"
        with mock.patch.object(bls, "bluetoothctl", side_effect=fake):
            self.assertFalse(bls.device_present(self.absent_monitor(), self.MAC, 5))

    def test_classic_answer_counts_as_present(self):
        monitor = self.absent_monitor()
        monitor.connect.return_value = True
        with mock.patch.object(bls, "bluetoothctl", return_value="Connected: no\n"):
            self.assertTrue(bls.device_present(monitor, self.MAC, 5))
        monitor.connect.assert_called_once_with(self.MAC.upper(), 5)


if __name__ == "__main__":
    unittest.main()

import os
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

import ble_lock_session as bls


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class BluetoothctlTest(unittest.TestCase):
    def test_strips_ansi_and_readline_codes(self):
        raw = "\x01\x1b[0;94m\x02[bluetooth]\x01\x1b[0m\x02 Device AA:BB:CC:DD:EE:FF Watch\n"
        with mock.patch.object(
                bls.subprocess, "run",
                return_value=completed(stdout=raw)) as run:
            output = bls.bluetoothctl(["devices"], 5)
        self.assertEqual(output, "[bluetooth] Device AA:BB:CC:DD:EE:FF Watch\n")
        self.assertEqual(run.call_args[1]["timeout"], 5)

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


class PromptMacTest(unittest.TestCase):
    def test_empty_keeps_current(self):
        with mock.patch("builtins.input", return_value="  "):
            self.assertIsNone(bls.prompt_mac("MAC", "AA:BB:CC:DD:EE:FF"))

    def test_valid_mac_is_uppercased(self):
        with mock.patch("builtins.input", return_value="aa:bb:cc:dd:ee:ff"):
            self.assertEqual(bls.prompt_mac("MAC", ""), "AA:BB:CC:DD:EE:FF")

    def test_invalid_mac_reprompts(self):
        answers = iter(["nonsense", "AA-BB-CC-DD-EE-FF", "AA:BB:CC:DD:EE:FF"])
        with mock.patch("builtins.input", side_effect=lambda _: next(answers)):
            self.assertEqual(bls.prompt_mac("MAC", ""), "AA:BB:CC:DD:EE:FF")


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
        fake_sock.settimeout.assert_called_once_with(5)
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

    def test_other_connection_errors_are_absent(self):
        errors = (
            ConnectionResetError(),
            ConnectionAbortedError(),
            BrokenPipeError(),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                monitor, fake_sock, result = self.connect(error)
                self.assertFalse(result)
                self.assertIsNone(monitor.sock)
                fake_sock.close.assert_called_once()

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
        self.assertFalse(bls.ClassicPresenceMonitor().still_present())

    def test_keepalive_response_confirms_presence(self):
        monitor = bls.ClassicPresenceMonitor()
        monitor.sock = mock.MagicMock()
        monitor.sock.recv.return_value = b"\x03\x00\x00"
        with mock.patch.object(
                bls.select, "select",
                return_value=([monitor.sock], [], [])) as sel:
            self.assertTrue(monitor.still_present(0.25))
        self.assertIsNotNone(monitor.sock)
        self.assertEqual(sel.call_args[0][3], 0.25)
        sent = monitor.sock.send.call_args[0][0]
        self.assertEqual(sent[0], 0x02)

    def test_unanswered_keepalive_is_absent(self):
        monitor = bls.ClassicPresenceMonitor()
        held_sock = mock.MagicMock()
        monitor.sock = held_sock
        with mock.patch.object(bls.select, "select", return_value=([], [], [])):
            self.assertFalse(monitor.still_present())
        held_sock.close.assert_called_once()
        self.assertIsNone(monitor.sock)

    def test_failed_keepalive_disconnects_without_reconnecting(self):
        monitor = bls.ClassicPresenceMonitor()
        dead_sock = mock.MagicMock()
        dead_sock.send.side_effect = OSError(107, "not connected")
        monitor.sock = dead_sock
        with mock.patch.object(bls.socket, "socket") as create_socket:
            self.assertFalse(monitor.still_present())
        dead_sock.close.assert_called_once()
        self.assertIsNone(monitor.sock)
        create_socket.assert_not_called()

    def test_idle_close_is_absent_without_reconnecting(self):
        monitor = bls.ClassicPresenceMonitor()
        old_sock = mock.MagicMock()
        old_sock.recv.return_value = b""
        monitor.sock = old_sock
        with mock.patch.object(
                bls.select, "select", return_value=([old_sock], [], [])), \
                mock.patch.object(bls.socket, "socket") as create_socket:
            self.assertFalse(monitor.still_present())
        old_sock.close.assert_called_once()
        self.assertIsNone(monitor.sock)
        create_socket.assert_not_called()

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


class ScanReportsPresentTest(unittest.TestCase):
    MAC = "AA:BB:CC:DD:EE:FF"

    def scan_output(self, event):
        return "Discovery started\n" + event + "\n"

    def test_new_device_after_discovery_started_is_present(self):
        output = self.scan_output(f"[NEW] Device {self.MAC} Watch")
        self.assertTrue(bls.scan_reports_present(output, self.MAC))

    def test_cached_new_device_before_discovery_started_is_ignored(self):
        output = f"[NEW] Device {self.MAC} Watch\nDiscovery started\n"
        self.assertFalse(bls.scan_reports_present(output, self.MAC))

    def test_positive_changes_are_present(self):
        changes = (
            "Connected: yes",
            "RSSI: -55",
            "TxPower: 12",
            "ManufacturerData.Key: 0x004c",
            "ServiceData.Key: 0000180f-0000-1000-8000-00805f9b34fb",
            "AdvertisingFlags:",
            "AdvertisingData.Key: 0x01",
        )
        for change in changes:
            with self.subTest(change=change):
                output = self.scan_output(
                    f"[CHG] Device {self.MAC} {change}"
                )
                self.assertTrue(bls.scan_reports_present(output, self.MAC))

    def test_negative_or_cached_changes_are_not_presence(self):
        changes = (
            "Connected: no",
            "ServicesResolved: no",
            "Trusted: yes",
            "Paired: yes",
            "Alias: Watch",
        )
        for change in changes:
            with self.subTest(change=change):
                output = self.scan_output(
                    f"[CHG] Device {self.MAC} {change}"
                )
                self.assertFalse(bls.scan_reports_present(output, self.MAC))

    def test_negative_change_does_not_hide_later_positive_evidence(self):
        output = self.scan_output(
            f"[CHG] Device {self.MAC} Connected: no\n"
            f"[CHG] Device {self.MAC} RSSI: -60"
        )
        self.assertTrue(bls.scan_reports_present(output, self.MAC))

    def test_mac_must_match_exactly(self):
        output = self.scan_output(
            "[NEW] Device 11:22:33:44:55:66 Other"
        )
        self.assertFalse(bls.scan_reports_present(output, self.MAC))


class InfoReportsConnectedTest(unittest.TestCase):
    MAC = "AA:BB:CC:DD:EE:FF"

    def test_target_property_is_connected(self):
        self.assertTrue(bls.info_reports_connected("\tConnected: yes\n", self.MAC))

    def test_target_change_is_connected(self):
        output = f"[CHG] Device {self.MAC} Connected: yes\n"
        self.assertTrue(bls.info_reports_connected(output, self.MAC))

    def test_other_device_change_is_ignored(self):
        output = "[CHG] Device 11:22:33:44:55:66 Connected: yes\n"
        self.assertFalse(bls.info_reports_connected(output, self.MAC))


class DevicePresentTest(unittest.TestCase):
    MAC = "aa:bb:cc:dd:ee:ff"

    def absent_monitor(self):
        monitor = mock.MagicMock(spec=bls.ClassicPresenceMonitor)
        monitor.sock = None
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

    def test_failed_held_channel_ignores_stale_connected_info(self):
        monitor = self.absent_monitor()
        monitor.sock = mock.sentinel.dead_socket

        def fake(args, timeout):
            self.assertNotEqual(args[0], "info")
            return "Discovery started\n"

        with mock.patch.object(bls, "bluetoothctl", side_effect=fake) as bt:
            self.assertFalse(bls.device_present(monitor, self.MAC, 5))
        self.assertEqual(bt.call_count, 1)
        monitor.connect.assert_called_once()

    def test_seen_in_discovery_scan(self):
        def fake(args, timeout):
            if args[0] == "info":
                return "Connected: no\n"
            return "Discovery started\n[NEW] Device AA:BB:CC:DD:EE:FF Watch\n"
        with mock.patch.object(bls, "bluetoothctl", side_effect=fake):
            self.assertTrue(bls.device_present(self.absent_monitor(), self.MAC, 5))

    def test_scan_evidence_before_deadline_is_not_discarded(self):
        def fake(args, timeout):
            if args[0] == "info":
                return "Connected: no\n"
            output = (
                b"Discovery started\n"
                b"[NEW] Device AA:BB:CC:DD:EE:FF Watch\n"
            )
            raise subprocess.TimeoutExpired(args, timeout, output=output)

        with mock.patch.object(bls, "bluetoothctl", side_effect=fake):
            self.assertTrue(bls.device_present(self.absent_monitor(), self.MAC, 5))

    def test_absent(self):
        def fake(args, timeout):
            if args[0] == "info":
                return "Connected: no\n"
            return "Discovery started\n[NEW] Device 11:22:33:44:55:66 Other\n"
        with mock.patch.object(bls, "bluetoothctl", side_effect=fake):
            self.assertFalse(bls.device_present(self.absent_monitor(), self.MAC, 5))

    def test_classic_answer_counts_as_present(self):
        monitor = self.absent_monitor()
        monitor.connect.return_value = True
        with mock.patch.object(bls, "bluetoothctl", return_value="Connected: no\n"):
            self.assertTrue(bls.device_present(monitor, self.MAC, 5))
        monitor.connect.assert_called_once()
        mac, timeout = monitor.connect.call_args[0]
        self.assertEqual(mac, self.MAC.upper())
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, bls.CLASSIC_PROBE_TIMEOUT)

    def test_check_budget_is_shared_between_classic_and_scan(self):
        now = [100.0]
        monitor = self.absent_monitor()
        connect_timeouts = []
        scan_timeouts = []

        def connect(mac, timeout):
            connect_timeouts.append(timeout)
            now[0] += timeout
            return False

        def bluetoothctl(args, timeout):
            if args[0] == "info":
                return "Connected: no\n"
            scan_timeouts.append(timeout)
            now[0] += timeout
            return "Discovery started\n"

        monitor.connect.side_effect = connect
        with mock.patch.object(bls.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(bls, "bluetoothctl", side_effect=bluetoothctl):
            self.assertFalse(bls.device_present(monitor, self.MAC, 7))

        self.assertEqual(len(connect_timeouts), 1)
        self.assertLessEqual(connect_timeouts[0], bls.CLASSIC_PROBE_TIMEOUT)
        self.assertEqual(len(scan_timeouts), 1)
        self.assertGreater(scan_timeouts[0], 0)
        self.assertLessEqual(now[0] - 100.0, 7)

    def test_slow_info_is_capped_and_total_budget_is_preserved(self):
        now = [100.0]
        monitor = self.absent_monitor()
        scan_timeouts = []

        def connect(mac, timeout):
            now[0] += timeout
            return False

        def bluetoothctl(args, timeout):
            now[0] += timeout
            if args[0] == "info":
                raise subprocess.TimeoutExpired(args, timeout)
            scan_timeouts.append(timeout)
            return "Discovery started\n"

        monitor.connect.side_effect = connect
        with mock.patch.object(bls.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(bls, "bluetoothctl", side_effect=bluetoothctl) as bt:
            self.assertFalse(bls.device_present(monitor, self.MAC, 7))

        info_timeout = bt.call_args_list[0][0][1]
        self.assertLessEqual(info_timeout, bls.INFO_TIMEOUT)
        self.assertEqual(len(scan_timeouts), 1)
        self.assertGreaterEqual(scan_timeouts[0], 3)
        self.assertEqual(now[0] - 100.0, 7)

    def test_failed_held_probe_and_fallbacks_share_one_budget(self):
        now = [100.0]
        monitor = self.absent_monitor()
        monitor.sock = mock.sentinel.dead_socket
        scan_timeouts = []

        def still_present(timeout):
            now[0] += timeout
            return False

        def connect(mac, timeout):
            now[0] += timeout
            return False

        def bluetoothctl(args, timeout):
            self.assertNotEqual(args[0], "info")
            now[0] += timeout
            scan_timeouts.append(timeout)
            return "Discovery started\n"

        monitor.still_present.side_effect = still_present
        monitor.connect.side_effect = connect
        with mock.patch.object(bls.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(bls, "bluetoothctl", side_effect=bluetoothctl):
            self.assertFalse(bls.device_present(monitor, self.MAC, 7))

        held_timeout = monitor.still_present.call_args[0][0]
        self.assertLessEqual(held_timeout, bls.KEEPALIVE_TIMEOUT)
        monitor.connect.assert_called_once()
        self.assertEqual(len(scan_timeouts), 1)
        self.assertGreaterEqual(scan_timeouts[0], 3)
        self.assertEqual(now[0] - 100.0, 7)

    def test_short_budget_still_reserves_time_for_ble(self):
        now = [100.0]
        monitor = self.absent_monitor()
        scans = []

        def bluetoothctl(args, timeout):
            now[0] += timeout
            if args[0] == "info":
                raise subprocess.TimeoutExpired(args, timeout)
            scans.append(timeout)
            return "Discovery started\n"

        with mock.patch.object(bls.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(bls, "bluetoothctl", side_effect=bluetoothctl):
            self.assertFalse(bls.device_present(monitor, self.MAC, 1))

        monitor.connect.assert_not_called()
        self.assertEqual(len(scans), 1)
        self.assertGreater(scans[0], 0)
        self.assertEqual(now[0] - 100.0, 1)


class StartTest(unittest.TestCase):
    MAC = "AA:BB:CC:DD:EE:FF"

    def test_adapter_errors_and_timeouts_count_as_misses(self):
        errors = (
            bls.BluetoothUnavailableError("adapter unavailable"),
            subprocess.TimeoutExpired("bluetoothctl", 7),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                monitor = mock.MagicMock(spec=bls.ClassicPresenceMonitor)
                monitor.supported.return_value = True
                monitor.wait.side_effect = [None, None, KeyboardInterrupt()]

                with mock.patch.object(bls, "ClassicPresenceMonitor", return_value=monitor), \
                        mock.patch.object(bls.shutil, "which", return_value="/bin/tool"), \
                        mock.patch.object(bls, "device_present", side_effect=[False, error, False]), \
                        mock.patch.object(bls.subprocess, "Popen") as popen, \
                        mock.patch.object(bls, "log"), \
                        mock.patch("builtins.print"):
                    bls.start(self.MAC, "lock", "unlock", 3, 7, 3)

                popen.assert_called_once()
                self.assertEqual(popen.call_args[0][0], "lock")

    def test_unexpected_errors_do_not_count_as_absence(self):
        monitor = mock.MagicMock(spec=bls.ClassicPresenceMonitor)
        monitor.supported.return_value = True
        monitor.wait.side_effect = [None, None, KeyboardInterrupt()]

        with mock.patch.object(bls, "ClassicPresenceMonitor", return_value=monitor), \
                mock.patch.object(bls.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(
                    bls, "device_present",
                    side_effect=[False, RuntimeError("bug"), False]), \
                mock.patch.object(bls.subprocess, "Popen") as popen, \
                mock.patch.object(bls, "log"), \
                mock.patch("builtins.print"):
            bls.start(self.MAC, "lock", "unlock", 3, 7, 3)

        popen.assert_not_called()

    def test_departure_just_after_success_locks_within_30_seconds(self):
        now = [0.0]
        locked_at = []
        checks = iter((True, False, False, False))
        monitor = mock.MagicMock(spec=bls.ClassicPresenceMonitor)
        monitor.supported.return_value = True

        def absent(*args):
            present = next(checks)
            if not present:
                now[0] += 7
            return present

        def wait(seconds):
            if locked_at:
                raise KeyboardInterrupt
            now[0] += seconds

        def popen(*args, **kwargs):
            locked_at.append(now[0])
            return mock.Mock()

        monitor.wait.side_effect = wait
        with mock.patch.object(bls, "ClassicPresenceMonitor", return_value=monitor), \
                mock.patch.object(bls.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(bls, "device_present", side_effect=absent), \
                mock.patch.object(bls.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(bls, "log"), \
                mock.patch("builtins.print"):
            bls.start(self.MAC, "lock", "unlock", 3, 7, 3)

        self.assertEqual(locked_at, [30])


if __name__ == "__main__":
    unittest.main()

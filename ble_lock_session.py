#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import configparser
import datetime
import math
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import time
from contextlib import nullcontext

__version__ = "1.0.0"

CONFIG_DIR = os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_FILE = os.path.join(CONFIG_DIR, "ble-lock-session/config.ini")
LOGFILE = os.getenv("BLE_LOCK_LOGFILE", "-")

# bluetoothctl colors its output even when piped; readline also injects \x01/\x02
ANSI_ESCAPE = re.compile("\x1b\\[[0-9;]*[A-Za-z]|[\x01\x02]")
# Safety margin for bluetoothctl calls that should return promptly
BT_TOOL_TIMEOUT = 10
# A local cached-info query should be effectively immediate; do not let it
# consume the whole per-check discovery budget if bluetoothd gets stuck.
INFO_TIMEOUT = 1
# L2CAP PSM for SDP: Classic devices answer connections here even with
# the screen off and without being discoverable.
SDP_PSM = 1
# Limit the Classic page attempt and preserve part of discover_time for a
# BLE scan. Together they must stay inside the single per-check deadline.
CLASSIC_PROBE_TIMEOUT = 5
# Keep a useful active-discovery window for BLE-only devices. With the
# default seven-second budget this leaves up to four seconds for a direct
# Classic page and guarantees three seconds for BLE, even after a failed
# held-channel check or a slow cached-info query.
BLE_SCAN_RESERVE = 3
# A quiet held socket is not proof of presence: require an SDP answer on
# every check so a vanished device is detected without waiting for the
# kernel's much longer link-supervision timeout.
KEEPALIVE_TIMEOUT = 1

# bluetoothctl prints cached devices while starting. Only events after
# "Discovery started" are fresh evidence from this scan.
DEVICE_EVENT = re.compile(
    r"\[(NEW|CHG)\]\s+DEVICE\s+([0-9A-F]{2}(?::[0-9A-F]{2}){5})(?=\s|$)",
    re.IGNORECASE,
)
PRESENCE_CHANGES = (
    "CONNECTED: YES",
    "RSSI:",
    "TXPOWER:",
    "MANUFACTURERDATA",
    "SERVICEDATA",
    "ADVERTISINGFLAGS:",
    "ADVERTISINGDATA",
)

class BluetoothUnavailableError(Exception):
    pass


def clean_bluetooth_output(output):
    if not output:
        return ""
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    return ANSI_ESCAPE.sub("", output)


# Run bluetoothctl and return its output with ANSI codes stripped
def bluetoothctl(args, timeout):
    try:
        result = subprocess.run(["bluetoothctl"] + args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise BluetoothUnavailableError("bluetoothctl not found (install BlueZ)")
    output = clean_bluetooth_output(result.stdout)
    if "No default controller" in output + result.stderr:
        raise BluetoothUnavailableError("no Bluetooth adapter available (is bluetoothd running?)")
    return output


def scan_reports_present(output, mac):
    mac = mac.upper()
    discovery_started = False
    for line in output.splitlines():
        upper = line.upper()
        if "DISCOVERY STARTED" in upper:
            discovery_started = True
            continue
        if not discovery_started:
            continue

        event = DEVICE_EVENT.search(upper)
        if event is None or event.group(2) != mac:
            continue
        if event.group(1) == "NEW":
            return True

        change = upper[event.end():].strip()
        if any(change.startswith(prefix) for prefix in PRESENCE_CHANGES):
            return True
    return False


def info_reports_connected(output, mac):
    mac = mac.upper()
    for line in output.splitlines():
        upper = line.strip().upper()
        # Normal `bluetoothctl info <mac>` property output.
        if upper == "CONNECTED: YES":
            return True
        # Ignore asynchronous changes for unrelated devices.
        event = DEVICE_EVENT.search(upper)
        if event is not None and event.group(2) == mac:
            change = upper[event.end():].strip()
            if change.startswith("CONNECTED: YES"):
                return True
    return False


def default_settings():
    desktop = os.getenv('XDG_CURRENT_DESKTOP', '').upper()
    return {
        'target_address': '',
        'lock_cmd': get_default_lock_command(desktop),
        'unlock_cmd': get_default_unlock_command(desktop),
        'sleep_time': '3',
        'discover_time': '7',
        'scan_duration': '60',
        'fail_checks': '3'
    }

# Function to load the configuration from a .ini file
def load_config():
    config = configparser.ConfigParser(interpolation=None)

    exists = os.path.exists(CONFIG_FILE)
    if exists:
        try:
            config.read(CONFIG_FILE)
        except configparser.Error as e:
            print(f"Error: could not parse {CONFIG_FILE}: {e}")
            sys.exit(1)
    else:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)

    if 'SETTINGS' not in config:
        config['SETTINGS'] = {}

    settings = config['SETTINGS']
    added = False
    for key, value in default_settings().items():
        if key not in settings:
            settings[key] = value
            added = True

    if added or not exists:
        save_config(config)

    return config

# Function to save the configuration
def save_config(config):
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)

# Read a settings value that must be a positive integer
def get_positive_int(settings, key):
    value = settings[key]
    try:
        number = int(value)
        if number <= 0:
            raise ValueError
    except ValueError:
        print(f"Error: {key} must be a positive integer, got {value!r}. Fix it with --config.")
        sys.exit(1)
    return number

# Prompt for a positive integer
def prompt_positive_int(label, current):
    while True:
        value = input(f"{label} (current: {current}) : ").strip()
        if not value:
            return None
        if value.isdigit() and int(value) > 0:
            return value
        print("Please enter a positive integer.")

# GNOME gets no special case: gnome-screensaver-command was removed years
# ago, and GNOME Shell honors the logind Lock/Unlock signals.
def get_default_lock_command(desktop):
    if 'SWAY' in desktop:
        return "swaylock"
    return "loginctl lock-session"

def get_default_unlock_command(desktop):
    if 'SWAY' in desktop:
        return "pkill -USR1 swaylock"
    return "loginctl unlock-session"

# Function to scan for Bluetooth devices (Classic and BLE)
def scan_device(target_name, scan_duration):
    deadline = time.time() + scan_duration
    while True:
        try:
            bluetoothctl(["--timeout", "4", "scan", "on"], 4 + BT_TOOL_TIMEOUT)
            for line in bluetoothctl(["devices"], BT_TOOL_TIMEOUT).splitlines():
                parts = line.strip().split(" ", 2)
                if len(parts) == 3 and parts[0] == "Device" and parts[2] == target_name:
                    return parts[1]
        except (BluetoothUnavailableError, subprocess.TimeoutExpired) as e:
            print(f"Error scanning for device: {e}")
            time.sleep(1)
        if time.time() >= deadline:
            return None

# Tracks a Classic device by holding an L2CAP connection to its SDP
# channel open across checks (phones answer even when not discoverable
# or with the screen off; no privileges needed, unlike hcitool/l2ping).
# Connecting and closing per check would make the desktop announce a
# connect/disconnect notification pair every cycle; holding the channel
# keeps the underlying ACL link stable, so real arrivals and departures
# surface as a single notification each.
class ClassicPresenceMonitor:
    def __init__(self):
        self.sock = None
        self.txid = 0

    def supported(self):
        return all(hasattr(socket, name) for name in (
            "AF_BLUETOOTH", "SOCK_SEQPACKET", "BTPROTO_L2CAP"
        ))

    # Minimal SDP service-search request (for the L2CAP UUID, which any
    # SDP server matches); the periodic exchange marks the link active.
    def keepalive(self, timeout=KEEPALIVE_TIMEOUT):
        packet = bytes([
            0x02,                          # ServiceSearchRequest
            self.txid >> 8, self.txid & 0xFF,
            0x00, 0x08,                    # parameter length
            0x35, 0x03, 0x19, 0x01, 0x00,  # pattern: DES { UUID16 0x0100 }
            0x00, 0x01,                    # max record count
            0x00,                          # no continuation state
        ])
        self.txid = (self.txid + 1) & 0xFFFF
        self.sock.send(packet)
        # Collect the answer here so wait() does not wake up for it. A
        # successful local send without a remote answer is not presence.
        readable, _, errored = select.select(
            [self.sock], [], [self.sock], timeout
        )
        if errored or not readable:
            raise TimeoutError("no SDP keepalive response")
        if self.sock.recv(1024) == b"":
            raise OSError("channel closed")

    # Try to reach the device; on success keep the channel open. Any
    # answer — even a rejected connection — proves the device is in
    # range; only silence (timeout / host down) means absence.
    def connect(self, mac, timeout):
        if not self.supported():
            return False
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        try:
            sock.settimeout(timeout)
            sock.connect((mac, SDP_PSM))
        except ConnectionRefusedError:
            sock.close()
            return True
        except OSError:
            sock.close()
            return False
        sock.setblocking(False)
        self.sock = sock
        return True

    # Validate the held channel with a real request. Reconnection belongs
    # to device_present(), which owns the single per-check time budget.
    def still_present(self, timeout=KEEPALIVE_TIMEOUT):
        if self.sock is None:
            return False
        try:
            self.keepalive(timeout)
            return True
        except (OSError, ValueError):
            self.disconnect()
            return False

    # Sleep between checks, waking early if the held channel reports an
    # event (usually the device idle-closing it) so device_present() can
    # probe again while the underlying ACL link is still up. Waiting the
    # full interval would let the link lapse and force a slow page to a
    # sleeping device.
    def wait(self, seconds):
        if self.sock is None:
            time.sleep(seconds)
            return
        try:
            readable, _, errored = select.select(
                [self.sock], [], [self.sock], seconds
            )
        except (OSError, ValueError):
            self.disconnect()
            time.sleep(0.5)
            return
        if readable or errored:
            # Let the close settle; also caps the cycle rate if the
            # device keeps closing the channel right away.
            time.sleep(0.5)

    def disconnect(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

# Check if the device is reachable, trying the cheapest signals first:
# the held Classic channel, an active connection (paired BLE wearables),
# a Classic connect attempt, then a discovery pass (BLE devices
# advertising nearby).
def device_present(monitor, target_address, discover_time):
    mac = target_address.upper()
    deadline = time.monotonic() + discover_time
    scan_reserve = max(
        0.0, min(BLE_SCAN_RESERVE, discover_time / 2)
    )

    def remaining():
        return max(0.0, deadline - time.monotonic())

    def non_scan_budget(limit):
        return min(limit, max(0.0, remaining() - scan_reserve))

    had_held_channel = getattr(monitor, "sock", None) is not None
    held_timeout = non_scan_budget(KEEPALIVE_TIMEOUT)
    if held_timeout > 0 and monitor.still_present(held_timeout):
        return True

    # If an actively verified channel just failed, a cached BlueZ
    # "Connected: yes" can remain stale until link supervision expires.
    # Skip that weaker signal and probe the device directly instead.
    if not had_held_channel:
        budget = non_scan_budget(INFO_TIMEOUT)
        if budget <= 0:
            info = ""
        else:
            try:
                info = bluetoothctl(["info", mac], budget)
            except subprocess.TimeoutExpired as e:
                info = clean_bluetooth_output(
                    getattr(e, "stdout", None) or getattr(e, "output", None)
                )
            if info_reports_connected(info, mac):
                return True

    classic_timeout = non_scan_budget(CLASSIC_PROBE_TIMEOUT)
    if classic_timeout > 0 and monitor.connect(mac, classic_timeout):
        return True

    scan_budget = remaining()
    if scan_budget <= 0:
        return False
    scan_duration = max(1, math.ceil(scan_budget))
    try:
        scan_output = bluetoothctl(
            ["--timeout", str(scan_duration), "scan", "on"],
            scan_budget,
        )
    except subprocess.TimeoutExpired as e:
        # The deadline is an expected way for a scan to end. Preserve any
        # fresh event bluetoothctl printed before it was stopped.
        scan_output = clean_bluetooth_output(
            getattr(e, "stdout", None) or getattr(e, "output", None)
        )
    return scan_reports_present(scan_output, mac)

# Write a timestamped message to the log destination
def log(file, message):
    event = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        file.write(f"[{event}] {message}\n")
        file.flush()
    except OSError as e:
        print(f"Error writing to log: {e}")

# Main function to activate automatic lock/unlock
def start(target_address, lock_cmd, unlock_cmd, sleep_time, discover_time, fail_checks):

    for name, cmd in (("lock_cmd", lock_cmd), ("unlock_cmd", unlock_cmd)):
        if not cmd.split() or not shutil.which(cmd.split()[0]):
            print(f"Error: {name} ({cmd!r}) is not a valid command.")
            sys.exit(1)

    if not shutil.which("bluetoothctl"):
        print("Error: bluetoothctl not found. Install BlueZ.")
        sys.exit(1)
    monitor = ClassicPresenceMonitor()
    if not monitor.supported():
        print("Note: this Python lacks Bluetooth socket support; Classic device probing disabled (BLE detection still works).")

    state = 1
    misses = 0

    def open_log():
        return nullcontext(sys.stdout) if LOGFILE == "-" else open(LOGFILE, 'a')

    try:
        with open_log() as file:
            while True:
                check = None
                try:
                    check = device_present(monitor, target_address, discover_time)
                except (BluetoothUnavailableError, subprocess.TimeoutExpired) as e:
                    log(file, f"Error checking device: {e}")
                    check = False
                except Exception as e:
                    log(file, f"Unexpected error: {e}")

                try:
                    if check:
                        misses = 0
                        if state == 0:
                            subprocess.Popen(unlock_cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            log(file, "➔ [UNLOCKED]")
                            state = 1
                    elif check is False and state == 1:
                        # A single failed lookup is often transient; adapter
                        # errors count too, so disabling Bluetooth fails safe.
                        misses += 1
                        if misses >= fail_checks:
                            subprocess.Popen(lock_cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            log(file, "➔ [LOCKED]")
                            state = 0
                except Exception as e:
                    log(file, f"Unexpected state-change error: {e}")
                finally:
                    monitor.wait(sleep_time)
    except KeyboardInterrupt:
        print("Monitoring stopped by user.")
    except Exception as e:
        # Reaching this means the loop itself is dead
        print(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        monitor.disconnect()

# Command-line arguments using argparse
def main():
    parser = argparse.ArgumentParser(description="Automatic PC Lock/Unlock using Bluetooth proximity")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--scan", action="store_true", help="Search and save a Bluetooth device")
    group.add_argument("--start", action="store_true", help="Activate automatic lock/unlock")
    group.add_argument("--config", action="store_true", help="Modify the current configuration")

    args = parser.parse_args()

    config = load_config()

    if args.scan:
        if not shutil.which("bluetoothctl"):
            print("Error: bluetoothctl not found. Install BlueZ.")
            sys.exit(1)
        target_name = input("Enter the name of the device to search : ")
        bdaddr = scan_device(target_name, get_positive_int(config["SETTINGS"], "scan_duration"))
        if bdaddr:
            print(f"Device found: {bdaddr}")
            config["SETTINGS"]["target_address"] = bdaddr
            save_config(config)
            print("Address saved to configuration file.")
        else:
            print("Device not found.")

    elif args.start:
        if config["SETTINGS"]["target_address"] == "":
            print("Error: No Bluetooth device configured. Use --scan to configure it.")
            return
        start(
            config["SETTINGS"]["target_address"],
            config["SETTINGS"]["lock_cmd"],
            config["SETTINGS"]["unlock_cmd"],
            get_positive_int(config["SETTINGS"], "sleep_time"),
            get_positive_int(config["SETTINGS"], "discover_time"),
            get_positive_int(config["SETTINGS"], "fail_checks")
        )

    elif args.config:
        lock_cmd = input(f"Lock command (current: {config['SETTINGS']['lock_cmd']}) : ")
        if lock_cmd:
            config["SETTINGS"]["lock_cmd"] = lock_cmd

        unlock_cmd = input(f"Unlock command (current: {config['SETTINGS']['unlock_cmd']}) : ")
        if unlock_cmd:
            config["SETTINGS"]["unlock_cmd"] = unlock_cmd

        sleep_time = prompt_positive_int("Time interval between checks in seconds", config["SETTINGS"]["sleep_time"])
        if sleep_time:
            config["SETTINGS"]["sleep_time"] = sleep_time

        discover_time = prompt_positive_int("Per-check lookup timeout for --start in seconds", config["SETTINGS"]["discover_time"])
        if discover_time:
            config["SETTINGS"]["discover_time"] = discover_time

        scan_duration = prompt_positive_int("Scan duration for --scan in seconds", config["SETTINGS"]["scan_duration"])
        if scan_duration:
            config["SETTINGS"]["scan_duration"] = scan_duration

        fail_checks = prompt_positive_int("Consecutive failed checks before locking", config["SETTINGS"]["fail_checks"])
        if fail_checks:
            config["SETTINGS"]["fail_checks"] = fail_checks

        save_config(config)
        print("Configuration saved.")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import subprocess
import time
import datetime
import sys
import configparser
import argparse
import shutil
from contextlib import nullcontext

__version__ = "1.0.0"

CONFIG_DIR = os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_FILE = os.path.join(CONFIG_DIR, "ble-lock-session/config.ini")
LOGFILE = os.getenv("BLE_LOCK_LOGFILE","-")

# bluetoothctl colors its output even when piped; readline also injects \x01/\x02
ANSI_ESCAPE = re.compile("\x1b\\[[0-9;]*[A-Za-z]|[\x01\x02]")
# Safety margin for bluetoothctl calls that should return promptly
BT_TOOL_TIMEOUT = 10

class BluetoothUnavailableError(Exception):
    pass

# Run bluetoothctl and return its output with ANSI codes stripped
def bluetoothctl(args, timeout):
    try:
        result = subprocess.run(["bluetoothctl"] + args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise BluetoothUnavailableError("bluetoothctl not found (install BlueZ)")
    output = ANSI_ESCAPE.sub("", result.stdout)
    if "No default controller" in output + result.stderr:
        raise BluetoothUnavailableError("no Bluetooth adapter available (is bluetoothd running?)")
    return output

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

# Check if the device is reachable, trying the cheapest signals first:
# an active connection (paired BLE wearables), a Classic name request
# (phones answer even when not discoverable), then a discovery pass
# (BLE devices advertising nearby).
def device_present(target_address, discover_time):
    mac = target_address.upper()

    info = bluetoothctl(["info", mac], BT_TOOL_TIMEOUT)
    if "Connected: yes" in info:
        return True

    if shutil.which("hcitool"):
        try:
            result = subprocess.run(["hcitool", "name", mac], capture_output=True, text=True, timeout=discover_time)
            if result.returncode == 0 and result.stdout.strip():
                return True
        except subprocess.TimeoutExpired:
            pass

    scan_output = bluetoothctl(["--timeout", str(discover_time), "scan", "on"], discover_time + BT_TOOL_TIMEOUT)
    for line in scan_output.splitlines():
        if "DEVICE " + mac in line.upper() and ("[NEW]" in line or "[CHG]" in line):
            return True
    return False

# Write a timestamped message to the log destination
def log(file, message):
    event = datetime.datetime.now().strftime("%d-%m-%y %H:%M:%S")
    try:
        file.write(f" [{event}] {message}\n")
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
    if not shutil.which("hcitool"):
        print("Note: hcitool not found; Classic name requests disabled (BLE detection still works).")

    state = 1
    misses = 0

    def open_log():
        return nullcontext(sys.stdout) if LOGFILE == "-" else open(LOGFILE, 'a')

    try:
        with open_log() as file:
            while True:
                try:
                    check = device_present(target_address, discover_time)

                    if check:
                        misses = 0
                        if state == 0:
                            subprocess.Popen(unlock_cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            log(file, "➔ [UNLOCKED]")
                            state = 1
                    elif state == 1:
                        # A single failed lookup is often transient; only lock
                        # after fail_checks consecutive misses.
                        misses += 1
                        if misses >= fail_checks:
                            subprocess.Popen(lock_cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            log(file, "➔ [LOCKED]")
                            state = 0
                except (BluetoothUnavailableError, subprocess.TimeoutExpired) as e:
                    log(file, f"Error checking device: {e}")
                except Exception as e:
                    log(file, f"Unexpected error: {e}")
                finally:
                    time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("Monitoring stopped by user.")
    except Exception as e:
        # Reaching this means the loop itself is dead
        print(f"Fatal error: {e}")
        sys.exit(1)

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

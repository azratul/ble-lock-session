#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import bluetooth
import os
import subprocess
import time
import datetime
import sys
import json
import configparser
import argparse
import shutil
from contextlib import nullcontext

CONFIG_DIR = os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_FILE = os.path.join(CONFIG_DIR, "ble-lock-session/config.ini")
LOGFILE = os.getenv("BLE_LOCK_LOGFILE","-")

# Function to load the configuration from a JSON file
def load_config():
    config = configparser.ConfigParser()

    if not os.path.exists(CONFIG_FILE):
        if not os.path.exists(os.path.dirname(CONFIG_FILE)):
            os.makedirs(os.path.dirname(CONFIG_FILE))

        desktop = os.getenv('XDG_CURRENT_DESKTOP', '').upper()
        
        config['SETTINGS'] = {
            'target_address': '',
            'lock_cmd': get_default_lock_command(desktop),
            'unlock_cmd': get_default_unlock_command(desktop),
            'sleep_time': '5',
            'discover_time': '25'
        }

        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
    else:
        config.read(CONFIG_FILE)

    return config

# Function to save the configuration
def save_config(config):
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)

def get_default_lock_command(desktop):
    if 'GNOME' in desktop:
        return "gnome-screensaver-command --lock"
    elif 'SWAY' in desktop:
        return "swaylock"
    else:
        return "loginctl lock-session"

def get_default_unlock_command(desktop):
    if 'GNOME' in desktop:
        return "gnome-screensaver-command -d"
    elif 'SWAY' in desktop:
        return "pkill -USR1 swaylock"
    else:
        return "loginctl unlock-session"

# Function to scan for Bluetooth devices
def scan_device(target_name, discover_time):
    try:
        for bdaddr in bluetooth.discover_devices(duration=discover_time):
            if target_name == bluetooth.lookup_name(bdaddr):
                return bdaddr
    except bluetooth.BluetoothError as e:
        print(f"Error scanning for device: {e}")
    return None

# Main function to activate automatic lock/unlock
def start(target_address, lock_cmd, unlock_cmd, sleep_time, discover_time):

    if not shutil.which(lock_cmd.split()[0]) or not shutil.which(unlock_cmd.split()[0]):
        print("Error: lock_cmd or unlock_cmd is not a valid command.")
        sys.exit(1)

    state = 1
    try:
        open_log = lambda: nullcontext(sys.stdout) if LOGFILE == "-" else open(LOGFILE, 'w')
        with open_log() as file:
            while True:
                try:
                    check = bluetooth.lookup_name(target_address, timeout=discover_time)

                    event = datetime.datetime.now().strftime("%d-%m-%y %H:%M:%S")

                    if check and state == 0:
                        subprocess.Popen(unlock_cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        output = f" [{event}] ➔ [UNLOCKED]"
                        file.write(output + '\n')
                        file.flush()
                        state = 1
                    elif not check and state == 1:
                        subprocess.Popen(lock_cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        output = f" [{event}] ➔ [LOCKED]"
                        file.write(output + '\n')
                        file.flush()
                        state = 0

                    time.sleep(sleep_time)
                except bluetooth.BluetoothError as e:
                    print(f"Error checking device: {e}")
    except KeyboardInterrupt:
        print("Monitoring stopped by user.")
    except Exception as e:
        print(f"Unexpected error: {e}")

# Command-line arguments using argparse
def main():
    parser = argparse.ArgumentParser(description="Automatic PC Lock/Unlock using Bluetooth proximity")
    parser.add_argument("--scan", action="store_true", help="Search and save a Bluetooth device")
    parser.add_argument("--start", action="store_true", help="Activate automatic lock/unlock")
    parser.add_argument("--config", action="store_true", help="Modify the current configuration")

    args = parser.parse_args()

    config = load_config()

    if args.scan:
        target_name = input("Enter the name of the device to search : ")
        bdaddr = scan_device(target_name, int(config["SETTINGS"]["discover_time"]))
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
            int(config["SETTINGS"]["sleep_time"]),
            int(config["SETTINGS"]["discover_time"])
        )

    elif args.config:
        lock_cmd = input(f"Lock command (current: {config["SETTINGS"]["lock_cmd"]}) : ")
        if lock_cmd:
            config["SETTINGS"]["lock_cmd"] = lock_cmd

        unlock_cmd = input(f"Unlock command (current: {config["SETTINGS"]["unlock_cmd"]}) : ")
        if unlock_cmd:
            config["SETTINGS"]["unlock_cmd"] = unlock_cmd

        sleep_time = input(f"Time interval between checks in seconds (current: {config["SETTINGS"]["sleep_time"]}) : ")
        if sleep_time:
            config["SETTINGS"]["sleep_time"] = sleep_time

        discover_time = input(f"Bluetooth device discovery time (current: {config["SETTINGS"]["discover_time"]}) : ")
        if discover_time:
            config["SETTINGS"]["discover_time"] = discover_time

        save_config(config)
        print("Configuration saved.")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()

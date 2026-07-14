# BLE Lock Session

**BLE Lock Session** is a tool that allows you to automatically lock and unlock your computer screen using the proximity of a Bluetooth device. Ideal for users who want to improve the security of their devices and enjoy a hands-free lock/unlock experience.

## Features

- **Automatic Lock/Unlock**: Locks the screen when you move away and unlocks it when you come closer, using a Bluetooth device.
- **BLE and Classic Bluetooth**: Detects both BLE devices (smartwatches, bands, tags) and Classic devices (phones), even when they are not in discoverable mode.
- **Flexible Configuration**: Compatible with popular desktop environments or WM in Linux.
- **No Python dependencies**: Uses the BlueZ tools already present on most Linux systems.

## Table of Contents
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Available Commands](#available-commands)
- [Contributing](#contributing)
- [License](#license)

## Requirements

- **Linux** (tested on Arch Linux)
- **Bluetooth Device**
- **Python 3.x**
- **BlueZ** - `bluetoothctl` must be available (package `bluez-utils` on Arch, `bluez` on Debian/Ubuntu/Fedora).

No Python packages need to be installed. Classic Bluetooth devices (e.g. phones) are detected via Python's built-in Bluetooth sockets — no extra tools required.

## Installation

Clone the repository and navigate to the project directory:

```bash
$ git clone https://github.com/azratul/ble-lock-session.git
$ cd ble-lock-session
```

## Configuration

The configuration file is located at `~/.config/ble-lock-session/config.ini`. If it does not exist, it will be created automatically with default values.

You can modify the options in this file or use the following command:

```bash
$ python ble-lock-session.py --config
```

You will be able to change the following parameters:
- **`target_address`**: MAC address of the Bluetooth device.
- **`lock_cmd`**: Command to lock the screen (depending on the desktop environment).
- **`unlock_cmd`**: Command to unlock the screen (depending on the desktop environment).
- **`sleep_time`**: Time interval between checks, in seconds.
- **`discover_time`**: Per-check timeout when looking for the device during `--start`, in seconds.
- **`scan_duration`**: Overall deadline for `--scan`, in seconds.
- **`fail_checks`**: Consecutive failed checks required before locking (protects against transient Bluetooth failures).

## Usage

To use BLE Lock Session, you first need to scan and save the address of your Bluetooth device:

```bash
$ python ble-lock-session.py --scan
```

Then, to start monitoring and automatically lock/unlock:

```bash
$ python ble-lock-session.py --start
```

You can stop the script with **Ctrl + C**.

## Available Commands

- `--scan`: Searches for a Bluetooth device and saves the MAC address in the configuration.
- `--start`: Starts monitoring the configured device for lock/unlock.
- `--config`: Interactively modifies the current configuration.

## Configuration Example (`config.ini`)

```ini
[SETTINGS]
target_address = 00:1A:7D:DA:71:13
lock_cmd = loginctl lock-session
unlock_cmd = loginctl unlock-session
sleep_time = 3
discover_time = 7
scan_duration = 60
fail_checks = 3
```

## Contributing

Contributions are welcome! If you want to improve the code, add new features, or fix a bug, feel free to open a **pull request**.

1. Fork the project.
2. Create a branch for your new feature: `git checkout -b my-new-feature`
3. Commit your changes: `git commit -am 'Add a new feature'`
4. Push the branch: `git push origin my-new-feature`
5. Open a **pull request**.

## License

This project is licensed under the GPL-3.0 License.

---

Thank you for trying BLE Lock Session! If you have any questions or suggestions, feel free to open an **issue** or contact me.

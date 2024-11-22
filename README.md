# BLE Lock Session

**BLE Lock Session** is a tool that allows you to automatically lock and unlock your computer screen using the proximity of a Bluetooth device. Ideal for users who want to improve the security of their devices and enjoy a hands-free lock/unlock experience.

## Features

- **Automatic Lock/Unlock**: Locks the screen when you move away and unlocks it when you come closer, using a Bluetooth device.
- **Flexible Configuration**: Compatible with popular desktop environments or WM in Linux.

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
- **Python 3.x**
- **BlueZ** - Library for Bluetooth management.
- **Python Dependencies**:
  - `pybluez`: To interact with the Bluetooth device.
  - `configparser`: For INI file configuration.

## Installation

First, clone the repository and navigate to the project directory:

```bash
$ git clone https://github.com/azratul/ble-lock-session.git
$ cd ble-lock-session
```

Install the required dependencies:

```bash
$ pip install pybluez
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
- **`sleep_time`**: Time interval between checks.
- **`discover_time`**: Duration of Bluetooth device scanning.

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
lock_cmd = gnome-screensaver-command --lock
unlock_cmd = gnome-screensaver-command -d
sleep_time = 5
discover_time = 25
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

# BLE Lock Session

**BLE Lock Session** is a tool that allows you to automatically lock and unlock your computer screen using the proximity of a Bluetooth device. Ideal for users who want a hands-free lock/unlock experience (see [Security Considerations](#security-considerations) before relying on it).

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
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Requirements

- **Linux** (tested on Arch Linux)
- **Bluetooth Device**
- **Python 3.x**
- **BlueZ** - `bluetoothctl` must be available (package `bluez-utils` on Arch, `bluez` on Debian/Ubuntu/Fedora).

No Python packages need to be installed. Classic Bluetooth devices (e.g. phones) are detected via Python's built-in Bluetooth sockets — no extra tools required.

## Installation

The recommended way is [pipx](https://pipx.pypa.io/), which installs the `ble-lock-session` command in an isolated environment:

```bash
pipx install git+https://github.com/azratul/ble-lock-session.git
```

Alternatively, with pip:

```bash
pip install --user git+https://github.com/azratul/ble-lock-session.git
```

Or run it straight from a clone, no installation needed:

```bash
git clone https://github.com/azratul/ble-lock-session.git
cd ble-lock-session
python ble_lock_session.py --help
```

## Configuration

The configuration file is located at `~/.config/ble-lock-session/config.ini`. If it does not exist, it will be created automatically with default values.

You can modify the options in this file or use the following command:

```bash
ble-lock-session --config
```

You will be able to change the following parameters:

- **`target_address`**: MAC address of the Bluetooth device. Normally set by `--scan`, but you can also enter it directly here if you already know it.
- **`lock_cmd`**: Command to lock the screen (depending on the desktop environment).
- **`unlock_cmd`**: Command to unlock the screen (depending on the desktop environment).
- **`sleep_time`**: Time interval between checks, in seconds.
- **`discover_time`**: Total time budget for each presence check during `--start`, shared by Classic and BLE probes, in seconds.
- **`scan_duration`**: Overall deadline for `--scan`, in seconds.
- **`fail_checks`**: Consecutive failed checks required before locking (protects against transient Bluetooth failures).

With the defaults, a departure immediately after a successful check locks the
session in about 30 seconds in the slowest normal case: one initial 3-second
wait, three checks of up to 7 seconds, and two 3-second waits between misses.

## Usage

To use BLE Lock Session, you first need to scan and save the address of your Bluetooth device:

```bash
ble-lock-session --scan
```

Then, to start monitoring and automatically lock/unlock:

```bash
ble-lock-session --start
```

You can stop the script with **Ctrl + C**.

### Running as a systemd service

To keep the monitor running in the background without an open terminal, install the bundled user service:

```bash
mkdir -p ~/.config/systemd/user
cp ble-lock-session.service ~/.config/systemd/user/
systemctl --user enable --now ble-lock-session
```

Check its status and logs with:

```bash
systemctl --user status ble-lock-session
journalctl --user -u ble-lock-session -f
```

The unit assumes the `ble-lock-session` command is in `~/.local/bin` (the pipx/`pip install --user` location). If you installed it elsewhere, edit the `ExecStart=` line accordingly.

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

## Security Considerations

**This is a convenience tool, not an authentication mechanism.** Presence is detected by Bluetooth MAC address, and MAC addresses can be spoofed by an attacker who knows (or sniffs) your device's address. Treat the automatic unlock as roughly equivalent to leaving your session open while you are nearby — do not rely on it as a security boundary. If that trade-off is not acceptable for your threat model, use only the lock half (set `unlock_cmd` to something harmless like `true`) or don't use the tool at all.

## Troubleshooting

- **The screen locks but never unlocks.** On some distributions `loginctl unlock-session` requires polkit authorization. Check with `loginctl unlock-session` in a terminal while locked from another TTY; if it prompts or fails, add a polkit rule for your user or use a desktop-specific unlock command.
- **The phone is not detected when its screen is off.** Phones stop advertising over BLE when idle, so detection relies on the Classic Bluetooth link. That needs Python built with Bluetooth socket support (all major distro packages have it — if yours doesn't, `--start` prints a note about it) and the phone's Bluetooth radio on. Being paired with the computer makes detection most reliable.
- **The phone shows as "connected" to the computer while in range.** Expected: the tool keeps a lightweight Bluetooth link open to track presence silently — opening and closing one per check would make desktops that announce Bluetooth connections (e.g. blueman) spam notifications.
- **"no Bluetooth adapter available".** Make sure `bluetoothd` is running (`systemctl status bluetooth`) and the adapter is not blocked (`rfkill list`).
- **The adapter is disabled while monitoring.** Adapter errors count as failed checks, so the session locks after `fail_checks` consecutive failures instead of remaining unlocked indefinitely.
- **Locks randomly while the device is next to the computer.** Increase `fail_checks` and/or `discover_time` with `--config`; some devices advertise infrequently to save battery.

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

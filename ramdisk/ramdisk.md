# RAM Disk Utility - (`ramdisk.py`)

[![Python Version](https://img.shields.io/badge/python-3.x-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](#important-considerations)
[![Made with: Shell commands](https://img.shields.io/badge/made%20with-shell%20commands-red)](#script-workflow)

`ramdisk.py` is a Python script designed to simplify the creation and removal of RAM disks on Linux systems. It supports both traditional `tmpfs` RAM disks and compressed `ZRAM` RAM disks, providing a command-line interface to manage a RAM disk located at `/ramdisk`.

## Features

* **Dual RAM Disk Types:**
    * **`tmpfs` RAM Disk:** Create a standard RAM disk stored in volatile system memory.
    * **`ZRAM` RAM Disk:** Create a compressed RAM disk using ZRAM, potentially saving memory while still offering RAM-like speeds.
* **User-Specified Size:** Easily create a RAM disk with a defined size (e.g., "4G" for 4 Gigabytes, "512M" for 512 Megabytes, "100K" for 100 Kilobytes).
* **Simple Removal:** Cleanly unmounts and removes the RAM disk, automatically detecting if it's `tmpfs` or `ZRAM` for appropriate cleanup.
* **Fixed Mount Point:** Operates on a predefined mount point: `/ramdisk`.
* **Directory Management:** Automatically creates the `/ramdisk` directory if it doesn't exist during creation and removes it during the removal process.
* **Sudo Requirement:** Enforces execution with `sudo` privileges, as mounting and unmounting filesystems are restricted operations.
* **Prerequisite Checks:** For ZRAM, checks for necessary utilities like `zramctl` and `mkfs.ext2`.
* **Input Validation:** Includes basic validation for the size argument format.
* **Status Messages:** Provides informative messages about its operations and any errors encountered.

## Script Workflow

The script operates based on the command-line arguments provided.

### RAM Disk Creation

1.  **Sudo Check:** Verifies if the script is run with `sudo` privileges. Exits if not.
2.  **Argument Parsing:** Determines if a `tmpfs` or `ZRAM` disk is requested (via the optional `--zram` flag) and validates the size argument.
3.  **Mount Point Check:** Ensures `/ramdisk` is not already a mount point.
4.  **Directory Creation:** If `/ramdisk` does not exist, it's created.

#### `tmpfs` Creation (`sudo python3 ramdisk.py <size>`)
5.  **Mount `tmpfs`:** Executes `mount -t tmpfs -o size=<size_str> tmpfs /ramdisk`.

#### `ZRAM` Creation (`sudo python3 ramdisk.py <size> --zram`)
5.  **Prerequisite Check:** Verifies `zramctl` and `mkfs.ext2` (or the configured `ZRAM_FS_TYPE` tool) are available.
6.  **Load ZRAM Module:** Ensures the `zram` kernel module is loaded using `modprobe zram`.
7.  **Configure ZRAM Device:**
    * Uses `zramctl --find --size <size_str> --algorithm <ZRAM_COMP_ALGORITHM>` to find an available ZRAM device (e.g., `/dev/zram0`), set its disk size, and specify the compression algorithm (default: `lz4`).
8.  **Format ZRAM Device:** Formats the allocated ZRAM device with the specified filesystem (default: `ext2`) using `mkfs.<ZRAM_FS_TYPE> /dev/zramX`.
9.  **Mount ZRAM Device:** Mounts the formatted ZRAM device to `/ramdisk`.

### RAM Disk Removal (`sudo python3 ramdisk.py remove`)

1.  **Sudo Check:** Verifies `sudo` privileges.
2.  **Mount Information Retrieval:** If `/ramdisk` is mounted, uses `findmnt` (with a fallback to parsing `/proc/mounts`) to determine the source device and filesystem type.
3.  **Unmount Operation:** Unmounts `/ramdisk` using `umount /ramdisk`.
4.  **ZRAM Device Reset (if applicable):** If the source device was a ZRAM device (e.g., `/dev/zramX`), it resets the device using `zramctl --reset /dev/zramX`. This frees up the ZRAM device.
5.  **Directory Removal:** Attempts to remove the `/ramdisk` directory if it exists and is empty.

## Installation

1.  **Save the Script:**
    Save the Python code into a file named `ramdisk.py`.

2.  **Make it Executable (Optional but Recommended):**
    Open your terminal and run:
    ```bash
    chmod +x ramdisk.py
    ```

3.  **Dependencies:**
    * Python 3.x
    * Standard Linux command-line utilities (`mount`, `umount`, `mkdir`, `modprobe`).
    * **For `tmpfs` (usually pre-installed):** No special dependencies beyond standard utilities.
    * **For `ZRAM`:**
        * `util-linux`: Provides the `zramctl` utility.
            * Debian/Ubuntu: `sudo apt install util-linux`
            * Fedora: `sudo dnf install util-linux`
        * `e2fsprogs` (or tools for your chosen `ZRAM_FS_TYPE`): Provides `mkfs.ext2`.
            * Debian/Ubuntu: `sudo apt install e2fsprogs`
            * Fedora: `sudo dnf install e2fsprogs`
        * The `zram` kernel module must be available and loadable.

## Usage Examples

**Note:** All commands must be run with `sudo`.

1.  **Create a 4GB `tmpfs` RAM Disk:**
    ```bash
    sudo python3 ramdisk.py 4G
    ```
    If executable: `sudo ./ramdisk.py 4G`

2.  **Create a 1GB `ZRAM` RAM Disk (compressed):**
    ```bash
    sudo python3 ramdisk.py 1G --zram
    ```
    If executable: `sudo ./ramdisk.py 1G --zram`

3.  **Create a 512MB `tmpfs` RAM Disk:**
    ```bash
    sudo python3 ramdisk.py 512M
    ```

4.  **Verify RAM Disk Creation:**
    * For both types:
        ```bash
        df -h /ramdisk
        mount | grep /ramdisk
        ```
    * Additionally for ZRAM:
        ```bash
        zramctl
        # Check /proc/swaps if you configured it as swap, though this script uses it as a block device for a filesystem
        ```

5.  **Remove the RAM Disk (works for both `tmpfs` and `ZRAM`):**
    ```bash
    sudo python3 ramdisk.py remove
    ```
    If executable: `sudo ./ramdisk.py remove`

## Important Considerations

* **Sudo Privileges:** Essential for all operations.
* **Linux Specific:** Relies on Linux-specific tools and kernel features (`tmpfs`, `zram`, `mount`, `zramctl`).
* **Data Volatility:**
    * **`tmpfs`:** Data is stored in RAM and is lost on unmount or reboot.
    * **`ZRAM`:** Data is stored in a compressed form in RAM. It is also lost on unmount (which includes ZRAM device reset) or reboot.
* **Mount Point:** Uses the hardcoded `/ramdisk` mount point.
* **ZRAM Specifics:**
    * **Compression:** ZRAM uses compression (default `lz4` in the script). The actual memory used will be less than the specified disk size, depending on data compressibility.
    * **Performance:** ZRAM involves a CPU overhead for compression/decompression. For highly compressible data, it can effectively increase available RAM for the disk. `lz4` is generally fast.
    * **Kernel Module:** The `zram` kernel module must be available.
    * **Filesystem on ZRAM:** The script formats the ZRAM device with `ext2` by default. This is a lightweight filesystem suitable for temporary use.
* **Error Handling:** The script provides basic error messages. If `umount` fails due to "target is busy," use `lsof /ramdisk` or `fuser -vm /ramdisk` to find and stop processes using the RAM disk.

## License

This script is released under the MIT License.

```text
MIT License

Copyright (c) 2025 Cameron Walker - me@cameronwalker.nz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

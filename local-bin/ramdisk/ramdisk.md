# RAM Disk Management Script (ramdisk.py)

[![Python Version](https://img.shields.io/badge/python-3.x-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](#important-considerations)
[![Made with: Shell commands](https://img.shields.io/badge/made%20with-shell%20commands-red)](#script-workflow)

`ramdisk.py` is a Python script designed to simplify the creation and removal of a RAM disk on Linux systems using `tmpfs`. It provides a command-line interface to manage a RAM disk located at `/ramdisk`.

## Features

* **Create RAM Disk:** Easily create a RAM disk with a user-specified size (e.g., "4G" for 4 Gigabytes, "512M" for 512 Megabytes).
* **Remove RAM Disk:** Cleanly unmount and remove the RAM disk.
* **`tmpfs` Backend:** Utilizes `tmpfs`, which stores files in virtual memory, offering high-speed temporary storage.
* **Fixed Mount Point:** Operates on a predefined mount point: `/ramdisk`.
* **Directory Management:** Automatically creates the `/ramdisk` directory if it doesn't exist during creation and removes it during the removal process.
* **Sudo Requirement:** Enforces execution with `sudo` privileges, as mounting and unmounting filesystems are restricted operations.
* **Input Validation:** Includes basic validation for the size argument format.
* **Status Messages:** Provides informative messages about its operations and any errors encountered.

## Script Workflow

The script operates based on the command-line arguments provided.

### RAM Disk Creation (`sudo python3 ramdisk.py <size>`)

1.  **Sudo Check:** Verifies if the script is run with `sudo` privileges. Exits if not.
2.  **Argument Validation:**
    * Checks if a size argument is provided.
    * Validates the size format (must end with 'G' or 'M', preceded by a number).
3.  **Mount Point Check:** Ensures `/ramdisk` is not already a mount point. If it is, an error is shown, prompting the user to remove it first.
4.  **Directory Creation:**
    * If `/ramdisk` does not exist, it attempts to create the directory.
    * If `/ramdisk` exists but is not a directory, it exits with an error.
5.  **Mount `tmpfs`:**
    * Constructs and executes the `mount` command:
        `mount -t tmpfs -o size=<size_str> tmpfs /ramdisk`
    * Prints success or error messages based on the outcome of the mount operation. If mounting fails, it attempts to clean up by removing the `/ramdisk` directory if it was created by the script in the current run and is empty.

### RAM Disk Removal (`sudo python3 ramdisk.py remove`)

1.  **Sudo Check:** Verifies if the script is run with `sudo` privileges. Exits if not.
2.  **Argument Validation:** Ensures no extra arguments are passed with `remove`.
3.  **Unmount Operation:**
    * Checks if `/ramdisk` is currently mounted.
    * If mounted, it executes the `umount /ramdisk` command.
    * Prints success or error messages. If unmounting fails (e.g., resource busy), it advises checking for processes using the RAM disk.
4.  **Directory Removal:**
    * If `/ramdisk` exists and is a directory, it attempts to remove it using `os.rmdir()`. This will only succeed if the directory is empty (which it should be after a successful `tmpfs` unmount).
    * Prints success or error messages for directory removal.

## Installation

1.  **Save the Script:**
    Save the Python code provided into a file named `ramdisk.py`.

2.  **Make it Executable (Optional but Recommended):**
    Open your terminal and run:
    ```bash
    chmod +x ramdisk.py
    ```
    This allows you to run the script as `./ramdisk.py` instead of `python3 ramdisk.py`.

3.  **Dependencies:**
    * Python 3.x
    * Standard Linux command-line utilities (`mount`, `umount`). These are typically pre-installed on most Linux distributions.

## Usage Examples

**Note:** All commands must be run with `sudo`.

1.  **Create a 4GB RAM Disk:**
    ```bash
    sudo python3 ramdisk.py 4G
    ```
    If you made it executable:
    ```bash
    sudo ./ramdisk.py 4G
    ```

2.  **Create a 512MB RAM Disk:**
    ```bash
    sudo python3 ramdisk.py 512M
    ```
    If you made it executable:
    ```bash
    sudo ./ramdisk.py 512M
    ```

3.  **Verify RAM Disk Creation:**
    After creating the RAM disk, you can check if it's mounted and its size using:
    ```bash
    df -h /ramdisk
    ```
    Or:
    ```bash
    mount | grep /ramdisk
    ```

4.  **Remove the RAM Disk:**
    ```bash
    sudo python3 ramdisk.py remove
    ```
    If you made it executable:
    ```bash
    sudo ./ramdisk.py remove
    ```

## Important Considerations

* **Sudo Privileges:** The script *must* be run with `sudo` as it performs system-level operations like mounting and unmounting filesystems.
* **Linux Specific:** This script is designed for Linux systems due to its reliance on `tmpfs` and standard Linux `mount`/`umount` commands. It will not work on Windows or macOS without significant modifications.
* **Data Volatility:** Files stored in a `tmpfs` RAM disk reside in virtual memory. **All data will be lost if the RAM disk is unmounted or if the system is rebooted.** It is suitable for temporary files and speeding up I/O-intensive tasks where data persistence is not required.
* **Mount Point:** The script uses a hardcoded mount point `/ramdisk`. Ensure this path is suitable for your system and does not conflict with existing critical directories.
* **Error Handling:** The script includes basic error handling and will print messages to `stdout` or `stderr` if issues occur (e.g., mount failure, directory creation failure, permissions issues).
* **Resource Busy:** If you encounter an error during removal stating that the device is busy (`umount: /ramdisk: target is busy.`), it means some process is still using files or has its current working directory within `/ramdisk`. You'll need to identify and stop these processes before the RAM disk can be unmounted. Commands like `lsof /ramdisk` or `fuser -m /ramdisk` can help identify such processes.

## License

This script is released under the MIT License.

```text
MIT License

Copyright (c) 2025 [Your Name or Organization - if you wish to add]

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

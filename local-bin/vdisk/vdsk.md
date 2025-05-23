# Virtual Disk Utility (vdisk.py)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.6%2B-blue.svg)](https://www.python.org/downloads/)
[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg?style=flat)](CONTRIBUTING.md)
<!-- Examples:
[![Build Status](https://img.shields.io/travis/com/username/repo.svg)](https://travis-ci.com/username/repo)
[![Code Coverage](https://img.shields.io/codecov/c/github/username/repo.svg)](https://codecov.io/gh/username/repo)
[![Release Version](https://img.shields.io/github/v/release/username/repo.svg)](https://github.com/username/repo/releases)
-->

## Introduction

VDSK (Virtual Disk Script) is a Python utility designed to simplify the creation, mounting, unmounting, resizing, and conversion of virtual disk images on Linux systems. It aims to provide a command-line interface similar to common disk management tools, streamlining workflows for developers, testers, and system administrators who frequently work with disk images. This script is a Python port of an original bash utility, offering more robust error handling and argument parsing.

## Features

* **Create and Format:** Easily create new virtual disk images of a specified size and format them with various filesystems (ext2, ext3, ext4, xfs, btrfs, jfs, fat16, fat32, ntfs).
* **Mount/Unmount:** Mount disk images to specified mount points and unmount them.
* **Read-only Mount:** Option to mount filesystems in read-only mode.
* **Auto-mount (fstab):** Add entries to `/etc/fstab` for automatic mounting on system boot.
* **No Mount Option:** Create and format an image without immediately mounting it.
* **Resize:** Extend existing disk images and attempt to resize the filesystem (primarily for ext2/3/4).
* **Convert to ISO:** Convert a raw disk image file to an ISO ( `.iso` ) format, useful for optical media or virtual machine booting.
* **Filesystem Labels:** Assign labels to filesystems during creation.
* **Dependency Checks:** Basic checks for required tools like `genisoimage`, `mkfs.vfat`, etc.

## Script Workflow

The `vdsk.py` script operates based on the command-line arguments provided. Here's a general overview of its different modes of operation:

1.  **Initialization & Argument Parsing:**
    * The script uses Python's `argparse` module to define and parse command-line arguments.
    * It determines the desired operation (mount, umount, resize, convertiso) and associated options.

2.  **Umount Operation (`--umount`):**
    * Identifies if the specified image file is currently mounted.
    * If mounted, it unmounts the filesystem from its mount point.
    * Attempts to detach any associated loop devices.

3.  **Resize Operation (`--resize`):**
    * Checks if the image file exists.
    * Extends the image file to the new size using `dd` (or `truncate`).
    * Sets up a loop device for the image.
    * Runs `e2fsck` to check the filesystem (primarily for ext*).
    * Uses `resize2fs` to expand the filesystem to the new image size (primarily for ext*).
    * Detaches the loop device.
    * *Note: Filesystem resizing is most reliable for non-partitioned ext2/3/4 images.*

4.  **Convert to ISO Operation (`--convertiso`):**
    * Checks if the input image file exists.
    * Verifies that `genisoimage` is installed.
    * Determines the ISO label (either provided or attempts to read from the image).
    * Uses `genisoimage` to create the `.iso` file from the input image.

5.  **Mount/Create Operation (Default):**
    * **Argument Validation:** Ensures all required arguments (size, image file, mount point unless `--nomount`) are present.
    * **Mount Point Creation:** If the specified mount point doesn't exist (and not `--nomount`), it creates it.
    * **Existing Mount Check:** Checks if the image file is already mounted to prevent conflicts.
    * **Image File Creation (if new):**
        * If the image file doesn't exist, it's created using `dd` (sparse file).
        * The new image is then formatted with the specified filesystem (`--fs`) and label (`--label`).
        * Appropriate `mkfs.*` commands are invoked (e.g., `mkfs.ext4`, `mkfs.vfat`).
        * Includes checks for necessary formatting tools (e.g., `dosfstools` for FAT, `ntfs-3g` for NTFS).
    * **Mounting (if not `--nomount`):**
        * The image file is mounted to the specified mount point using a loop device.
        * Read-only option (`--readonly`) is applied if specified.
    * **Auto-Mount (`--auto-mount`):**
        * If requested, an entry is added to `/etc/fstab` to enable automatic mounting on boot.
    * **Ownership Changes:**
        * Adjusts ownership of the mount point or image file. Typically, the mount point (if mounted) or the image file itself (if not mounted or after unmounting) is set to the current user.

6.  **Error Handling & Cleanup:**
    * The script uses `try...except` blocks to catch errors during critical operations.
    * If an error occurs during the creation of a new image file before it's fully formatted and mounted, a cleanup function attempts to remove the partially created image file.
    * Commands are executed via `subprocess.run`, with checks for success and error reporting.

## Installation

1.  **Prerequisites:**
    * Python 3.6 or newer.
    * Standard Linux command-line utilities (`dd`, `mount`, `umount`, `losetup`, `mkdir`, `chown`, `blkid`).
    * Filesystem-specific tools (install as needed):
        * `e2fsprogs` (for ext2/3/4: `mkfs.ext[234]`, `e2fsck`, `resize2fs`) - usually installed by default.
        * `xfsprogs` (for XFS: `mkfs.xfs`).
        * `btrfs-progs` (for BTRFS: `mkfs.btrfs`).
        * `jfsutils` (for JFS: `mkfs.jfs`).
        * `dosfstools` (for FAT16/FAT32: `mkfs.vfat`).
        * `ntfs-3g` (for NTFS: `mkfs.ntfs` or `mkntfs`).
        * `genisoimage` (for ISO conversion, typically from the `genisoimage` or `cdrkit` package).

    You can typically install these using your distribution's package manager. For example, on Debian/Ubuntu:
    ```bash
    sudo apt update
    sudo apt install python3 dosfstools ntfs-3g genisoimage xfsprogs btrfs-progs jfsutils
    ```

2.  **Download the script:**
    Save the Python script as `vdsk.py` in your desired location.

3.  **Make it executable:**
    ```bash
    chmod +x vdsk.py
    ```

4.  **(Optional) Add to PATH:**
    For easier access, you can move `vdsk.py` to a directory in your system's PATH (e.g., `/usr/local/bin`) or add its location to your PATH environment variable.
    ```bash
    sudo mv vdsk.py /usr/local/bin/vdsk
    ```
    If you do this, you can run the script as `vdsk` instead of `./vdsk.py`. Remember to use `sudo vdsk` for operations requiring root privileges.

## Usage Examples

**Note:** Most operations require root privileges. Prefix commands with `sudo`.

1.  **Show Help:**
    ```bash
    ./vdsk.py --help
    # or if in PATH:
    # vdsk --help
    ```

2.  **Create and Mount a 1GB ext4 image:**
    ```bash
    sudo ./vdsk.py 1G /tmp/myimage.img /mnt/myvirtualdisk
    ```

3.  **Create and Mount with a specific label and filesystem (FAT32):**
    ```bash
    sudo ./vdsk.py --fs fat32 --label MYDATA 500M /srv/fatdisk.img /media/fatdrive
    ```

4.  **Create, format, but do not mount:**
    ```bash
    sudo ./vdsk.py --nomount --fs xfs --label ArchiveXL 10G /opt/images/archive.img
    ```

5.  **Mount an existing image read-only:**
    (Assuming `/tmp/myimage.img` was created previously and is formatted)
    ```bash
    sudo ./vdsk.py --readonly 0G /tmp/myimage.img /mnt/readonlydisk
    # Note: Size '0G' is a placeholder here as it's not creating a new image.
    # A better approach for mounting existing would be a dedicated --mount-existing flag if added.
    # For now, ensure the image exists; the script will skip creation if it does.
    ```

6.  **Unmount an image:**
    ```bash
    sudo ./vdsk.py --umount /tmp/myimage.img
    # or by specifying the mount point if the script is enhanced to support that
    ```

7.  **Resize an existing ext4 image to 2GB:**
    (Ensure the image is unmounted first for safety, though the script attempts loop setup)
    ```bash
    sudo ./vdsk.py --umount /tmp/myimage.img # Unmount if mounted
    sudo ./vdsk.py --resize 2G /tmp/myimage.img
    ```

8.  **Convert an image file to ISO:**
    ```bash
    sudo ./vdsk.py --convertiso /tmp/myimage.img /tmp/myimage.iso MyISOName
    ```

9.  **Create an image and add it to fstab for auto-mounting:**
    ```bash
    sudo ./vdsk.py --auto-mount --label BackupDisk 20G /var/backup_image.img /mnt/backup
    ```

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

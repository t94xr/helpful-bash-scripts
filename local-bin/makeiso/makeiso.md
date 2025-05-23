# MakeISO (`makeiso.py`)

[![Python 3.6+](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://shields.io/)
[![Depends on: mkisofs/genisoimage](https://img.shields.io/badge/depends%20on-mkisofs%2Fgenisoimage-lightgrey.svg)](https://shields.io/)

This document provides an overview, features, and usage instructions for the `makeiso.py` Python script, a utility designed to create ISO image files from a source directory.

## Features

* **ISO Creation:** Creates ISO image files using `mkisofs` (or its successor `genisoimage`) as the backend.
* **Multiple ISO Formats:**
    * Supports standard ISO9660 format with Joliet and RockRidge extensions for broad compatibility.
    * Supports UDF (Universal Disk Format) via the `--udf` flag, suitable for large files and modern systems/Blu-Ray.
* **Source Directory Flexibility:** Allows specifying a source directory, defaulting to the current working directory.
* **Customizable Output:** Users can define the output ISO filename and the internal Volume Label.
* **Media Size Pre-checks:**
    * Option to pre-check if the source content is likely to fit on various standard media types: CD, DVD, DVD-DL, Blu-Ray (25GB, 50GB, 100GB, 125GB).
    * The script uses conservative estimates for media capacities.
* **Automatic Media Selection:** Includes an `--autoselect-media` option to automatically determine the smallest suitable media type for the source data and perform a pre-check against it.
* **Post-Creation Analysis:**
    * Reports the final size of the created ISO image.
    * Indicates which standard media types the resulting ISO file would fit onto.
* **External Tool Path:** Allows specifying a custom path to the `mkisofs` or `genisoimage` executable.
* **User-Friendly Interface:** Provides detailed command-line help (`--help`) and informative progress messages.

## Script Workflow

The script generally follows these steps:

1.  **Argument Parsing:** Parses command-line arguments provided by the user (e.g., output name, source directory, format options, media check flags).
2.  **Initial Information Display:** Prints basic information about the operation, such as the source directory being processed, the intended output ISO name, and the selected filesystem type (standard or UDF).
3.  **Media Pre-check (if requested):**
    * If a specific media type (e.g., `--dvd`, `--br25`) or `--autoselect-media` is chosen:
        * Calculates the total size of the files and symlinks within the source directory.
        * If `--autoselect-media` is used, it determines the smallest standard media type that can accommodate the calculated size.
        * Compares the calculated size against the (selected or auto-selected) target media's capacity.
        * If the source content size exceeds the media capacity, the script prints an error message and exits before attempting ISO creation.
        * If the content is likely to fit, a confirmation message is displayed.
4.  **`mkisofs` Command Construction:** Assembles the appropriate `mkisofs` (or `genisoimage`) command based on the user's input and selected options (e.g., adding `-udf` if requested).
5.  **Command Execution:**
    * Displays the `mkisofs` command that will be executed.
    * Runs the `mkisofs` command as a subprocess.
6.  **Result Reporting:**
    * After `mkisofs` completes, the script reports whether the ISO creation was successful or if `mkisofs` encountered an error (including any output or error messages from `mkisofs`).
    * If successful:
        * Verifies the existence of the output ISO file.
        * Reports the final, actual size of the created ISO file in a human-readable format.
        * Lists all standard media types (CD, DVD, Blu-Ray, etc.) onto which the created ISO file could fit.

## Installation Notes

1.  **Python 3:** The script is written for Python 3. Ensure you have Python 3 installed on your system.
2.  **`mkisofs` Dependency:** This script relies on the `mkisofs` utility (or `genisoimage`, which is a common successor found in packages like `cdrtools` or `cdrkit` on Linux systems). This external program must be installed separately on your system and be accessible via your system's PATH.
    * On Debian/Ubuntu based systems, you can typically install it using:
        ```bash
        sudo apt-get update
        sudo apt-get install genisoimage
        ```
    * On Fedora/RHEL based systems:
        ```bash
        sudo dnf install genisoimage
        ```
    * For other systems, please consult your package manager.
3.  **Script File:**
    * Save the Python script code to a file, for example, `makeiso.py`.
    * On Unix-like systems (Linux, macOS), you can make the script directly executable:
        ```bash
        chmod +x makeiso.py
        ```
4.  **Python Libraries:** The script uses only standard Python libraries (`os`, `subprocess`, `argparse`, `sys`, `collections`) and does not require installation of additional Python packages via pip.

## Usage Examples

(Assuming the script is named `makeiso.py` and is executable/in PATH, or run with `python3 makeiso.py`)

1.  **Create a standard ISO from the current directory:**
    ```bash
    ./makeiso.py my_standard_archive
    ```
    *This will create `my_standard_archive.iso` with a standard ISO9660/Joliet/RockRidge filesystem.*

2.  **Create a UDF ISO from a specific source directory, pre-checking for a 50GB Blu-Ray:**
    ```bash
    ./makeiso.py important_backup --source_dir /mnt/data/project_x --udf --br50
    ```
    *This creates `important_backup.iso` in UDF format from `/mnt/data/project_x`.*

3.  **Create an ISO and automatically select the best media fit for pre-check:**
    ```bash
    ./makeiso.py photos_collection --autoselect-media
    ```
    *The script will determine if the contents of the current directory best fit a CD, DVD, BR25, etc., and pre-check against that size before creating `photos_collection.iso`.*

4.  **Create a UDF ISO without any specific media pre-check:**
    ```bash
    ./makeiso.py large_files_archive --udf
    ```
    *The script will create `large_files_archive.iso` and then report its size and suitable media.*

5.  **Specify path to `mkisofs` (if not in default PATH or using a specific version):**
    ```bash
    ./makeiso.py old_system_backup --mkisofs_path /opt/schily/bin/mkisofs --cd
    ```

6.  **View Help Information:**
    ```bash
    ./makeiso.py --help
    ```
    *Or run without any arguments to display the help message.*

## Important Considerations

* **`mkisofs`/`genisoimage` Backend:** The functionality of this script is entirely dependent on a working installation of `mkisofs` or `genisoimage`. Ensure it's correctly installed and accessible.
* **ISO Format Choice:**
    * **Standard (default):** ISO9660 with Joliet/RockRidge extensions is highly compatible across older and newer systems.
    * **UDF (`--udf`):** Recommended for Blu-Ray discs, ISOs larger than 4GB, very large individual files, or when broader compatibility with modern media players and operating systems for large storage is needed.
* **Size Estimations vs. Actual Size:** The pre-check feature calculates the sum of file sizes in the source directory. The actual final ISO size can be slightly different due to filesystem overhead, metadata, block padding by `mkisofs`, and how symlinks are stored. The script uses conservative estimates for media capacities to mitigate this.
* **File Permissions:** The script needs read permissions for the source directory and all its contents. It also requires write permission in the directory where the ISO file will be created (the current working directory by default).
* **Symbolic Links (Symlinks):** By default, `mkisofs` (when used with RockRidge extensions, as this script does) stores symbolic links *as links* within the ISO image, rather than archiving the content of the files they point to. The script's size calculation for pre-checks reflects this by summing the small size of the link itself, not the target's content.
* **Error Reporting:** The script attempts to catch common errors (e.g., `mkisofs` not found, source directory not readable) and will relay error messages or output from `mkisofs` if the ISO creation process fails.

## License (MIT License)

Copyright 2025 Cameron Walker - me@cameronwalker.nz

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

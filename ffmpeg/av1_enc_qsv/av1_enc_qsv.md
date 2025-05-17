# AV1 Batch Encoder (QSV) - `av1_enc_qsv.py`

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.x](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/downloads/)
[![Hardware Acceleration: Intel QSV](https://img.shields.io/badge/Hardware%20Acceleration-Intel%20QSV-orange.svg)](#)
[![Interface: Curses TUI](https://img.shields.io/badge/Interface-Curses%20TUI-lightgrey.svg)](#)

This script provides a Terminal User Interface (TUI) for batch encoding video files to AV1 format using FFmpeg with Intel Quick Sync Video (QSV) hardware acceleration. It's designed to process a folder of videos recursively, manage a queue, and provide real-time status updates.

## Features

* **Batch Video Encoding:** Processes all compatible video files within a specified directory and its subdirectories.
* **Intel QSV Hardware Acceleration:** Utilizes FFmpeg with Intel QSV for both decoding (where applicable) and AV1 encoding, significantly speeding up the process on compatible hardware.
* **Curses-based TUI:** Offers an interactive terminal interface to:
    * Display a scrollable list of all found video files.
    * Show real-time status for each file (e.g., Pending, Checking, Skipped, Transferring, Encoding, Ready, Success, Error, Cancelled, Deleted).
    * Color-code statuses for easy visual identification.
    * Allow users to scroll through the list (Up/Down arrows, PageUp/PageDown).
    * Cancel processing for selected files ('c' key).
* **Dynamic Codec Detection:** Uses `ffprobe` to check the input video codec and determine if a file is already AV1 (skipping it if so).
* **Pipelined Processing:** Implements a multi-threaded pipeline:
    * File scanning.
    * File preparation (codec checking, copying to a temporary directory).
    * FFmpeg encoding.
    This helps to keep the FFmpeg encoder busy by preparing subsequent files while the current one is encoding.
* **File Management:**
    * Copies files to a temporary directory for processing.
    * Replaces original files with their AV1 encoded versions upon successful completion.
    * Cleans up temporary files.
* **File Size Reporting:** Displays original file size, encoded AV1 file size, and percentage reduction for successful encodes.
* **FFmpeg Timeout:** Configurable timeout for individual FFmpeg encoding jobs to prevent stalls.
* **Conditional Deletion of Source Files:**
    * `--delete-zeros`: Optionally delete 0-byte source files found during the initial scan.
    * `--delete-errors`: Optionally delete source files that cause an `ffprobe` error.
* **Logging:**
    * Provides an in-TUI live log view (F2 key).
    * Generates detailed log messages about script operations and FFmpeg/ffprobe commands.
* **Help Screen:** In-TUI help (F1 key) for keybindings.
* **Configurable:** Key parameters like source/temp directories, QSV device, FFmpeg paths, and number of files to prepare can be set via variables within the script.

## Script Workflow

The script operates using a multi-threaded pipeline to efficiently manage video processing:

1.  **Scanner Thread (`file_scanner_worker`):**
    * Recursively scans the `SOURCE_DIRECTORY` for video files matching `VIDEO_EXTENSIONS`.
    * Checks for 0-byte files and deletes them if `--delete-zeros` is active.
    * Populates a global list of files (`all_files`) for UI display and a `pending_files_queue` for processing.

2.  **Preparer Thread (`file_preparer_worker`):**
    * Takes files from the `pending_files_queue`.
    * Uses `ffprobe` to check the video codec.
        * If already AV1, marks as `[SKIPPED]`.
        * If `ffprobe` fails and `--delete-errors` is active, marks as `[DELETED]` and removes the source file. Otherwise, marks as `[ERROR]`.
    * If suitable for encoding, copies the file to `TEMP_DIRECTORY`.
    * Adds the prepared file to the `ready_for_encode_queue`.
    * Aims to keep `NUM_FILES_TO_PREPARE` files in the ready/preparing state.

3.  **Encoder Thread (`ffmpeg_encoder_worker`):**
    * Takes one file at a time from the `ready_for_encode_queue`.
    * Constructs and executes the `ffmpeg` command using QSV for AV1 encoding.
    * Monitors for a configurable `FFMPEG_ENCODE_TIMEOUT_SECONDS`. If timeout occurs, the process is terminated, and the file is marked as `[ERROR] FFmpeg Timeout`.
    * **On Success:**
        1.  Deletes the temporary source copy.
        2.  Renames the temporary encoded AV1 file.
        3.  Moves the final AV1 file back to the original source directory, replacing the original.
        4.  Marks as `[SUCCESS]`.
    * **On FFmpeg Error/Timeout/User Cancel:**
        1.  Marks with appropriate status (`[ERROR]`, `[CANCELLED]`).
        2.  Cleans up associated temporary files.
        3.  Original source file is *not* deleted for FFmpeg errors or timeouts (unless `--delete-errors` specifically targeted an `ffprobe` failure earlier).

4.  **Main Thread (Curses UI):**
    * Manages the curses-based TUI, displaying file lists, statuses, and the bottom status panel.
    * Handles user input (scrolling, cancellation, help/log toggles, quitting).
    * Refreshes the UI periodically and when worker threads signal updates.
    * On exit, initiates cleanup of any remaining files in the `TEMP_DIRECTORY`.

## Installation Notes

### Dependencies

* **Python 3:** The script is written for Python 3.x. The `curses` module is part of the standard library on Unix-like systems.
* **FFmpeg & FFprobe:** You must have FFmpeg and FFprobe installed and accessible in your system's PATH, or their paths must be correctly specified in the script's configuration variables. They need to be compiled with support for Intel QSV and AV1 encoding (e.g., `av1_qsv` encoder).

### Download

* Save the script as `av1_enc_qsv.py`.
* Make it executable: `chmod +x av1_enc_qsv.py`

```bash
sudo wget -O /usr/local/bin/av1_enc.py https://raw.githubusercontent.com/t94xr/helpful-scripts/refs/heads/main/ffmpeg/av1_enc_qsv/av1_enc_qsv.py
sudo chmod +x /usr/local/bin/av1_enc.py
```

### Configurable Variables

The following variables at the top of the `av1_enc_qsv.py` script can be configured:

* `SOURCE_DIRECTORY`: Directory containing videos to process (default: ".").
* `TEMP_DIRECTORY`: Temporary storage for processing (SSD/Ramdisk recommended, default: "/ssd/av1_tmp/").
* `NUM_FILES_TO_PREPARE`: Number of files to copy to temp before encoding (default: 2).
* `NUM_FFMPEG_WORKERS`: Number of concurrent FFmpeg processes (default: 1, recommended for single QSV encoder).
* `QSV_DEVICE`: Path to your Intel QSV render device (default: "/dev/dri/renderD128").
* `FFMPEG_PATH`: Path to FFmpeg executable (default: "ffmpeg").
* `FFPROBE_PATH`: Path to FFprobe executable (default: "ffprobe").
* `LOG_MAX_LINES`: Maximum lines for the in-TUI log view (default: 300).
* `VIDEO_EXTENSIONS`: Tuple of video file extensions to process.
* `FFMPEG_ENCODE_TIMEOUT_SECONDS`: Timeout for individual FFmpeg encodes (default: 600 seconds / 10 minutes). Set to 0 or less to disable.

## Usage Example

Run the script from your terminal:

```bash
./av1_enc_qsv.py
```

To delete 0-byte files found during the scan:
```bash
./av1_enc_qsv.py --delete-zeros
```

To delete source files that cause an ffprobe error:
```bash
./av1_enc_qsv.py --delete-errors
```

You can combine flags:
```bash
./av1_enc_qsv.py --delete-zeros --delete-errors
```
```
Interactive Controls (within the TUI):
Up/Down Arrows: Scroll through the file list.
PageUp/PageDown: Scroll by a page.
'c' or 'C': Marks the highlighted file for cancellation.
```


## Important Considerations

> [!CAUTION]
> 
> **DATA LOSS WARNING**
> 
> This script is designed to **REPLACE THE ORIGINAL VIDEO FILES** with the newly encoded AV1 versions in the same location and with the same filename. Ensure you have **VERIFIED BACKUPS** of your media files before running this script. The author is not responsible for any data loss.

> [!TIP]
> 
> **Use NVMe for TEMP_DIRECTORY**
> 
> Encoding involves heavy I/O operations (reading the source, writing the temporary encoded file). Using a fast drive, ideally an NVMe SSD, for the `TEMP_DIRECTORY` can significantly improve encoding speed and reduce strain on slower drives.
>
> If your system/server has enough memory, [set up a ramdisk](//linuxbabe.com/command-line/create-ramdisk-linux) of about 10-16 GB, depending on your video file sizes for far more efficient IO.

* **BACKUP YOUR VIDEOS:** This script replaces original files with encoded versions upon successful completion. **It is strongly recommended to back up your important video files before running it.**
* **FFmpeg/FFprobe Installation:** Ensure `ffmpeg` and `ffprobe` are correctly installed, in your PATH, and compiled with the necessary QSV and AV1 support.
* **Intel QSV Hardware:** Your system must have compatible Intel hardware with QSV support. Verify the `QSV_DEVICE` path.
* **Temporary Directory Space:** The `TEMP_DIRECTORY` needs sufficient free space to hold copies of the videos being processed and their encoded outputs. An SSD is recommended for performance.
* **Permissions:** The script requires read permissions for the source directory and read/write/delete permissions for the temporary directory and the source directory (for replacing files).
* **Error Handling:** While the script attempts to handle errors gracefully, unexpected issues with specific files or `ffmpeg` configurations can occur. Check the log (F2) for details.
* **`--delete-errors` and `--delete-zeros` flags:** Use these flags with caution, as they will permanently delete original source files under the specified conditions.

## Example Output

The script provides a dynamic curses-based TUI:

![](av1_enc_qsv.gif)
*(This is a placeholder for your GIF. Ensure `av1_enc_qsv.gif` is in the same directory as this README or update the path.)*

The interface shows:
* A header with keybindings and an optional FFmpeg timeout countdown.
* A summary of file statuses.
* A scrollable list of video files with their:
  * Filename.
  * Original size, encoded AV1 size, and percentage reduction (for successful encodes).
  * Current processing status (color-coded).
* A bottom panel showing the currently encoding file and the next two files in the ready queue.
* An optional live log view.

## License

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

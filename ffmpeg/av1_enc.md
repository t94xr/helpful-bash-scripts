# AV1 Batch Encoder to AV1 (QSV)

A Python script to recursively find video files in a directory, encode them to AV1 using Intel Quick Sync Video (QSV) hardware acceleration, replace the original files with the smaller AV1 versions, and provide a live, scrollable curses UI for monitoring progress.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.x](https://img.shields.io/badge/Python-3.x-blue.svg)
![Hardware Acceleration](https://img.shields.io/badge/Hardware%20Accel-Intel%20QSV-blueviolet.svg)

---

## Features

* **Batch Processing:** Encodes multiple video files concurrently using a configurable thread pool.
* **Intel QSV Acceleration:** Leverages Quick Sync Video hardware for fast decoding and encoding.
* **Recursive Scanning:** Finds video files in the source directory and all its subdirectories.
* **In-Place Replacement:** Replaces the original video files with the newly encoded AV1 versions, preserving the directory structure. (**See Important Considerations below**)
* **Live Curses UI:** Provides a real-time, interactive terminal interface to monitor the status, size reduction, and logs of each file being processed, with vertical scrolling using arrow keys.
* **Metrics Logging:** Saves encoding details (original/encoded size, reduction, time, command) to a JSON file.
* **Codec Detection:** Uses `ffprobe` to determine the input video codec and selects the appropriate QSV decoder.
* **AV1 Skip:** Automatically skips files that are already encoded with the AV1 codec.
* **Error Handling:** Attempts to catch and log errors during file operations and encoding processes.
* **Temporary Files:** Uses a temporary directory for intermediate files during encoding.
* **No CPU Fallback:** CPU av1 encoding is power hungry - this will only encode on the av1 encode supported gpu _(Im using an Arc A380)_

## Script Workflow

1.  Perform pre-flight checks for dependencies (`ffmpeg`, `ffprobe`), QSV device, and directory permissions.
2.  Load existing metrics data from the metrics file.
3.  Recursively find all supported video files in the `SOURCE_DIRECTORY`.
4.  Populate an internal list of files to process.
5.  Start a separate thread for the Curses UI display.
6.  Start a `ThreadPoolExecutor` for concurrent encoding jobs.
7.  For each video file:
    * Determine the video codec using `ffprobe`.
    * If the codec is already AV1, mark as skipped and log it.
    * If the codec is supported by a QSV decoder:
        * Copy the original file to the `TEMP_DIRECTORY`.
        * Run `ffmpeg` using QSV for decoding and AV1 encoding, saving the output to the `TEMP_DIRECTORY`.
        * Verify the encoded file exists and contains an AV1 stream using `ffprobe`.
        * Calculate size reduction.
        * **Atomically replace the original file** in its location with the new AV1 file.
        * Clean up the temporary copied original file and the temporary encoded file.
        * Record metrics for the file (success/failure, sizes, time).
    * If the codec is not supported or ffprobe fails, mark as error.
    * Update the status of the file in the UI.
8.  The UI thread continuously updates the terminal display based on status changes and user scrolling input.
9.  After all files are processed (or upon user quit 'q'):
    * Wait for worker threads to finish current tasks.
    * Save the accumulated metrics data to the JSON file.
    * Signal the UI thread to exit.
    * Perform minor cleanup of the temporary directory if empty.
10. Exit the script.

## Installation

1.  **Dependencies:**
    * Python 3.x
    * `ffmpeg` with Intel QSV support enabled.
    * `ffprobe` (usually included with `ffmpeg`).
    * Intel Graphics Drivers and iHD driver (for QSV). Ensure your system recognizes `/dev/dri/card0` (or the path specified in `QSV_DEVICE`).
    * `curses` library (standard in most Python installations, but might require `ncurses-devel` or similar packages on some Linux distributions).

2.  **Download:**
    Download the script and make it executable.
    
    ```bash
    sudo wget -O /usr/local/bin/av1_enc.py https://raw.githubusercontent.com/t94xr/helpful-scripts/refs/heads/main/ffmpeg/av1_enc.py
    sudo chmod +x /usr/local/bin/av1_enc.py
    ```

4.  **Configure Variables:**
    Edit the configuration variables at the top of the downloaded script file (`/usr/local/bin/av1_qsv_encoder.py`) using a text editor like `nano` or `vim` to match your system and preferences:

    ```python
    # --- Configuration ---
    SOURCE_DIRECTORY = Path(".")  # Directory to scan ('.' for current, or e.g., '/mnt/videos')
    TEMP_DIRECTORY = Path("/ssd/av1_tmp") # Temporary space for encoding (SSD/NVMe recommended)
    METRICS_FILE = Path("/ssd/av1_enc.json") # File to save encoding results
    MAX_CONCURRENT_JOBS = 15  # Max number of ffmpeg processes at a time (adjust based on CPU/QSV capability)
    FFMPEG_PATH = "ffmpeg"  # Path to ffmpeg executable if not in PATH
    FFPROBE_PATH = "ffprobe" # Path to ffprobe executable if not in PATH
    QSV_DEVICE = "/dev/dri/card0" # QSV device path (verify on your system)

    # Supported video extensions (lowercase)
    VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.ts', '.mpg', '.mpeg')

    # ... rest of the script ...
    ```
    * **`SOURCE_DIRECTORY`**: The root directory to start scanning for videos. Defaults to the current directory (`.`).
    * **`TEMP_DIRECTORY`**: A directory to store temporary files during encoding. Needs significant free space.
    * **`METRICS_FILE`**: The path where the JSON metrics file will be saved.
    * **`MAX_CONCURRENT_JOBS`**: Controls how many `ffmpeg` processes run simultaneously. Adjust based on your CPU core count and QSV capabilities. Too many jobs might saturate QSV or other system resources.
    * **`FFMPEG_PATH`** / **`FFPROBE_PATH`**: Set these if `ffmpeg` or `ffprobe` are not in your system's PATH.
    * **`QSV_DEVICE`**: The path to your Intel QSV device node. `/dev/dri/card0` is common, but verify on your system.


## Usage Examples

Run the script from your terminal. Navigate to the directory containing your videos first, or set the `SOURCE_DIRECTORY` variable in the script.

cd the directory containing your videos:
```bash
av1_enc.py
```
If you have issues,
```bash
python3 /usr/local/bin/av1_enc.py
```
The script will start, scan for files, and launch the curses UI. Use the Up Arrow and Down Arrow keys to scroll through the file list. Press 'q' to initiate a graceful shutdown (allows current jobs to finish).

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

* **Backups:** Seriously, make backups. This script will overwrite your originals.
* **Disk Space:** The `TEMP_DIRECTORY` will temporarily hold copies of the source files and the encoded files simultaneously during processing. Ensure you have enough free space on the drive hosting the temporary directory, at least equal to the size of the largest video file being processed concurrently by `MAX_CONCURRENT_JOBS`. The source drive also needs space as files are processed sequentially (copying out, then moving the new file back in).
* **Encoding Time:** Encoding speed is primarily dependent on your Intel CPU's QSV capabilities and the complexity of the video. The script uses default `av1_qsv` settings which aim for a balance of speed and quality; you may need to modify the `ffmpeg` command in the script for specific quality/bitrate targets if needed.
* **Quality Parameters:** The current `ffmpeg` command uses the default `av1_qsv` encoder settings (`-c:v av1_qsv`). For more control over quality vs. file size, you would need to add options like `-global_quality <0-255>` (for VBR mode) or other QSV-specific AV1 options to the `ffmpeg` command in the `run_ffmpeg_encode` function. Refer to the `ffmpeg` QSV AV1 encoder documentation.
* **QSV Compatibility:** Quick Sync Video is an Intel-specific hardware acceleration technology. This script will *only* work if your system has a compatible Intel CPU with integrated graphics that supports QSV, the necessary drivers are installed, and the QSV device node (`/dev/dri/cardX`) is accessible.

## Example Output

![](av1_enc_demo.gif)

## License
Copyright 2025 Cameron Walker - me@cameronwalker.nz

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


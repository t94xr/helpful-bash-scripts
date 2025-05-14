# AV1 Encoding Script (av1_enc.py)

**Licence:** MIT Licence

## Features
- **Automated AV1 Encoding:** This program recursively scans your specified directory (and subfolders) for common video files and automatically converts them to the efficient AV1 codec.
- **Multi-threaded Processing:** Leverages multi-core processors by encoding up to 4 videos in parallel, significantly speeding up the overall conversion process.
- **AV1 Skip Logic:** Intelligently detects files that are already encoded in AV1 and skips them, preventing unnecessary re-encoding.
- **In-Place Replacement:** After successful encoding, the original video file in its source folder is replaced with the newly created AV1 file (with the .mkv extension).
- **Real-time Colored Output:** Provides clear and informative feedback directly in your terminal, using colors to distinguish between successful operations (green), errors (red), and skipped files (yellow).
- **Detailed Logging:** Records all processing steps, including file sizes and compression ratios, in a log file for later review.
- **FFmpeg with QSV Acceleration:** Designed to utilize Intel's Quick Sync Video (QSV) for hardware-accelerated encoding, potentially leading to faster processing on compatible systems.
- **Handles Common Video Formats:** It supports a wide range of input video formats, including .mp4, .mkv, .avi, .mov, .wmv, .flv, and .webm.
- **Size Reduction Reporting:** Displays the size reduction percentage achieved after encoding, allowing you to see the benefits of AV1 compression.

## AV1 Encoding Script Workflow

Here's a breakdown of the steps the script performs for each video file it processes:

1.  **Initialization:**
    * Sets up constants for the log file, AV1 extension, screen output preference, and the number of threads.
    * Defines helper functions for formatting file sizes, getting file sizes, and colored terminal output.

2.  **File Discovery:**
    * The `main()` function uses `os.walk(".")` to recursively find all files within the current directory and its subfolders.
    * It filters these files based on a list of common video file extensions (`.mp4`, `.mkv`, `.avi`, etc.).

3.  **Parallel Processing:**
    * For each discovered video file, a new thread is created to execute the `process_video_file` function.
    * The script limits the number of concurrently running threads to the value specified by `NUM_THREADS`.

4.  **`process_video_file()` Function - Per File Steps:**
    * **Initialization:** Creates the temporary directory `/ssd/av1_tmp/` if it doesn't exist. Constructs paths for the source file in the temporary directory and the final AV1 file.
    * **Logging Start:** Logs the start of processing for the current file.
    * **Get Source Size:** Determines and logs the size of the original video file.
    * **Detect Input Codec:** Uses `ffprobe` to identify the video codec of the input file. If the codec cannot be determined, the script skips the file.
    * **Skip AV1 Files:** If the detected input codec is already `av1`, the script logs a message and moves on to the next file.
    * **Copy to Temporary Directory:** The original video file is copied to `/ssd/av1_tmp/`.
    * **Encode to AV1:** FFmpeg is called to encode the video file in the temporary directory to AV1 format (`filename.mkv` in `/ssd/av1_tmp/`). Hardware acceleration (QSV) is attempted.
    * **Logging Encoding Results:** If encoding is successful, the size of the encoded file and the size reduction percentage are calculated and logged.
    * **Delete Temporary Source:** The original video file in `/ssd/av1_tmp/` is deleted.
    * **Move Back to Source Folder:** The newly encoded AV1 file is moved from `/ssd/av1_tmp/` back to the original directory of the source file, **replacing** the original file.
    * **Logging Completion:** Logs the successful move of the encoded file.
    * **Error Handling:** Includes `try...except` blocks to catch potential errors during file operations (copying, encoding, moving, deleting) and logs these errors.
    * **Cleanup (on Encoding Failure):** If encoding fails for a file, any temporary copy in `/ssd/av1_tmp/` is cleaned up.

5.  **Completion:**
    * The `main()` function waits for all threads to finish processing.
    * A final "encoding process complete" message is logged and printed.

Throughout the process, messages are printed to the terminal with color-coding (green for success, red for errors, yellow for skipped AV1 files) to provide real-time feedback. All actions are also recorded in the specified log file.

## Prerequisites

* **Python 3:** Ensure Python 3 is installed. Check with `python3 --version`.
* **FFmpeg:** FFmpeg is required for encoding and probing. Install it if you haven't:
    * **Debian/Ubuntu:** `sudo apt update && sudo apt install ffmpeg`
    * **macOS (Homebrew):** `brew install ffmpeg`
    * **Windows:** Download from the FFmpeg website and add its `bin` directory to your PATH.
* **Intel QSV (Optional):** For hardware acceleration, you need an Intel CPU with QSV support and the necessary drivers. Linux users should ensure `/dev/dri/renderD128` (or similar) exists.

## Script Setup

1.  **Save the Script:** Save the Python script code as a `.py` file (e.g., `av1_encoder.py`).
2.  **Make Executable (Linux/macOS):** In your terminal, navigate to the script's directory and run:
    ```bash
    chmod +x av1_encoder.py
    ```
3.  **Adjust Configuration (Optional):**
    * **`LOG_FILE`:** Change the log file path if needed (default: `/ssd/av1_tmp/log.txt`).
    * **`AV1_EXTENSION`:** Modify the output AV1 file extension (default: `.mkv`).
    * **`PRINT_TO_SCREEN`:** Set to `False` to disable colored terminal output.
    * **`NUM_THREADS`:** Adjust the number of parallel encoding jobs (default: 4).

## Usage Examples

**Basic Usage (from the directory containing videos):**

1.  Open your terminal.
2.  Navigate to the directory with your video files (and subfolders).
3.  Run the script:
    ```bash
    python3 av1_encoder.py
    ```
    or
    ```bash
    ./av1_encoder.py
    ```
    The script will:
    * Scan the current directory and subfolders.
    * For each non-AV1 video:
        * Copy to `/ssd/av1_tmp/`.
        * Encode to AV1 (`.mkv` in `/ssd/av1_tmp/`).
        * Delete the original from `/ssd/av1_tmp/`.
        * Move the AV1 file back to the original directory, replacing the source.
    * Provide colored terminal output and log details.

**Running from a Different Directory:**

The current script version is designed to be run from the root of your video library due to its use of `os.walk(".")`. To process a specific directory from elsewhere, you would need to modify the `main()` function to accept a command-line argument for the target directory.

## Important Considerations

> [!CAUTION]
> This script will delete the original media in a given directory it's run on - it will delete the file AFTER a successful encode.

> [!TIP]
> I highly recommend the "tmp" directory be a mounted NVME _(if possible)_ SSD, the fastest storage available to you for more efficient encoding.
> This script was designed to encode files from a mounted network location, copy them to the SSD, encode them, delete the source file, move back the av1 encoded file.

* **Backup:** **Crucially, back up your video files** before running the script, as it replaces the originals.
* **Disk Space:** Ensure sufficient free space in `/ssd/av1_tmp/` for temporary storage.
* **Encoding Time:** AV1 encoding is resource-intensive and can take time.
* **Quality:** The default encoding parameters are used. Adjust the `encode_av1` function for specific quality needs (e.g., using `-crf`).
* **QSV Compatibility:** If QSV is not available, software encoding will be used, which is slower. Check the output for QSV-related messages.


## Example Output

```
[2025-05-15 09:30:00] Starting AV1 encoding process with 4 threads. [BLUE]Starting AV1 encoding process with 4 threads.[RESET]
[Thread 140737008879360] Processing: ./Movies/Action/old_movie.mp4
[Thread 140737008879360] Source file size: 1.20 GB
[Thread 140737008879360] Detected input codec: h264
[Thread 140737008879360] Copied to: /ssd/av1_tmp/old_movie.mp4 [BLUE]Copied to: /ssd/av1_tmp/old_movie.mp4[RESET]
[Thread 140737008879360] Encoded file size: 450.56 MB [GREEN]Encoded file size: 450.56 MB[RESET]
[Thread 140737008879360] Size reduction: 62.45% [GREEN]Size reduction: 62.45%[RESET]
[Thread 140737008879360] Deleted source file in temp: /ssd/av1_tmp/old_movie.mp4 [BLUE]Deleted source file in temp: /ssd/av1_tmp/old_movie.mp4[RESET]
[Thread 140737008879360] Moved to destination: ./Movies/Action/old_movie.mkv [GREEN]Moved to destination: ./Movies/Action/old_movie.mkv[RESET]
[Thread 140737000493824] Processing: ./TV Shows/Drama/episode1.mkv
[Thread 140737000493824] Source file size: 800.10 MB
[Thread 140737000493824] Detected input codec: hevc
[Thread 140737000493824] Copied to: /ssd/av1_tmp/episode1.mkv [BLUE]Copied to: /ssd/av1_tmp/episode1.mkv[RESET]
[Thread 140737000493824] Encoded file size: 300.90 MB [GREEN]Encoded file size: 300.90 MB[RESET]
[Thread 140737000493824] Size reduction: 62.39% [GREEN]Size reduction: 62.39%[RESET]
[Thread 140737000493824] Deleted source file in temp: /ssd/av1_tmp/episode1.mkv [BLUE]Deleted source file in temp: /ssd/av1_tmp/episode1.mkv[RESET]
[Thread 140737000493824] Moved to destination: ./TV Shows/Drama/episode1.mkv [GREEN]Moved to destination: ./TV Shows/Drama/episode1.mkv[RESET]
[Thread 140736992108288] Processing: ./AlreadyAV1.mkv
[Thread 140736992108288] Source file size: 500.00 MB
[Thread 140736992108288] Detected input codec: av1
[Thread 140736992108288] Input codec is already AV1. Skipping file. [YELLOW]Input codec is already AV1. Skipping file.[RESET]
[2025-05-15 09:35:00] AV1 encoding process complete. [BLUE]AV1 encoding process complete.[RESET]
```


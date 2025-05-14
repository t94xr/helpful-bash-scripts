#!/usr/bin/env python3

import os
import shutil
import subprocess
import glob
import json
from datetime import datetime
import threading
import queue

LOG_FILE = "/ssd/av1_tmp/log.txt"
AV1_EXTENSION = ".mkv"
PRINT_TO_SCREEN = True  # Set to True to enable screen output
NUM_THREADS = 4  # You can adjust the number of threads here

# ANSI escape codes for colors
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def colored(text, color):
    """Returns text colored with ANSI escape codes."""
    return f"{color}{text}{RESET}"

def format_size(size_bytes):
    """
    Converts bytes to a human-readable format (MB or GB).

    Args:
        size_bytes (int): The size in bytes.

    Returns:
        str: The size in a human-readable format with units.
    """
    if size_bytes >= 1024**3:
        size = size_bytes / (1024**3)
        unit = "GB"
    elif size_bytes >= 1024**2:
        size = size_bytes / (1024**2)
        unit = "MB"
    else:
        size = size_bytes / 1024
        unit = "KB"  # Added KB for smaller files
    return f"{size:.2f} {unit}"

def get_file_size_bytes(filepath):
    """
    Returns the size of a file in bytes.

    Args:
        filepath (str): Path to the file.

    Returns:
        int: The size of the file in bytes.
    """
    try:
        return os.path.getsize(filepath)
    except FileNotFoundError:
        return 0

def get_video_codec_from_ffprobe(filepath):
    """
    Uses ffprobe to determine the video codec of a file.

    Args:
        filepath (str): Path to the video file.

    Returns:
        str: The video codec string (e.g., 'h264', 'hevc', 'av1'), or None if not found.
    """
    ffprobe_cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        filepath
    ]
    try:
        result = subprocess.run(ffprobe_cmd, capture_output=True, check=True, text=True)
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                return stream.get("codec_name")
        return None
    except subprocess.CalledProcessError as e:
        log_and_print(colored(f"[Thread {threading.get_ident()}] Error running ffprobe on {filepath}: {e}\nFFprobe stderr: {e.stderr.decode()}", RED))
        return None
    except json.JSONDecodeError as e:
        log_and_print(colored(f"[Thread {threading.get_ident()}] Error decoding ffprobe output for {filepath}: {e}", RED))
        return None

def encode_av1(input_file, output_dir, input_codec):
    """
    Encodes a video file to AV1 using ffmpeg with QSV acceleration,
    using the determined input codec.

    Args:
        input_file (str): Path to the input video file.
        output_dir (str): Directory to store the temporary output file.
        input_codec (str): The video codec of the input file.

    Returns:
        str: Path to the encoded AV1 file, or None if encoding failed.
    """
    output_file = os.path.join(output_dir, os.path.splitext(os.path.basename(input_file))[0] + "_temp_av1" + AV1_EXTENSION)
    ffmpeg_input_codec_flag = f"{input_codec}_qsv" if input_codec in ("h264", "hevc") else input_codec
    ffmpeg_cmd = [
        "ffmpeg",
        "-hwaccel", "qsv",
        "-qsv_device", "/dev/dri/renderD128",
        "-c:v", ffmpeg_input_codec_flag,
        "-i", input_file,
        "-c:v", "av1_qsv",
        output_file
    ]
    try:
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
        return output_file
    except subprocess.CalledProcessError as e:
        log_and_print(colored(f"[Thread {threading.get_ident()}] Error encoding {input_file}: {e}\nFFmpeg stderr: {e.stderr.decode()}", RED))
        return None

def log_message(message):
    """
    Logs a message to the specified log file with a timestamp.

    Args:
        message (str): The message to log.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {message}\n")

def print_message(message, color=RESET):
    """
    Prints a message to the screen with the specified color if PRINT_TO_SCREEN is True.

    Args:
        message (str): The message to print.
        color (str): ANSI escape code for the color. Defaults to RESET.
    """
    if PRINT_TO_SCREEN:
        print(f"{color}{message}{RESET}")

def log_and_print(message, color=RESET):
    """
    Logs a message to the file and prints it to the screen with the specified color.

    Args:
        message (str): The message to log and print.
        color (str): ANSI escape code for the color. Defaults to RESET.
    """
    log_message(message)
    print_message(message, color)

def process_video_file(filepath, queue):
    """
    Processes a single video file:
    1. Copies to /ssd/av1_tmp/
    2. Encodes to filename.mkv in /ssd/av1_tmp/
    3. Deletes source in /ssd/av1_tmp/
    4. Moves the encoded file back to the source folder.

    Args:
        filepath (str): Path to the video file.
        queue (queue.Queue): Queue for putting processed filepaths (optional).
    """
    temp_dir = "/ssd/av1_tmp/"
    os.makedirs(temp_dir, exist_ok=True)
    source_filename = os.path.basename(filepath)
    temp_source_path = os.path.join(temp_dir, source_filename)
    filename_base, source_ext = os.path.splitext(source_filename)
    final_encoded_name = f"{filename_base}{AV1_EXTENSION}"
    final_encoded_path = os.path.join(temp_dir, final_encoded_name)

    log_and_print(f"[Thread {threading.get_ident()}] Processing: {filepath}")
    source_size_bytes = get_file_size_bytes(filepath)
    source_size_human = format_size(source_size_bytes)
    log_and_print(f"[Thread {threading.get_ident()}] Source file size: {source_size_human}")

    input_codec = get_video_codec_from_ffprobe(filepath)
    if not input_codec:
        log_and_print(colored(f"[Thread {threading.get_ident()}] Could not determine video codec for {filepath}. Skipping.", RED))
        return

    log_and_print(f"[Thread {threading.get_ident()}] Detected input codec: {input_codec}")

    # --- SKIP IF INPUT CODEC IS AV1 ---
    if input_codec == "av1":
        log_and_print(colored(f"[Thread {threading.get_ident()}] Input codec is already AV1. Skipping file.", YELLOW))
        return
    # --- END OF SKIP LOGIC ---

    # 1. Copy to /ssd/av1_tmp/
    try:
        shutil.copy2(filepath, temp_source_path)
        log_and_print(f"[Thread {threading.get_ident()}] Copied to: {temp_source_path}", BLUE)
    except Exception as e:
        log_and_print(colored(f"[Thread {threading.get_ident()}] Error copying {filepath} to temp: {e}", RED))
        return

    # 2. Encode in /ssd/av1_tmp/
    encoded_filepath = encode_av1(temp_source_path, temp_dir, input_codec)
    if encoded_filepath:
        encoded_size_bytes = get_file_size_bytes(encoded_filepath)
        encoded_size_human = format_size(encoded_size_bytes)
        log_and_print(f"[Thread {threading.get_ident()}] Encoded file size: {encoded_size_human}", GREEN)

        if source_size_bytes > 0:
            reduction_percentage = ((source_size_bytes - encoded_size_bytes) / source_size_bytes) * 100
            log_and_print(f"[Thread {threading.get_ident()}] Size reduction: {reduction_percentage:.2f}%", GREEN)
        else:
            log_and_print(f"[Thread {threading.get_ident()}] Source file size was zero, cannot calculate reduction for {filepath}.")

        # 3. Delete source in /ssd/av1_tmp/
        try:
            os.remove(temp_source_path)
            log_and_print(f"[Thread {threading.get_ident()}] Deleted source file in temp: {temp_source_path}", BLUE)
        except Exception as e:
            log_and_print(colored(f"[Thread {threading.get_ident()}] Error deleting source in temp: {e}", RED))

        # 4. Move the encoded file back to the source folder (renaming happens during the move)
        destination_path = os.path.join(os.path.dirname(filepath), final_encoded_name)
        try:
            shutil.move(encoded_filepath, destination_path)
            log_and_print(colored(f"[Thread {threading.get_ident()}] Moved to destination: {destination_path}", GREEN))
            if queue:
                queue.put(filepath)  # Indicate successful processing
        except Exception as e:
            log_and_print(colored(f"[Thread {threading.get_ident()}] Error moving to destination: {e}", RED))

    else:
        log_and_print(colored(f"[Thread {threading.get_ident()}] Encoding failed for {filepath}.", RED))

def main():
    """
    Recursively finds all video files and processes them in parallel using threads,
    following the specified workflow with colored output.
    """
    video_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm')  # Add more if needed
    filepaths = []
    for root, _, files in os.walk("."):
        for file in files:
            if file.lower().endswith(video_extensions):
                filepaths.append(os.path.join(root, file))

    # Ensure the log directory exists
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    log_and_print(colored(f"Starting AV1 encoding process with {NUM_THREADS} threads.", BLUE))

    threads = []
    file_queue = queue.Queue()

    for filepath in filepaths:
        thread = threading.Thread(target=process_video_file, args=(filepath, file_queue))
        threads.append(thread)
        thread.start()
        if len(threads) >= NUM_THREADS:
            for thread in threads:
                thread.join()  # Wait for the current batch of threads to complete
            threads = []  # Reset the list for the next batch

    # Wait for any remaining threads
    for thread in threads:
        thread.join()

    log_and_print(colored("AV1 encoding process complete.", BLUE))

if __name__ == "__main__":
    main()

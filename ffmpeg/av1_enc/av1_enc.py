#!/usr/bin/env python3
import curses
import os
import shutil
import subprocess
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError
from pathlib import Path
import queue

# --- Configuration ---
SOURCE_DIRECTORY = Path(".")  # Current directory (can be changed to an absolute path)
TEMP_DIRECTORY = Path("/ssd/av1_tmp")
METRICS_FILE = Path("/ssd/av1_enc.json")
MAX_CONCURRENT_JOBS = 5  # Max number of ffmpeg processes at a time
FFMPEG_PATH = "ffmpeg"  # Assumed ffmpeg is in PATH
FFPROBE_PATH = "ffprobe" # Assumed ffprobe is in PATH
QSV_DEVICE = "/dev/dri/card0" # QSV device path

# Supported video extensions (lowercase)
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.ts', '.mpg', '.mpeg')

# Map ffprobe codec names to ffmpeg QSV decoder options
QSV_DECODER_MAP = {
    'h264': 'h264_qsv',
    'hevc': 'hevc_qsv',
    'mpeg2video': 'mpeg2_qsv',
    'vc1': 'vc1_qsv',
    'vp9': 'vp9_qsv',
    # 'av1': 'av1_qsv' # Input AV1 is skipped
}

# --- Global state for UI ---
# file_status_display will store dicts like:
# {'id': unique_path_string, 'filename': relative_path, 'status': '...', 'orig_size': N, 'new_size': N, 'reduction': N, 'error': bool, 'final_status_icon': '...', 'filename_for_sort': lowercase_relative_path}
file_status_display = [] 
ui_queue = queue.Queue() # Thread-safe queue for UI updates from workers

# --- Helper Functions ---

def format_size(size_bytes):
    """Converts bytes to a human-readable string."""
    if not isinstance(size_bytes, (int, float)) or size_bytes < 0:
        return "-"
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = 0
    size_bytes = float(size_bytes) # Ensure float for division
    while size_bytes >= 1024 and i < len(size_name) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f}{size_name[i]}"

def get_video_codec(filepath: Path) -> str | None:
    """Gets the video codec name using ffprobe."""
    command = [
        FFPROBE_PATH,
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(filepath)
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
        codec = result.stdout.strip()
        return codec if codec else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        # Log error via queue, but return None to the caller
        error_msg = f"ffprobe error for {filepath.name}: {e}"
        if isinstance(e, subprocess.CalledProcessError):
            error_msg = f"ffprobe error ({filepath.name}): {e.stderr[:100]}..."
        elif isinstance(e, FileNotFoundError):
             error_msg = f"CRITICAL: {FFPROBE_PATH} not found during get_video_codec."
        elif isinstance(e, subprocess.TimeoutExpired):
            error_msg = f"ffprobe timeout for {filepath.name}"
        ui_queue.put({'type': 'log', 'message': error_msg})
        if isinstance(e, FileNotFoundError): # Re-raise critical errors
             raise
        return None


def run_ffmpeg_encode(input_file: Path, output_file: Path, input_qsv_codec: str) -> tuple[bool, str]:
    """Runs the ffmpeg encoding command."""
    command = [
        FFMPEG_PATH,
        '-y',
        '-loglevel', 'error', # Only show errors from ffmpeg
        '-hwaccel', 'qsv',
        '-qsv_device', QSV_DEVICE,
        '-c:v', input_qsv_codec,
        '-i', str(input_file),
        '-c:v', 'av1_qsv',
        '-c:a', 'copy',
        '-strict', '-2',
        str(output_file)
    ]
    cmd_str = ' '.join(command)
    process = None
    try:
        # Use Popen and communicate with timeout for better control
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(timeout=3600) # 1 hour timeout

        if process.returncode == 0:
            return True, cmd_str
        else:
            # Log stderr if encoding failed
            ui_queue.put({'type': 'log', 'message': f"ffmpeg error ({input_file.name}): {stderr[:200]}..."})
            return False, cmd_str
    except subprocess.TimeoutExpired:
        ui_queue.put({'type': 'log', 'message': f"ffmpeg timeout for {input_file.name}. Killing process."})
        if process: process.kill() # Ensure process is killed
        return False, cmd_str
    except FileNotFoundError:
        ui_queue.put({'type': 'log', 'message': f"CRITICAL: {FFMPEG_PATH} not found during encoding."})
        raise
    except Exception as e:
        ui_queue.put({'type': 'log', 'message': f"Exception during ffmpeg ({input_file.name}): {str(e)}"})
        return False, cmd_str

def load_metrics() -> list:
    """Loads existing metrics from the JSON file."""
    if METRICS_FILE.exists():
        try:
            with open(METRICS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            ui_queue.put({'type': 'log', 'message': f"Warning: Metrics file {METRICS_FILE} corrupted. Starting fresh."})
        except IOError as e:
            ui_queue.put({'type': 'log', 'message': f"Warning: Could not read metrics file {METRICS_FILE}: {e}"})
    return []

def save_metrics(metrics_data: list):
    """Saves metrics to the JSON file."""
    try:
        METRICS_FILE.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists
        # Use a temporary file for atomic save
        temp_metrics_file = METRICS_FILE.with_suffix('.tmp')
        with open(temp_metrics_file, 'w') as f:
            json.dump(metrics_data, f, indent=2)
        shutil.move(str(temp_metrics_file), str(METRICS_FILE))
    except IOError as e:
        ui_queue.put({'type': 'log', 'message': f"Error: Could not write to metrics file {METRICS_FILE}: {e}"})
    except Exception as e:
         ui_queue.put({'type': 'log', 'message': f"Error during saving metrics: {e}"})


# --- Worker Function ---
def process_file(file_path: Path, metrics_list: list, metrics_lock: threading.Lock):
    """Processes a single video file. file_path is the absolute, resolved path."""

    # Filename for display in UI (relative to source_directory)
    try:
        # Get the resolved source directory first
        resolved_source_dir = SOURCE_DIRECTORY.resolve()
        filename_for_display = str(file_path.relative_to(resolved_source_dir))
    except ValueError: # If file_path is not under SOURCE_DIRECTORY (e.g. if SOURCE_DIRECTORY was a symlink itself)
        filename_for_display = file_path.name

    # Actual filename for operations and unique ID
    actual_filename = file_path.name
    file_id = str(file_path.resolve()) # Unique ID for UI updates

    # Initial UI state (will be added to file_status_display in main logic)
    # The 'line' number is NOT fixed here; it's dynamic in the UI display loop

    def update_ui(status_msg, orig_size=None, new_size=None, reduction=None, is_final=False, error=False):
        update = {
            'type': 'file_update',
            'id': file_id,
            'filename': filename_for_display, # Use relative path for display
            'status': status_msg,
        }
        # Only add keys if values are provided/relevant
        if orig_size is not None: update['orig_size'] = orig_size
        if new_size is not None: update['new_size'] = new_size
        if reduction is not None: update['reduction'] = reduction

        if is_final: update['final_status_icon'] = "[encoded]" if not error else "[error]"
        if error: update['error'] = True
        ui_queue.put(update)

    # Initial state update is handled in main_logic_with_curses before starting threads
    # We just update the status from here

    update_ui("Checking codec...")

    metric_entry_base = {
        "original_filename": actual_filename, # Just the name.ext
        "original_path": str(file_path),       # Full original path
        "encode_date": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        original_size_bytes = file_path.stat().st_size
        metric_entry_base["original_size_bytes"] = original_size_bytes
        update_ui("Processing...", orig_size=original_size_bytes)
    except FileNotFoundError:
        update_ui(f"Error: Original file not found.", error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": "Error: Original file disappeared before processing"}
        with metrics_lock: metrics_list.append(metric_entry)
        return
    except Exception as e:
        update_ui(f"Error getting size: {e}", error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": f"Error: Failed to get original size - {e}"}
        with metrics_lock: metrics_list.append(metric_entry)
        return


    input_codec = get_video_codec(file_path)

    if not input_codec:
        update_ui(f"Error: Could not determine codec", orig_size=original_size_bytes, error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": "Error: ffprobe failed or no video stream"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    metric_entry_base["original_codec"] = input_codec

    if input_codec == 'av1':
        update_ui("Skipped (already AV1)", orig_size=original_size_bytes, is_final=True)
        metric_entry = {**metric_entry_base, "status": "skipped (already AV1)"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    input_qsv_decoder = QSV_DECODER_MAP.get(input_codec)
    if not input_qsv_decoder:
        update_ui(f"Error: Unsupported input codec '{input_codec}' for QSV.", orig_size=original_size_bytes, error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": f"Error: Unsupported input codec '{input_codec}' for QSV"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    update_ui(f"Copying to temp...", orig_size=original_size_bytes)

    # Use file_path.stem and a random suffix for uniqueness in the flat temp dir
    # Preserve original extension for temp file
    temp_source_file = TEMP_DIRECTORY / f"{file_path.stem}_{os.urandom(4).hex()}_orig{file_path.suffix}"
    temp_av1_output_file = TEMP_DIRECTORY / f"{file_path.stem}_{os.urandom(4).hex()}_av1{file_path.suffix}"


    try:
        shutil.copy2(file_path, temp_source_file)
    except Exception as e:
        update_ui(f"Error copying: {e}", orig_size=original_size_bytes, error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": f"Error: Failed to copy to temp - {e}"}
        with metrics_lock: metrics_list.append(metric_entry)
        if temp_source_file.exists(): temp_source_file.unlink(missing_ok=True)
        return

    update_ui(f"Encoding ({input_codec} -> av1_qsv)...", orig_size=original_size_bytes)
    start_time = time.time()
    encode_successful, ffmpeg_cmd = run_ffmpeg_encode(temp_source_file, temp_av1_output_file, input_qsv_decoder)
    encoding_time_seconds = time.time() - start_time
    metric_entry_base["ffmpeg_command"] = ffmpeg_cmd
    metric_entry_base["encoding_time_seconds"] = round(encoding_time_seconds, 2)

    if not encode_successful:
        update_ui("Error during encoding.", orig_size=original_size_bytes, error=True, is_final=True)
        # Cleanup temp files
        if temp_source_file.exists(): temp_source_file.unlink(missing_ok=True)
        if temp_av1_output_file.exists(): temp_av1_output_file.unlink(missing_ok=True)
        metric_entry = {**metric_entry_base, "status": "Error: ffmpeg encoding failed"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    if not temp_av1_output_file.exists() or temp_av1_output_file.stat().st_size == 0:
        update_ui("Error: Encoded file missing/empty.", orig_size=original_size_bytes, error=True, is_final=True)
        # Cleanup temp files
        if temp_source_file.exists(): temp_source_file.unlink(missing_ok=True)
        if temp_av1_output_file.exists(): temp_av1_output_file.unlink(missing_ok=True)
        metric_entry = {**metric_entry_base, "status": "Error: Encoded file missing or empty"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    update_ui("Verifying AV1 output...", orig_size=original_size_bytes)
    encoded_codec = get_video_codec(temp_av1_output_file)

    if encoded_codec != 'av1':
        update_ui(f"Error: Verification failed (is {encoded_codec}).", orig_size=original_size_bytes, error=True, is_final=True)
        # Cleanup temp files
        if temp_source_file.exists(): temp_source_file.unlink(missing_ok=True)
        if temp_av1_output_file.exists(): temp_av1_output_file.unlink(missing_ok=True)
        metric_entry = {**metric_entry_base, "status": f"Error: AV1 verification failed (got {encoded_codec})"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    av1_size_bytes = temp_av1_output_file.stat().st_size
    reduction_percent = ((original_size_bytes - av1_size_bytes) / original_size_bytes) * 100 if original_size_bytes > 0 else 0
    update_ui("Finalizing...", orig_size=original_size_bytes, new_size=av1_size_bytes, reduction=reduction_percent)

    try:
        # Destination is the original file's full path to preserve subfolder structure.
        final_destination_path = file_path

        # Ensure parent directory of the final destination exists (it should, as it's the original location)
        final_destination_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomically replace the original file
        # Use replace for atomic replacement if on the same filesystem
        # If not on the same filesystem, move is necessary, but less atomic
        try:
            shutil.replace(str(temp_av1_output_file), str(final_destination_path))
            # replace handles deleting the destination if it exists
        except Exception as e:
             # Fallback to move if replace fails (e.g., different devices)
             ui_queue.put({'type': 'log', 'message': f"Warning: shutil.replace failed for {file_path.name} ({e}). Falling back to shutil.move."})
             if file_path.exists():
                 file_path.unlink() # Manually delete original
             shutil.move(str(temp_av1_output_file), str(final_destination_path))

        # Clean up the original temp source file
        if temp_source_file.exists():
            temp_source_file.unlink(missing_ok=True)


        update_ui(
            f"Done. Reduction: {reduction_percent:.1f}%",
            orig_size=original_size_bytes, new_size=av1_size_bytes, reduction=reduction_percent, is_final=True
        )
        metric_entry = {
            **metric_entry_base,
            "encoded_filename": final_destination_path.name, # Should be same as original_filename
            "encoded_path": str(final_destination_path),  # Should be same as original_path
            "encoded_size_bytes": av1_size_bytes,
            "size_reduction_percent": round(reduction_percent, 2),
            "status": "success"
        }
        with metrics_lock: metrics_list.append(metric_entry)

    except Exception as e:
        update_ui(f"Error during cleanup/move: {e}", orig_size=original_size_bytes, new_size=av1_size_bytes, error=True, is_final=True)
        # Ensure temp files are cleaned up if possible
        if temp_av1_output_file.exists(): temp_av1_output_file.unlink(missing_ok=True)
        if temp_source_file.exists(): temp_source_file.unlink(missing_ok=True)
        metric_entry = {**metric_entry_base, "status": f"Error: Post-processing file operations failed - {e}"}
        with metrics_lock: metrics_list.append(metric_entry)


# --- Curses UI Function ---
def display_ui(stdscr, shutdown_event_ref):
    """Manages the curses UI display with scrolling and flicker reduction."""
    global file_status_display # Access the global list

    curses.curs_set(0) # Hide cursor
    stdscr.nodelay(True) # Make getch non-blocking
    stdscr.timeout(100) # Wait 100ms for input before redrawing

    # Setup colors
    curses.start_color()
    curses.use_default_colors() # Use terminal's default background
    curses.init_pair(1, curses.COLOR_GREEN, -1) # Success
    curses.init_pair(2, curses.COLOR_RED, -1)   # Error
    curses.init_pair(3, curses.COLOR_YELLOW, -1) # Skipped/Warning
    curses.init_pair(4, curses.COLOR_CYAN, -1)  # Header/Info
    curses.init_pair(5, curses.COLOR_WHITE, -1) # Default/Normal

    log_messages = []
    max_log_lines_display = 5

    # Dictionary to quickly find a file item by its ID for updates
    file_ui_map = {} # Will be populated based on file_status_display

    scroll_offset = 0 # Index of the first item currently displayed
    previous_display_lines = {} # { line_num: (text, attr), ... } for flicker reduction

    # Initialize file_ui_map based on the initial file_status_display
    for item in file_status_display:
         file_ui_map[item['id']] = item

    while not shutdown_event_ref.is_set():
        # --- Process UI updates from the queue ---
        while not ui_queue.empty():
            try:
                update = ui_queue.get_nowait()
                if update['type'] == 'file_update':
                    file_id = update['id']
                    if file_id in file_ui_map:
                         # Update the existing item in the global list via the map reference
                         file_ui_map[file_id].update(update)
                    # else: This case shouldn't happen if the file list is correctly initialized

                elif update['type'] == 'log':
                    log_messages.append(f"[{time.strftime('%H:%M:%S')}] {update['message']}")
                    # Keep log messages buffer size reasonable
                    if len(log_messages) > max_log_lines_display * 5:
                         log_messages = log_messages[-max_log_lines_display * 3:] # Keep more than displayed

                elif update['type'] == 'exit_ui':
                    return # Exit the UI thread

            except queue.Empty:
                break # No more items in queue
            except Exception as e:
                log_messages.append(f"UI Queue Error: {str(e)}") # Log processing errors

        # --- Get terminal dimensions ---
        try:
            max_y, max_x = stdscr.getmaxyx()
        except curses.error:
            # Handle potential resize during getmaxyx
            time.sleep(0.1) # Wait a bit before trying again
            continue # Skip this draw cycle

        # --- Calculate layout ---
        header_lines = 2 # Header text + Column titles
        log_header_lines = 1 # "--- Logs ---"
        log_lines_height = min(max_log_lines_display, max_y - header_lines - log_header_lines - 2) # Don't go below screen
        if log_lines_height < 0: log_lines_height = 0

        log_area_start_line = max_y - log_lines_height - log_header_lines -1
        if log_area_start_line < header_lines + 1: # Ensure log area doesn't overlap header/titles
             log_area_start_line = header_lines + 1
             log_lines_height = max_y - log_area_start_line - log_header_lines - 1
             if log_lines_height < 0: log_lines_height = 0

        visible_lines = log_area_start_line - header_lines - 1 # Lines available for file list

        # --- Handle Scrolling ---
        total_files = len(file_status_display)
        # Ensure scroll_offset is within valid bounds
        max_scroll = max(0, total_files - visible_lines)
        scroll_offset = max(0, min(scroll_offset, max_scroll))


        # --- Prepare content for drawing (line by line) ---
        current_display_lines = {} # { line_num: (text, attr), ... }

        # Header
        header_text = f"AV1 Batch Encoder (QSV) - {MAX_CONCURRENT_JOBS} workers - Use ↑↓ to scroll, 'q' to quit"
        current_display_lines[0] = (header_text[:max_x-1], curses.A_BOLD | curses.color_pair(4))

        # Dynamic column widths
        size_col_w = 10
        percent_col_w = 9
        info_col_w = 10 # For "[encoded]" tag
        other_cols_width = size_col_w * 2 + percent_col_w + info_col_w + 4 # 4 spaces between columns
        file_col_w = max(1, max_x - other_cols_width - 1) # Filename takes remaining space, ensure at least 1
        status_col_w = file_col_w # Status column removed from title line for simplicity, status shown with filename


        col_titles = f"{'File':<{file_col_w}} {'Orig':>{size_col_w}} {'AV1':>{size_col_w}} {'%Red':>{percent_col_w}} {'Done':<{info_col_w}}"
        current_display_lines[1] = (col_titles[:max_x-1], curses.A_UNDERLINE | curses.color_pair(5))

        # File list lines
        display_start_line = header_lines # Start drawing files from line 2
        for i in range(visible_lines):
            list_index = i + scroll_offset
            line_num = display_start_line + i
            if line_num >= max_y - log_lines_height - log_header_lines -1: # Don't draw over log area
                 continue

            if 0 <= list_index < total_files:
                item = file_status_display[list_index]

                filename_disp = item.get('filename', 'N/A')
                status_disp = item.get('status', 'Pending...')

                # Combine filename and status, truncating if necessary
                combined_info = f"{filename_disp}: {status_disp}"
                file_status_disp = combined_info[:file_col_w-1] # Truncate to fit column width

                orig_s = format_size(item.get('orig_size'))
                new_s = format_size(item.get('new_size'))
                reduct_val = item.get('reduction')
                reduct = f"{reduct_val:.1f}%" if isinstance(reduct_val, float) else '-'

                final_icon = item.get('final_status_icon', '')

                line_str = (f"{file_status_disp:<{file_col_w}} "
                            f"{orig_s:>{size_col_w}} "
                            f"{new_s:>{size_col_w}} "
                            f"{reduct:>{percent_col_w}} "
                            f"{final_icon:<{info_col_w}}")

                color_attr = curses.color_pair(5) # Default
                if item.get('error'): color_attr = curses.color_pair(2) # Red for Error
                elif final_icon == "[encoded]": color_attr = curses.color_pair(1) # Green for Success
                elif "Skipped" in status_disp or final_icon == "[skipped]": color_attr = curses.color_pair(3) # Yellow for Skipped
                elif "Encoding" in status_disp or "Copying" in status_disp or "Verifying" in status_disp:
                     color_attr = curses.color_pair(4) # Cyan for In Progress

                current_display_lines[line_num] = (line_str[:max_x-1], color_attr)

            else:
                # Draw empty lines for the rest of the visible area
                empty_line = ""
                current_display_lines[line_num] = (empty_line[:max_x-1], curses.color_pair(5))


        # Log area header
        if log_lines_height > 0:
             current_display_lines[log_area_start_line] = ("--- Logs ---"[:max_x-1], curses.A_BOLD | curses.color_pair(4))

             # Log messages
             for i, log_msg in enumerate(log_messages[-log_lines_height:]):
                 line_num = log_area_start_line + 1 + i
                 if line_num < max_y: # Ensure we don't write past the screen height
                     current_display_lines[line_num] = (log_msg[:max_x-1], curses.color_pair(5))

        # --- Flicker Reduction Drawing ---
        # Clear lines that were previously drawn but are not in the current display
        # or whose content/attributes have changed
        lines_to_clear = set(previous_display_lines.keys()) - set(current_display_lines.keys())
        for y in lines_to_clear:
             if y < max_y: # Only clear if within bounds
                 stdscr.move(y, 0)
                 stdscr.clrtoeol() # Clear to end of line

        # Draw or update lines that are in the current display
        for y, (text, attr) in current_display_lines.items():
             if y < max_y: # Only draw if within bounds
                 # Check if the line has changed
                 if y not in previous_display_lines or previous_display_lines[y] != (text, attr):
                     # Clear the existing content on the line before drawing new
                     stdscr.move(y, 0)
                     stdscr.clrtoeol()
                     try:
                         stdscr.addstr(y, 0, text, attr)
                     except curses.error:
                         # Handle potential error if addstr fails (e.g., terminal too small after check)
                         pass # Ignore and continue

        # Update the record of what was just drawn
        previous_display_lines = current_display_lines

        # --- Handle Input ---
        try:
            key = stdscr.getch()
            if key == ord('q'):
                ui_queue.put({'type': 'log', 'message': "Quit signal received. Finishing current jobs..."})
                shutdown_event_ref.set() # Signal workers and main loop to shut down
            elif key == curses.KEY_UP:
                scroll_offset = max(0, scroll_offset - 1)
            elif key == curses.KEY_DOWN:
                scroll_offset = min(max_scroll, scroll_offset + 1)
            elif key != -1: # Any other key when nodelay is True
                pass # Ignore other keys
        except curses.error:
             # getch() might throw an error on some terminals during resize, just ignore
             pass
        except Exception as e:
             log_messages.append(f"UI Input Error: {str(e)}")


        # --- Refresh the screen ---
        stdscr.refresh()

        # Short sleep to prevent burning CPU when queue is empty and no input
        time.sleep(0.01)

    # --- Clean exit ---
    # Ensure screen is cleared and reset after the loop exits
    stdscr.clear()
    stdscr.addstr(0,0, "Exiting UI...", curses.color_pair(4))
    stdscr.refresh()
    time.sleep(0.5) # Give user a moment to see the message


# --- Main Logic ---
def main_logic_with_curses(stdscr):
    global file_status_display

    overall_shutdown_event = threading.Event()

    # Pre-flight checks for directories - handled in __main__ now

    metrics_data = load_metrics()
    metrics_data_lock = threading.Lock()

    # Find video files recursively
    video_files_to_process_paths_temp = []
    resolved_source_dir = SOURCE_DIRECTORY.resolve() # Resolve once for relative path calculations
    try:
        for ext in VIDEO_EXTENSIONS:
            # Use rglob for recursive search
            video_files_to_process_paths_temp.extend(list(resolved_source_dir.rglob(f"*{ext}")))
            video_files_to_process_paths_temp.extend(list(resolved_source_dir.rglob(f"*{ext.upper()}")))

        # Deduplicate (rglob might find same file via symlinks, resolve helps) and sort by resolved path initially
        video_files_to_process_paths = sorted(list(set(p.resolve() for p in video_files_to_process_paths_temp)))
    except Exception as e:
        ui_queue.put({'type': 'log', 'message': f"Error finding files in '{resolved_source_dir}': {e}"})
        video_files_to_process_paths = []


    if not video_files_to_process_paths:
        ui_queue.put({'type': 'log', 'message': f"No video files found in '{resolved_source_dir}' or its subfolders."})
        # Still start UI to show message, then exit
        ui_thread = threading.Thread(target=display_ui, args=(stdscr, overall_shutdown_event), daemon=True)
        ui_thread.start()
        time.sleep(2) # Give UI time to display message
        overall_shutdown_event.set() # Signal UI to exit
        ui_queue.put({'type': 'exit_ui'}) # Explicitly tell UI to exit
        ui_thread.join(timeout=5)
        return # Exit main logic if no files found

    # Populate the initial file_status_display list BEFORE starting UI thread
    file_status_display.clear()
    for f_path_resolved in video_files_to_process_paths:
        try:
            # filename for display is relative to the initial SOURCE_DIRECTORY
            display_name = str(f_path_resolved.relative_to(resolved_source_dir))
        except ValueError: # Should not happen if f_path_resolved is from rglob of resolved_source_dir
            display_name = f_path_resolved.name

        file_status_display.append({
            'id': str(f_path_resolved),
            'filename': display_name, # This is the relative path for UI
            'filename_for_sort': display_name.lower(), # For consistent sorting in UI
            'status': 'Waiting...',
            'orig_size': None, 'new_size': None, 'reduction': None,
            'error': False, 'final_status_icon': ''
        })
    # Sort initially by the display name for a consistent UI list appearance
    file_status_display.sort(key=lambda x: x.get('filename_for_sort', ''))

    ui_queue.put({'type': 'log', 'message': f"Found {len(file_status_display)} video files. Starting encoding..."})

    # Start UI thread
    ui_thread = threading.Thread(target=display_ui, args=(stdscr, overall_shutdown_event), daemon=True)
    ui_thread.start()

    # Start worker threads
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS) as executor:
        # Pass the resolved full path to process_file
        future_to_file = {
            executor.submit(process_file, video_file_path, metrics_data, metrics_data_lock): video_file_path
            for video_file_path in video_files_to_process_paths
        }

        try:
            for future in as_completed(future_to_file):
                if overall_shutdown_event.is_set():
                    ui_queue.put({'type': 'log', 'message': "Shutdown initiated, cancelling pending tasks..."})
                    # Attempt to cancel remaining futures
                    for f_cancel in future_to_file:
                        if f_cancel != future and not f_cancel.done():
                            f_cancel.cancel()
                    # No need to break here, as_completed will yield futures that finish/are cancelled
                try:
                    # Check result immediately or handle cancellation/exceptions
                    # Using a small timeout allows the main loop to respond to shutdown_event
                    result = future.result(timeout=0.01) # Check result, but don't block long
                except CancelledError:
                     # This future was cancelled due to shutdown
                     file_path = future_to_file[future]
                     file_id = str(file_path.resolve())
                     # Update UI for cancelled tasks
                     ui_queue.put({'type': 'file_update', 'id': file_id, 'status': 'Cancelled', 'final_status_icon': '[cancel]', 'error': True})
                except Exception as exc:
                    # An exception occurred in the worker thread
                    file_path = future_to_file[future]
                    file_id = str(file_path.resolve())
                    ui_queue.put({'type': 'log', 'message': f"Worker exception for {file_path.name}: {exc}"})
                    # Update UI for errored tasks (status already handled in process_file usually)
                    # Ensure final state is marked
                    ui_queue.put({'type': 'file_update', 'id': file_id, 'final_status_icon': '[error]', 'error': True})
                except Exception as e:
                     # Handle exceptions from future.result itself (less likely)
                     ui_queue.put({'type': 'log', 'message': f"Error waiting for future result: {e}"})
        finally:
            # Ensure all futures are waited on or cancelled before exiting context
            # This also handles cases where as_completed might stop early on exception
            for future in future_to_file:
                 if not future.done():
                      future.cancel() # Ensure pending tasks are marked as cancelled
            # Note: as_completed might still return cancelled futures

    # All futures are done, cancelled, or resulted in exception
    if not overall_shutdown_event.is_set():
        ui_queue.put({'type': 'log', 'message': "All processing tasks finished."})
    else:
         ui_queue.put({'type': 'log', 'message': "Processing interrupted by user shutdown."})


    # Save metrics after workers are done
    save_metrics(metrics_data)
    ui_queue.put({'type': 'log', 'message': f"Metrics saved to {METRICS_FILE}."})

    if not overall_shutdown_event.is_set():
        ui_queue.put({'type': 'log', 'message': "Processing complete. Press 'q' to exit UI or waiting for tasks to finish."})
        # Wait a bit longer for UI to show final status
        time.sleep(2)

    # Signal UI thread to exit and wait for it
    overall_shutdown_event.set()
    ui_queue.put({'type': 'exit_ui'})
    ui_thread.join(timeout=5) # Give UI thread a short time to clean up

    # Final cleanup of temp directory
    try:
        if TEMP_DIRECTORY.exists():
            ui_queue.put({'type': 'log', 'message': f"Cleaning up temporary directory: {TEMP_DIRECTORY}"})
            # Check if directory is empty or contains only specific temp files
            temp_files = list(TEMP_DIRECTORY.iterdir())
            if not temp_files:
                 TEMP_DIRECTORY.rmdir() # Only remove if empty
                 ui_queue.put({'type': 'log', 'message': f"Removed empty temp directory: {TEMP_DIRECTORY}"})
            else:
                # Optionally remove specific temp files created by this script
                # Or just leave non-empty temp dirs for manual inspection
                ui_queue.put({'type': 'log', 'message': f"Temp directory {TEMP_DIRECTORY} not empty, skipping removal."})

    except Exception as e:
         ui_queue.put({'type': 'log', 'message': f"Error during temp directory cleanup: {e}"})


if __name__ == '__main__':
    # --- Pre-flight Checks ---
    critical_error = False
    error_messages = []

    # Check executables
    for tool, path_var in [(FFMPEG_PATH, "FFMPEG_PATH"), (FFPROBE_PATH, "FFPROBE_PATH")]:
        try:
            # Check if exists and is executable
            if not shutil.which(tool):
                 error_messages.append(f"CRITICAL: Executable '{tool}' not found in PATH.")
                 critical_error = True
                 continue # Skip version check if not found
            # Check version/run simple command
            subprocess.run([tool, "-version"], capture_output=True, check=True, text=True, timeout=5)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            error_messages.append(f"CRITICAL: Failed to run '{tool}' (from {path_var}='{globals()[path_var]}').")
            error_messages.append(f"  Details: {e}")
            critical_error = True
        except Exception as e:
             error_messages.append(f"CRITICAL: Unexpected error during pre-flight check for '{tool}': {e}")
             critical_error = True


    # Check QSV device
    if not QSV_DEVICE or not Path(QSV_DEVICE).exists():
        error_messages.append(f"CRITICAL: QSV device '{QSV_DEVICE}' not found or not accessible.")
        critical_error = True

    # Check paths
    try:
        # Check if TEMP_DIRECTORY can be created or parent is writable
        if not TEMP_DIRECTORY.exists():
            # Try to create parent directories to check writability
            TEMP_DIRECTORY.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(TEMP_DIRECTORY.parent, os.W_OK | os.X_OK): # Need execute for directory traversal
                 error_messages.append(f"CRITICAL: Parent of TEMP_DIRECTORY ('{TEMP_DIRECTORY.parent}') is not writable or executable.")
                 critical_error = True
            # Note: actual TEMP_DIRECTORY creation will happen in main_logic
        elif not os.access(TEMP_DIRECTORY, os.W_OK | os.R_OK | os.X_OK): # If exists, check R/W/X
            error_messages.append(f"CRITICAL: TEMP_DIRECTORY ('{TEMP_DIRECTORY}') exists but is not readable, writable, or executable.")
            critical_error = True
    except Exception as e:
        error_messages.append(f"CRITICAL: Error checking TEMP_DIRECTORY ('{TEMP_DIRECTORY}'): {e}")
        critical_error = True

    try:
        resolved_source = SOURCE_DIRECTORY.resolve(strict=True) # Check if source exists and resolve it
        if not os.access(resolved_source, os.R_OK | os.W_OK | os.X_OK):
            error_messages.append(f"CRITICAL: Source directory ('{resolved_source}') not readable, writable, or executable.")
            critical_error = True
    except FileNotFoundError:
        error_messages.append(f"CRITICAL: Source directory ('{SOURCE_DIRECTORY}') does not exist.")
        critical_error = True
    except Exception as e:
        error_messages.append(f"CRITICAL: Error accessing source directory ('{SOURCE_DIRECTORY}'): {e}")
        critical_error = True


    if critical_error:
        print("\n--- SCRIPT PRE-FLIGHT CHECKS FAILED ---")
        for msg in error_messages:
            print(msg)
        print("Please resolve the issues above and try again.")
        exit(1)

    # --- Run Curses UI and Main Logic ---
    try:
        curses.wrapper(main_logic_with_curses)
    except Exception as e:
         print(f"\nAn unhandled error occurred: {e}")
         import traceback
         traceback.print_exc()
    finally:
        # This block runs even if curses exits cleanly or with an error
        print(f"\nScript execution finished. Metrics saved to {METRICS_FILE}")
        # Note: Temp directory cleanup message might appear before curses exits depending on timing.

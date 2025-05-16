#!/usr/bin/env python3
import curses
import os
import shutil
import subprocess
import json
import threading
import time
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError
from pathlib import Path
import signal
import logging

# --- Configuration ---
SOURCE_DIRECTORY = Path(".")
TEMP_DIRECTORY = Path("/ssd/av1_tmp")
METRICS_FILE = Path("/ssd/av1_enc.json")
MAX_CONCURRENT_JOBS = 1
FFMPEG_PATH = "ffmpeg"
FFPROBE_PATH = "ffprobe"
QSV_DEVICE = "/dev/dri/card0"

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.ts', '.mpg', '.mpeg')

QSV_DECODER_MAP = {
    'h264': 'h264_qsv',
    'hevc': 'hevc_qsv',
    'mpeg2video': 'mpeg2_qsv',
    'vc1': 'vc1_qsv',
    'vp9': 'vp9_qsv',
}

# --- Logging Setup ---
# ONLY log to the file when running Curses UI
logging.basicConfig(filename='av1_encoder_debug.log', level=logging.DEBUG,
                    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
# Removed: logging.getLogger().addHandler(logging.StreamHandler())


# --- Global state for UI and Process Management ---
file_status_display = []
ui_queue = queue.Queue()
command_queue = queue.Queue()

active_processes_info = {}
active_processes_lock = threading.Lock()

# --- Helper Functions ---

def format_size(size_bytes):
    if not isinstance(size_bytes, (int, float)) or size_bytes < 0:
        return "-"
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = 0
    size_bytes = float(size_bytes)
    while size_bytes >= 1024 and i < len(size_name) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f}{size_name[i]}"

def get_video_codec(filepath: Path) -> str | None:
    command = [
        FFPROBE_PATH,
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(filepath)
    ]
    try:
        logging.debug(f"Running ffprobe command: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
        codec = result.stdout.strip()
        logging.debug(f"ffprobe for {filepath.name} returned codec: {codec}")
        return codec if codec else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        error_msg = f"ffprobe error for {filepath.name}: {e}"
        if isinstance(e, subprocess.CalledProcessError):
            error_msg = f"ffprobe error ({filepath.name}): {e.stderr[:100]}..."
        elif isinstance(e, FileNotFoundError):
             error_msg = f"CRITICAL: {FFPROBE_PATH} not found during get_video_codec."
        elif isinstance(e, subprocess.TimeoutExpired):
            error_msg = f"ffprobe timeout for {filepath.name}"
        logging.error(error_msg)
        ui_queue.put({'type': 'log', 'message': error_msg})
        if isinstance(e, FileNotFoundError):
             raise
        return None


def run_ffmpeg_encode(input_file: Path, output_file: Path, input_qsv_codec: str, cancel_event: threading.Event) -> tuple[bool, str, bool]:
    command = [
        FFMPEG_PATH,
        '-y',
        '-loglevel', 'error',
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
    was_cancelled = False
    logging.debug(f"Attempting to run ffmpeg command: {cmd_str}")
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        logging.debug(f"ffmpeg process started with PID: {process.pid}")

        while process.poll() is None and not cancel_event.is_set():
             if cancel_event.is_set():
                  break
             time.sleep(0.1)

        if cancel_event.is_set():
            was_cancelled = True
            logging.debug(f"Cancellation event set for PID {process.pid}. Terminating...")
            ui_queue.put({'type': 'log', 'message': f"Terminating ffmpeg for {input_file.name}..."})
            try:
                os.kill(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                 logging.warning(f"SIGTERM failed for {process.pid} ({input_file.name}). Sending SIGKILL.")
                 ui_queue.put({'type': 'log', 'message': f"Kill ffmpeg for {input_file.name} (SIGTERM failed or process gone). Sending SIGKILL."})
                 try:
                     os.kill(process.pid, signal.SIGKILL)
                 except ProcessLookupError:
                     pass
            except Exception as e:
                 logging.error(f"Error during termination for {process.pid} ({input_file.name}): {e}")
                 ui_queue.put({'type': 'log', 'message': f"Error terminating ffmpeg for {input_file.name}: {e}"})

            process.wait()
            logging.debug(f"ffmpeg process {process.pid} terminated. Return code: {process.returncode}")
            stderr_output = process.stderr.read().decode(errors='ignore')
            if stderr_output:
                 logging.debug(f"ffmpeg stderr (killed): {stderr_output[:500]}...")


        if not was_cancelled:
            process.wait()
            stderr_output = process.stderr.read().decode(errors='ignore')
            if process.returncode != 0:
                 logging.error(f"ffmpeg process {process.pid} failed with return code {process.returncode} for {input_file.name}")
                 if stderr_output:
                      logging.error(f"ffmpeg stderr (failed): {stderr_output[:500]}...")
                      ui_queue.put({'type': 'log', 'message': f"ffmpeg error for {input_file.name}: {stderr_output.strip()[:100]}..."})


        return (process.returncode == 0 if not was_cancelled else False), cmd_str, was_cancelled

    except FileNotFoundError:
        logging.critical(f"CRITICAL: {FFMPEG_PATH} not found during encoding.")
        ui_queue.put({'type': 'log', 'message': f"CRITICAL: {FFMPEG_PATH} not found during encoding."})
        raise
    except Exception as e:
        logging.error(f"Exception starting or managing ffmpeg ({input_file.name}): {str(e)}")
        ui_queue.put({'type': 'log', 'message': f"Exception during ffmpeg ({input_file.name}): {str(e)}"})
        return False, cmd_str, False
    finally:
         with active_processes_lock:
              # Attempt to remove based on original file_id, which is the resolved path string
              # Need the original file_id here, which isn't directly available from temp_file path easily
              # The worker receives file_id, let's use that for removal
              # Note: This finally block is inside run_ffmpeg_encode, not process_file.
              # Active process removal should happen when the worker finishes.
              pass # Removal is now handled in process_file's outer finally block


def load_metrics() -> list:
    logging.debug(f"Attempting to load metrics from {METRICS_FILE}")
    if METRICS_FILE.exists():
        try:
            with open(METRICS_FILE, 'r') as f:
                metrics = json.load(f)
                logging.debug(f"Loaded {len(metrics)} metrics entries.")
                return metrics
        except json.JSONDecodeError:
            logging.warning(f"Metrics file {METRICS_FILE} corrupted. Starting fresh.")
            ui_queue.put({'type': 'log', 'message': f"Warning: Metrics file {METRICS_FILE} corrupted. Starting fresh."})
        except IOError as e:
            logging.error(f"Could not read metrics file {METRICS_FILE}: {e}")
            ui_queue.put({'type': 'log', 'message': f"Warning: Could not read metrics file {METRICS_FILE}: {e}"})
    else:
        logging.debug(f"Metrics file {METRICS_FILE} not found. Starting fresh.")
    return []

def save_metrics(metrics_data: list):
    logging.debug(f"Attempting to save {len(metrics_data)} metrics entries to {METRICS_FILE}")
    try:
        METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_metrics_file = METRICS_FILE.with_suffix('.tmp')
        with open(temp_metrics_file, 'w') as f:
            json.dump(metrics_data, f, indent=2)
        shutil.move(str(temp_metrics_file), str(METRICS_FILE))
        logging.debug("Metrics saved successfully.")
    except IOError as e:
        logging.error(f"Could not write to metrics file {METRICS_FILE}: {e}")
        ui_queue.put({'type': 'log', 'message': f"Error: Could not write to metrics file {METRICS_FILE}: {e}"})
    except Exception as e:
         logging.error(f"Error during saving metrics: {e}")
         ui_queue.put({'type': 'log', 'message': f"Error during saving metrics: {e}"})


# --- Worker Function ---
def process_file(file_path: Path, file_id: str, metrics_list: list, metrics_lock: threading.Lock, active_processes_info, active_processes_lock):
    logging.debug(f"Worker starting for file_id: {file_id}")

    try:
        resolved_source_dir = SOURCE_DIRECTORY.resolve()
        filename_for_display = str(file_path.relative_to(resolved_source_dir))
    except ValueError:
        filename_for_display = file_path.name

    actual_filename = file_path.name

    def update_ui(status_msg, orig_size=None, new_size=None, reduction=None, is_final=False, error=False, cancelled=False):
        update = {
            'type': 'file_update',
            'id': file_id,
            'filename': filename_for_display,
            'status': status_msg,
        }
        if orig_size is not None: update['orig_size'] = orig_size
        if new_size is not None: update['new_size'] = new_size
        if reduction is not None: update['reduction'] = reduction

        if is_final:
             if cancelled: update['final_status_icon'] = "[killed]"
             elif error: update['final_status_icon'] = "[error]"
             else: update['final_status_icon'] = "[encoded]"
        if error: update['error'] = True
        if cancelled: update['cancelled'] = True
        update['is_final'] = is_final
        logging.debug(f"Worker sending UI update for {file_id}: Status='{status_msg}', is_final={is_final}, error={error}, cancelled={cancelled}")
        ui_queue.put(update)

    metric_entry_base = {
        "original_filename": actual_filename,
        "original_path": str(file_path),
        "encode_date": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    original_size_bytes = None
    try:
        original_size_bytes = file_path.stat().st_size
        metric_entry_base["original_size_bytes"] = original_size_bytes
        update_ui("Checking codec...", orig_size=original_size_bytes)
        logging.debug(f"Worker {file_id}: Got original size {original_size_bytes}.")
    except FileNotFoundError:
        logging.error(f"Worker {file_id}: Original file not found.")
        update_ui(f"Error: Original file not found.", error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": "Error: Original file disappeared before processing"}
        with metrics_lock: metrics_list.append(metric_entry)
        return
    except Exception as e:
        logging.error(f"Worker {file_id}: Error getting size: {e}")
        update_ui(f"Error getting size: {e}", error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": f"Error: Failed to get original size - {e}"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    input_codec = get_video_codec(file_path)

    if not input_codec:
        logging.error(f"Worker {file_id}: Could not determine codec.")
        update_ui(f"Error: Could not determine codec", orig_size=original_size_bytes, error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": "Error: ffprobe failed or no video stream"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    logging.debug(f"Worker {file_id}: Input codec is {input_codec}.")
    metric_entry_base["original_codec"] = input_codec

    if input_codec == 'av1':
        logging.debug(f"Worker {file_id}: File is already AV1, skipping.")
        update_ui("Skipped (already AV1)", orig_size=original_size_bytes, is_final=True)
        metric_entry = {**metric_entry_base, "status": "skipped (already AV1)"}
        with metrics_lock: metrics_list.append(metric_entry)
        return

    input_qsv_decoder = QSV_DECODER_MAP.get(input_codec)
    if not input_qsv_decoder:
        logging.error(f"Worker {file_id}: Unsupported input codec '{input_codec}' for QSV.")
        update_ui(f"Error: Unsupported input codec '{input_codec}' for QSV.", orig_size=original_size_bytes, error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": f"Error: Unsupported input codec '{input_codec}' for QSV"}
        with metrics_lock: metrics_list.append(metric_entry)
        return
    logging.debug(f"Worker {file_id}: Using QSV decoder: {input_qsv_decoder}.")

    update_ui(f"Copying to temp...", orig_size=original_size_bytes)
    logging.debug(f"Worker {file_id}: Starting copy to temp.")

    temp_source_file = TEMP_DIRECTORY / f"{file_path.stem}_{os.urandom(4).hex()}_orig{file_path.suffix}"
    temp_av1_output_file = TEMP_DIRECTORY / f"{file_path.stem}_{os.urandom(4).hex()}_av1{file_path.suffix}"
    logging.debug(f"Worker {file_id}: Temp source: {temp_source_file}, Temp AV1: {temp_av1_output_file}")

    try:
        shutil.copy2(file_path, temp_source_file)
        if not temp_source_file.exists() or temp_source_file.stat().st_size == 0:
             raise IOError("Temp source file is missing or empty after copy.")
        logging.debug(f"Worker {file_id}: Copy to temp successful.")

    except Exception as e:
        logging.error(f"Worker {file_id}: Error copying to temp: {e}")
        update_ui(f"Error copying: {e}", orig_size=original_size_bytes, error=True, is_final=True)
        metric_entry = {**metric_entry_base, "status": f"Error: Failed to copy to temp - {e}"}
        with metrics_lock: metrics_list.append(metric_entry)
        if temp_source_file.exists(): temp_source_file.unlink(missing_ok=True)
        return

    update_ui(f"Encoding ({input_codec} -> av1_qsv)...", orig_size=original_size_bytes)
    logging.debug(f"Worker {file_id}: Starting encoding.")
    start_time = time.time()

    cancel_event = threading.Event()
    with active_processes_lock:
         active_processes_info[file_id] = {'cancel_event': cancel_event}
    logging.debug(f"Worker {file_id}: Registered cancellation event.")

    encode_successful = False
    ffmpeg_cmd = "N/A"
    was_cancelled = False
    try:
        encode_successful, ffmpeg_cmd, was_cancelled = run_ffmpeg_encode(temp_source_file, temp_av1_output_file, input_qsv_decoder, cancel_event)
        encoding_time_seconds = time.time() - start_time
        metric_entry_base["ffmpeg_command"] = ffmpeg_cmd
        metric_entry_base["encoding_time_seconds"] = round(encoding_time_seconds, 2)
        logging.debug(f"Worker {file_id}: Encoding finished. Success={encode_successful}, Cancelled={was_cancelled}, Time={encoding_time_seconds:.2f}s")

        if was_cancelled:
            logging.debug(f"Worker {file_id}: Encoding was cancelled.")
            update_ui("Killed by user.", orig_size=original_size_bytes, cancelled=True, is_final=True)
            metric_entry = {**metric_entry_base, "status": "killed by user"}
            with metrics_lock: metrics_list.append(metric_entry)
            return

        if not encode_successful:
            logging.error(f"Worker {file_id}: Encoding reported not successful.")
            update_ui("Error during encoding.", orig_size=original_size_bytes, error=True, is_final=True)
            metric_entry = {**metric_entry_base, "status": "Error: ffmpeg encoding failed"}
            with metrics_lock: metrics_list.append(metric_entry)
            return

        logging.debug(f"Worker {file_id}: Encoding successful, checking output file.")
        if not temp_av1_output_file.exists() or temp_av1_output_file.stat().st_size == 0:
            logging.error(f"Worker {file_id}: Encoded file missing or empty.")
            update_ui("Error: Encoded file missing/empty.", orig_size=original_size_bytes, error=True, is_final=True)
            metric_entry = {**metric_entry_base, "status": "Error: Encoded file missing or empty"}
            with metrics_lock: metrics_list.append(metric_entry)
            return

        update_ui("Verifying AV1 output...", orig_size=original_size_bytes)
        logging.debug(f"Worker {file_id}: Verifying output codec.")
        encoded_codec = get_video_codec(temp_av1_output_file)

        if encoded_codec != 'av1':
            logging.error(f"Worker {file_id}: Verification failed, got codec '{encoded_codec}'.")
            update_ui(f"Error: Verification failed (is {encoded_codec}).", orig_size=original_size_bytes, error=True, is_final=True)
            metric_entry = {**metric_entry_base, "status": f"Error: AV1 verification failed (got {encoded_codec})"}
            with metrics_lock: metrics_list.append(metric_entry)
            return
        logging.debug(f"Worker {file_id}: Verification successful, codec is AV1.")

        av1_size_bytes = temp_av1_output_file.stat().st_size
        reduction_percent = ((original_size_bytes - av1_size_bytes) / original_size_bytes) * 100 if original_size_bytes > 0 else 0
        update_ui("Finalizing...", orig_size=original_size_bytes, new_size=av1_size_bytes, reduction=reduction_percent)
        logging.debug(f"Worker {file_id}: Starting finalization (replacing original). New size: {av1_size_bytes}, Reduction: {reduction_percent:.1f}%")

        try:
            final_destination_path = file_path
            final_destination_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                shutil.replace(str(temp_av1_output_file), str(final_destination_path))
                logging.debug(f"Worker {file_id}: shutil.replace successful.")
            except Exception as e:
                 logging.warning(f"Worker {file_id}: shutil.replace failed: {e}. Falling back to shutil.move.")
                 ui_queue.put({'type': 'log', 'message': f"Warning: shutil.replace failed for {file_path.name} ({e}). Falling back to shutil.move."})
                 if final_destination_path.exists():
                     logging.debug(f"Worker {file_id}: Deleting original before move fallback.")
                     final_destination_path.unlink()
                 shutil.move(str(temp_av1_output_file), str(final_destination_path))
                 logging.debug(f"Worker {file_id}: shutil.move fallback successful.")


            if temp_source_file.exists():
                logging.debug(f"Worker {file_id}: Cleaning up temp source file.")
                temp_source_file.unlink(missing_ok=True)

            logging.debug(f"Worker {file_id}: File processed successfully.")
            update_ui(
                f"Done. Reduction: {reduction_percent:.1f}%",
                orig_size=original_size_bytes, new_size=av1_size_bytes, reduction=reduction_percent, is_final=True
            )
            metric_entry = {
                **metric_entry_base,
                "encoded_filename": final_destination_path.name,
                "encoded_path": str(final_destination_path),
                "encoded_size_bytes": av1_size_bytes,
                "size_reduction_percent": round(reduction_percent, 2),
                "status": "success"
            }
            with metrics_lock: metrics_list.append(metric_entry)

        except Exception as e:
            logging.error(f"Worker {file_id}: Error during cleanup/move: {e}")
            update_ui(f"Error during cleanup/move: {e}", orig_size=original_size_bytes, new_size=av1_size_bytes, error=True, is_final=True)
            metric_entry = {**metric_entry_base, "status": f"Error: Post-processing file operations failed - {e}"}
            with metrics_lock: metrics_list.append(metric_entry)

    finally:
        logging.debug(f"Worker {file_id}: Starting final temp file cleanup.")
        if temp_source_file.exists():
             logging.debug(f"Worker {file_id}: Cleaning up temp source file in finally.")
             temp_source_file.unlink(missing_ok=True)
        if temp_av1_output_file.exists():
             logging.debug(f"Worker {file_id}: Cleaning up temp AV1 output file in finally.")
             temp_av1_output_file.unlink(missing_ok=True)

        with active_processes_lock:
            active_processes_info.pop(file_id, None)
        logging.debug(f"Worker {file_id}: Removed from active processes. Worker finished.")


def display_help(stdscr):
    """Displays a help screen."""
    stdscr.clear()
    max_y, max_x = stdscr.getmaxyx()

    title = "Help - AV1 Batch Encoder"
    stdscr.addstr(0, (max_x - len(title)) // 2, title, curses.A_BOLD | curses.color_pair(4))
    stdscr.addstr(1, 0, "-" * max_x, curses.color_pair(4))

    help_text = [
        "Navigation & Selection:",
        "  ↑ / ↓    : Select file (moves cursor)",
        "  PgUp/PgDn: Scroll list quickly",
        "  SPACE    : Mark/Unmark selected file for killing (marked: [*])",
        "  K        : Attempt to kill all marked processes",
        "",
        "Display Options:",
        "  F1       : Show/Hide this Help screen",
        "  F2       : Toggle hiding Completed/Skipped files",
        "  F3       : Toggle hiding Log messages at bottom",
        "",
        "Concurrency:",
        "  F5       : Decrease Max Concurrent Jobs by 1 (Min 1)",
        "  F6       : Increase Max Concurrent Jobs by 1",
        "",
        "Script Control:",
        "  Q        : Signal graceful shutdown (finish current jobs)",
        "",
        "Statuses:",
        "  Waiting  : Queued, waiting for a worker slot",
        "  Queued   : Assigned a worker, waiting to start file operations",
        "  Copying  : Copying original to temporary directory",
        "  Encoding : Running ffmpeg encoding (QSV)",
        "  Verifying: Checking encoded file",
        "  Finalizing: Replacing original file",
        "  Done     : Encoding successful ([encoded])",
        "  Skipped  : Input was already AV1",
        "  Failed   : Encoding or file error ([error])",
        "  Cancelled: Killed by user ([killed])",
        "",
        "Press ESC to return to the main view."
    ]

    for i, line in enumerate(help_text):
        if i + 3 < max_y - 2:
            stdscr.addstr(i + 3, 2, line[:max_x-4], curses.color_pair(5))

    footer = "Press ESC to return"
    if max_y > 1:
        stdscr.addstr(max_y - 2, (max_x - len(footer)) // 2, footer, curses.A_BOLD)

    stdscr.refresh()

    stdscr.nodelay(False)
    try:
        while True:
            key = stdscr.getch()
            if key == 27:
                break
            time.sleep(0.01)
    except curses.error:
        pass


# --- Curses UI Function ---
def display_ui(stdscr, shutdown_event_ref, command_queue_ref, active_processes_info_ref, active_processes_lock_ref, config_vars):
    """Manages the curses UI display with scrolling, selection, and flicker reduction."""
    global file_status_display

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(50) # ms

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1) # Done
    curses.init_pair(2, curses.COLOR_RED, -1)   # Failed
    curses.init_pair(3, curses.COLOR_YELLOW, -1) # Skipped, Cancelled, Copying
    curses.init_pair(4, curses.COLOR_CYAN, -1)  # Encoding, Verifying, Finalizing, Running, Queued, Header/Info
    curses.init_pair(5, curses.COLOR_WHITE, -1) # Waiting, Default/Normal
    curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_WHITE) # Selected default (Waiting, Queued)
    curses.init_pair(7, curses.COLOR_RED, curses.COLOR_WHITE)   # Selected Failed
    curses.init_pair(8, curses.COLOR_YELLOW, curses.COLOR_WHITE) # Selected Skipped, Cancelled, Copying
    curses.init_pair(9, curses.COLOR_CYAN, curses.COLOR_WHITE) # Selected Encoding, Verifying, Finalizing, Running

    log_messages = []
    max_log_lines_display = 5

    file_ui_map = {}
    marked_files_ids = set()

    scroll_offset = 0
    selected_index = 0
    hide_completed = False
    show_logs = True

    for item in file_status_display:
         file_ui_map[item['id']] = item
         item['marked_to_kill'] = False

    previous_display_lines = {}

    while not shutdown_event_ref.is_set():
        while not ui_queue.empty():
            try:
                update = ui_queue.get_nowait()
                if update['type'] == 'file_update':
                    file_id = update['id']
                    if file_id in file_ui_map:
                         current_item = file_ui_map[file_id]
                         was_marked = current_item.get('marked_to_kill', False)
                         current_item.update(update)
                         if not current_item.get('is_final', False):
                              current_item['marked_to_kill'] = was_marked
                         else:
                              if file_id in marked_files_ids:
                                   marked_files_ids.remove(file_id)

                elif update['type'] == 'log':
                    log_messages.append(f"[{time.strftime('%H:%M:%S')}] {update['message']}")
                    if len(log_messages) > max_log_lines_display * 5:
                         log_messages = log_messages[-max_log_lines_display * 3:]

                elif update['type'] == 'exit_ui':
                    return

            except queue.Empty:
                break
            except Exception as e:
                log_messages.append(f"UI Queue Error: {str(e)}")

        try:
            max_y, max_x = stdscr.getmaxyx()
        except curses.error:
            time.sleep(0.1)
            continue

        min_y, min_x = 10, 60
        if max_y < min_y or max_x < min_x:
             stdscr.clear()
             try:
                 stdscr.addstr(0, 0, f"Terminal too small! Min size: {min_x}x{min_y}")
                 stdscr.addstr(1, 0, f"Current size: {max_x}x{max_y}")
             except curses.error:
                 pass
             stdscr.refresh()
             time.sleep(0.1)
             previous_display_lines = {}
             continue

        filtered_files_display = []
        for item in file_status_display:
             if hide_completed:
                  if not item.get('is_final', False):
                       filtered_files_display.append(item)
             else:
                  filtered_files_display.append(item)

        header_lines = 2
        log_header_lines = 1 if show_logs else 0
        log_lines_height = max_log_lines_display if show_logs else 0
        if show_logs:
            log_lines_height = min(max_log_lines_display, max_y - header_lines - log_header_lines - 2)
            if log_lines_height < 0: log_lines_height = 0

        log_area_start_line = max_y - log_lines_height - log_header_lines -1
        if log_area_start_line < header_lines + 1:
             log_area_start_line = header_lines + 1
             log_lines_height = max_y - log_area_start_line - log_header_lines - 1
             if log_lines_height < 0: log_lines_height = 0

        visible_lines = log_area_start_line - header_lines - 1
        if visible_lines < 1: visible_lines = 1

        total_filtered_files = len(filtered_files_display)
        max_scroll = max(0, total_filtered_files - visible_lines)
        scroll_offset = max(0, min(scroll_offset, max_scroll))

        if total_filtered_files == 0:
             selected_index = 0
        else:
             selected_index = max(0, min(selected_index, total_filtered_files - 1))

        if selected_index < scroll_offset:
             scroll_offset = selected_index
        elif total_filtered_files > visible_lines and selected_index >= scroll_offset + visible_lines:
             scroll_offset = selected_index - visible_lines + 1

        current_display_lines = {}

        header_text = f"AV1 Batch Encoder (QSV) - {config_vars['current_max_jobs']} workers - ↑↓ Select, SPACE Mark/Unmark, K Kill Marked, F1 Help, F2 Hide Done, F3 Hide Logs, F5/F6 Workers, Q Quit"
        current_display_lines[0] = (header_text[:max_x-1], curses.A_BOLD | curses.color_pair(4))

        select_col_w = 4
        size_col_w = 10
        percent_col_w = 9
        status_col_w = 12
        info_col_w = 10
        other_cols_width = select_col_w + size_col_w + status_col_w + size_col_w + percent_col_w + info_col_w + 5
        file_col_w = max(1, max_x - other_cols_width - 1)

        col_titles = (f"{'Sel':<{select_col_w}}"
                      f"{'File':<{file_col_w}} "
                      f"{'Orig':>{size_col_w}} "
                      f"{'Status':<{status_col_w}} "
                      f"{'AV1':>{size_col_w}} "
                      f"{'%Red':>{percent_col_w}} "
                      f"{'Done':<{info_col_w}}")
        current_display_lines[1] = (col_titles[:max_x-1], curses.A_UNDERLINE | curses.color_pair(4))

        display_start_line = header_lines
        for i in range(visible_lines):
            list_index = i + scroll_offset
            line_num = display_start_line + i

            if 0 <= list_index < total_filtered_files:
                item = filtered_files_display[list_index]
                file_id = item['id']

                status_disp = item.get('status', 'Waiting...')
                if item.get('cancelled'): status_disp = "Cancelled"
                elif item.get('error'): status_disp = "Failed"
                elif item.get('is_final', False):
                     if item.get('final_status_icon') == "[encoded]": status_disp = "Done"
                     elif "Skipped" in item.get('status', ''): status_disp = "Skipped"
                elif file_id in active_processes_info_ref:
                     detailed_status = item.get('status', '')
                     if "Copying" in detailed_status: status_disp = "Copying"
                     elif "Encoding" in detailed_status: status_disp = "Encoding"
                     elif "Verifying" in detailed_status: status_disp = "Verifying"
                     elif "Finalizing" in detailed_status: status_disp = "Finalizing"
                     elif "Queued" in detailed_status: status_disp = "Queued"
                     else: status_disp = "Running"
                else:
                    status_disp = "Waiting"


                mark_indicator = "[*] " if item.get('marked_to_kill') else "[ ] "

                filename_disp = item.get('filename', 'N/A')[:file_col_w-1]
                orig_s = format_size(item.get('orig_size'))[:size_col_w-1]
                av1_s = format_size(item.get('new_size'))[:size_col_w-1]
                reduct_val = item.get('reduction')
                reduct = (f"{reduct_val:.1f}%" if isinstance(reduct_val, float) else '-')[:percent_col_w-1]
                status_str = status_disp[:status_col_w-1]

                final_icon = item.get('final_status_icon', '')[:info_col_w-1]

                line_str = (f"{mark_indicator:<{select_col_w}}"
                            f"{filename_disp:<{file_col_w}} "
                            f"{orig_s:>{size_col_w}} "
                            f"{status_str:<{status_col_w}} "
                            f"{av1_s:>{size_col_w}} "
                            f"{reduct:>{percent_col_w}} "
                            f"{final_icon:<{info_col_w}}")

                # --- Refined Color Logic based on Status_disp ---
                color_attr = curses.color_pair(5) # Default: Waiting, White

                if status_disp == "Done":
                    color_attr = curses.color_pair(1) # Green
                elif status_disp == "Failed":
                    color_attr = curses.color_pair(2) # Red
                elif status_disp in ["Skipped", "Cancelled", "Copying"]:
                    color_attr = curses.color_pair(3) # Yellow
                elif status_disp in ["Encoding", "Verifying", "Finalizing", "Running", "Queued"]:
                    color_attr = curses.color_pair(4) # Cyan


                # Apply selection highlighting on top
                if list_index == selected_index:
                    if status_disp == "Failed":
                         color_attr = curses.color_pair(7) # Selected + Red
                    elif status_disp in ["Skipped", "Cancelled", "Copying"]:
                         color_attr = curses.color_pair(8) # Selected + Yellow
                    elif status_disp in ["Encoding", "Verifying", "Finalizing", "Running", "Queued"]:
                         color_attr = curses.color_pair(9) # Selected + Cyan
                    else: # Waiting
                         color_attr = curses.color_pair(6) # Selected + White


                current_display_lines[line_num] = (line_str[:max_x-1], color_attr)

            else:
                empty_line = ""
                current_display_lines[line_num] = (empty_line[:max_x-1], curses.color_pair(5))


        if show_logs and log_lines_height > 0:
             current_display_lines[log_area_start_line] = ("--- Logs ---"[:max_x-1], curses.A_BOLD | curses.color_pair(4))

             for i, log_msg in enumerate(log_messages[-log_lines_height:]):
                 line_num = log_area_start_line + 1 + i
                 if line_num < max_y:
                     current_display_lines[line_num] = (log_msg[:max_x-1], curses.color_pair(5))

        lines_to_clear = set(previous_display_lines.keys()) - set(current_display_lines.keys())
        for y in lines_to_clear:
             if y < max_y:
                 stdscr.move(y, 0)
                 stdscr.clrtoeol()

        for y, (text, attr) in current_display_lines.items():
             if y < max_y:
                 if y not in previous_display_lines or previous_display_lines[y] != (text, attr):
                     stdscr.move(y, 0)
                     stdscr.clrtoeol()
                     try:
                         stdscr.addstr(y, 0, text, attr)
                     except curses.error:
                         pass

        previous_display_lines = current_display_lines

        try:
            key = stdscr.getch()
            if key != -1:
                if key == ord('q') or key == ord('Q'):
                    ui_queue.put({'type': 'log', 'message': "Quit signal received. Finishing current jobs..."})
                    shutdown_event_ref.set()
                elif key == curses.KEY_UP:
                    if total_filtered_files > 0:
                        selected_index = max(0, selected_index - 1)
                elif key == curses.KEY_DOWN:
                    if total_filtered_files > 0:
                        selected_index = min(total_filtered_files - 1, selected_index + 1)
                elif key == curses.KEY_PPAGE:
                    if total_filtered_files > 0:
                         scroll_jump = max(1, visible_lines -1)
                         selected_index = max(0, selected_index - scroll_jump)
                elif key == curses.KEY_NPAGE:
                     if total_filtered_files > 0:
                         scroll_jump = max(1, visible_lines -1)
                         selected_index = min(total_filtered_files - 1, selected_index + scroll_jump)

                elif key == curses.KEY_F1:
                     stdscr.clear()
                     display_help(stdscr)
                     previous_display_lines = {}
                     stdscr.clear()
                     stdscr.refresh()
                     stdscr.nodelay(True)
                     stdscr.timeout(50)

                elif key == curses.KEY_F2:
                     hide_completed = not hide_completed
                     selected_index = 0
                     scroll_offset = 0
                     previous_display_lines = {}
                     stdscr.clear()
                     ui_queue.put({'type': 'log', 'message': f"Hide Completed/Skipped toggled: {'ON' if hide_completed else 'OFF'}"})
                elif key == curses.KEY_F3:
                     show_logs = not show_logs
                     selected_index = 0
                     scroll_offset = 0
                     previous_display_lines = {}
                     stdscr.clear()
                     ui_queue.put({'type': 'log', 'message': f"Show Logs toggled: {'ON' if show_logs else 'OFF'}"})
                elif key == curses.KEY_F5:
                     command_queue_ref.put({'command': 'set_max_jobs', 'value': max(1, config_vars['current_max_jobs'] - 1)})
                elif key == curses.KEY_F6:
                     command_queue_ref.put({'command': 'set_max_jobs', 'value': config_vars['current_max_jobs'] + 1})

                elif key == ord(' '):
                     if total_filtered_files > 0 and selected_index < total_filtered_files:
                         item = filtered_files_display[selected_index]
                         if not item.get('is_final', False) and not item.get('cancelled', False) and not item.get('error', False):
                              file_id_to_mark = item['id']
                              is_marked = item.get('marked_to_kill', False)
                              file_ui_map[file_id_to_mark]['marked_to_kill'] = not is_marked
                              if file_ui_map[file_id_to_mark]['marked_to_kill']:
                                   marked_files_ids.add(file_id_to_mark)
                              else:
                                   marked_files_ids.discard(file_id_to_mark)

                elif key == ord('k') or key == ord('K'):
                     if marked_files_ids:
                          ui_queue.put({'type': 'log', 'message': f"Attempting to kill {len(marked_files_ids)} marked files."})
                          command_queue_ref.put({'command': 'kill', 'file_ids': list(marked_files_ids)})

        except curses.error:
             pass
        except Exception as e:
             log_messages.append(f"UI Input Error: {str(e)}")


        stdscr.refresh()

        time.sleep(0.01)

    stdscr.clear()
    if max_y > 0 and max_x > 0:
         stdscr.addstr(0,0, "Exiting UI...", curses.color_pair(4))
    stdscr.refresh()
    time.sleep(0.5)


# --- Main Logic ---
def main_logic_with_curses(stdscr):
    global file_status_display, active_processes_info, active_processes_lock, MAX_CONCURRENT_JOBS

    overall_shutdown_event = threading.Event()

    config_vars = {'current_max_jobs': MAX_CONCURRENT_JOBS}

    try:
        TEMP_DIRECTORY.mkdir(parents=True, exist_ok=True)
        logging.debug(f"Temp directory checked/created: {TEMP_DIRECTORY}")
    except Exception as e:
         logging.critical(f"FATAL: Cannot create temp dir {TEMP_DIRECTORY}: {e}")
         ui_queue.put({'type': 'log', 'message': f"FATAL: Cannot create temp dir {TEMP_DIRECTORY}: {e}"})
         overall_shutdown_event.set()
         ui_thread = threading.Thread(target=display_ui, args=(stdscr, overall_shutdown_event, command_queue, active_processes_info, active_processes_lock, config_vars), daemon=True)
         ui_thread.start()
         time.sleep(3)
         ui_queue.put({'type': 'exit_ui'})
         ui_thread.join(timeout=5)
         return


    metrics_data = load_metrics()
    metrics_data_lock = threading.Lock()

    video_files_to_process_paths_temp = []
    resolved_source_dir = SOURCE_DIRECTORY.resolve()
    logging.debug(f"Scanning source directory: {resolved_source_dir}")
    try:
        for ext in VIDEO_EXTENSIONS:
            video_files_to_process_paths_temp.extend(list(resolved_source_dir.rglob(f"*{ext}")))
            video_files_to_process_paths_temp.extend(list(resolved_source_dir.rglob(f"*{ext.upper()}")))

        video_files_to_process_paths = sorted(list(set(p.resolve() for p in video_files_to_process_paths_temp)))
        logging.debug(f"Found {len(video_files_to_process_paths)} video files.")
    except Exception as e:
        logging.error(f"Error finding files in '{resolved_source_dir}': {e}")
        ui_queue.put({'type': 'log', 'message': f"Error finding files in '{resolved_source_dir}': {e}"})
        video_files_to_process_paths = []


    if not video_files_to_process_paths:
        logging.info(f"No video files found in '{resolved_source_dir}' or its subfolders.")
        ui_queue.put({'type': 'log', 'message': f"No video files found in '{resolved_source_dir}' or its subfolders."})
        ui_thread = threading.Thread(target=display_ui, args=(stdscr, overall_shutdown_event, command_queue, active_processes_info, active_processes_lock, config_vars), daemon=True)
        ui_thread.start()
        time.sleep(2)
        overall_shutdown_event.set()
        ui_queue.put({'type': 'exit_ui'})
        ui_thread.join(timeout=5)
        save_metrics(metrics_data)
        return

    file_status_display.clear()
    waiting_files_paths = []
    for f_path_resolved in video_files_to_process_paths:
        try:
            display_name = str(f_path_resolved.relative_to(resolved_source_dir))
        except ValueError:
            display_name = f_path_resolved.name

        item = {
            'id': str(f_path_resolved),
            'filename': display_name,
            'filename_for_sort': display_name.lower(),
            'status': 'Waiting...',
            'orig_size': None, 'new_size': None, 'reduction': None,
            'error': False, 'cancelled': False, 'is_final': False, 'final_status_icon': '',
            'marked_to_kill': False
        }
        file_status_display.append(item)
        waiting_files_paths.append(f_path_resolved)

        ui_queue.put({'type': 'file_update', **item})

    file_status_display.sort(key=lambda x: x.get('filename_for_sort', ''))


    logging.info(f"Found {len(file_status_display)} video files. Starting encoding...")
    ui_queue.put({'type': 'log', 'message': f"Found {len(file_status_display)} video files. Starting encoding..."})

    ui_thread = threading.Thread(target=display_ui, args=(stdscr, overall_shutdown_event, command_queue, active_processes_info, active_processes_lock, config_vars), daemon=True)
    ui_thread.start()

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS) as executor:
        submitted_futures = {}

        while submitted_futures or waiting_files_paths or not overall_shutdown_event.is_set():
             while not command_queue.empty():
                  try:
                      command = command_queue.get_nowait()
                      if command['command'] == 'kill':
                           file_ids_to_kill = command['file_ids']
                           logging.debug(f"Main logic received KILL command for {len(file_ids_to_kill)} files.")
                           with active_processes_lock:
                                for file_id in file_ids_to_kill:
                                     if file_id in active_processes_info:
                                          logging.debug(f"Setting cancel event for {file_id}.")
                                          active_processes_info[file_id]['cancel_event'].set()
                                     else:
                                          logging.warning(f"Received KILL for {file_id} but it's not in active_processes_info.")

                      elif command['command'] == 'set_max_jobs':
                           new_max = command['value']
                           if new_max > 0:
                                config_vars['current_max_jobs'] = new_max
                                logging.info(f"Max concurrent jobs set to {config_vars['current_max_jobs']}")
                                ui_queue.put({'type': 'log', 'message': f"Max concurrent jobs set to {config_vars['current_max_jobs']}"})
                           else:
                                logging.warning(f"Attempted to set max jobs to {new_max}, ignoring (min is 1).")
                                ui_queue.put({'type': 'log', 'message': "Max jobs must be at least 1."})


                  except queue.Empty:
                       pass
                  except Exception as e:
                       logging.error(f"Error processing UI command: {e}")
                       ui_queue.put({'type': 'log', 'message': f"Error processing UI command: {e}"})

             with active_processes_lock:
                 currently_running = len(active_processes_info)

             while currently_running < config_vars['current_max_jobs'] and waiting_files_paths and not overall_shutdown_event.is_set():
                  next_file_path = waiting_files_paths.pop(0)
                  next_file_id = str(next_file_path.resolve())

                  for item in file_status_display:
                       if item['id'] == next_file_id:
                           item['status'] = "Queued"
                           logging.debug(f"Main logic submitting {next_file_id}. Status set to 'Queued'.")
                           ui_queue.put({'type': 'file_update', **item})
                           break

                  future = executor.submit(process_file, next_file_path, next_file_id, metrics_data, metrics_data_lock, active_processes_info, active_processes_lock)
                  submitted_futures[future] = next_file_id
                  logging.debug(f"Main logic submitted future for {next_file_id}.")

                  with active_processes_lock:
                      currently_running = len(active_processes_info)


             completed_futures = []
             for future in submitted_futures:
                 if future.done():
                      completed_futures.append(future)

             for future in completed_futures:
                  file_id = submitted_futures[future]
                  del submitted_futures[future]
                  logging.debug(f"Main logic detected completion for {file_id}.")

                  try:
                      future.result()
                  except CancelledError:
                      logging.info(f"Task for {file_id} was cancelled by executor or before starting.")
                      ui_queue.put({'type': 'log', 'message': f"Task for {file_id} was cancelled by executor or before starting."})
                      for item in file_status_display:
                           if item['id'] == file_id:
                                if not item.get('is_final', False):
                                     item.update({'status': 'Cancelled (Executor)', 'final_status_icon': '[cancel]', 'cancelled': True, 'is_final': True})
                                     ui_queue.put({'type': 'file_update', **item})
                                break
                  except Exception as exc:
                       logging.error(f"Unexpected worker exception for {file_id}: {exc}", exc_info=True)
                       ui_queue.put({'type': 'log', 'message': f"Unexpected worker exception for {file_id}: {exc}"})
                       for item in file_status_display:
                           if item['id'] == file_id:
                                if not item.get('is_final', False):
                                     item.update({'status': f"Unexpected Error: {str(exc)[:50]}...", 'final_status_icon': '[error]', 'error': True, 'is_final': True})
                                     ui_queue.put({'type': 'file_update', **item})
                                break

             if overall_shutdown_event.is_set():
                 logging.info("Global shutdown event set. Shutting down executor and clearing waiting list.")
                 executor.shutdown(wait=False, cancel_futures=True)
                 waiting_files_paths.clear()

             if command_queue.empty() and not completed_futures and (currently_running >= config_vars['current_max_jobs'] or not waiting_files_paths):
                 time.sleep(0.01)


        logging.info("Main processing loop finished.")
        if not overall_shutdown_event.is_set():
            ui_queue.put({'type': 'log', 'message': "All processing tasks finished."})
        else:
             ui_queue.put({'type': 'log', 'message': "Processing interrupted or completed during shutdown."})


    save_metrics(metrics_data)
    ui_queue.put({'type': 'log', 'message': f"Metrics saved to {METRICS_FILE}."})

    if not overall_shutdown_event.is_set():
        ui_queue.put({'type': 'log', 'message': "Processing complete. Press 'q' to exit UI or waiting for tasks to finish."})
        time.sleep(1)

    overall_shutdown_event.set()
    ui_queue.put({'type': 'exit_ui'})
    ui_thread.join(timeout=5)
    logging.debug("UI thread joined.")


    try:
        if TEMP_DIRECTORY.exists():
            temp_files = list(TEMP_DIRECTORY.iterdir())
            if not temp_files:
                 logging.info(f"Removing empty temp directory: {TEMP_DIRECTORY}")
                 ui_queue.put({'type': 'log', 'message': f"Removing empty temp directory: {TEMP_DIRECTORY}"})
                 TEMP_DIRECTORY.rmdir()
            else:
                logging.info(f"Temp directory {TEMP_DIRECTORY} not empty, skipping removal.")
                ui_queue.put({'type': 'log', 'message': f"Temp directory {TEMP_DIRECTORY} not empty, skipping removal."})

    except Exception as e:
         logging.error(f"Error during temp directory cleanup: {e}")
         ui_queue.put({'type': 'log', 'message': f"Error during temp directory cleanup: {e}"})


if __name__ == '__main__':
    logging.info("Script starting.")
    critical_error = False
    error_messages = []

    for tool, path_var in [(FFMPEG_PATH, "FFMPEG_PATH"), (FFPROBE_PATH, "FFPROBE_PATH")]:
        try:
            logging.debug(f"Checking executable: {tool}")
            if not shutil.which(tool):
                 msg = f"CRITICAL: Executable '{tool}' not found in PATH."
                 logging.critical(msg)
                 error_messages.append(msg)
                 critical_error = True
                 continue
            subprocess.run([tool, "-version"], capture_output=True, check=True, text=True, timeout=5)
            logging.debug(f"Executable '{tool}' check passed.")
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            msg = f"CRITICAL: Failed to run '{tool}' (from {path_var}='{globals()[path_var]}'). Details: {e}"
            logging.critical(msg, exc_info=True)
            error_messages.append(msg)
            critical_error = True
        except Exception as e:
             msg = f"CRITICAL: Unexpected error during pre-flight check for '{tool}': {e}"
             logging.critical(msg, exc_info=True)
             error_messages.append(msg)
             critical_error = True

    logging.debug(f"Checking QSV device: {QSV_DEVICE}")
    if not QSV_DEVICE or not Path(QSV_DEVICE).exists():
        msg = f"CRITICAL: QSV device '{QSV_DEVICE}' not found or not accessible."
        logging.critical(msg)
        error_messages.append(msg)
        critical_error = True
    else:
        logging.debug("QSV device check passed.")

    logging.debug(f"Checking TEMP_DIRECTORY: {TEMP_DIRECTORY}")
    try:
        if not TEMP_DIRECTORY.exists():
            TEMP_DIRECTORY.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(TEMP_DIRECTORY.parent, os.W_OK | os.X_OK):
                 msg = f"CRITICAL: Parent of TEMP_DIRECTORY ('{TEMP_DIRECTORY.parent}') is not writable or executable."
                 logging.critical(msg)
                 error_messages.append(msg)
                 critical_error = True
            else: logging.debug("TEMP_DIRECTORY parent is writable.")
        elif not os.access(TEMP_DIRECTORY, os.W_OK | os.R_OK | os.X_OK):
            msg = f"CRITICAL: TEMP_DIRECTORY ('{TEMP_DIRECTORY}') exists but is not readable, writable, or executable."
            logging.critical(msg)
            error_messages.append(msg)
            critical_error = True
        else: logging.debug("TEMP_DIRECTORY is accessible.")
    except Exception as e:
        msg = f"CRITICAL: Error checking TEMP_DIRECTORY ('{TEMP_DIRECTORY}'): {e}"
        logging.critical(msg, exc_info=True)
        error_messages.append(msg)
        critical_error = True

    logging.debug(f"Checking SOURCE_DIRECTORY: {SOURCE_DIRECTORY}")
    try:
        resolved_source = SOURCE_DIRECTORY.resolve(strict=True)
        if not os.access(resolved_source, os.R_OK | os.W_OK | os.X_OK):
            msg = f"CRITICAL: Source directory ('{resolved_source}') not readable, writable, or executable."
            logging.critical(msg)
            error_messages.append(msg)
            critical_error = True
        else: logging.debug("SOURCE_DIRECTORY is accessible.")
    except FileNotFoundError:
        msg = f"CRITICAL: Source directory ('{SOURCE_DIRECTORY}') does not exist."
        logging.critical(msg)
        error_messages.append(msg)
        critical_error = True
    except Exception as e:
        msg = f"CRITICAL: Error accessing source directory ('{SOURCE_DIRECTORY}'): {e}"
        logging.critical(msg, exc_info=True)
        error_messages.append(msg)
        critical_error = True


    if critical_error:
        print("\n--- SCRIPT PRE-FLIGHT CHECKS FAILED ---")
        for msg in error_messages:
            print(msg)
        print("Please resolve the issues above and try again.")
        logging.critical("Pre-flight checks failed. Exiting.")
        exit(1)

    logging.info("Pre-flight checks passed. Starting Curses UI.")
    try:
        curses.wrapper(main_logic_with_curses)
    except Exception as e:
         logging.critical(f"An unhandled error occurred outside curses wrapper: {e}", exc_info=True)
         print(f"\nAn unhandled error occurred: {e}")
         if 'overall_shutdown_event' in locals() and not overall_shutdown_event.is_set():
              overall_shutdown_event.set()
         if 'metrics_data' in locals():
              save_metrics(metrics_data)
         print(f"\nAttempted to save metrics to {METRICS_FILE}.")
    finally:
        logging.info("Script execution finished.")
        print(f"\nScript execution finished. Metrics saved to {METRICS_FILE}")

#!/usr/bin/env python3

import curses
import os
import shutil
import subprocess
import threading
import time
import json
from collections import deque
from dataclasses import dataclass, field
import math
import sys 
import argparse 

# --- Configuration ---
SOURCE_DIRECTORY = "."  
TEMP_DIRECTORY = "/ssd/av1_tmp/" 
NUM_FILES_TO_PREPARE = 2  
NUM_FFMPEG_WORKERS = 1 
QSV_DEVICE = "/dev/dri/renderD128"  
FFMPEG_PATH = "ffmpeg"
FFPROBE_PATH = "ffprobe"
LOG_MAX_LINES = 300 
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.ts', '.mts', '.m2ts') 
FFMPEG_ENCODE_TIMEOUT_SECONDS = 10 * 60 

FFMPEG_BASE_ARGS = ['-y', '-hide_banner', '-loglevel', 'error']
AV1_QSV_ENCODE_ARGS = ['-c:v', 'av1_qsv', '-preset', 'medium', '-look_ahead', '1'] 
AUDIO_COPY_ARGS = ['-c:a', 'copy']
BOTTOM_STATUS_LINES = 4 
SPINNER_CHARS = ['|', '/', '-', '\\']

ARGS = None 

# --- File Item Dataclass ---
@dataclass
class FileItem:
    id: int
    original_path: str
    filename: str = field(init=False)
    extension: str = field(init=False)
    temp_source_path: str = None 
    temp_encoded_path: str = None 
    status: str = "pending" 
    status_message: str = ""
    original_size: int = 0
    encoded_size: int = None
    error_details: str = None
    input_codec: str = None 
    qsv_input_codec: str = None 
    use_cpu_decode: bool = False 
    encoding_start_time: float = None # Added to track when FFmpeg encoding starts

    def __post_init__(self):
        self.filename = os.path.basename(self.original_path)
        _ , self.extension = os.path.splitext(self.filename)

    def get_display_strings(self):
        base_filename = self.filename
        size_details_str = ""
        
        display_status_upper = self.status.upper()
        if self.status in ["deleted_zero", "deleted_error"]:
            display_status_upper = "DELETED"

        status_text_str = f"[{display_status_upper}]"
        if self.status_message:
            status_text_str += f" {self.status_message}"

        if self.status == "success" and self.original_size > 0 and self.encoded_size is not None and self.encoded_size > 0:
            reduction_abs = self.original_size - self.encoded_size
            reduction_pct = (reduction_abs / self.original_size) * 100
            size_details_str = f"({format_size(self.original_size)}) ({format_size(self.encoded_size)} | {reduction_pct:.1f}%)"
        elif self.status == "skipped" and self.original_size > 0 : 
            size_details_str = f"({format_size(self.original_size)})"
        elif self.original_size > 0: 
             size_details_str = f"({format_size(self.original_size)})"
        
        return base_filename, size_details_str, status_text_str


# --- Global State ---
all_files = []
pending_files_queue = deque()
preparing_files_list = [] 
ready_for_encode_queue = deque() 
encoding_files_list = [] 

log_messages = deque(maxlen=LOG_MAX_LINES)
stop_event = threading.Event()
ui_needs_update = threading.Event()
ui_lock = threading.Lock() 
spinner_index = 0 

# --- Helper Functions ---
def format_size(size_bytes):
    if size_bytes is None or size_bytes <= 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    if size_bytes < 1: return "0B"
    i = int(math.floor(math.log(size_bytes, 1024)))
    i = max(0, min(i, len(size_name) - 1)) 
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s}{size_name[i]}" 

def add_log_message(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_messages.append(f"[{timestamp}] {message}")
    ui_needs_update.set()

def get_video_codec_info(filepath):
    stdout = "" 
    stderr = "" 
    try:
        command = [
            FFPROBE_PATH, '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-select_streams', 'v:0', filepath
        ]
        add_log_message(f"FFPROBE: Running for {os.path.basename(filepath)}")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        stdout, stderr = process.communicate(timeout=30)

        if process.returncode == 0:
            data = json.loads(stdout)
            if data.get('streams') and data['streams']: 
                codec_name = data['streams'][0].get('codec_name')
                add_log_message(f"FFPROBE: Codec for {os.path.basename(filepath)} is {codec_name}")
                return codec_name
            else:
                add_log_message(f"FFPROBE: No video streams found for {os.path.basename(filepath)}. FFprobe stdout: {stdout.strip() if stdout else '<empty>'}")
                return None
        else:
            full_error_output = f"Stdout: '{stdout.strip() if stdout else '<empty>'}' Stderr: '{stderr.strip() if stderr else '<empty>'}'"
            add_log_message(f"FFPROBE Error for {os.path.basename(filepath)}: RC={process.returncode} Output: {full_error_output}")
            return None
    except subprocess.TimeoutExpired:
        add_log_message(f"FFPROBE: Timeout for {os.path.basename(filepath)}")
        if 'process' in locals() and hasattr(process, 'kill') and process.poll() is None:
            process.kill()
        return None
    except json.JSONDecodeError as je:
        add_log_message(f"FFPROBE: JSON decode error for {os.path.basename(filepath)}: {je}. FFprobe stdout: {stdout[:200] if stdout else '<empty>'}")
        return None
    except Exception as e:
        add_log_message(f"FFPROBE: Exception for {os.path.basename(filepath)}: {type(e).__name__} {e}")
        return None

# --- Worker Threads ---
def file_scanner_worker():
    global ARGS 
    add_log_message(f"SCANNER: Starting file scan in '{SOURCE_DIRECTORY}'")
    file_id_counter = 0
    discovered_files_this_scan = [] 

    abs_source_directory = os.path.abspath(SOURCE_DIRECTORY)
    abs_temp_directory = os.path.abspath(TEMP_DIRECTORY)
    add_log_message(f"SCANNER: Absolute source path: {abs_source_directory}")
    add_log_message(f"SCANNER: Absolute temp path: {abs_temp_directory}")

    for root, _, files in os.walk(abs_source_directory): 
        if stop_event.is_set():
            add_log_message("SCANNER: Stop event received, halting scan.")
            return
        
        if os.path.commonpath([root, abs_temp_directory]) == abs_temp_directory:
            add_log_message(f"SCANNER: Skipping temp directory scan: {root}")
            continue

        for file_idx, filename in enumerate(files):
            if stop_event.is_set():
                add_log_message("SCANNER: Stop event received, halting scan.")
                return
            
            if filename.lower().endswith(VIDEO_EXTENSIONS):
                original_path = os.path.join(root, filename) 
                item = None 
                try:
                    original_size = os.path.getsize(original_path)
                    item = FileItem(id=file_id_counter, original_path=original_path, original_size=original_size)
                    
                    if original_size == 0 and ARGS.delete_zeros:
                        add_log_message(f"SCANNER: File '{original_path}' is 0 bytes. Deleting as per --delete-zeros.")
                        try:
                            os.remove(original_path)
                            add_log_message(f"SCANNER: Successfully deleted 0-byte file: {original_path}")
                            item.status = "deleted_zero"
                            item.status_message = "0-byte file (deleted)"
                        except OSError as e_del:
                            add_log_message(f"SCANNER: Error deleting 0-byte file {original_path}: {e_del}")
                            item.status = "error" 
                            item.status_message = "0-byte (delete failed)"
                            item.error_details = str(e_del)
                    
                    discovered_files_this_scan.append(item)
                    file_id_counter += 1

                    if file_idx % 50 == 0: 
                        with ui_lock:
                            all_files[:] = sorted(discovered_files_this_scan, key=lambda x: x.original_path) 
                        ui_needs_update.set()
                        time.sleep(0.01) 
                except OSError as e:
                    add_log_message(f"SCANNER: Error accessing {original_path}: {e}")
                    if item is None: 
                        item = FileItem(id=file_id_counter, original_path=original_path, original_size=0)
                        file_id_counter +=1
                    item.status = "error"
                    item.status_message = "Access error"
                    item.error_details = str(e)
                    discovered_files_this_scan.append(item) 
    
    with ui_lock:
        all_files[:] = sorted(discovered_files_this_scan, key=lambda x: x.original_path) 
        pending_files_queue.clear() 
        
        for item_to_queue in all_files:
            if item_to_queue.status == "pending": 
                pending_files_queue.append(item_to_queue)
            
    add_log_message(f"SCANNER: Found {len(all_files)} video files. Queued {len(pending_files_queue)} for processing.")
    ui_needs_update.set()

def file_preparer_worker():
    global ARGS
    while not stop_event.is_set():
        with ui_lock:
            no_pending_in_queue = not pending_files_queue
            all_system_files_processed_past_pending = all(f.status != "pending" for f in all_files)
            other_queues_empty = not ready_for_encode_queue and not encoding_files_list and not preparing_files_list

        if no_pending_in_queue and all_system_files_processed_past_pending and other_queues_empty:
            add_log_message("PREPARER: No pending files and other queues empty. Preparer idling.")
            time.sleep(2)
            continue
        elif no_pending_in_queue: 
            time.sleep(0.5)
            continue

        with ui_lock:
            num_being_readied = len(ready_for_encode_queue) + len(preparing_files_list)
            num_can_prepare = NUM_FILES_TO_PREPARE - num_being_readied
            
        if num_can_prepare <= 0:
            time.sleep(0.2)
            continue

        try:
            with ui_lock:
                if not pending_files_queue: continue
                file_item = pending_files_queue.popleft()
                if file_item.status in ["cancelled", "deleted_zero", "deleted_error"]: 
                    add_log_message(f"PREPARER: Skipped item {file_item.filename} from pending queue due to status: {file_item.status}.")
                    continue 
                preparing_files_list.append(file_item)
        except IndexError: 
            time.sleep(0.1)
            continue
        
        try:
            with ui_lock:
                if file_item.status in ["cancelled", "skipped", "error", "success", "deleted_zero", "deleted_error"]: 
                    if file_item in preparing_files_list: preparing_files_list.remove(file_item)
                    add_log_message(f"PREPARER: Item {file_item.filename} already in terminal/skip state '{file_item.status}', removing from preparing.")
                    continue
                file_item.status = "checking"
                file_item.status_message = "ffprobe"
            ui_needs_update.set()

            codec = get_video_codec_info(file_item.original_path)
            with ui_lock:
                if file_item not in preparing_files_list and file_item.status == "cancelled": 
                    add_log_message(f"PREPARER: Item {file_item.filename} was cancelled during ffprobe check, not proceeding.")
                    continue 
            
            file_item.input_codec = codec

            if stop_event.is_set(): break
            with ui_lock: 
                if file_item.status == "cancelled": 
                    add_log_message(f"PREPARER: Item {file_item.filename} cancelled during ffprobe. Skipping copy.")
                    if file_item in preparing_files_list: preparing_files_list.remove(file_item)
                    continue

            if codec == 'av1':
                with ui_lock:
                    file_item.status = "skipped"
                    file_item.status_message = "Already AV1"
                    if file_item in preparing_files_list: preparing_files_list.remove(file_item)
                ui_needs_update.set()
                add_log_message(f"PREPARER: Skipped {file_item.filename}, already AV1.")
                continue
            
            if codec is None: 
                with ui_lock:
                    file_item.status = "error" 
                    file_item.status_message = "ffprobe failed"
                    if not file_item.error_details: 
                         file_item.error_details = "ffprobe failed to determine codec or file is invalid."
                    
                    if ARGS.delete_errors: 
                        add_log_message(f"PREPARER: --delete-errors active. Attempting to delete original source '{file_item.original_path}' due to ffprobe error.")
                        try:
                            os.remove(file_item.original_path)
                            add_log_message(f"PREPARER: Successfully deleted original source '{file_item.original_path}' due to ffprobe error.")
                            file_item.status = "deleted_error" 
                            file_item.status_message = "ffprobe error (deleted)"
                        except OSError as e_del:
                            add_log_message(f"PREPARER: Error deleting original source '{file_item.original_path}' after ffprobe error: {e_del}")
                            file_item.status_message = "ffprobe error (del failed)" 
                            file_item.error_details += f" | Delete failed: {e_del}"
                    
                    if file_item in preparing_files_list: preparing_files_list.remove(file_item)
                ui_needs_update.set()
                add_log_message(f"PREPARER: Error checking codec for {file_item.filename}. File status: {file_item.status}.")
                continue 

            qsv_decoder_map = {"h264": "h264_qsv", "hevc": "hevc_qsv", "mpeg2video": "mpeg2_qsv", "vp9": "vp9_qsv"}
            file_item.qsv_input_codec = qsv_decoder_map.get(codec)
            if not file_item.qsv_input_codec:
                file_item.use_cpu_decode = True 
                add_log_message(f"PREPARER: No direct QSV decoder for {codec} on {file_item.filename}. Will try CPU decode to QSV surface.")

            with ui_lock: 
                if file_item.status == "cancelled": 
                    add_log_message(f"PREPARER: Item {file_item.filename} cancelled before copy. Skipping.")
                    if file_item in preparing_files_list: preparing_files_list.remove(file_item)
                    continue
                file_item.status = "transferring_to_temp"
                file_item.status_message = "Copying..."
            ui_needs_update.set()

            os.makedirs(TEMP_DIRECTORY, exist_ok=True)
            temp_source_filename = f"{file_item.id}_{file_item.filename}"
            file_item.temp_source_path = os.path.join(TEMP_DIRECTORY, temp_source_filename)

            add_log_message(f"PREPARER: Copying {file_item.filename} to {file_item.temp_source_path}")
            shutil.copy2(file_item.original_path, file_item.temp_source_path)
            add_log_message(f"PREPARER: Copied {file_item.filename} to temp.")

            with ui_lock:
                 if file_item.status == "cancelled": 
                    add_log_message(f"PREPARER: Item {file_item.filename} cancelled during copy. Cleaning up temp source.")
                    if file_item.temp_source_path and os.path.exists(file_item.temp_source_path):
                        try: os.remove(file_item.temp_source_path)
                        except OSError as oe: add_log_message(f"PREPARER: Error cleaning temp source for item cancelled during copy: {oe}")
                    if file_item in preparing_files_list: preparing_files_list.remove(file_item)
                    continue
                 file_item.status = "ready"
                 file_item.status_message = "In temp"
                 if file_item in preparing_files_list: preparing_files_list.remove(file_item)
                 ready_for_encode_queue.append(file_item)
            ui_needs_update.set()

        except Exception as e:
            with ui_lock:
                file_item.status = "error"
                file_item.status_message = "Preparation failed"
                file_item.error_details = str(e)
                if file_item in preparing_files_list: preparing_files_list.remove(file_item)
            add_log_message(f"PREPARER: Error preparing {file_item.filename}: {e}")
            ui_needs_update.set()
            if file_item.temp_source_path and os.path.exists(file_item.temp_source_path):
                try: os.remove(file_item.temp_source_path)
                except OSError as oe: add_log_message(f"PREPARER: Error cleaning up temp file {file_item.temp_source_path}: {oe}")
        time.sleep(0.05)
    add_log_message("PREPARER: Shutting down.")

def ffmpeg_encoder_worker():
    global ARGS 
    ffmpeg_encoder_worker.last_q_len = -1
    ffmpeg_encoder_worker.last_enc_list_len = -1

    while not stop_event.is_set():
        current_encoding_item = None
        with ui_lock:
            q_len = len(ready_for_encode_queue)
            enc_list_len = len(encoding_files_list)
            if q_len != ffmpeg_encoder_worker.last_q_len or enc_list_len != ffmpeg_encoder_worker.last_enc_list_len :
                add_log_message(f"ENCODER_LOOP_START: ReadyQ: {q_len}, EncodingList: {enc_list_len}, MaxWorkers: {NUM_FFMPEG_WORKERS}")
                ffmpeg_encoder_worker.last_q_len = q_len
                ffmpeg_encoder_worker.last_enc_list_len = enc_list_len
            
            if ready_for_encode_queue and len(encoding_files_list) < NUM_FFMPEG_WORKERS:
                current_encoding_item = ready_for_encode_queue.popleft()
                encoding_files_list.append(current_encoding_item)
                add_log_message(f"ENCODER_PICKED: Picked '{current_encoding_item.filename}'. EncodingList size: {len(encoding_files_list)}, ReadyQ size: {len(ready_for_encode_queue)}")
        
        if not current_encoding_item:
            time.sleep(0.2) 
            with ui_lock:
                if not pending_files_queue and not preparing_files_list and not ready_for_encode_queue and not encoding_files_list:
                    all_terminal = True
                    for f_item_check in all_files:
                        if f_item_check.status not in ["success", "skipped", "error", "cancelled", "deleted_zero", "deleted_error"]:
                            all_terminal = False; break
                    if all_terminal:
                        add_log_message("ENCODER: All files processed. Encoder idling.")
                        time.sleep(2) 
            continue

        file_item = current_encoding_item
        original_filename_for_log = file_item.filename 
        process = None 
        timed_out = False 
        
        try:
            is_already_cancelled_or_deleted = False
            with ui_lock:
                if file_item.status in ["cancelled", "deleted_zero", "deleted_error"]: 
                    is_already_cancelled_or_deleted = True
            
            if is_already_cancelled_or_deleted:
                add_log_message(f"ENCODER: Item '{file_item.filename}' was already in terminal state '{file_item.status}' when picked. Cleaning up temp source if any.")
                if file_item.temp_source_path and os.path.exists(file_item.temp_source_path):
                    try: 
                        os.remove(file_item.temp_source_path)
                        add_log_message(f"ENCODER: Cleaned temp source for pre-terminal item: {file_item.temp_source_path}")
                    except Exception as e_clean: add_log_message(f"ENCODER: Error cleaning temp source for pre-terminal '{file_item.filename}': {e_clean}")
            else: 
                with ui_lock:
                    file_item.status = "encoding"
                    file_item.status_message = "FFmpeg running"
                    file_item.encoding_start_time = time.time() # Set encoding start time
                ui_needs_update.set()

                base, ext = os.path.splitext(file_item.temp_source_path)
                file_item.temp_encoded_path = base + "_av1" + ext 

                add_log_message(f"ENCODER: Starting FFmpeg for {file_item.filename}")
                
                ffmpeg_command_list = [FFMPEG_PATH] + list(FFMPEG_BASE_ARGS)
                if file_item.use_cpu_decode or not file_item.qsv_input_codec:
                    ffmpeg_command_list.extend(['-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv', '-i', file_item.temp_source_path])
                else:
                    ffmpeg_command_list.extend(['-hwaccel', 'qsv', '-qsv_device', QSV_DEVICE, 
                                                 '-c:v', file_item.qsv_input_codec, '-i', file_item.temp_source_path])
                ffmpeg_command_list.extend(AV1_QSV_ENCODE_ARGS)
                ffmpeg_command_list.extend(AUDIO_COPY_ARGS)
                ffmpeg_command_list.append(file_item.temp_encoded_path)

                add_log_message(f"FFMPEG CMD_LIST: {' '.join(ffmpeg_command_list)}")
                
                # start_time already set when status became "encoding"
                process = subprocess.Popen(ffmpeg_command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
                
                user_initiated_cancel_detected_in_poll = False
                app_stop_event_detected_in_poll = False

                while process.poll() is None:
                    if stop_event.is_set():
                        app_stop_event_detected_in_poll = True
                        add_log_message(f"ENCODER: App stop event detected for FFmpeg on '{file_item.filename}'. Terminating.")
                        process.terminate()
                        break 
                    with ui_lock: 
                        if file_item.status == "cancelled":
                            user_initiated_cancel_detected_in_poll = True
                    if user_initiated_cancel_detected_in_poll:
                        add_log_message(f"ENCODER: User cancellation detected for FFmpeg on '{file_item.filename}'. Terminating.")
                        process.terminate()
                        break
                    
                    if FFMPEG_ENCODE_TIMEOUT_SECONDS > 0 and \
                       file_item.encoding_start_time is not None and \
                       (time.time() - file_item.encoding_start_time) > FFMPEG_ENCODE_TIMEOUT_SECONDS:
                        add_log_message(f"ENCODER: FFmpeg timeout for '{file_item.filename}' after {FFMPEG_ENCODE_TIMEOUT_SECONDS}s. Terminating.")
                        process.terminate()
                        timed_out = True
                        break
                    time.sleep(0.1)
                
                if not timed_out:
                    if app_stop_event_detected_in_poll or user_initiated_cancel_detected_in_poll:
                        try:
                            add_log_message(f"ENCODER: Waiting for FFmpeg on '{file_item.filename}' to terminate (cancel/stop)...")
                            process.wait(timeout=10) 
                            add_log_message(f"ENCODER: FFmpeg on '{file_item.filename}' terminated with RC={process.returncode}.")
                        except subprocess.TimeoutExpired:
                            add_log_message(f"ENCODER: FFmpeg for '{file_item.filename}' did not terminate gracefully (cancel/stop), killing.")
                            process.kill(); process.wait() 
                            add_log_message(f"ENCODER: FFmpeg for '{file_item.filename}' killed.")
                    
                    stdout_data, stderr_data = process.communicate() 
                    return_code = process.returncode
                else: 
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                    stdout_data, stderr_data = "", "FFmpeg process timed out." 
                    return_code = -9 
                
                final_status_is_user_cancelled = False
                with ui_lock: 
                    if file_item.status == "cancelled": 
                        final_status_is_user_cancelled = True

                if timed_out:
                    with ui_lock:
                        file_item.status = "error"
                        file_item.status_message = "FFmpeg Timeout"
                        file_item.error_details = f"Process exceeded timeout of {FFMPEG_ENCODE_TIMEOUT_SECONDS}s."
                    add_log_message(f"ENCODER: '{file_item.filename}' marked as error due to timeout.")
                elif final_status_is_user_cancelled:
                    add_log_message(f"ENCODER: Confirmed cancelled status for '{file_item.filename}' post-FFmpeg (RC={return_code}). Cleaning up.")
                elif app_stop_event_detected_in_poll: 
                    with ui_lock:
                        file_item.status = "error"; file_item.status_message = "Interrupted (app exit)"
                        file_item.error_details = f"Process stopped by application exit. FFmpeg RC={return_code}"
                    add_log_message(f"ENCODER: Marked '{file_item.filename}' as interrupted due to app exit. RC={return_code}")
                elif return_code == 0: 
                    add_log_message(f"ENCODER: FFmpeg success for {file_item.filename}")
                    if not os.path.exists(file_item.temp_encoded_path):
                         with ui_lock:
                            file_item.status = "error"; file_item.status_message = "Output missing"
                            file_item.error_details = f"FFmpeg success but output {file_item.temp_encoded_path} missing."
                         add_log_message(f"ENCODER: ERROR - FFmpeg success but output missing for {file_item.filename}")
                    else: 
                        file_item.encoded_size = os.path.getsize(file_item.temp_encoded_path)
                        if file_item.temp_source_path and os.path.exists(file_item.temp_source_path): os.remove(file_item.temp_source_path)
                        final_temp_name = os.path.splitext(file_item.temp_source_path)[0] + file_item.extension 
                        shutil.move(file_item.temp_encoded_path, final_temp_name)
                        file_item.temp_encoded_path = None 
                        with ui_lock: file_item.status = "transferring_to_source"; file_item.status_message = "Moving..."
                        ui_needs_update.set()
                        os.makedirs(os.path.dirname(file_item.original_path), exist_ok=True)
                        add_log_message(f"ENCODER: Moving {final_temp_name} to {file_item.original_path}")
                        shutil.move(final_temp_name, file_item.original_path)
                        with ui_lock: file_item.status = "success"; file_item.status_message = "AV1 Encoded"
                        add_log_message(f"ENCODER: Successfully processed and replaced {file_item.filename}")
                else: 
                    err_msg = stderr_data.strip() if stderr_data else stdout_data.strip()
                    with ui_lock:
                        file_item.status = "error"; file_item.status_message = "FFmpeg failed"
                        file_item.error_details = f"FFmpeg Error (code {return_code}): {err_msg}"
                    add_log_message(f"ENCODER: FFmpeg error for '{file_item.filename}'. Code: {return_code}. Stderr: {err_msg[:500]}") 
                
                if file_item.status != "success" and file_item.status != "transferring_to_source":
                    if file_item.temp_encoded_path and os.path.exists(file_item.temp_encoded_path): os.remove(file_item.temp_encoded_path)
                    if file_item.temp_source_path and os.path.exists(file_item.temp_source_path): os.remove(file_item.temp_source_path)
                
                # Reset encoding_start_time after processing (success or failure)
                with ui_lock:
                    file_item.encoding_start_time = None
        
        except Exception as e: 
            with ui_lock:
                file_item.status = "error"; file_item.status_message = "Processing exception"
                file_item.error_details = str(e)
                file_item.encoding_start_time = None # Reset on exception too
            add_log_message(f"ENCODER: Exception processing '{original_filename_for_log}': {type(e).__name__} {e}")
            if process and process.poll() is None:
                add_log_message(f"ENCODER: Terminating FFmpeg for '{original_filename_for_log}' due to exception.")
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill()
            if hasattr(file_item,'temp_source_path') and file_item.temp_source_path and os.path.exists(file_item.temp_source_path):
                try: os.remove(file_item.temp_source_path)
                except Exception: pass
            if hasattr(file_item,'temp_encoded_path') and file_item.temp_encoded_path and os.path.exists(file_item.temp_encoded_path):
                try: os.remove(file_item.temp_encoded_path)
                except Exception: pass
        finally:
            with ui_lock:
                if file_item in encoding_files_list: 
                    encoding_files_list.remove(file_item)
                    add_log_message(f"ENCODER_FINALLY: Removed '{original_filename_for_log}' from EncodingList. Size now: {len(encoding_files_list)}")
                    ffmpeg_encoder_worker.last_enc_list_len = len(encoding_files_list) 
                else:
                    add_log_message(f"ENCODER_FINALLY: Item '{original_filename_for_log}' was NOT in EncodingList.")
            ui_needs_update.set()
            time.sleep(0.01) 
    add_log_message("ENCODER: Shutting down.")

# --- Curses UI ---
CP_SUCCESS, CP_TRANSFERRING, CP_ENCODING_TEXT_COLOR, CP_ERROR, CP_READY, CP_DEFAULT_PENDING, \
CP_SELECTED, CP_CANCELLED_SKIPPED_BASE, CP_ENCODING_HIGHLIGHT_BG, CP_DELETED, CP_TIMEOUT_TEXT = range(1, 12) # Added CP_TIMEOUT_TEXT

def draw_ui(stdscr, current_selection_idx, scroll_offset, show_help, show_log, window_height, window_width):
    global spinner_index
    stdscr.erase()
    
    color_pairs = {i: curses.color_pair(i) for i in range(1, 12)} 
    COLOR_SUCCESS = color_pairs.get(CP_SUCCESS, curses.A_NORMAL) 
    COLOR_TRANSFERRING = color_pairs.get(CP_TRANSFERRING, curses.A_NORMAL)
    COLOR_ENCODING_TEXT = color_pairs.get(CP_ENCODING_TEXT_COLOR, curses.A_NORMAL) 
    COLOR_ERROR = color_pairs.get(CP_ERROR, curses.A_NORMAL)
    COLOR_READY = color_pairs.get(CP_READY, curses.A_NORMAL) 
    COLOR_DEFAULT = color_pairs.get(CP_DEFAULT_PENDING, curses.A_NORMAL)
    COLOR_SELECTED = color_pairs.get(CP_SELECTED, curses.A_REVERSE) 
    COLOR_CANCELLED_SKIPPED_BASE_ATTR = color_pairs.get(CP_CANCELLED_SKIPPED_BASE, curses.A_NORMAL)
    COLOR_ENCODING_HIGHLIGHT_BG = color_pairs.get(CP_ENCODING_HIGHLIGHT_BG, curses.A_NORMAL) 
    COLOR_DELETED = color_pairs.get(CP_DELETED, curses.A_NORMAL)
    COLOR_TIMEOUT_TEXT = color_pairs.get(CP_TIMEOUT_TEXT, curses.A_BOLD) # Fallback to A_BOLD


    status_color_map = {
        "success": COLOR_SUCCESS, 
        "skipped": COLOR_CANCELLED_SKIPPED_BASE_ATTR, 
        "transferring_to_temp": COLOR_TRANSFERRING, "transferring_to_source": COLOR_TRANSFERRING,
        "encoding": COLOR_ENCODING_TEXT, 
        "error": COLOR_ERROR, "ready": COLOR_READY,
        "cancelled": COLOR_CANCELLED_SKIPPED_BASE_ATTR, 
        "pending": COLOR_DEFAULT, "checking": COLOR_TRANSFERRING, 
        "deleted_zero": COLOR_DELETED, "deleted_error": COLOR_DELETED,
    }

    # --- Header ---
    base_header_text = "AV1 Batch Encoder | F1: Help | F2: Log | 'c': Cancel | PgUp/PgDn | Q: Quit"
    timeout_display_str = ""
    
    with ui_lock:
        current_encoding_item_for_header = encoding_files_list[0] if encoding_files_list else None

    if current_encoding_item_for_header and \
       current_encoding_item_for_header.encoding_start_time is not None and \
       FFMPEG_ENCODE_TIMEOUT_SECONDS > 0:
        elapsed_time = time.time() - current_encoding_item_for_header.encoding_start_time
        remaining_time = FFMPEG_ENCODE_TIMEOUT_SECONDS - elapsed_time
        if remaining_time < 0: remaining_time = 0
        minutes = int(remaining_time // 60)
        seconds = int(remaining_time % 60)
        timeout_display_str = f"Timeout: {minutes:02d}:{seconds:02d}"

    header_to_draw = base_header_text
    if timeout_display_str:
        # Adjust header length if timeout is shown
        available_for_main_header = window_width - 1 - len(timeout_display_str) - 2 # -2 for spacing
        if len(header_to_draw) > available_for_main_header and available_for_main_header > 0 :
            header_to_draw = header_to_draw[:available_for_main_header-3] + "..." if available_for_main_header > 3 else header_to_draw[:available_for_main_header]
        elif available_for_main_header <=0: # Not enough space for main header at all
             header_to_draw = ""
    
    stdscr.addstr(0, 0, header_to_draw[:window_width-1], curses.A_BOLD)
    if timeout_display_str:
        try:
            stdscr.addstr(0, window_width - 1 - len(timeout_display_str), timeout_display_str, COLOR_TIMEOUT_TEXT | curses.A_BOLD)
        except curses.error: pass # If window too small


    # --- Summary ---
    with ui_lock:
        s_pending = sum(1 for f in all_files if f.status == "pending")
        s_ready_q_len = len(ready_for_encode_queue)
        s_encoding_list_len = len(encoding_files_list)
        s_success = sum(1 for f in all_files if f.status == "success")
        s_skipped = sum(1 for f in all_files if f.status == "skipped")
        s_error = sum(1 for f in all_files if f.status == "error")
        s_cancelled = sum(1 for f in all_files if f.status == "cancelled")
        s_deleted = sum(1 for f in all_files if f.status in ["deleted_zero", "deleted_error"])
        s_total = len(all_files)
    summary_text = f"Total: {s_total} | Pend: {s_pending} | Ready: {s_ready_q_len} | Enc: {s_encoding_list_len} | Done: {s_success+s_skipped} (S:{s_success},K:{s_skipped}) | Err: {s_error} | Canc: {s_cancelled} | Del: {s_deleted}"
    stdscr.addstr(1, 0, summary_text[:window_width-1])

    view_area_start_y = 2
    available_height_for_views = window_height - view_area_start_y - BOTTOM_STATUS_LINES
    
    list_area_height = available_height_for_views
    log_area_height = 0
    log_display_start_y = 0

    if show_log:
        list_area_height = available_height_for_views // 2
        log_area_height = available_height_for_views - list_area_height
        log_display_start_y = view_area_start_y + list_area_height
    
    list_area_height = max(1, list_area_height)
    log_area_height = max(0, log_area_height)

    with ui_lock:
        num_files = len(all_files)
        if num_files == 0: visible_files = []
        else:
            current_selection_idx = max(0, min(current_selection_idx, num_files - 1))
            scroll_offset = max(0, min(scroll_offset, num_files - list_area_height if num_files > list_area_height else 0))
            visible_files = all_files[scroll_offset : scroll_offset + list_area_height]

    for i, file_item in enumerate(visible_files):
        y_pos = i + view_area_start_y 
        if y_pos >= view_area_start_y + list_area_height: break 

        base_filename, size_details_str, status_text_str = file_item.get_display_strings()
        
        line_attr = status_color_map.get(file_item.status, COLOR_DEFAULT)
        if file_item.status == "cancelled" or file_item.status == "skipped": 
            line_attr |= curses.A_DIM
        elif file_item.status == "encoding": 
             line_attr |= curses.A_BOLD
        
        actual_idx_in_all_files = scroll_offset + i
        if actual_idx_in_all_files == current_selection_idx: line_attr = COLOR_SELECTED

        cursor_char = ">" if actual_idx_in_all_files == current_selection_idx and line_attr != COLOR_SELECTED else " "
        
        available_width = window_width - 1 - len(cursor_char) 
        max_status_len = 30 
        if len(status_text_str) > max_status_len:
            status_text_str = status_text_str[:max_status_len-3] + "..."
        
        max_size_details_len = 35 
        if len(size_details_str) > max_size_details_len:
            size_details_str = size_details_str[:max_size_details_len-3] + "..."

        space_for_filename = available_width - len(size_details_str) - len(status_text_str) - (1 if size_details_str else 0) - (1 if status_text_str else 0)
        
        filename_display = base_filename
        if space_for_filename <= 3: 
            filename_display = "..." if space_for_filename > 0 else ""
        elif len(base_filename) > space_for_filename:
            filename_display = base_filename[:space_for_filename-3] + "..."
        
        line_parts = [cursor_char, filename_display]
        current_line_len = len(cursor_char) + len(filename_display)
        
        right_part = ""
        if size_details_str:
            right_part += size_details_str
        if status_text_str:
            if right_part: right_part += " " 
            right_part += status_text_str
        
        padding_len = available_width - len(filename_display) - len(right_part)
        padding_len = max(1, padding_len) 

        line_parts.append(" " * padding_len)
        line_parts.append(right_part)
            
        full_line = "".join(line_parts)
        
        try: 
            stdscr.addstr(y_pos, 0, full_line[:window_width-1], line_attr)
        except curses.error: pass 

    if show_log and log_area_height > 1:
        log_win = stdscr.subwin(log_area_height, window_width, log_display_start_y, 0)
        log_win.erase(); log_win.box()
        log_win.addstr(0, 2, "Live Log (F2 to close)", curses.A_BOLD)
        with ui_lock: display_logs = list(log_messages)[- (log_area_height - 2) :] 
        for i, msg in enumerate(display_logs):
            if i + 1 < log_area_height -1: 
                try: log_win.addstr(i + 1, 1, msg[:window_width-2])
                except curses.error: pass 
        log_win.refresh()

    bottom_panel_start_y = window_height - BOTTOM_STATUS_LINES
    try:
        stdscr.hline(bottom_panel_start_y, 0, curses.ACS_HLINE, window_width)
    except curses.error: pass 


    with ui_lock:
        encoding_item = encoding_files_list[0] if encoding_files_list else None
        ready_item1 = ready_for_encode_queue[0] if len(ready_for_encode_queue) > 0 else None
        ready_item2 = ready_for_encode_queue[1] if len(ready_for_encode_queue) > 1 else None

    status_lines_data = [
        ("Encoding:", encoding_item),
        ("Next #1: ", ready_item1),
        ("Next #2: ", ready_item2)
    ]
    
    current_spinner_char = SPINNER_CHARS[spinner_index % len(SPINNER_CHARS)]
    spinner_index +=1

    for i, (label, item) in enumerate(status_lines_data):
        line_y = bottom_panel_start_y + 1 + i
        if line_y >= window_height: break 

        display_line_attr = COLOR_DEFAULT 
        
        if item:
            base_fn, _, status_txt_item = item.get_display_strings() 
            item_status_color_for_text = status_color_map.get(item.status, COLOR_DEFAULT) 
            if item.status == "cancelled" or item.status == "skipped": 
                item_status_color_for_text |= curses.A_DIM
            elif item.status in ["deleted_zero", "deleted_error"]:
                item_status_color_for_text = COLOR_DELETED 
            
            item_display_name = base_fn 
            
            if label == "Encoding:":
                display_line_attr = COLOR_ENCODING_HIGHLIGHT_BG 
                status_txt_item = f"{current_spinner_char} {status_txt_item}"
                item_status_color_for_text = COLOR_ENCODING_HIGHLIGHT_BG 

            left_part = f"{label}{item_display_name}"
            right_part = status_txt_item

            max_left_len = window_width - 1 - len(right_part) -1 
            if len(left_part) > max_left_len and max_left_len > 3:
                left_part = left_part[:max_left_len-3] + "..."
            elif len(left_part) > max_left_len:
                 left_part = left_part[:max_left_len]
            
            padding = window_width - 1 - len(left_part) - len(right_part)
            padding = max(0, padding)

            full_line_text = f"{left_part}{' '*padding}{right_part}"

            try:
                if label == "Encoding:":
                     stdscr.addstr(line_y, 0, full_line_text.ljust(window_width-1)[:window_width-1], display_line_attr)
                else: 
                    stdscr.addstr(line_y, 0, left_part[:window_width -1 -len(right_part) -1], COLOR_DEFAULT) 
                    stdscr.addstr(line_y, window_width -1 - len(right_part), right_part, item_status_color_for_text) 
            except curses.error: pass
        else:
            try: stdscr.addstr(line_y, 0, (label + " <none>")[:window_width-1], COLOR_DEFAULT)
            except curses.error: pass


    if show_help:
        help_win_height, help_win_width = 10, 60
        help_win_y, help_win_x = (window_height - help_win_height) // 2, (window_width - help_win_width) // 2
        help_win = curses.newwin(help_win_height, help_win_width, help_win_y, help_win_x)
        help_win.border(); help_win.addstr(1, 2, "Help (F1 to close)", curses.A_BOLD)
        help_win.addstr(3, 2, "Up/Down Arrows: Scroll file list"); help_win.addstr(4, 2, "PgUp/PgDn: Page scroll")
        help_win.addstr(5, 2, "'c' or 'C': Cancel processing for selected file"); help_win.addstr(6, 2, "F2: Toggle live log view")
        help_win.addstr(7, 2, "'q' or 'Q': Quit the application"); help_win.refresh()

    stdscr.refresh()

def cleanup_all_temp_files():
    add_log_message(f"CLEANUP: Starting final cleanup of TEMP_DIRECTORY: {TEMP_DIRECTORY}")
    cleaned_count = 0
    error_count = 0
    
    files_to_check_for_cleanup = set()
    with ui_lock: 
        for item in all_files:
            if item.temp_source_path:
                files_to_check_for_cleanup.add(item.temp_source_path)
            if item.temp_encoded_path: 
                files_to_check_for_cleanup.add(item.temp_encoded_path)

    abs_temp_directory = os.path.abspath(TEMP_DIRECTORY)

    for f_path in list(files_to_check_for_cleanup): 
        if f_path and os.path.exists(f_path) and os.path.isfile(f_path):
            if os.path.commonpath([os.path.abspath(f_path), abs_temp_directory]) == abs_temp_directory:
                try:
                    os.remove(f_path)
                    add_log_message(f"CLEANUP: Removed temp file: {f_path}")
                    cleaned_count += 1
                except OSError as e:
                    add_log_message(f"CLEANUP: Error removing temp file {f_path}: {e}")
                    error_count += 1
            else:
                add_log_message(f"CLEANUP: Skipped removing {f_path} as it's not confirmed to be in TEMP_DIRECTORY.")
    
    if cleaned_count > 0 or error_count > 0:
        add_log_message(f"CLEANUP: Finished. Removed {cleaned_count} files. Errors: {error_count}.")
    else:
        add_log_message("CLEANUP: Finished. No orphaned temp files found based on FileItem records.")


def curses_main(stdscr):
    global stop_event, all_files, pending_files_queue, ready_for_encode_queue, encoding_files_list, preparing_files_list, spinner_index
    curses.curs_set(0); stdscr.nodelay(1); stdscr.timeout(100) 

    if curses.has_colors():
        curses.start_color()
        if curses.COLORS >= 8: 
            curses.init_pair(CP_SUCCESS, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(CP_TRANSFERRING, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(CP_ENCODING_TEXT_COLOR, curses.COLOR_CYAN, curses.COLOR_BLACK) 
            curses.init_pair(CP_ERROR, curses.COLOR_RED, curses.COLOR_BLACK) 
            curses.init_pair(CP_READY, curses.COLOR_YELLOW, curses.COLOR_BLACK) 
            curses.init_pair(CP_DEFAULT_PENDING, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(CP_CANCELLED_SKIPPED_BASE, curses.COLOR_WHITE, curses.COLOR_BLACK) 
            curses.init_pair(CP_ENCODING_HIGHLIGHT_BG, curses.COLOR_BLACK, curses.COLOR_YELLOW) 
            curses.init_pair(CP_DELETED, curses.COLOR_RED, curses.COLOR_BLACK) 
            curses.init_pair(CP_TIMEOUT_TEXT, curses.COLOR_RED, curses.COLOR_BLACK) # Bold will be applied at draw time
        else: 
            curses.init_pair(CP_SUCCESS, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(CP_TRANSFERRING, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(CP_ENCODING_TEXT_COLOR, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(CP_ERROR, curses.COLOR_WHITE, curses.COLOR_BLACK) 
            curses.init_pair(CP_READY, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(CP_DEFAULT_PENDING, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_WHITE) 
            curses.init_pair(CP_CANCELLED_SKIPPED_BASE, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(CP_ENCODING_HIGHLIGHT_BG, curses.COLOR_BLACK, curses.COLOR_WHITE) 
            curses.init_pair(CP_DELETED, curses.COLOR_WHITE, curses.COLOR_BLACK) 
            curses.init_pair(CP_TIMEOUT_TEXT, curses.COLOR_WHITE, curses.COLOR_BLACK) # Fallback for timeout text
    
    current_selection_idx, scroll_offset, show_help, show_log = 0, 0, False, False
    spinner_index = 0 
    try: os.makedirs(TEMP_DIRECTORY, exist_ok=True)
    except OSError as e:
        print(f"Fatal: Cannot create TEMP_DIR {TEMP_DIRECTORY}: {e}\n", file=sys.stderr)
        return 

    threads = [
        threading.Thread(target=file_scanner_worker, daemon=True, name="ScannerThread"),
        threading.Thread(target=file_preparer_worker, daemon=True, name="PreparerThread"),
        threading.Thread(target=ffmpeg_encoder_worker, daemon=True, name="EncoderThread")
    ]
    
    try:
        stop_event.clear()
        for t in threads: t.start()
        add_log_message("UI: Threads started.")
        last_update_time = time.time()

        while not stop_event.is_set():
            window_height, window_width = stdscr.getmaxyx()
            
            view_area_start_y_calc = 2
            available_height_for_views_calc = window_height - view_area_start_y_calc - BOTTOM_STATUS_LINES
            current_list_area_height_calc = available_height_for_views_calc
            if show_log:
                current_list_area_height_calc = available_height_for_views_calc // 2
            current_list_area_height_calc = max(1, current_list_area_height_calc) 

            try: key = stdscr.getch()
            except curses.error: key = -1 

            if key != -1:
                ui_needs_update.set() 
                if show_help:
                    if key == curses.KEY_F1: show_help = False
                elif key in (ord('q'), ord('Q')): add_log_message("UI: Quit signal received."); stop_event.set(); break
                elif key == curses.KEY_F1: show_help = True
                elif key == curses.KEY_F2: show_log = not show_log
                elif key == curses.KEY_UP:
                    if current_selection_idx > 0:
                        current_selection_idx -= 1
                        if current_selection_idx < scroll_offset: scroll_offset = current_selection_idx
                elif key == curses.KEY_DOWN:
                    with ui_lock: num_files = len(all_files)
                    if current_selection_idx < num_files - 1:
                        current_selection_idx += 1
                        if current_selection_idx >= scroll_offset + current_list_area_height_calc:
                             scroll_offset = current_selection_idx - current_list_area_height_calc + 1
                elif key == curses.KEY_PPAGE: 
                    page_size = current_list_area_height_calc
                    current_selection_idx = max(0, current_selection_idx - page_size)
                    scroll_offset = max(0, scroll_offset - page_size)
                    if current_selection_idx < scroll_offset : scroll_offset = current_selection_idx 
                    
                elif key == curses.KEY_NPAGE: 
                    with ui_lock: num_files = len(all_files)
                    if num_files > 0:
                        page_size = current_list_area_height_calc
                        current_selection_idx = min(num_files - 1, current_selection_idx + page_size)
                        max_scroll = max(0, num_files - page_size) 
                        scroll_offset = min(max_scroll, scroll_offset + page_size)
                        if current_selection_idx >= scroll_offset + page_size :
                            scroll_offset = current_selection_idx - page_size + 1
                        if current_selection_idx < scroll_offset: 
                            scroll_offset = current_selection_idx
                        scroll_offset = min(max_scroll, scroll_offset) 
                        scroll_offset = max(0, scroll_offset) 

                elif key in (ord('c'), ord('C')):
                    with ui_lock:
                        if 0 <= current_selection_idx < len(all_files):
                            item_to_cancel = all_files[current_selection_idx]
                            if item_to_cancel.status not in ["success", "skipped", "error", "cancelled", "deleted_zero", "deleted_error"]:
                                item_to_cancel.status = "cancelled"
                                item_to_cancel.status_message = "User cancelled"
                                add_log_message(f"UI: Signalled cancel for '{item_to_cancel.filename}'")
                                if item_to_cancel in preparing_files_list: 
                                    try: preparing_files_list.remove(item_to_cancel)
                                    except ValueError: pass 
                                if item_to_cancel in ready_for_encode_queue: 
                                    try: ready_for_encode_queue.remove(item_to_cancel)
                                    except ValueError: pass 
                            else: add_log_message(f"UI: Cannot cancel '{item_to_cancel.filename}', status: {item_to_cancel.status}")
            
            current_time = time.time()
            if ui_needs_update.is_set() or (current_time - last_update_time > 0.1): 
                if not show_help: 
                    with ui_lock: num_files = len(all_files)
                    if num_files > 0: 
                        current_selection_idx = max(0, min(current_selection_idx, num_files - 1))
                        max_possible_scroll = max(0, num_files - current_list_area_height_calc)
                        scroll_offset = max(0, min(scroll_offset, max_possible_scroll))
                        if current_selection_idx < scroll_offset:
                            scroll_offset = current_selection_idx
                        elif current_selection_idx >= scroll_offset + current_list_area_height_calc:
                            scroll_offset = current_selection_idx - current_list_area_height_calc + 1
                            scroll_offset = max(0, min(scroll_offset, max_possible_scroll))

                    draw_ui(stdscr, current_selection_idx, scroll_offset, show_help, show_log, window_height, window_width)
                ui_needs_update.clear()
                last_update_time = current_time
            
            with ui_lock:
                no_active_tasks_in_queues = not pending_files_queue and not ready_for_encode_queue and not encoding_files_list and not preparing_files_list
                all_items_in_terminal_state = all(f.status in ["success", "skipped", "error", "cancelled", "deleted_zero", "deleted_error"] for f in all_files) if all_files else False
                scanner_thread_inactive = not threads[0].is_alive()

            if scanner_thread_inactive and no_active_tasks_in_queues and (all_items_in_terminal_state or not all_files) :
                 time.sleep(0.5) 
                 with ui_lock: 
                     final_check_queues = not pending_files_queue and not ready_for_encode_queue and not encoding_files_list and not preparing_files_list
                     preparer_inactive = not threads[1].is_alive()
                     encoder_inactive = not threads[2].is_alive()

                 if final_check_queues and preparer_inactive and encoder_inactive:
                    if len(all_files) > 0 : 
                        add_log_message("UI: All tasks complete. You can press Q to quit.")
    finally:
        add_log_message("UI: Main loop ended or exception. Ensuring stop event is set for threads.")
        stop_event.set() 

        add_log_message("UI: Waiting for threads to join...")
        for i, t in enumerate(threads):
            if t.is_alive(): 
                add_log_message(f"UI: Joining {t.name}...")
                t.join(timeout= (5 if i==0 else 12) ) 
                if t.is_alive():
                     add_log_message(f"UI: {t.name} did not join in time.")
        add_log_message("UI: All threads joined or timed out.")
        
        cleanup_all_temp_files() 
    
        add_log_message("UI: Exiting.")
        if stdscr: 
            stdscr.erase()
            final_logs = list(log_messages)[-(window_height-1 if 'window_height' in locals() else 10):] 
            for i, msg in enumerate(final_logs): 
                if i < (window_height-1 if 'window_height' in locals() else 10) : 
                    try: stdscr.addstr(i,0, msg[:(window_width-1 if 'window_width' in locals() else 79)])
                    except curses.error: pass 
            try:
                last_line_y = max(0,min(len(final_logs), (window_height-1 if 'window_height' in locals() else 10)))
                stdscr.addstr(last_line_y, 0, "Exited. Press any key.", curses.A_BOLD)
                stdscr.refresh()
                stdscr.nodelay(0) 
                stdscr.getch()
            except curses.error: pass 

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Batch AV1 video encoder with curses TUI.")
    parser.add_argument(
        '--delete-errors', 
        action='store_true', 
        help="Delete original source files if ffprobe fails to analyze them." 
    )
    parser.add_argument(
        '--delete-zeros', 
        action='store_true', 
        help="Delete original source files if they are found to be 0 bytes during scan."
    )
    ARGS = parser.parse_args() 

    try:
        os.makedirs(TEMP_DIRECTORY, exist_ok=True)
        test_file = os.path.join(TEMP_DIRECTORY, ".permission_test_av1enc")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
    except Exception as e:
        print(f"CRITICAL ERROR: Cannot create or write to TEMP_DIRECTORY ('{TEMP_DIRECTORY}'): {e}", file=sys.stderr)
        print("Please check the path and ensure you have write permissions.", file=sys.stderr)
        sys.exit(1)
        
    curses.wrapper(curses_main)

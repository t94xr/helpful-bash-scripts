#!/usr/bin/env python3

import argparse
import datetime
import hashlib
import os
import re
import sys
import zlib # For CRC32

# --------- CONFIG & GLOBALS ----------
LOG_FILE_DEFAULT = "log.t"
SCRIPT_NAME = os.path.basename(sys.argv[0])

# ANSI colors
COLOR_PASS = "\033[1;32m"
COLOR_FAIL = "\033[1;31m"
COLOR_RESET = "\033[0m"

# --------- HELPER FUNCTIONS ----------

def color_text(text, color_code):
    """Applies ANSI color to text."""
    # Disable color if output is not a TTY (e.g., redirected to file)
    if sys.stdout.isatty():
        return f"{color_code}{text}{COLOR_RESET}"
    return text

def log_message(message, log_file_path, enabled):
    """Logs a message to the specified log file if logging is enabled."""
    if enabled:
        try:
            with open(log_file_path, 'a') as f:
                f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        except IOError as e:
            print(f"Error writing to log file {log_file_path}: {e}", file=sys.stderr)

def calculate_checksum(filepath, hash_type):
    """Calculates the checksum for a given file and hash type."""
    if not os.path.isfile(filepath):
        return None

    try:
        if hash_type == "crc32":
            crc_val = 0
            with open(filepath, 'rb') as f:
                while chunk := f.read(8192): # Read in chunks
                    crc_val = zlib.crc32(chunk, crc_val)
            return format(crc_val & 0xffffffff, '08x') # Format as 8-char hex

        hasher = None
        if hash_type == "md5":
            hasher = hashlib.md5()
        elif hash_type == "sha1":
            hasher = hashlib.sha1()
        elif hash_type == "sha256":
            hasher = hashlib.sha256()
        else:
            raise ValueError(f"Unsupported hash type: {hash_type}")

        with open(filepath, 'rb') as f:
            while chunk := f.read(8192): # Read in chunks
                hasher.update(chunk)
        return hasher.hexdigest()

    except IOError:
        return None
    except ValueError:
        return None


def get_target_files(all_types_mode, log_file_name):
    """Collects files from the current directory based on mode."""
    files_found = []
    excluded_names = [f"chksum.{ht}.t" for ht in ["md5", "sha1", "sha256", "crc32"]]
    excluded_names.append(log_file_name)
    excluded_names.append(SCRIPT_NAME)
    # Also exclude potential variations if script is symlinked or called via python
    if __file__ and os.path.basename(__file__) != SCRIPT_NAME:
        excluded_names.append(os.path.basename(__file__))


    for item in os.listdir("."):
        if os.path.isfile(item):
            if all_types_mode:
                is_checksum_file = False
                for chk_pattern in ["chksum.md5.t", "chksum.sha1.t", "chksum.sha256.t", "chksum.crc32.t"]:
                    if item == chk_pattern:
                        is_checksum_file = True
                        break
                if not is_checksum_file and item != log_file_name and item != SCRIPT_NAME \
                   and (__file__ is None or item != os.path.basename(__file__)):
                    files_found.append(item)
            else:  # Default to *.iso files
                if item.endswith(".iso"):
                    files_found.append(item)
    return sorted(files_found)


# --------- MAIN LOGIC FUNCTIONS ----------

def generate_checksums(hash_type, files_to_process, chksum_file_path, log_fp, log_enabled):
    """Generates checksums for new files and appends them to the checksum file."""
    print(f"💾 Generating {chksum_file_path}")
    log_message(f"Generating {chksum_file_path}", log_fp, log_enabled)

    existing_filenames = set()
    if os.path.exists(chksum_file_path):
        try:
            with open(chksum_file_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split('  ', 1) # Expect "hash  filename"
                    if len(parts) == 2:
                        existing_filenames.add(parts[1])
        except IOError as e:
            print(f"Error reading existing checksum file {chksum_file_path}: {e}", file=sys.stderr)
            log_message(f"Error reading existing checksum file {chksum_file_path}: {e}", log_fp, log_enabled)


    new_entries = []
    for f_path in files_to_process:
        if f_path not in existing_filenames:
            print(f"Calculating {hash_type} for: {f_path}")
            checksum_val = calculate_checksum(f_path, hash_type)
            if checksum_val:
                new_entries.append(f"{checksum_val}  {f_path}")
                log_message(f"Added to {chksum_file_path}: {checksum_val}  {f_path}", log_fp, log_enabled)
            else:
                print(f"⚠️  Could not calculate checksum for {f_path}")
                log_message(f"Could not calculate checksum for {f_path}", log_fp, log_enabled)


    if new_entries:
        try:
            with open(chksum_file_path, 'a') as f:
                for entry in new_entries:
                    f.write(entry + "\n")
                f.write(f"# Checked: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            print(f"✅ Done writing {chksum_file_path}")
            log_message(f"Done writing {chksum_file_path}", log_fp, log_enabled)
        except IOError as e:
            print(f"Error appending to checksum file {chksum_file_path}: {e}", file=sys.stderr)
            log_message(f"Error appending to checksum file {chksum_file_path}: {e}", log_fp, log_enabled)
    else:
        print(f"⚠️  No new files to add for {hash_type} — skipping {chksum_file_path}")
        log_message(f"No new files to add for {hash_type} — skipping {chksum_file_path}", log_fp, log_enabled)


def verify_checksums(hash_type, chksum_file_path, show_summary, log_fp, log_enabled):
    """Verifies files against the checksum file."""
    if not os.path.exists(chksum_file_path):
        print(f"❌ No {chksum_file_path} found.")
        log_message(f"No {chksum_file_path} found.", log_fp, log_enabled)
        return

    print(f"🔍 Verifying with {chksum_file_path}")
    log_message(f"Verifying with {chksum_file_path}", log_fp, log_enabled)

    pass_count = 0
    fail_count = 0

    try:
        with open(chksum_file_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Regex to capture hash and filename (allows spaces in filename)
                match = re.match(r'([a-fA-F0-9]+)\s\s(.+)', line)
                if not match:
                    print(f"⚠️  Skipping malformed line {line_num} in {chksum_file_path}: {line}")
                    log_message(f"Skipping malformed line {line_num} in {chksum_file_path}: {line}", log_fp, log_enabled)
                    continue

                stored_hash = match.group(1).lower()
                filename = match.group(2)

                if not os.path.isfile(filename):
                    print(color_text(f"✖ FAIL (File Missing): {filename}", COLOR_FAIL))
                    log_message(f"FAIL (File Missing): {filename}", log_fp, log_enabled)
                    fail_count += 1
                    continue

                current_hash = calculate_checksum(filename, hash_type)

                if current_hash and current_hash == stored_hash:
                    print(color_text(f"✔ PASS: {filename}", COLOR_PASS))
                    log_message(f"PASS: {filename}", log_fp, log_enabled)
                    pass_count += 1
                elif current_hash: # Hash calculated but does not match
                    print(color_text(f"✖ FAIL: {filename}", COLOR_FAIL))
                    log_message(f"FAIL: {filename} (Expected: {stored_hash}, Got: {current_hash})", log_fp, log_enabled)
                    fail_count += 1
                else: # Could not calculate hash
                    print(color_text(f"✖ ERROR (Could not hash): {filename}", COLOR_FAIL))
                    log_message(f"ERROR (Could not hash): {filename}", log_fp, log_enabled)
                    fail_count += 1
    except IOError as e:
        print(f"Error reading checksum file {chksum_file_path}: {e}", file=sys.stderr)
        log_message(f"Error reading checksum file {chksum_file_path}: {e}", log_fp, log_enabled)
        return


    if show_summary:
        print(f"\nSummary for {chksum_file_path}:")
        print(f"  Passed: {pass_count}")
        print(f"  Failed: {fail_count}")
        log_message(f"Summary for {chksum_file_path}: Passed={pass_count} Failed={fail_count}", log_fp, log_enabled)


def update_checksums(hash_type, files_to_update, chksum_file_path, log_fp, log_enabled, all_types_mode):
    """Updates checksums for specified files or all relevant files."""
    print(f"🔁 Updating {chksum_file_path}")
    log_message(f"Updating {chksum_file_path}", log_fp, log_enabled)

    updated_lines = {} # Store as {filename: "hash  filename"}

    # Read existing entries
    if os.path.exists(chksum_file_path):
        try:
            with open(chksum_file_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    match = re.match(r'([a-fA-F0-9]+)\s\s(.+)', line)
                    if match:
                        filename = match.group(2)
                        updated_lines[filename] = line
        except IOError as e:
            print(f"Error reading existing checksum file {chksum_file_path} for update: {e}", file=sys.stderr)
            log_message(f"Error reading existing checksum file {chksum_file_path} for update: {e}", log_fp, log_enabled)


    # Determine which files to process for updates
    target_update_files = files_to_update
    if not target_update_files: # If no specific files given, get from directory
        target_update_files = get_target_files(all_types_mode, log_fp if log_enabled else LOG_FILE_DEFAULT)

    for f_path in target_update_files:
        if os.path.isfile(f_path):
            print(f"Calculating {hash_type} for update: {f_path}")
            new_hash = calculate_checksum(f_path, hash_type)
            if new_hash:
                updated_lines[f_path] = f"{new_hash}  {f_path}"
                print(f"Updated entry for: {f_path}")
                log_message(f"Updated entry for {f_path} in {chksum_file_path}", log_fp, log_enabled)
            else:
                print(f"⚠️  Could not calculate checksum for {f_path}, not updated.")
                log_message(f"Could not calculate checksum for {f_path}, not updated in {chksum_file_path}", log_fp, log_enabled)
        elif f_path in updated_lines: # File was in list but now not found, keep old entry or remove? Bash keeps.
            print(f"⚠️  File previously in checksum list not found: {f_path}. Keeping old entry if present.")
            log_message(f"File previously in checksum list not found: {f_path}. Keeping old entry if present.", log_fp, log_enabled)
        elif not files_to_update : # Only warn if scanning directory; if specific file not found, it's an error for that file.
             pass # Handled by the initial files_to_update check if it was specific
        else: # Specific file given on CLI not found
            print(f"⚠️  Specified file not found: {f_path}")
            log_message(f"Specified file not found for update: {f_path}", log_fp, log_enabled)


    # Write sorted entries back to the file
    try:
        with open(chksum_file_path, 'w') as f:
            # Sort by filename (the keys of updated_lines) for consistent output
            for filename in sorted(updated_lines.keys()):
                f.write(updated_lines[filename] + "\n")
            f.write(f"# Checked: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        print(f"✅ Updated {chksum_file_path}")
        log_message(f"Updated {chksum_file_path}", log_fp, log_enabled)
    except IOError as e:
        print(f"Error writing updated checksum file {chksum_file_path}: {e}", file=sys.stderr)
        log_message(f"Error writing updated checksum file {chksum_file_path}: {e}", log_fp, log_enabled)


# --------- ARGUMENT PARSING ----------
def main():
    parser = argparse.ArgumentParser(
        description=f"Generate and verify checksums (md5, sha1, sha256, crc32) for files.",
        usage=f"{SCRIPT_NAME} [OPTIONS] [FILES...]",
        epilog="""EXAMPLES:
  Generate checksums (default md5) in current directory:
    {0}
  Generate all checksum types for all files:
    {0} --all --alltypes
  Verify files and show summary:
    {0} --check --summary
  Update hash entry for one file in all checksum files:
    {0} --all --update "MyFile.iso"

REQUIREMENTS:
  This script uses Python's built-in hashlib and zlib libraries.
  No external checksum utilities are required.
""".format(SCRIPT_NAME),
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        '--check',
        action='store_true',
        help='Verify existing checksum files (default uses chksum.[hash_type].t)'
    )
    parser.add_argument(
        '--update',
        action='store_true',
        help='Update checksum for specified file(s) or all relevant files if none specified'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Enable all hash types (md5, sha1, sha256, crc32)'
    )
    parser.add_argument(
        '--alltypes',
        action='store_true',
        help='Include all file types in current directory (not just *.iso by default)'
    )
    parser.add_argument('--md5', action='store_true', help='Use only MD5 checksums (default if no specific type chosen)')
    parser.add_argument('--sha1', action='store_true', help='Use only SHA1 checksums')
    parser.add_argument('--sha256', action='store_true', help='Use only SHA256 checksums')
    parser.add_argument('--crc32', action='store_true', help='Use only CRC32 checksums')
    parser.add_argument(
        '--log',
        action='store_true',
        help=f'Log results to {LOG_FILE_DEFAULT}'
    )
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Show pass/fail summary after verification'
    )
    parser.add_argument(
        'files',
        nargs='*',
        help='File(s) to process. Used with --update or for generation if specified.'
    )

    args = parser.parse_args()

    # Determine active hash types
    active_hash_types = []
    if args.all:
        active_hash_types = ["md5", "sha1", "sha256", "crc32"]
    else:
        if args.md5: active_hash_types.append("md5")
        if args.sha1: active_hash_types.append("sha1")
        if args.sha256: active_hash_types.append("sha256")
        if args.crc32: active_hash_types.append("crc32")

    if not active_hash_types: # Default if no specific or --all
        active_hash_types = ["md5"]
    active_hash_types = sorted(list(set(active_hash_types))) # Unique and sorted

    # Prepare log file path (can be customized further if needed)
    log_file_path = LOG_FILE_DEFAULT
    if args.log:
        # Clear log file at start of a new run if desired, or keep appending.
        # The original script implies appending within a single run, but not clearing between runs.
        pass


    # File collection for generate mode (if no specific files given on CLI)
    # For update mode, file list is handled within the update_checksums function
    files_for_generation = args.files
    if not args.check and not args.update and not files_for_generation:
        files_for_generation = get_target_files(args.alltypes, log_file_path)
        if not files_for_generation:
            print("⚠️ No target files found in the current directory.")
            parser.print_help()
            sys.exit(1)
    elif args.check and args.files:
        print("⚠️  [FILES...] argument is not used with --check mode. Files are read from checksum file.")
    elif args.update and not args.files:
        print("ℹ️  --update specified without [FILES...], will update all relevant files found by --alltypes or *.iso pattern.")


    # Main logic dispatch
    for hash_t in active_hash_types:
        chksum_filename = f"chksum.{hash_t}.t"

        if args.check:
            verify_checksums(hash_t, chksum_filename, args.summary, log_file_path, args.log)
        elif args.update:
            update_checksums(hash_t, args.files, chksum_filename, log_file_path, args.log, args.alltypes)
        else: # Generate mode
            # If specific files are given for generation, use them. Otherwise, use auto-collected ones.
            current_files_to_process = args.files if args.files else files_for_generation
            if not current_files_to_process: # Could happen if args.files was empty and get_target_files also found nothing
                 print(f"⚠️ No files to process for {hash_t}. Check --alltypes or *.iso files.")
                 log_message(f"No files to process for {hash_t}", log_file_path, args.log)
                 continue
            generate_checksums(hash_t, current_files_to_process, chksum_filename, log_file_path, args.log)

if __name__ == "__main__":
    try:
        # The bash script uses `cd . || exit`, which is an implicit check.
        # Python will raise OSError if current dir is inaccessible for listing.
        os.listdir(".") 
    except OSError as e:
        print(f"❌ Failed to access current directory: {e}", file=sys.stderr)
        sys.exit(1)
    main()

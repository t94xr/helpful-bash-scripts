#!/usr/bin/env python3

import os
import subprocess
import argparse
import sys
from collections import OrderedDict

# Define conservative maximum capacities in bytes for various media types
# Ordered by size for the auto-select feature
MEDIA_CAPACITIES = OrderedDict([
    ("cd", 680 * 1024 * 1024),          # Approx 680 MiB for a 700MB CD-R
    ("dvd", 4_400 * 1024 * 1024),       # Approx 4.4 GiB for a 4.7GB DVD
    ("dvd_dl", 8_100 * 1024 * 1024),    # Approx 8.1 GiB for an 8.5GB DVD-DL
    ("br25", 24_500_000_000),           # Approx 24.5 GB for a 25GB Blu-Ray
    ("br50", 49_500_000_000),           # Approx 49.5 GB for a 50GB Blu-Ray
    ("br100", 99_000_000_000),          # Approx 99 GB for a 100GB Blu-Ray
    ("br125", 124_000_000_000)          # Approx 124 GB for a 125GB Blu-Ray
])

MKISOFS_COMMAND = "mkisofs" # Or "genisoimage" if that's what your system uses

def format_size(num_bytes):
    """Converts bytes to a human-readable string (B, KiB, MiB, GiB)."""
    if num_bytes is None:
        return "N/A"
    if num_bytes == 0:
        return "0B"
    size_name = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB")
    i = 0
    temp_bytes = float(num_bytes)
    while temp_bytes >= 1024 and i < len(size_name) - 1:
        temp_bytes /= 1024.0
        i += 1
    return f"{temp_bytes:.2f}{size_name[i]}"

def get_directory_size(start_path):
    """
    Calculates the total size of files in the directory.
    For symlinks, it adds the size of the link itself.
    """
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(start_path, followlinks=False):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                stat_info = os.lstat(fp) # Use lstat for symlinks
                total_size += stat_info.st_size
            except OSError as e:
                print(f"Warning: Could not get size of {fp} ({e}). Skipping.", file=sys.stderr)
    return total_size

def existing_directory_type(path_str):
    """Argparse type for a readable directory, returns absolute path."""
    abs_path = os.path.abspath(path_str)
    if not os.path.isdir(abs_path):
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")
    if not os.access(abs_path, os.R_OK):
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a readable directory.")
    return abs_path

def main():
    # Get script name for help message
    script_name = os.path.basename(sys.argv[0])

    epilog_text = f"""
Usage Examples:
  {script_name} my_archive --source_dir /data/archive --udf --br50
      Create 'my_archive.iso' (UDF format) from /data/archive, pre-checking for a 50GB Blu-Ray.

  {script_name} project_backup --autoselect-media
      Create 'project_backup.iso' (standard format) from the current directory,
      auto-selecting the smallest suitable media type for a pre-check.

  {script_name} quick_iso --udf
      Create 'quick_iso.iso' (UDF format) from the current directory without any media pre-check.
      The final ISO size and compatible media will still be reported.

Important Notes:
- This script requires '{MKISOFS_COMMAND}' (or 'genisoimage') to be installed and accessible in your system's PATH.
  You can specify a custom path using the --mkisofs_path argument.
- The 'standard' ISO format (created if --udf is NOT specified) is ISO9660 with Joliet and RockRidge
  extensions. This provides good compatibility across most operating systems.
- The UDF format (using --udf) is generally recommended for Blu-Ray discs, very large files, or when
  specific UDF features are needed for broader compatibility with modern devices.
- Reported source data sizes for pre-checks are estimates. The final ISO file size may differ due to
  filesystem overhead, metadata, and block padding by the 'mkisofs' utility. The pre-checks use
  conservative estimates for media capacities to account for this.
- If no media pre-check option (e.g., --dvd, --br25, --autoselect-media) is chosen, the script will proceed
  to create the ISO. Afterwards, it will report the actual size of the created ISO file and suggest
  which standard media types it could fit onto.
"""

    parser = argparse.ArgumentParser(
        description=f"""
A Python script to create ISO image files from a source directory using '{MKISOFS_COMMAND}' (or 'genisoimage').
It supports creating standard ISO9660 (with Joliet/RockRidge extensions) or UDF image formats.
Includes features for pre-checking content size against various media capacities (CD, DVD, DVD-DL, Blu-Ray)
and can automatically select a suitable media type. The script also reports the final ISO size and its
compatibility with standard media types after creation.
""",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=epilog_text
    )
    parser.add_argument(
        "label_filename_base",
        help="Mandatory base name for the output .iso file and the ISO's Volume Label.\n"
             "For example, providing 'my_backup' will result in 'my_backup.iso' and a volume label 'my_backup'."
    )
    parser.add_argument(
        "--source_dir",
        default=".",
        type=existing_directory_type,
        help="Specifies the directory whose contents will be archived into the ISO.\n"
             "If not provided, the script uses the current working directory (CWD) from which it is executed.\n"
             "Example: --source_dir /path/to/important_files"
    )
    parser.add_argument(
        "--udf",
        action="store_true",
        help="Create a UDF filesystem in the ISO. If not specified, a standard ISO9660 image\n"
             "with Joliet and RockRidge extensions will be created (recommended for general compatibility)."
    )

    # Media target selection group
    media_target_group = parser.add_argument_group(
        "Media Size Pre-check Options (mutually exclusive)",
        "These options allow you to pre-check if your source data is likely to fit a specific media type.\n"
        "If a pre-check option is chosen and the data is too large, the script will exit before ISO creation."
    )
    # Mutually exclusive group for these specific options
    exclusive_media_options = media_target_group.add_mutually_exclusive_group()
    exclusive_media_options.add_argument(
        "--cd", action="store_const", const="cd", dest="target_media_type",
        help=f"Target CD. Pre-checks if source data fits ~{format_size(MEDIA_CAPACITIES['cd'])}."
    )
    exclusive_media_options.add_argument(
        "--dvd", action="store_const", const="dvd", dest="target_media_type",
        help=f"Target DVD. Pre-checks if source data fits ~{format_size(MEDIA_CAPACITIES['dvd'])}."
    )
    exclusive_media_options.add_argument(
        "--dvd-dl", action="store_const", const="dvd_dl", dest="target_media_type",
        help=f"Target DVD-DL. Pre-checks if source data fits ~{format_size(MEDIA_CAPACITIES['dvd_dl'])}."
    )
    exclusive_media_options.add_argument(
        "--br25", action="store_const", const="br25", dest="target_media_type",
        help=f"Target 25GB Blu-Ray. Pre-checks if source data fits ~{format_size(MEDIA_CAPACITIES['br25'])}."
    )
    exclusive_media_options.add_argument(
        "--br50", action="store_const", const="br50", dest="target_media_type",
        help=f"Target 50GB Blu-Ray. Pre-checks if source data fits ~{format_size(MEDIA_CAPACITIES['br50'])}."
    )
    exclusive_media_options.add_argument(
        "--br100", action="store_const", const="br100", dest="target_media_type",
        help=f"Target 100GB Blu-Ray. Pre-checks if source data fits ~{format_size(MEDIA_CAPACITIES['br100'])}."
    )
    exclusive_media_options.add_argument(
        "--br125", action="store_const", const="br125", dest="target_media_type",
        help=f"Target 125GB Blu-Ray. Pre-checks if source data fits ~{format_size(MEDIA_CAPACITIES['br125'])}."
    )
    exclusive_media_options.add_argument(
        "--autoselect-media",
        action="store_true",
        help="Automatically analyze source directory size and select the smallest suitable standard\n"
             "media type (CD, DVD, DVD-DL, BR25/50/100/125) as the target for a pre-check.\n"
             "If content is too large for any known type, an error will be reported."
    )
    
    parser.add_argument(
        "--mkisofs_path",
        default=MKISOFS_COMMAND,
        help=f"Path to the '{MKISOFS_COMMAND}' (or 'genisoimage') executable if it's not in your system's PATH.\n"
             f"(default: {MKISOFS_COMMAND})"
    )

    if len(sys.argv) == 1: # Show help if no arguments are given
        parser.print_help(sys.stderr)
        sys.exit(1)
        
    args = parser.parse_args()

    var_name = args.label_filename_base
    source_directory = args.source_dir 
    mkisofs_exec = args.mkisofs_path

    print(f"Source directory: {source_directory}")
    print(f"Output ISO base name: {var_name}")
    print(f"Volume Label: {var_name}")
    if args.udf:
        print("Filesystem type: UDF requested")
    else:
        print("Filesystem type: Standard ISO9660 + Joliet/RockRidge")

    current_dir_size = -1 # Initialize to indicate not yet calculated

    # Handle media target selection and pre-check
    effective_target_media_type = args.target_media_type

    if args.autoselect_media:
        print(f"\n--autoselect-media specified. Calculating source content size...")
        current_dir_size = get_directory_size(source_directory)
        print(f"Total calculated source size: {format_size(current_dir_size)} ({current_dir_size} bytes)")
        
        selected_type = None
        for media_key, capacity in MEDIA_CAPACITIES.items():
            if current_dir_size <= capacity:
                selected_type = media_key
                break
        
        if selected_type:
            effective_target_media_type = selected_type
            print(f"Auto-selected media type for pre-check: {selected_type.upper()} (Target Capacity: ~{format_size(MEDIA_CAPACITIES[selected_type])})")
        else:
            print(f"\nError: Source content size ({format_size(current_dir_size)}) exceeds the largest known media capacity ({format_size(list(MEDIA_CAPACITIES.values())[-1])}).", file=sys.stderr)
            sys.exit(1)

    if effective_target_media_type:
        if current_dir_size == -1: # Calculate if not already done by autoselect
            print(f"\nCalculating source content size for {effective_target_media_type.upper()} pre-check...")
            current_dir_size = get_directory_size(source_directory)
            print(f"Total calculated source size: {format_size(current_dir_size)} ({current_dir_size} bytes)")

        target_max_bytes = MEDIA_CAPACITIES[effective_target_media_type]
        print(f"Performing pre-check for {effective_target_media_type.upper()} (Target capacity: ~{format_size(target_max_bytes)})")

        if current_dir_size > target_max_bytes:
            print(f"\nError: Source contents ({format_size(current_dir_size)}) are likely too large for "
                  f"a {effective_target_media_type.upper()} disc ({format_size(target_max_bytes)}).",
                  file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Source contents should fit on a {effective_target_media_type.upper()} disc.")
    else:
        print("\nNo specific media target selected for pre-check.")

    output_iso_file = f"{var_name}.iso"
    
    mkisofs_cmd = [
        mkisofs_exec,
        "-o", output_iso_file,
        "-V", var_name,
        "-J",  # Joliet extensions
        "-R",  # Rock Ridge extensions
    ]
    if args.udf:
        mkisofs_cmd.append("-udf")
    
    mkisofs_cmd.append(source_directory) # Source directory must be the last path argument

    print(f"\nAttempting to create ISO '{output_iso_file}' with the following command:")
    # Quote parts of the command that contain spaces for display
    print(" ".join(f"'{cmd_part}'" if " " in cmd_part else cmd_part for cmd_part in mkisofs_cmd))

    try:
        process = subprocess.Popen(mkisofs_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()

        if process.returncode == 0:
            print("\nISO creation process completed.")
            if stdout: # mkisofs often prints stats to stdout
                print("mkisofs output (if any):\n", stdout.decode(errors='replace'))
            
            if os.path.exists(output_iso_file):
                created_iso_size = os.path.getsize(output_iso_file)
                print(f"Successfully created ISO: {output_iso_file}")
                print(f"Actual ISO file size: {format_size(created_iso_size)} ({created_iso_size} bytes)")

                print("\nThe created ISO file would fit on the following media types:")
                fit_found = False
                for media, capacity in MEDIA_CAPACITIES.items():
                    if created_iso_size <= capacity:
                        print(f"  - {media.upper()} (Capacity: ~{format_size(capacity)})")
                        fit_found = True
                if not fit_found:
                     print("  - None of the predefined standard media types (too large).")
            else:
                print(f"Error: mkisofs reported success, but the ISO file '{output_iso_file}' was not found.", file=sys.stderr)
                if stderr:
                    print("mkisofs stderr (if any):\n", stderr.decode(errors='replace'), file=sys.stderr)
                sys.exit(1)

        else:
            print(f"\nError: mkisofs failed with exit code {process.returncode}.", file=sys.stderr)
            if stdout:
                print("mkisofs stdout (if any):\n", stdout.decode(errors='replace'), file=sys.stderr)
            if stderr:
                print("mkisofs stderr (if any):\n", stderr.decode(errors='replace'), file=sys.stderr)
            sys.exit(process.returncode)

    except FileNotFoundError:
        print(f"\nError: The command '{mkisofs_exec}' was not found.", file=sys.stderr)
        print("Please ensure mkisofs (or genisoimage) is installed and in your PATH,", file=sys.stderr)
        print(f"or specify the correct path using --mkisofs_path.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

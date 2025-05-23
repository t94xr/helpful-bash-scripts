#!/usr/bin/env python3

import os
import sys
import subprocess
import shutil

RAMDISK_PATH = "/ramdisk"

def check_sudo():
    """Checks if the script is run with sudo privileges."""
    if os.geteuid() != 0:
        print("Error: This script must be run with sudo privileges.")
        print("Please run as: sudo python3 ramdisk.py <size|remove>")
        sys.exit(1)

def create_ramdisk(size_gb_str):
    """Creates a tmpfs RAM disk."""
    print(f"Attempting to create RAM disk at {RAMDISK_PATH} with size {size_gb_str}...")

    # Validate size format (e.g., "6G", "512M")
    if not (size_gb_str.upper().endswith('G') or size_gb_str.upper().endswith('M')):
        print("Error: Invalid size format. Please use 'G' for Gigabytes or 'M' for Megabytes (e.g., 6G, 512M).")
        sys.exit(1)
    
    try:
        # Check if size (without G/M) is a number
        int(size_gb_str[:-1])
    except ValueError:
        print("Error: Invalid size value. Please provide a numeric value before 'G' or 'M'.")
        sys.exit(1)

    # Check if RAMDISK_PATH is already mounted
    if os.path.ismount(RAMDISK_PATH):
        print(f"Error: {RAMDISK_PATH} is already a mount point.")
        print("If you want to change it, please remove it first using 'sudo python3 ramdisk.py remove'.")
        sys.exit(1)

    # Create RAMDISK_PATH directory if it doesn't exist
    if not os.path.exists(RAMDISK_PATH):
        try:
            print(f"Creating directory {RAMDISK_PATH}...")
            os.makedirs(RAMDISK_PATH)
            print(f"Directory {RAMDISK_PATH} created successfully.")
        except OSError as e:
            print(f"Error creating directory {RAMDISK_PATH}: {e}")
            sys.exit(1)
    elif not os.path.isdir(RAMDISK_PATH):
        print(f"Error: {RAMDISK_PATH} exists but is not a directory.")
        sys.exit(1)

    # Mount tmpfs
    mount_command = ["mount", "-t", "tmpfs", "-o", f"size={size_gb_str}", "tmpfs", RAMDISK_PATH]
    print(f"Executing: {' '.join(mount_command)}")
    try:
        subprocess.run(mount_command, check=True, capture_output=True, text=True)
        print(f"RAM disk created successfully at {RAMDISK_PATH} with size {size_gb_str}.")
        print(f"You can verify with: df -h {RAMDISK_PATH}")
    except subprocess.CalledProcessError as e:
        print(f"Error mounting tmpfs at {RAMDISK_PATH}:")
        print(f"Command: {' '.join(e.cmd)}")
        print(f"Return code: {e.returncode}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        # Attempt to clean up directory if we created it and mount failed
        if not os.path.ismount(RAMDISK_PATH): # double check it didn't partially mount
            # Check if we created it in this run, to avoid deleting pre-existing user data if mount failed
            # This is a bit tricky without more state. For now, if it's empty, safe to remove.
            if os.path.exists(RAMDISK_PATH) and not os.listdir(RAMDISK_PATH):
                 try:
                    if not any(entry.name == RAMDISK_PATH.split('/')[-1] for entry in os.scandir('/'.join(RAMDISK_PATH.split('/')[:-1])) if entry.is_dir()):
                        # This logic is to prevent deleting a user's /ramdisk folder if it existed before and mount failed.
                        # A simpler approach is to just leave it if mount fails.
                        pass # Decided to leave the folder if mount fails and it wasn't empty or was pre-existing.
                 except Exception:
                    pass # Best effort
        sys.exit(1)

def remove_ramdisk():
    """Removes the tmpfs RAM disk."""
    print(f"Attempting to remove RAM disk at {RAMDISK_PATH}...")

    # Check if RAMDISK_PATH is currently mounted
    if os.path.ismount(RAMDISK_PATH):
        unmount_command = ["umount", RAMDISK_PATH]
        print(f"Executing: {' '.join(unmount_command)}")
        try:
            subprocess.run(unmount_command, check=True, capture_output=True, text=True)
            print(f"Successfully unmounted {RAMDISK_PATH}.")
        except subprocess.CalledProcessError as e:
            print(f"Error unmounting {RAMDISK_PATH}:")
            print(f"Command: {' '.join(e.cmd)}")
            print(f"Return code: {e.returncode}")
            print(f"Stdout: {e.stdout}")
            print(f"Stderr: {e.stderr}")
            print("Please check if any processes are using the RAM disk.")
            sys.exit(1)
    else:
        print(f"{RAMDISK_PATH} is not currently mounted or does not appear to be a mount point.")

    # Remove the RAMDISK_PATH directory if it exists
    if os.path.exists(RAMDISK_PATH):
        if os.path.isdir(RAMDISK_PATH): # Ensure it's a directory
            try:
                print(f"Removing directory {RAMDISK_PATH}...")
                # shutil.rmtree might be too aggressive if umount failed and there are files.
                # os.rmdir is safer as it only removes empty directories.
                # After a successful umount, tmpfs should be empty.
                os.rmdir(RAMDISK_PATH)
                print(f"Directory {RAMDISK_PATH} removed successfully.")
            except OSError as e:
                print(f"Error removing directory {RAMDISK_PATH}: {e}")
                print("This can happen if the directory is not empty (e.g., unmount failed or was incomplete).")
                print(f"Please check {RAMDISK_PATH} manually.")
                sys.exit(1)
        else:
            print(f"Warning: {RAMDISK_PATH} exists but is not a directory. Skipping removal of this path.")
    else:
        print(f"Directory {RAMDISK_PATH} does not exist. No need to remove.")
    
    print("RAM disk removal process finished.")


def print_usage():
    """Prints usage instructions."""
    print("Usage:")
    print("  sudo python3 ramdisk.py <size>  (e.g., 6G, 512M)")
    print("  sudo python3 ramdisk.py remove")

if __name__ == "__main__":
    check_sudo()

    if len(sys.argv) < 2:
        print("Error: Missing arguments.")
        print_usage()
        sys.exit(1)

    action = sys.argv[1]

    if action.lower() == "remove":
        if len(sys.argv) != 2:
            print("Error: 'remove' action does not take additional arguments.")
            print_usage()
            sys.exit(1)
        remove_ramdisk()
    else:
        # Assume it's a size for creation
        if len(sys.argv) != 2: # Script name + size
            print("Error: Invalid arguments for create action.")
            print_usage()
            sys.exit(1)
        ram_size = action # The first argument is the size
        create_ramdisk(ram_size)

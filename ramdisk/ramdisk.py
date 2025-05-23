#!/usr/bin/env python3

import os
import sys
import subprocess
import shutil # For shutil.which

RAMDISK_PATH = "/ramdisk"
ZRAM_FS_TYPE = "ext2" # Filesystem for ZRAM (ext2 is lightweight)
ZRAM_COMP_ALGORITHM = "lz4" # Common and fast compression algorithm

def check_sudo():
    """Checks if the script is run with sudo privileges."""
    if os.geteuid() != 0:
        print("Error: This script must be run with sudo privileges.")
        print("Please run as: sudo python3 ramdisk.py <size|remove> [--zram]")
        sys.exit(1)

def run_command(command, check=True, capture_output=False, text=False, shell=False):
    """Helper function to run shell commands."""
    print(f"Executing: {' '.join(command) if isinstance(command, list) else command}")
    try:
        # If shell=True, command should be a string
        # Otherwise, it should be a list of arguments
        process = subprocess.run(command, check=check, capture_output=capture_output, text=text, shell=shell)
        return process
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {' '.join(e.cmd) if isinstance(e.cmd, list) else e.cmd}")
        if e.stdout:
            print(f"Stdout: {e.stdout}")
        if e.stderr:
            print(f"Stderr: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Command '{command[0] if isinstance(command, list) else command.split()[0]}' not found. Is it installed and in PATH?")
        sys.exit(1)


def get_mount_info(path):
    """Gets source device and fstype for a given mount path."""
    if not os.path.ismount(path):
        return None, None
    try:
        # Use findmnt to get source and fstype
        # -n: no heading
        # -o SOURCE,FSTYPE: output only these columns
        # --target: specify the mountpoint
        result = run_command(
            ["findmnt", "-n", "-o", "SOURCE,FSTYPE", "--target", path],
            capture_output=True, text=True, check=True
        )
        output = result.stdout.strip()
        if output:
            parts = output.split()
            source_device = parts[0]
            fstype = parts[1] if len(parts) > 1 else "unknown" # Should normally be 2 parts
            return source_device, fstype
    except subprocess.CalledProcessError:
        # findmnt might fail if path is not a mountpoint or other issues
        pass # Fallthrough to check /proc/mounts or return None
    except FileNotFoundError:
        print("Error: 'findmnt' command not found. Cannot reliably determine mount type.")
        # As a fallback, could try parsing /proc/mounts, but it's more complex
        return None, "unknown_findmnt_missing"

    # Fallback or for systems without findmnt readily usable for this simple script parsing
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == path:
                    return parts[0], parts[2] # source_device, fstype
    except FileNotFoundError:
        print("Warning: /proc/mounts not found. Cannot determine mount type.")
    return None, None


def create_ramdisk(size_str, use_zram=False):
    """Creates a tmpfs or ZRAM RAM disk."""
    print(f"Attempting to create {'ZRAM' if use_zram else 'tmpfs'} RAM disk at {RAMDISK_PATH} with size {size_str}...")

    # Validate size format (e.g., "6G", "512M")
    if not (size_str.upper().endswith('G') or size_str.upper().endswith('M') or size_str.upper().endswith('K')):
        print("Error: Invalid size format. Please use 'G' (Gigabytes), 'M' (Megabytes), or 'K' (Kilobytes).")
        sys.exit(1)
    try:
        int(size_str[:-1])
    except ValueError:
        print("Error: Invalid size value. Please provide a numeric value before 'G', 'M', or 'K'.")
        sys.exit(1)

    if os.path.ismount(RAMDISK_PATH):
        print(f"Error: {RAMDISK_PATH} is already a mount point.")
        print(f"If you want to change it, please remove it first: sudo python3 {sys.argv[0]} remove")
        sys.exit(1)

    if not os.path.exists(RAMDISK_PATH):
        print(f"Creating directory {RAMDISK_PATH}...")
        run_command(["mkdir", "-p", RAMDISK_PATH])
        print(f"Directory {RAMDISK_PATH} created successfully.")
    elif not os.path.isdir(RAMDISK_PATH):
        print(f"Error: {RAMDISK_PATH} exists but is not a directory.")
        sys.exit(1)

    if use_zram:
        # ZRAM specific creation
        if not shutil.which("zramctl"):
            print("Error: 'zramctl' command not found. Please install 'util-linux' or equivalent package.")
            sys.exit(1)
        if not shutil.which(f"mkfs.{ZRAM_FS_TYPE}"):
            print(f"Error: 'mkfs.{ZRAM_FS_TYPE}' command not found. Please install tools for {ZRAM_FS_TYPE} (e.g., e2fsprogs for ext2/ext3/ext4).")
            sys.exit(1)

        print("Loading zram module...")
        run_command(["modprobe", "zram"])

        print(f"Finding and configuring a ZRAM device with size {size_str} and algorithm {ZRAM_COMP_ALGORITHM}...")
        # Use zramctl to find an unused device, set size and algorithm
        # This command prints the device path, e.g., /dev/zram0
        zram_device_proc = run_command(
            ["zramctl", "--find", "--size", size_str, "--algorithm", ZRAM_COMP_ALGORITHM],
            capture_output=True, text=True
        )
        zram_device_path = zram_device_proc.stdout.strip()
        if not zram_device_path or not zram_device_path.startswith("/dev/zram"):
            print(f"Error: Could not setup ZRAM device. Output: {zram_device_path}")
            # Attempt to cleanup directory if we created it
            if os.listdir(RAMDISK_PATH) == []: os.rmdir(RAMDISK_PATH)
            sys.exit(1)
        print(f"ZRAM device {zram_device_path} configured.")

        print(f"Formatting {zram_device_path} with {ZRAM_FS_TYPE}...")
        run_command([f"mkfs.{ZRAM_FS_TYPE}", "-F", zram_device_path]) # -F forces if already formatted or has data

        print(f"Mounting {zram_device_path} to {RAMDISK_PATH}...")
        run_command(["mount", zram_device_path, RAMDISK_PATH])
        print(f"ZRAM RAM disk created successfully at {RAMDISK_PATH} backed by {zram_device_path}.")

    else:
        # tmpfs specific creation
        mount_command = ["mount", "-t", "tmpfs", "-o", f"size={size_str}", "tmpfs", RAMDISK_PATH]
        run_command(mount_command)
        print(f"tmpfs RAM disk created successfully at {RAMDISK_PATH} with size {size_str}.")
    
    print(f"You can verify with: df -h {RAMDISK_PATH}")
    print(f"And for ZRAM, also with: zramctl")


def remove_ramdisk():
    """Removes the RAM disk at RAMDISK_PATH."""
    print(f"Attempting to remove RAM disk at {RAMDISK_PATH}...")

    source_device, fstype = None, None
    if os.path.ismount(RAMDISK_PATH):
        source_device, fstype = get_mount_info(RAMDISK_PATH)
        print(f"Detected mount: Source='{source_device}', Type='{fstype}' at {RAMDISK_PATH}")
        
        unmount_command = ["umount", RAMDISK_PATH]
        run_command(unmount_command)
        print(f"Successfully unmounted {RAMDISK_PATH}.")
    else:
        print(f"{RAMDISK_PATH} is not currently mounted or does not appear to be a mount point.")

    # ZRAM specific cleanup if it was a ZRAM device
    if source_device and source_device.startswith("/dev/zram"):
        if not shutil.which("zramctl"):
            print("Warning: 'zramctl' command not found. Cannot reset ZRAM device. Please do it manually if needed.")
        else:
            print(f"Resetting ZRAM device {source_device}...")
            run_command(["zramctl", "--reset", source_device])
            print(f"ZRAM device {source_device} reset successfully.")

    if os.path.exists(RAMDISK_PATH):
        if os.path.isdir(RAMDISK_PATH):
            try:
                if not os.listdir(RAMDISK_PATH): # Only remove if empty
                    print(f"Removing directory {RAMDISK_PATH}...")
                    os.rmdir(RAMDISK_PATH)
                    print(f"Directory {RAMDISK_PATH} removed successfully.")
                else:
                    print(f"Warning: Directory {RAMDISK_PATH} is not empty. Manual removal might be required.")
            except OSError as e:
                print(f"Error removing directory {RAMDISK_PATH}: {e}")
                print("This can happen if the directory is not empty (e.g., unmount failed or was incomplete).")
        else:
            print(f"Warning: {RAMDISK_PATH} exists but is not a directory. Skipping removal of this path.")
    else:
        print(f"Directory {RAMDISK_PATH} does not exist. No need to remove.")
    
    print("RAM disk removal process finished.")


def print_usage():
    script_name = sys.argv[0]
    print("Usage:")
    print(f"  sudo python3 {script_name} <size>             (Creates a tmpfs RAM disk, e.g., 6G, 512M)")
    print(f"  sudo python3 {script_name} <size> --zram      (Creates a ZRAM RAM disk)")
    print(f"  sudo python3 {script_name} remove           (Removes the RAM disk at {RAMDISK_PATH})")
    print("\nExamples:")
    print(f"  sudo python3 {script_name} 4G")
    print(f"  sudo python3 {script_name} 1G --zram")
    print(f"  sudo python3 {script_name} remove")


if __name__ == "__main__":
    check_sudo()

    if len(sys.argv) < 2:
        print("Error: Missing arguments.")
        print_usage()
        sys.exit(1)

    action_or_size = sys.argv[1]

    if action_or_size.lower() == "remove":
        if len(sys.argv) != 2:
            print("Error: 'remove' action does not take additional arguments.")
            print_usage()
            sys.exit(1)
        remove_ramdisk()
    else:
        # This is a create action, action_or_size is the size
        ram_size = action_or_size
        use_zram_flag = False

        if len(sys.argv) == 3:
            if sys.argv[2].lower() == "--zram":
                use_zram_flag = True
            else:
                print(f"Error: Unknown option '{sys.argv[2]}'")
                print_usage()
                sys.exit(1)
        elif len(sys.argv) > 3:
            print("Error: Too many arguments for create action.")
            print_usage()
            sys.exit(1)
        
        create_ramdisk(ram_size, use_zram=use_zram_flag)


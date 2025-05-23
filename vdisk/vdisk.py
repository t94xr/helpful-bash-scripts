#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import shutil
from pathlib import Path

# Global variable to keep track of the image file for cleanup
IMAGE_FILE_TO_CLEANUP = None

def run_command(command, check=True, capture_output=False, text=False, shell=False):
    """Helper function to run a shell command."""
    print(f"Executing: {' '.join(command) if isinstance(command, list) else command}")
    try:
        process = subprocess.run(
            command,
            check=check,
            capture_output=capture_output,
            text=text,
            shell=shell  # Use shell=True cautiously
        )
        return process
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {' '.join(e.cmd) if isinstance(e.cmd, list) else e.cmd}")
        print(f"Return code: {e.returncode}")
        if e.stdout:
            print(f"Stdout: {e.stdout}")
        if e.stderr:
            print(f"Stderr: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Command '{command[0] if isinstance(command, list) else command.split()[0]}' not found.")
        sys.exit(1)

def cleanup_on_error():
    """Cleans up the image file if it was created and an error occurred."""
    global IMAGE_FILE_TO_CLEANUP
    if IMAGE_FILE_TO_CLEANUP and Path(IMAGE_FILE_TO_CLEANUP).is_file():
        print(f"Cleaning up: removing {IMAGE_FILE_TO_CLEANUP} due to error.")
        try:
            run_command(['sudo', 'rm', '-f', str(IMAGE_FILE_TO_CLEANUP)])
        except Exception as e:
            print(f"Error during cleanup: {e}")

def check_command_exists(command_name):
    """Checks if a command is available in PATH."""
    return shutil.which(command_name) is not None

def get_image_label(image_file):
    """Tries to get the label of an image file using blkid."""
    if not Path(image_file).exists():
        return None
    try:
        process = run_command(['sudo', 'blkid', str(image_file), '-o', 'value', '-s', 'LABEL'],
                              capture_output=True, text=True, check=False)
        if process.returncode == 0 and process.stdout.strip():
            return process.stdout.strip()
    except Exception:
        pass # Ignore errors, blkid might fail if not formatted or no label
    return None

def convert_to_iso(image_file_path, iso_file_path, label):
    """Converts a disk image to an ISO file."""
    global IMAGE_FILE_TO_CLEANUP
    IMAGE_FILE_TO_CLEANUP = None # Don't cleanup original image in this function

    if not image_file_path or not iso_file_path:
        print(f"Usage: {Path(__file__).name} --convertiso <image_file> <output_iso_file> [<label>]")
        sys.exit(1)

    if not image_file_path.is_file():
        print(f"Error: {image_file_path} does not exist.")
        sys.exit(1)

    if not label:
        label = get_image_label(image_file_path)
        if not label:
            print(f"Warning: No label found in {image_file_path}, using 'NO_LABEL' instead.")
            label = "NO_LABEL"

    if not check_command_exists("genisoimage"):
        print("Error: genisoimage is not installed. Please install it (e.g., sudo apt-get install genisoimage)")
        sys.exit(1)

    print(f"Creating ISO from {image_file_path} with label '{label}'...")
    try:
        run_command(['sudo', 'genisoimage', '-o', str(iso_file_path), '-V', label, '-J', '-r', str(image_file_path)])
        print(f"ISO created: {iso_file_path}")
    except Exception as e:
        print(f"Error during ISO creation: {e}")
        # Unlike the main mount, we don't auto-delete the input image_file here
        # but if iso_file_path was created and is partial, it might be desirable to clean it.
        # For simplicity, this is omitted, but could be added.
        sys.exit(1)


def main():
    global IMAGE_FILE_TO_CLEANUP

    parser = argparse.ArgumentParser(description="Manage virtual disk images.", add_help=False)

    # Custom help action
    parser.add_argument(
        "--help", action="help", default=argparse.SUPPRESS,
        help="Show this help message and exit"
    )

    # Operation modes
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--umount", metavar="IMAGE_FILE", help="Umount the specified image file.")
    group.add_argument("--resize", nargs=2, metavar=("NEW_SIZE", "IMAGE_FILE"), help="Resize the specified image file.")
    group.add_argument("--convertiso", nargs='+', metavar=("IMAGE_FILE", "OUTPUT_ISO_FILE", "[LABEL]"), help="Convert image to ISO.")

    # Mount options
    parser.add_argument("--fs", default="ext4", help="Specify the filesystem type (default: ext4). Supported: ext4, ext3, ext2, xfs, btrfs, jfs, fat32, ntfs, fat16.")
    parser.add_argument("--label", default="", help="Specify the label for the filesystem.")
    parser.add_argument("--readonly", action="store_true", help="Mount the filesystem as read-only.")
    parser.add_argument("--auto-mount", action="store_true", help="Automatically mount the filesystem on boot via fstab.")
    parser.add_argument("--nomount", action="store_true", help="Only format the image without mounting it.")

    # Positional arguments for mount operation
    parser.add_argument("size", nargs="?", help="Size of the virtual disk (e.g., 1G, 500M). Required for mount unless --umount, --resize, or --convertiso is used.")
    parser.add_argument("image_file", nargs="?", help="Path to the image file. Required for mount unless --umount, --resize, or --convertiso is used.")
    parser.add_argument("mount_point", nargs="?", help="Path to the mount point. Required for mount unless --nomount, --umount, --resize, or --convertiso is used.")

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(0)

    args = parser.parse_args()

    # --umount operation
    if args.umount:
        image_file_to_umount = Path(args.umount).resolve()
        if not image_file_to_umount:
            print("Error: Missing image file for --umount")
            parser.print_help(sys.stderr)
            sys.exit(1)

        try:
            mount_output = run_command(['mount'], capture_output=True, text=True).stdout
            mounted_path = None
            for line in mount_output.splitlines():
                if str(image_file_to_umount) in line or (Path(image_file_to_umount).name in line and "loop" in line): # More robust check
                    # Heuristic: find loop device associated with the file
                    loop_dev_search = run_command(['sudo', 'losetup', '-j', str(image_file_to_umount)], capture_output=True, text=True, check=False)
                    if loop_dev_search.returncode == 0 and loop_dev_search.stdout:
                        loop_dev_path = loop_dev_search.stdout.split(':')[0]
                        for l in mount_output.splitlines():
                            if loop_dev_path in l:
                                mounted_path = l.split()[2]
                                break
                    if not mounted_path: # Fallback if losetup didn't help directly
                         mounted_path = line.split()[2]
                    break


            if mounted_path:
                print(f"Unmounting {image_file_to_umount} from {mounted_path}...")
                run_command(['sudo', 'umount', mounted_path])
                # Also detach loop device if it was used directly
                loop_devices = run_command(['sudo', 'losetup', '--list'], capture_output=True, text=True).stdout
                for line in loop_devices.splitlines():
                    if str(image_file_to_umount) in line:
                        loop_dev = line.split()[0]
                        print(f"Detaching loop device {loop_dev}...")
                        run_command(['sudo', 'losetup', '-d', loop_dev])
                        break
                print("Done.")
            else:
                print(f"Image {image_file_to_umount} is not currently mounted or not found mounted directly.")
        except Exception as e:
            print(f"Error during umount: {e}")
            sys.exit(1)
        sys.exit(0)

    # --resize operation
    elif args.resize:
        new_size, image_file_to_resize_str = args.resize
        image_file_to_resize = Path(image_file_to_resize_str).resolve()

        if not new_size or not image_file_to_resize:
            print("Error: Missing arguments for --resize")
            parser.print_help(sys.stderr)
            sys.exit(1)

        if not image_file_to_resize.is_file():
            print(f"Error: Image file {image_file_to_resize} does not exist.")
            sys.exit(1)

        print(f"Resizing {image_file_to_resize} to {new_size}...")
        try:
            # This command truncates or extends the file.
            # dd seek is block based, qemu-img resize is often better for this.
            # For simplicity, using dd as in the original script.
            # Ensure the file is at least new_size. If shrinking, data loss can occur.
            # The original script's dd command is for extending, not robustly shrinking.
            # A safer approach for extending is 'truncate -s NEW_SIZE IMAGE_FILE'
            # For resizing filesystems, the filesystem must support it and be unmounted or mounted with care.
            print(f"Extending image file to {new_size} (this might not shrink correctly)...")
            run_command(['sudo', 'dd', 'if=/dev/zero', f'bs=1', 'count=0', f'seek={new_size}', f'of={image_file_to_resize}'])

            # Filesystem resize part (assuming ext2/3/4 as per e2fsck/resize2fs)
            # This part is highly dependent on the filesystem and partitioning.
            # The original script assumes a raw filesystem image, not partitioned.
            print("Attempting to resize filesystem (assuming ext2/3/4 and no partitions)...")
            loop_device_proc = run_command(['sudo', 'losetup', '--find', '--show', str(image_file_to_resize)], capture_output=True, text=True)
            loop_device = loop_device_proc.stdout.strip()
            if not loop_device:
                print("Error: Could not set up loop device.")
                sys.exit(1)

            print(f"Loop device {loop_device} created.")
            run_command(['sudo', 'e2fsck', '-f', '-y', loop_device], check=False) # Add -y for non-interactive
            run_command(['sudo', 'resize2fs', loop_device])
            run_command(['sudo', 'losetup', '-d', loop_device])
            print("Filesystem resize attempted. NOTE: If the image has partitions or a different filesystem, you may need to resize them manually using fdisk, parted, or other tools.")
        except Exception as e:
            print(f"Error during resize: {e}")
            sys.exit(1)
        sys.exit(0)

    # --convertiso operation
    elif args.convertiso:
        if not (2 <= len(args.convertiso) <= 3):
            print("Error: Incorrect number of arguments for --convertiso")
            parser.print_help(sys.stderr)
            sys.exit(1)

        img_file = Path(args.convertiso[0]).resolve()
        iso_out_file = Path(args.convertiso[1]).resolve()
        iso_label = args.convertiso[2] if len(args.convertiso) > 2 else None
        convert_to_iso(img_file, iso_out_file, iso_label)
        sys.exit(0)


    # --- Main mount operation ---
    if not args.size or not args.image_file or (not args.mount_point and not args.nomount):
        print("Error: Missing arguments for mount operation.")
        parser.print_help(sys.stderr)
        sys.exit(1)

    size = args.size
    image_file = Path(args.image_file).resolve()
    IMAGE_FILE_TO_CLEANUP = str(image_file) # Set for potential cleanup
    mount_point = Path(args.mount_point).resolve() if args.mount_point else None
    fs_type = args.fs.lower()
    label = args.label
    readonly = args.readonly
    auto_mount = args.auto_mount
    no_mount = args.nomount

    # Register cleanup function to be called on exit (including errors after this point)
    # This is a more global approach than try/finally around everything.
    # However, for specific cleanup of IMAGE_FILE, try/finally in the creation block is better.
    # For now, we'll rely on explicit try/finally for the image creation part.


    try:
        if not no_mount:
            if not mount_point:
                print("Error: Mount point is required unless --nomount is specified.")
                sys.exit(1)
            if not mount_point.exists():
                print(f"Creating mount point at {mount_point}")
                run_command(['sudo', 'mkdir', '-p', str(mount_point)])
            elif not mount_point.is_dir():
                print(f"Error: Mount point {mount_point} exists but is not a directory.")
                sys.exit(1)

        # Check if image is already mounted
        if not no_mount:
            mount_output = run_command(['mount'], capture_output=True, text=True).stdout
            # A more robust check would involve checking losetup for the file and then checking if that loop device is mounted.
            loop_dev_path_for_file = None
            try:
                losetup_output = run_command(['sudo', 'losetup', '-j', str(image_file)], capture_output=True, text=True, check=False).stdout
                if losetup_output:
                    loop_dev_path_for_file = losetup_output.split(':')[0].strip()
            except Exception:
                pass # File might not be associated with a loop device

            for line in mount_output.splitlines():
                # Check if image_file path itself is in mount output (direct mount, less common for files)
                # or if the associated loop device is mounted at the target mount_point
                # or if the image_file (by name) is associated with a loop device mounted anywhere
                is_mounted_direct = str(image_file) in line and str(mount_point) in line
                is_mounted_via_loop_at_target = loop_dev_path_for_file and loop_dev_path_for_file in line and str(mount_point) in line
                is_mounted_via_loop_anywhere = loop_dev_path_for_file and loop_dev_path_for_file in line

                if is_mounted_direct or is_mounted_via_loop_at_target or (is_mounted_via_loop_anywhere and line.split()[2] == str(mount_point)):
                    print(f"Image {image_file} appears to be already mounted:")
                    print(line)
                    sys.exit(0)

        if not image_file.exists():
            print(f"Creating virtual disk image at {image_file} with size {size}...")
            # Using dd for sparse file creation. 'truncate -s' is often preferred.
            run_command(['sudo', 'dd', 'if=/dev/zero', f'of={image_file}', 'bs=1', 'count=0', f'seek={size}'])

            print(f"Formatting with {fs_type}...")
            mkfs_cmd = []
            if fs_type in ["ext4", "ext3", "ext2", "xfs", "btrfs", "jfs"]:
                mkfs_cmd = [f'sudo mkfs.{fs_type}']
                if label:
                    if fs_type == "xfs" or fs_type == "btrfs": # XFS/BTRFS use -L differently or need specific subcommands
                         mkfs_cmd.append(f'-L "{label}"') # This might need adjustment based on exact mkfs util
                    else:
                        mkfs_cmd.append(f'-L "{label}"')
                mkfs_cmd.append(f'"{str(image_file)}"')
                if fs_type == "btrfs": # Btrfs might need -f if the file was used before
                    mkfs_cmd.insert(1, "-f")

            elif fs_type in ["fat32", "fat16"]:
                if not check_command_exists("mkfs.vfat"):
                    print("Error: FAT32/FAT16 formatting requires 'dosfstools'.")
                    print("Please install it using: sudo apt install dosfstools")
                    cleanup_on_error() # Clean up image file
                    sys.exit(1)
                mkfs_cmd = ['sudo', 'mkfs.vfat']
                if fs_type == "fat32":
                    mkfs_cmd.append("-F 32")
                elif fs_type == "fat16":
                    mkfs_cmd.append("-F 16") # Or let mkfs.vfat decide based on size
                if label:
                    # FAT label max 11 chars, no spaces, uppercase. mkfs.vfat might truncate/adjust.
                    fat_label = label.upper().replace(" ", "")[:11]
                    mkfs_cmd.extend(['-n', fat_label])
                mkfs_cmd.append(str(image_file))

            elif fs_type == "ntfs":
                mkfs_ntfs_path = shutil.which("mkfs.ntfs")
                mkntfs_path = shutil.which("mkntfs")
                if mkfs_ntfs_path:
                    mkfs_cmd = ['sudo', mkfs_ntfs_path]
                elif mkntfs_path:
                    mkfs_cmd = ['sudo', mkntfs_path]
                else:
                    print("Error: NTFS formatting requires 'ntfs-3g' and a usable 'mkfs.ntfs' or 'mkntfs'.")
                    print("Please install it using: sudo apt install ntfs-3g")
                    cleanup_on_error()
                    sys.exit(1)
                if label:
                    mkfs_cmd.extend(['-L', label])
                mkfs_cmd.extend(['-F', str(image_file)]) # -F for force, common for ntfs-3g's mkfs
            else:
                print(f"Unsupported filesystem: {fs_type}")
                cleanup_on_error()
                sys.exit(1)

            # If mkfs_cmd is a list of lists due to shell=True style command, join it.
            if isinstance(mkfs_cmd[0], str) and ' ' in mkfs_cmd[0] and len(mkfs_cmd) == 1 : # Heuristic for commands built as strings
                run_command(mkfs_cmd[0], shell=True)
            else:
                # Filter out empty strings that might result from conditional appends
                final_mkfs_cmd = [item for item in mkfs_cmd if item]
                run_command(final_mkfs_cmd)
        else:
            print(f"Image file {image_file} already exists. Skipping creation and formatting.")

        # Mount the image
        if not no_mount:
            mount_options = ["loop"]
            if readonly:
                mount_options.append("ro")

            print(f"Mounting {image_file} to {mount_point}...")
            run_command(['sudo', 'mount', '-o', ",".join(mount_options), str(image_file), str(mount_point)])
            print("Mounted successfully.")

            if auto_mount:
                fstab_entry = f"{image_file} {mount_point} {fs_type} loop"
                if readonly:
                    fstab_entry += ",ro"
                fstab_entry += " 0 0"

                try:
                    with open('/etc/fstab', 'r') as f:
                        fstab_content = f.read()
                    if str(image_file) not in fstab_content: # Simple check
                        print("Adding auto-mount entry to /etc/fstab...")
                        # Use tee for writing to /etc/fstab with sudo
                        echo_cmd = ['echo', fstab_entry]
                        tee_cmd = ['sudo', 'tee', '-a', '/etc/fstab']

                        # Popen for piping
                        echo_proc = subprocess.Popen(echo_cmd, stdout=subprocess.PIPE)
                        tee_proc = subprocess.Popen(tee_cmd, stdin=echo_proc.stdout, stdout=subprocess.PIPE)
                        echo_proc.stdout.close() # Allow echo_proc to receive a SIGPIPE if tee_proc exits.
                        tee_output = tee_proc.communicate()[0]
                        if tee_proc.returncode != 0:
                            print(f"Error writing to /etc/fstab: {tee_output.decode()}")
                            # Note: entry might be partially written. Manual check advised.
                    else:
                        print(f"An entry for {image_file} likely already exists in /etc/fstab. Skipping.")
                except Exception as e:
                    print(f"Error accessing /etc/fstab: {e}. Manual entry might be required for auto-mount.")

        # Change ownership (after successful operations that might involve the image/mount)
        IMAGE_FILE_TO_CLEANUP = None # Successfully processed, disable cleanup for this file

        # Check mount status again before chown
        is_currently_mounted = False
        if not no_mount and mount_point:
            try:
                mount_output = run_command(['mount'], capture_output=True, text=True).stdout
                if str(mount_point) in mount_output: # Check if mount_point is in the output
                    is_currently_mounted = True
            except Exception:
                pass # Ignore error if mount command fails, proceed with chown based on logic

        current_user = os.environ.get('USER', 'root') # Fallback to root if USER not set
        if not no_mount and mount_point and is_currently_mounted:
            print(f"Changing ownership of mount point {mount_point} to current user {current_user} (requires sudo permissions for mount point itself).")
            # Typically, the content of the mount point would be owned by root by default for loop mounts.
            # The user might want to own the *contents* after mounting.
            # Changing ownership of the mount point directory itself while mounted can be tricky.
            # The original script changes mount_point to root:root if mounted, else user:user for image_file.
            # Let's stick to the original logic's target for mount point.
            run_command(['sudo', 'chown', f'{current_user}:{current_user}', str(mount_point)])
        elif not image_file.is_dir(): # Only chown if it's a file and not mounted (or nomount)
            print(f"Changing ownership of image file {image_file} to {current_user}:{current_user}")
            run_command(['sudo', 'chown', f'{current_user}:{current_user}', str(image_file)])
        else:
             if no_mount:
                 print(f"Image was not mounted. Ownership of {image_file} can be set to {current_user}:{current_user} if needed.")
                 run_command(['sudo', 'chown', f'{current_user}:{current_user}', str(image_file)])


    except Exception as e:
        print(f"An error occurred: {e}")
        cleanup_on_error() # Attempt to clean up the image file if one was being created
        sys.exit(1)
    finally:
        # Ensure IMAGE_FILE_TO_CLEANUP is cleared if no error occurred or handled by cleanup_on_error
        if 'IMAGE_FILE_TO_CLEANUP' in globals() and IMAGE_FILE_TO_CLEANUP is not None and Path(IMAGE_FILE_TO_CLEANUP).exists() and not sys.exc_info()[0]:
            pass # No error, or cleanup was already called
        elif 'IMAGE_FILE_TO_CLEANUP' in globals() and IMAGE_FILE_TO_CLEANUP is None:
            pass # Was reset intentionally


if __name__ == "__main__":
    # Check for root privileges early if certain operations are expected
    # This is a bit complex as not all operations need root.
    # For now, we rely on sudo being prepended to commands.
    # A more robust check could be done based on parsed args.
    # if os.geteuid() != 0:
    # print("Warning: Some operations require root privileges. Please run with sudo if necessary.")
    main()

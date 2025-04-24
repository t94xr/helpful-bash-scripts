#!/bin/bash

set -e

cleanup_on_error() {
    if [[ -f "$IMAGE_FILE" ]]; then
        echo "Cleaning up: removing $IMAGE_FILE due to error."
        sudo rm -f "$IMAGE_FILE"
    fi
}

trap cleanup_on_error ERR

show_help() {
    SCRIPT_NAME=$(basename "$0")
    echo "Usage:"
    echo "  Mount:   $SCRIPT_NAME [--fs <fstype>] [--label <label>] [--readonly] [--auto-mount] [--nomount] <size> <image_file> <mount_point>"
    echo "           Example: $SCRIPT_NAME --fs ext4 --label MyDisk 1G /tmp/disk.img /mnt/drive"
    echo ""
    echo "  Umount:  $SCRIPT_NAME --umount <image_file>"
    echo "  Resize:  $SCRIPT_NAME --resize <new_size> <image_file>"
    echo "  Convert: $SCRIPT_NAME --convertiso <image_file> <output_iso_file> [<label>]"
    echo "  Help:    $SCRIPT_NAME --help"
    echo ""
    echo "  Supported filesystems: ext4, ext3, ext2, xfs, btrfs, jfs, fat32, ntfs, fat16"
    echo ""
    echo "Flags:"
    echo "  --fs <fstype>        Specify the filesystem type (e.g., ext4, fat32, ntfs)"
    echo "  --label <label>      Specify the label for the filesystem"
    echo "  --readonly           Mount the filesystem as read-only"
    echo "  --auto-mount         Automatically mount the filesystem on boot via fstab"
    echo "  --nomount            Only format the image without mounting it"
    echo "  --help               Show this help message"
}

check_genisoimage() {
    if ! command -v genisoimage &> /dev/null; then
        echo "Error: genisoimage is not installed. Please install it using: sudo apt-get install genisoimage"
        exit 1
    fi
}

convert_to_iso() {
    IMAGE_FILE="$1"
    ISO_FILE="$2"
    LABEL="$3"

    if [[ -z "$IMAGE_FILE" || -z "$ISO_FILE" ]]; then
        echo "Usage: $0 --convertiso <image_file> <output_iso_file> [<label>]"
        exit 1
    fi

    if [ ! -f "$IMAGE_FILE" ]; then
        echo "Error: $IMAGE_FILE does not exist."
        exit 1
    fi

    if [ -z "$LABEL" ]; then
        LABEL=$(sudo blkid "$IMAGE_FILE" -o value -s LABEL)
        if [ -z "$LABEL" ]; then
            echo "Warning: No label found in $IMAGE_FILE, using 'NO_LABEL' instead."
            LABEL="NO_LABEL"
        fi
    fi

    echo "Creating ISO from $IMAGE_FILE with label '$LABEL'..."
    sudo genisoimage -o "$ISO_FILE" -V "$LABEL" -J -r "$IMAGE_FILE"
    echo "ISO created: $ISO_FILE"
}

FS_TYPE="ext4"
LABEL=""
READONLY=false
AUTOMOUNT=false
NO_MOUNT=false

# Show help if no arguments are passed
if [ $# -eq 0 ]; then
    show_help
    exit 0
fi

while [[ "$1" == --* ]]; do
    case "$1" in
        --help)
            show_help
            exit 0
            ;;
        --umount)
            IMAGE_FILE="$2"
            if [[ -z "$IMAGE_FILE" ]]; then
                echo "Error: Missing image file for --umount"
                show_help
                exit 1
            fi
            MOUNTED_PATH=$(mount | grep "$IMAGE_FILE" | awk '{print $3}')
            if [[ -n "$MOUNTED_PATH" ]]; then
                echo "Unmounting $IMAGE_FILE from $MOUNTED_PATH..."
                sudo umount "$MOUNTED_PATH"
                echo "Done."
            else
                echo "Image $IMAGE_FILE is not currently mounted."
            fi
            exit 0
            ;;
        --resize)
            NEW_SIZE="$2"
            IMAGE_FILE="$3"
            if [[ -z "$NEW_SIZE" || -z "$IMAGE_FILE" ]]; then
                echo "Error: Missing arguments for --resize"
                show_help
                exit 1
            fi
            echo "Resizing $IMAGE_FILE to $NEW_SIZE..."
            if [ ! -f "$IMAGE_FILE" ]; then
                echo "Error: Image file $IMAGE_FILE does not exist."
                exit 1
            fi
            sudo dd if=/dev/zero bs=1 count=0 seek="$NEW_SIZE" of="$IMAGE_FILE"
            LOOP_DEVICE=$(sudo losetup --find --show "$IMAGE_FILE")
            sudo e2fsck -f "$LOOP_DEVICE"
            sudo resize2fs "$LOOP_DEVICE"
            sudo losetup -d "$LOOP_DEVICE"
            echo "Filesystem resized. NOTE: If the image has partitions, you may need to resize them manually using fdisk or parted."
            exit 0
            ;;
        --convertiso)
            IMAGE_FILE="$2"
            ISO_FILE="$3"
            LABEL="$4"
            if [[ -z "$IMAGE_FILE" || -z "$ISO_FILE" ]]; then
                echo "Error: Missing arguments for --convertiso"
                show_help
                exit 1
            fi
            check_genisoimage
            convert_to_iso "$IMAGE_FILE" "$ISO_FILE" "$LABEL"
            exit 0
            ;;
        --fs)
            FS_TYPE="$2"
            shift 2
            ;;
        --label)
            LABEL="$2"
            shift 2
            ;;
        --readonly)
            READONLY=true
            shift
            ;;
        --auto-mount)
            AUTOMOUNT=true
            shift
            ;;
        --nomount)
            NO_MOUNT=true
            shift
            ;;
        *)
            echo "Unknown flag: $1"
            show_help
            exit 1
            ;;
    esac
done

SIZE="$1"
IMAGE_FILE="$2"
MOUNT_POINT="$3"

if [[ -z "$SIZE" || -z "$IMAGE_FILE" || -z "$MOUNT_POINT" && "$NO_MOUNT" == false ]]; then
    echo "Error: Missing arguments."
    show_help
    exit 1
fi

if [ ! -d "$MOUNT_POINT" ] && [ "$NO_MOUNT" == false ]; then
    echo "Creating mount point at $MOUNT_POINT"
    sudo mkdir -p "$MOUNT_POINT"
fi

if mount | grep -q "$IMAGE_FILE"; then
    echo "Image $IMAGE_FILE is already mounted:"
    mount | grep "$IMAGE_FILE"
    exit 0
fi

if [ ! -f "$IMAGE_FILE" ]; then
    echo "Creating virtual disk image at $IMAGE_FILE with size $SIZE..."
    sudo dd if=/dev/zero of="$IMAGE_FILE" bs=1 count=0 seek="$SIZE"
    echo "Formatting with $FS_TYPE..."
    case "$FS_TYPE" in
        ext4|ext3|ext2|xfs|btrfs|jfs)
            CMD="sudo mkfs.$FS_TYPE"
            [[ -n "$LABEL" ]] && CMD+=" -L \"$LABEL\""
            eval "$CMD \"$IMAGE_FILE\""
            ;;
        fat32|fat16)
            if ! command -v mkfs.vfat &> /dev/null; then
                echo "Error: FAT32/FAT16 formatting requires 'dosfstools'."
                echo "Please install it using: sudo apt install dosfstools"
                exit 1
            fi
            CMD="sudo mkfs.vfat -F 32"
            [[ -n "$LABEL" ]] && CMD+=" -n \"$LABEL\""
            eval "$CMD \"$IMAGE_FILE\""
            ;;
        ntfs)
            if command -v mkfs.ntfs &> /dev/null; then
                CMD="sudo mkfs.ntfs"
            elif command -v mkntfs &> /dev/null; then
                CMD="sudo mkntfs"
            else
                echo "Error: NTFS formatting requires 'ntfs-3g' and a usable 'mkfs.ntfs' or 'mkntfs'."
                echo "Please install it using: sudo apt install ntfs-3g"
                exit 1
            fi
            [[ -n "$LABEL" ]] && CMD+=" -L \"$LABEL\""
            CMD+=" -F"
            eval "$CMD \"$IMAGE_FILE\""
            ;;
        *)
            echo "Unsupported filesystem: $FS_TYPE"
            exit 1
            ;;
    esac
else
    echo "Image file $IMAGE_FILE already exists."
fi

if [ "$NO_MOUNT" == false ]; then
    MOUNT_OPTIONS="loop"
    $READONLY && MOUNT_OPTIONS+="\,ro"

    echo "Mounting $IMAGE_FILE to $MOUNT_POINT..."
    sudo mount -o "$MOUNT_OPTIONS" "$IMAGE_FILE" "$MOUNT_POINT"
    echo "Mounted successfully."

    if $AUTOMOUNT; then
        FSTAB_ENTRY="$IMAGE_FILE $MOUNT_POINT $FS_TYPE loop"
        $READONLY && FSTAB_ENTRY+="\,ro"
        FSTAB_ENTRY+=" 0 0"

        if ! grep -qs "$IMAGE_FILE" /etc/fstab; then
            echo "Adding auto-mount entry to /etc/fstab..."
            echo "$FSTAB_ENTRY" | sudo tee -a /etc/fstab > /dev/null
        else
            echo "An entry for $IMAGE_FILE already exists in /etc/fstab. Skipping."
        fi
    fi
fi

# Change ownership based on mount status
if mount | grep -q "$IMAGE_FILE"; then
    echo "Changing ownership to root:root for $IMAGE_FILE"
    sudo chown root:root "$MOUNT_POINT"
else
    echo "Changing ownership to $USER:$USER for $IMAGE_FILE"
    sudo chown "$USER:$USER" "$MOUNT_POINT"
fi

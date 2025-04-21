#!/bin/bash

# ================================
# Smart AV1 Encoder w/ Auto-Detect
#
# Cameron Walker - github.com/t94xr
# ================================

# Usage: ./hevc_auto_encode.sh [-j <jobs>] *.mp4

# === Config ===
MAX_JOBS=2  # Default
JOB_COUNT=0

# Check for -j (jobs) argument
while getopts "j:" opt; do
  case $opt in
    j)
      MAX_JOBS="$OPTARG"
      ;;
  esac
done

# Shift out processed options
shift $((OPTIND - 1))

if [ $# -eq 0 ]; then
  echo "Usage: $0 [-j <parallel_jobs>] <video_file(s)>"
  exit 1
fi

# 🔍 Find Intel render node (QSV)
find_qsv_device() {
  for dev in /dev/dri/render*; do
    if udevadm info --query=all --name="$dev" | grep -qi 'vendor=0x8086'; then
      echo "$dev"
      return 0
    fi
  done
  return 1
}

# 🧠 Choose best encoder
detect_hevc_encoder() {
  QSV_DEVICE=$(find_qsv_device)

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'hevc_nvenc'; then
    echo "hevc_nvenc"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'hevc_qsv' && [ -n "$QSV_DEVICE" ]; then
    if ffmpeg -init_hw_device qsv=hw:"$QSV_DEVICE" -filter_hw_device hw \
      -f lavfi -i testsrc2=s=16x16:d=1 \
      -c:v hevc_qsv -t 1 -f null -y /dev/null 2>&1 | grep -q 'Error creating a MFX session'; then
      echo "⚠️ hevc_qsv found but failed — skipping"
    else
      echo "hevc_qsv"
      return
    fi
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'libx265'; then
    echo "libx265"
    return
  fi

  echo "❌ No supported HEVC encoder found"
  exit 1
}

ENCODER=$(detect_hevc_encoder)
echo "✅ Selected HEVC encoder: $ENCODER"

# Set filename suffix
case "$ENCODER" in
  hevc_nvenc) SUFFIX="_hevc_nvenc" ;;
  hevc_qsv)   SUFFIX="_hevc_qsv" ;;
  libx265)    SUFFIX="_hevc_cpu" ;;
esac

# ======================
# Encode function
# ======================
encode_file() {
  INPUT_FILE="$1"
  QSV_DEVICE="$2"

  BASENAME=$(basename "$INPUT_FILE")
  FILENAME="${BASENAME%.*}"
  EXT="${BASENAME##*.}"
  OUTPUT_FILE="${FILENAME}${SUFFIX}.${EXT}"

  if [[ -f "$OUTPUT_FILE" ]]; then
    echo "⏭️  Skipping already encoded: $OUTPUT_FILE"
    return
  fi

  echo "▶️ Encoding: $INPUT_FILE → $OUTPUT_FILE"

  case "$ENCODER" in
    hevc_nvenc)
      ffmpeg -y -hwaccel cuda -i "$INPUT_FILE" \
        -c:v hevc_nvenc -rc vbr -cq 28 -preset p7 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
    hevc_qsv)
      ffmpeg -y -init_hw_device qsv=hw:"$QSV_DEVICE" -filter_hw_device hw \
        -i "$INPUT_FILE" \
        -c:v hevc_qsv -global_quality 28 -look_ahead 1 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
    libx265)
      ffmpeg -y -i "$INPUT_FILE" \
        -c:v libx265 -crf 28 -preset medium \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
  esac

  echo "✅ Done: $OUTPUT_FILE"
  echo ""
}

# ======================
# Process input files
# ======================
QSV_DEVICE=$(find_qsv_device)

for INPUT_FILE in "$@"; do
  if [ ! -f "$INPUT_FILE" ]; then
    echo "⚠️ Skipping: $INPUT_FILE not found"
    continue
  fi

  # Launch job in background
  encode_file "$INPUT_FILE" "$QSV_DEVICE" &

  JOB_COUNT=$((JOB_COUNT + 1))

  # Limit parallel jobs
  if [ "$JOB_COUNT" -ge "$MAX_JOBS" ]; then
    wait -n  # Wait for any job to finish
    JOB_COUNT=$((JOB_COUNT - 1))
  fi
done

# Wait for any remaining jobs
wait
echo "✅ All encoding jobs complete."

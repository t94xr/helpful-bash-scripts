#!/bin/bash

# ================================
# Smart AV1 Encoder w/ Auto-Detect
#
# Cameron Walker - github.com/t94xr
# ================================

# Usage: ./av1_auto_encode.sh [-j <jobs>] *.mp4

# === Config ===
MAX_JOBS=2  # Default parallel jobs
JOB_COUNT=0

# Parse -j option
while getopts "j:" opt; do
  case $opt in
    j) MAX_JOBS="$OPTARG" ;;
  esac
done
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
detect_av1_encoder() {
  QSV_DEVICE=$(find_qsv_device)

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'av1_nvenc'; then
    echo "av1_nvenc"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'av1_qsv' && [ -n "$QSV_DEVICE" ]; then
    if ffmpeg -init_hw_device qsv=hw:"$QSV_DEVICE" -filter_hw_device hw \
      -f lavfi -i testsrc2=s=16x16:d=1 \
      -c:v av1_qsv -t 1 -f null -y /dev/null 2>&1 | grep -q 'Error creating a MFX session'; then
      echo "⚠️ av1_qsv found but failed — skipping"
    else
      echo "av1_qsv"
      return
    fi
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'libsvtav1'; then
    echo "libsvtav1"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'libaom-av1'; then
    echo "libaom-av1"
    return
  fi

  echo "❌ No supported AV1 encoder found"
  exit 1
}

ENCODER=$(detect_av1_encoder)
echo "✅ Selected AV1 encoder: $ENCODER"

# Suffix by encoder
case "$ENCODER" in
  av1_nvenc)   SUFFIX="_av1_nvenc" ;;
  av1_qsv)     SUFFIX="_av1_qsv" ;;
  libsvtav1)   SUFFIX="_av1_svt" ;;
  libaom-av1)  SUFFIX="_av1_aom" ;;
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
    av1_nvenc)
      ffmpeg -y -hwaccel cuda -i "$INPUT_FILE" \
        -c:v av1_nvenc -cq 28 -preset p7 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
    av1_qsv)
      ffmpeg -y -init_hw_device qsv=hw:"$QSV_DEVICE" -filter_hw_device hw \
        -i "$INPUT_FILE" \
        -c:v av1_qsv -global_quality 28 -look_ahead 1 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
    libsvtav1)
      ffmpeg -y -i "$INPUT_FILE" \
        -c:v libsvtav1 -preset 8 -crf 35 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
    libaom-av1)
      ffmpeg -y -i "$INPUT_FILE" \
        -c:v libaom-av1 -crf 35 -b:v 0 -cpu-used 6 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
  esac

  echo "✅ Done: $OUTPUT_FILE"
  echo ""
}

# ======================
# Loop through files
# ======================
QSV_DEVICE=$(find_qsv_device)

for INPUT_FILE in "$@"; do
  if [ ! -f "$INPUT_FILE" ]; then
    echo "⚠️ Skipping: $INPUT_FILE not found"
    continue
  fi

  encode_file "$INPUT_FILE" "$QSV_DEVICE" &

  JOB_COUNT=$((JOB_COUNT + 1))

  if [ "$JOB_COUNT" -ge "$MAX_JOBS" ]; then
    wait -n
    JOB_COUNT=$((JOB_COUNT - 1))
  fi
done

wait
echo "✅ All AV1 encoding jobs complete."

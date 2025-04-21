#!/bin/bash

# ================================
# Smart AV1 Encoder w/ Auto-Detect
#
# Cameron Walker - github.com/t94xr
# ================================

# Usage: ./av1_auto_encode.sh *.mp4

if [ $# -eq 0 ]; then
  echo "Usage: $0 <video_file(s)>"
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

# Set filename suffix
case "$ENCODER" in
  av1_qsv)    SUFFIX="_av1_qsv" ;;
  libsvtav1)  SUFFIX="_av1_svt" ;;
  libaom-av1) SUFFIX="_av1_cpu" ;;
esac

# ======================
# Process input files
# ======================
for INPUT_FILE in "$@"; do
  if [ ! -f "$INPUT_FILE" ]; then
    echo "⚠️ Skipping: $INPUT_FILE not found"
    continue
  fi

  BASENAME=$(basename "$INPUT_FILE")
  FILENAME="${BASENAME%.*}"
  EXT="${BASENAME##*.}"
  OUTPUT_FILE="${FILENAME}${SUFFIX}.${EXT}"

  if [[ -f "$OUTPUT_FILE" ]]; then
    echo "⏭️  Skipping already encoded: $OUTPUT_FILE"
    continue
  fi

  echo "▶️ Encoding: $INPUT_FILE → $OUTPUT_FILE"

  case "$ENCODER" in
    av1_qsv)
      ffmpeg -y -init_hw_device qsv=hw:"$QSV_DEVICE" -filter_hw_device hw \
        -i "$INPUT_FILE" \
        -c:v av1_qsv -global_quality 30 -look_ahead 1 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
    libsvtav1)
      ffmpeg -y -i "$INPUT_FILE" \
        -c:v libsvtav1 -crf 30 -preset 6 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
    libaom-av1)
      ffmpeg -y -i "$INPUT_FILE" \
        -c:v libaom-av1 -crf 30 -b:v 0 -cpu-used 4 \
        -c:a aac -b:a 96k "$OUTPUT_FILE"
      ;;
  esac

  echo "✅ Done: $OUTPUT_FILE"
  echo ""
done

#!/bin/bash

# ================================
# Smart Video Encoder
# Auto-selects best AV1 or HEVC encoder
# Supports parallel jobs, delete original, rename output
# ================================

MAX_JOBS=2
JOB_COUNT=0
DELETE_ORIGINAL=false
CODEC="av1"
FILES=()
FALLBACK=false

# === Handle CTRL+C ===
trap "echo '🛑 Caught interrupt. Terminating all encoding jobs...'; kill 0; exit 1" SIGINT

# === Help message ===
show_help() {
  cat <<EOF
Usage: $0 [OPTIONS] <video_files...>

Options:
  --codec av1|hevc     Select codec to use (default: av1)
  -j <jobs>            Set number of parallel encoding jobs (default: 2)
  --delete             Delete original file after successful encoding
  --fallback           If encoding fails, fallback to alternative encoders:
                       - AV1 fallback: hevc_nvenc → hevc_qsv → libx265
  --help               Show this help message and exit

Examples:
  $0 --codec av1 video.mp4
  $0 -j 4 --delete *.mkv
  $0 --codec hevc --fallback video1.mp4 video2.mp4
EOF
}

# === Parse CLI arguments ===
while [[ $# -gt 0 ]]; do
  case "$1" in
    -j)
      MAX_JOBS="$2"
      shift 2
      ;;
    --delete)
      DELETE_ORIGINAL=true
      shift
      ;;
    --codec)
      CODEC="$2"
      shift 2
      ;;
    --fallback)
      FALLBACK=true
      shift
      ;;
    --help)
      show_help
      exit 0
      ;;
    -* )
      echo "Unknown option: $1"
      exit 1
      ;;
    *)
      FILES+=("$1")
      shift
      ;;
  esac
done

if [ ${#FILES[@]} -eq 0 ]; then
  show_help
  exit 1
fi

# === Find Intel QSV device ===
find_qsv_device() {
  for dev in /dev/dri/render*; do
    if udevadm info --query=all --name="$dev" | grep -qi 'vendor=0x8086'; then
      echo "$dev"
      return 0
    fi
  done
  return 1
}

# === Auto-select AV1 encoder ===
detect_av1_encoder() {
  QSV_DEVICE=$(find_qsv_device)

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'av1_nvenc'; then
    echo "av1_nvenc"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'av1_qsv' && [ -n "$QSV_DEVICE" ]; then
    echo "av1_qsv"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'libsvtav1'; then
    echo "libsvtav1"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'libaom-av1'; then
    echo "libaom-av1"
    return
  fi

  echo "❌ No AV1 encoder found"
  exit 1
}

# === Auto-select HEVC encoder ===
detect_hevc_encoder() {
  QSV_DEVICE=$(find_qsv_device)

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'hevc_nvenc'; then
    echo "hevc_nvenc"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'hevc_qsv' && [ -n "$QSV_DEVICE" ]; then
    echo "hevc_qsv"
    return
  fi

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -qw 'libx265'; then
    echo "libx265"
    return
  fi

  echo "❌ No HEVC encoder found"
  exit 1
}

# === Detect and set encoder ===
case "$CODEC" in
  av1)
    ENCODER=$(detect_av1_encoder)
    ;;
  hevc)
    ENCODER=$(detect_hevc_encoder)
    ;;
  *)
    echo "❌ Unknown codec: $CODEC (use av1 or hevc)"
    exit 1
    ;;
esac

echo "✅ Selected codec: $CODEC using encoder: $ENCODER"

# === Encode Function ===
encode_file() {
  INPUT_FILE="$1"
  QSV_DEVICE="$2"

  BASENAME=$(basename "$INPUT_FILE")
  FILENAME="${BASENAME%.*}"
  EXT="${BASENAME##*.}"
  TMP_OUTPUT="${FILENAME}_${ENCODER}.${EXT}"
  FINAL_OUTPUT="${FILENAME}.${EXT}"

  if [[ -f "$FINAL_OUTPUT" && "$DELETE_ORIGINAL" == true ]]; then
    echo "⏭️  Skipping already encoded: $FINAL_OUTPUT"
    return
  fi

  echo "▶️ Encoding: $INPUT_FILE → $TMP_OUTPUT"

  run_ffmpeg_encode() {
    local encoder="$1"
    TMP_OUTPUT="${FILENAME}_${encoder}.${EXT}"
    case "$encoder" in
      av1_nvenc)
        ffmpeg -y -hwaccel cuda -i "$INPUT_FILE" \
          -c:v av1_nvenc -cq 28 -preset p7 \
          -c:a aac -b:a 96k "$TMP_OUTPUT"
        ;;
      av1_qsv)
        ffmpeg -y -init_hw_device qsv=hw:"$QSV_DEVICE" -filter_hw_device hw \
          -i "$INPUT_FILE" \
          -c:v av1_qsv -global_quality 28 -look_ahead 1 \
          -c:a aac -b:a 96k "$TMP_OUTPUT"
        ;;
      libsvtav1)
        ffmpeg -y -i "$INPUT_FILE" \
          -c:v libsvtav1 -preset 8 -crf 35 \
          -c:a aac -b:a 96k "$TMP_OUTPUT"
        ;;
      libaom-av1)
        ffmpeg -y -i "$INPUT_FILE" \
          -c:v libaom-av1 -crf 35 -b:v 0 -cpu-used 6 \
          -c:a aac -b:a 96k "$TMP_OUTPUT"
        ;;
      hevc_nvenc)
        ffmpeg -y -hwaccel cuda -i "$INPUT_FILE" \
          -c:v hevc_nvenc -cq 28 -preset p7 \
          -c:a aac -b:a 96k "$TMP_OUTPUT"
        ;;
      hevc_qsv)
        ffmpeg -y -init_hw_device qsv=hw:"$QSV_DEVICE" -filter_hw_device hw \
          -i "$INPUT_FILE" \
          -c:v hevc_qsv -global_quality 28 -look_ahead 1 \
          -c:a aac -b:a 96k "$TMP_OUTPUT"
        ;;
      libx265)
        ffmpeg -y -i "$INPUT_FILE" \
          -c:v libx265 -preset slow -crf 28 \
          -c:a aac -b:a 96k "$TMP_OUTPUT"
        ;;
    esac
    return $?
  }

  run_ffmpeg_encode "$ENCODER"
  if [[ $? -ne 0 && "$FALLBACK" == true ]]; then
    echo "⚠️ Encoder $ENCODER failed for $INPUT_FILE"
    FALLBACK_ENCODERS=(hevc_nvenc hevc_qsv libx265)
    for fallback_encoder in "${FALLBACK_ENCODERS[@]}"; do
      echo "🔁 Trying fallback: $fallback_encoder"
      run_ffmpeg_encode "$fallback_encoder"
      if [[ $? -eq 0 ]]; then
        ENCODER="$fallback_encoder"
        break
      else
        [[ -f "$TMP_OUTPUT" ]] && rm -f "$TMP_OUTPUT"
      fi
    done
  fi

  if [[ -f "$TMP_OUTPUT" ]]; then
    if [ "$DELETE_ORIGINAL" = true ]; then
      mv "$TMP_OUTPUT" "$FINAL_OUTPUT"
      echo "✅ Encoded & Renamed: $FINAL_OUTPUT"
      echo "🗑️ Deleting original: $INPUT_FILE"
      rm -f "$INPUT_FILE"
    else
      echo "✅ Encoded: $TMP_OUTPUT"
    fi
  else
    echo "❌ Failed to encode: $INPUT_FILE"
    [[ -f "$TMP_OUTPUT" ]] && rm -f "$TMP_OUTPUT"
  fi

  echo ""
}

# === Start Encoding ===
QSV_DEVICE=$(find_qsv_device)

for INPUT_FILE in "${FILES[@]}"; do
  if [ ! -f "$INPUT_FILE" ]; then
    echo "⚠️ Skipping: File not found: $INPUT_FILE"
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
echo "🎉 All encoding complete!"

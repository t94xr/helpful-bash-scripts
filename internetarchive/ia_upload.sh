#!/bin/bash

# THIS SCRIPT REQUIRES THAT YOU HAVE THE INTERNET ARCHIVE PYTHON SCRIPT INSTALLED
# https://github.com/jjjake/internetarchive/releases/
# THIS SCRIPT WILL NOT OPERATE INDEPENDENTLY WITHOUT THIS INSTALLED.
# THUS THE "HELPER" PART OF THE NAME.

#
# INTERNET ARCHIVE UPLOAD HELPER SCRIPT
# 
# by Cameron W | github.com/t94xr
# github.com/t94xr/helpful-bash-scripts
#

#
# Usage:
# ./upload_to_ia.sh -p -v -d retro-tech-mags 2025*
# Example:
# ./upload_to_ia.sh -p -v -d retro-tech-mags 2025*
#

# Upload to Internet Archive with duplicate checking
# Supports: Wildcards, parallel uploads, verbose/debug modes

# ---- CONFIG ----
MAX_JOBS=4

# ---- FLAGS ----
PARALLEL=false
VERBOSE=false
DEBUG=false

# ---- JQ Check ----
if ! command -v jq &> /dev/null; then
  echo "⚠️  'jq' is required but not installed."
  echo "Install it with: sudo apt install jq (or brew install jq on macOS)"
  exit 1
fi

# ---- Argument Parsing ----
while [[ "$1" == -* ]]; do
  case "$1" in
    -p) PARALLEL=true ;;
    -v) VERBOSE=true ;;
    -d) DEBUG=true ;;
    *) echo "❌ Unknown option: $1"; exit 1 ;;
  esac
  shift
done

COLLECTION="$1"
shift
FILES=("$@")

if [[ -z "$COLLECTION" || ${#FILES[@]} -eq 0 ]]; then
  echo "Usage: $0 [-p] [-v] [-d] <collection> <files...>"
  echo "Example: $0 -p -v coppercab-archive 2025-04-10*"
  exit 1
fi

# ---- Collection Permission Check ----
echo "🧪 Verifying upload access to '$COLLECTION'..."
if ! ia metadata "$COLLECTION" &> /dev/null; then
  echo "❌ ERROR: Cannot access collection '$COLLECTION'."
  echo "Check your login with 'ia whoami' or re-auth with 'ia configure'."
  exit 1
fi
echo "✅ Access confirmed. Proceeding..."

# ---- Main Upload Function ----
process_file() {
  local FILE="$1"
  local COLLECTION="$2"

  [[ "$VERBOSE" == true ]] && echo "📁 Processing '$FILE'..."

  if [[ ! -f "$FILE" ]]; then
    echo "❌ Skipping '$FILE' — not a regular file."
    return
  fi

  local BASENAME=$(basename "$FILE")
  local MD5=$(md5sum "$FILE" | awk '{ print $1 }')
  local SHA1=$(sha1sum "$FILE" | awk '{ print $1 }')

  [[ "$DEBUG" == true ]] && echo "🔎 MD5: $MD5 | SHA1: $SHA1"

  local MATCHED=false

  ia search "collection:$COLLECTION" | awk 'NR > 1 { print $1 }' | while read -r ID; do
    [[ "$DEBUG" == true ]] && echo "🔍 Checking in item: $ID"

    local METADATA=$(ia metadata "$ID" --format=json 2>/dev/null)
    [[ "$DEBUG" == true ]] && echo "$METADATA" | jq '.files?'

    MATCH=$(echo "$METADATA" | jq -r --arg fname "$BASENAME" --arg md5 "$MD5" --arg sha1 "$SHA1" '
      .files[]? | select(.name == $fname or .md5 == $md5 or .sha1 == $sha1) | .name')

    if [[ -n "$MATCH" ]]; then
      echo "✅ Match found in '$ID': $MATCH"
      MATCHED=true
      break
    fi
  done

  if [[ "$MATCHED" == true ]]; then
    echo "🚫 '$BASENAME' already exists in collection. Skipping."
    return
  fi

  # Sanitize identifier
  local RAW_ID=$(basename "$FILE" | sed 's/\.[^.]*$//')
  local IDENTIFIER=$(echo "$RAW_ID" | tr '[:space:]/' '-' | tr -cd '[:alnum:]._-' | sed 's/^-*//;s/-*$//')

  echo "⬆️  Uploading '$BASENAME' as '$IDENTIFIER'..."

  ia upload "$IDENTIFIER" "$FILE" \
    --metadata="collection:$COLLECTION" \
    --metadata="mediatype:movies" \
    --metadata="title:$RAW_ID" \
    --metadata="creator:Uploader Script"

  [[ "$VERBOSE" == true ]] && echo "✅ Upload complete: $IDENTIFIER"
}

# ---- Parallel Control ----
limit_parallel() {
  while (( $(jobs -r | wc -l) >= MAX_JOBS )); do
    sleep 0.5
  done
}

# ---- Process All Files ----
for FILE in "${FILES[@]}"; do
  if $PARALLEL; then
    limit_parallel
    process_file "$FILE" "$COLLECTION" &
  else
    process_file "$FILE" "$COLLECTION"
  fi
done

if $PARALLEL; then
  wait
fi

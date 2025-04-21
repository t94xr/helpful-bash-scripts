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
# ./ia_upload.sh <collection_name> <file(s)>
# Example:
# ./ia_upload.sh retro-tech-magazines 2025-04-10*
#

# Check for jq (required!!)
if ! command -v jq &> /dev/null; then
  echo "⚠️  This script requires 'jq' but it's not installed."
  echo ""
  echo "👉 To install jq:"
  echo "   - Debian/Ubuntu: sudo apt install jq"
  echo "   - macOS (with Homebrew): brew install jq"
  echo "   - Fedora: sudo dnf install jq"
  echo "   - Arch: sudo pacman -S jq"
  echo ""
  echo "❌ Exiting. Please install jq and try again."
  exit 1
fi

COLLECTION="$1"
shift
FILES=("$@")

if [[ -z "$COLLECTION" || ${#FILES[@]} -eq 0 ]]; then
  echo "Usage: $0 <collection_name> <file(s)>"
  echo "Example: $0 retro-tech-magazines 2025-04-10*"
  exit 1
fi

for FILE in "${FILES[@]}"; do
  if [[ ! -f "$FILE" ]]; then
    echo "❌ Skipping '$FILE' — not a regular file."
    continue
  fi

  BASENAME=$(basename "$FILE")
  MD5=$(md5sum "$FILE" | awk '{ print $1 }')

  echo "🔍 Searching for file '$BASENAME' (MD5: $MD5) in collection '$COLLECTION'..."

  MATCH_FOUND=false

  ia search "collection:$COLLECTION" | awk 'NR > 1 { print $1 }' | while read -r ID; do
    echo "   ↪️ Checking item: $ID..."

    JSON=$(ia metadata "$ID" --format=json 2>/dev/null)

    # Check if filename matches exactly in metadata
    MATCH_NAME=$(echo "$JSON" | jq -r --arg name "$BASENAME" '.files[]?.name' | grep -Fx "$BASENAME")
    if [[ -n "$MATCH_NAME" ]]; then
      echo "✅ Match found by filename in item '$ID'"
      MATCH_FOUND=true
      break
    fi

    # Check if MD5 matches exactly in metadata
    MATCH_MD5=$(echo "$JSON" | jq -r --arg md5 "$MD5" '.files[]?.md5' | grep -Fx "$MD5")
    if [[ -n "$MATCH_MD5" ]]; then
      echo "✅ Match found by MD5 in item '$ID'"
      MATCH_FOUND=true
      break
    fi
  done

  if $MATCH_FOUND; then
    echo "🚫 '$BASENAME' already exists in collection. Skipping upload."
    continue
  fi

  RAW_ID=$(basename "$FILE" | sed 's/\.[^.]*$//')
  IDENTIFIER=$(echo "$RAW_ID" | tr '[:space:]/' '-' | tr -cd '[:alnum:]._-' | sed 's/^-*//;s/-*$//')

  echo "⬆️  Uploading '$BASENAME' as identifier '$IDENTIFIER'..."

  ia upload "$IDENTIFIER" "$FILE" \
    --metadata="collection:$COLLECTION" \
    --metadata="mediatype:movies" \
    --metadata="title:$RAW_ID" \
    --metadata="creator:Uploader Script"
done

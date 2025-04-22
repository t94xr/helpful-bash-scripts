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

# ---- CONFIG ----
MAX_JOBS=4

# ---- FLAGS ----
PARALLEL=false
VERBOSE=false
DEBUG=false

# ---- HELP ----
show_help() {
  echo ""
  echo "📦 Internet Archive Upload Script"
  echo "----------------------------------"
  echo "Usage: $0 [options] <collection> <file(s)>"
  echo ""
  echo "Options:"
  echo "  -p              Enable parallel uploads (max $MAX_JOBS simultaneous jobs)"
  echo "  -v              Verbose mode – show more upload info"
  echo "  -d              Debug mode – print hashes, metadata, etc."
  echo "  --help          Show this help message"
  echo ""
  echo "Arguments:"
  echo "  <collection>    Internet Archive collection identifier (e.g. coppercab-archive)"
  echo "  <file(s)>       One or more files or wildcards to upload"
  echo ""
  echo "Examples:"
  echo "  $0 coppercab-archive video1.mp4"
  echo "  $0 -p -v coppercab-archive 2025-04-10*"
  echo ""
  echo "Requires:"
  echo "  - 'ia' CLI authenticated with 'ia configure'"
  echo "  - 'jq' installed (for JSON parsing)"
  echo ""
}

# ---- PARSE FLAGS ----
while [[ "$1" == -* ]]; do
  case "$1" in
    -p) PARALLEL=true ;;
    -v) VERBOSE=true ;;
    -d) DEBUG=true ;;
    --help)
      show_help
      exit 0
      ;;
    *)
      echo "❌ Unknown option: $1"
      show_help
      exit 1
      ;;
  esac
  shift
done

# ---- CHECK ARGS ----
COLLECTION="$1"
shift
FILES=("$@")

if [[ -z "$COLLECTION" || ${#FILES[@]} -eq 0 ]]; then
  echo "❗ Missing collection or files."
  show_help
  exit 1
fi

# ---- CHECK JQ ----
if ! command -v jq &> /dev/null; then
  echo "⚠️  'jq' is required but not installed."
  echo "Install it with: sudo apt install jq (Linux) or brew install jq (macOS)"
  exit 1
fi

# ---- CHECK COLLECTION ACCESS ----
echo "🧪 Verifying upload access to '$COLLECTION'..."
if ! ia metadata "$COLLECTION" &> /dev/null; then
  echo "❌ ERROR: Cannot access collection '$COLLECTION'."
  echo "Check your login with 'ia whoami' or re-authenticate with 'ia configure'."
  exit 1
fi
echo "✅ Access confirmed. Proceeding..."

# ---- FILE CHECK + UPLOAD ----
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

  [[ "$DEBUG" == true ]] && echo "🔎 Hashes for '$BASENAME' — MD5: $MD5 | SHA1: $SHA1"

  local MATCHED=false

  ia search "collection:$COLLECTION" | awk 'NR > 1 { print $1 }' | while read -r ID; do
    [[ "$DEBUG" == true ]] && echo "🔍 Searching in item: $ID"

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

  # Create safe identifier
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

# ---- PARALLEL LOGIC ----
limit_parallel() {
  while (( $(jobs -r | wc -l) >= MAX_JOBS )); do
    sleep 0.5
  done
}

# ---- PROCESS ALL FILES ----
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


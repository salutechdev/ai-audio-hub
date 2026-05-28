#!/usr/bin/env bash

set -e

# ============================================================
# Configuration
# ============================================================

START_DATE="2026-05-28"
END_DATE="2026-08-31"
COMMIT_COUNT=144

# Repository to use only as a source of commit-message ideas
SOURCE_REPO="https://github.com/BinWang28/audio-ai-hub.git"
SOURCE_DIR=".source-history-temp"

# ============================================================
# Basic validation
# ============================================================

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERROR: Run this script inside a Git repository."
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: Git is not installed."
    exit 1
fi

if ! command -v date >/dev/null 2>&1; then
    echo "ERROR: date command is not available."
    exit 1
fi

echo "=============================================="
echo " Synthetic Git History Generator"
echo "=============================================="
echo "Start date : $START_DATE"
echo "End date   : $END_DATE"
echo "Commits    : $COMMIT_COUNT"
echo "=============================================="
echo

# ============================================================
# Clone source repository temporarily
# ============================================================

rm -rf "$SOURCE_DIR"

echo "Fetching source repository..."
git clone --quiet "$SOURCE_REPO" "$SOURCE_DIR"

# ============================================================
# Extract source commit messages
# ============================================================

mapfile -t SOURCE_MESSAGES < <(
    git -C "$SOURCE_DIR" log --all --format="%s" --reverse
)

SOURCE_COUNT=${#SOURCE_MESSAGES[@]}

echo "Source commits found: $SOURCE_COUNT"

if [ "$SOURCE_COUNT" -eq 0 ]; then
    echo "ERROR: No commits found in source repository."
    rm -rf "$SOURCE_DIR"
    exit 1
fi

# Use exactly COMMIT_COUNT messages.
# If the source has fewer messages, cycle through them.
COMMIT_MESSAGES=()

for ((i=0; i<COMMIT_COUNT; i++)); do
    index=$((i % SOURCE_COUNT))
    COMMIT_MESSAGES+=("[Synthetic history] ${SOURCE_MESSAGES[$index]}")
done

# ============================================================
# Calculate date range
# ============================================================

START_SECONDS=$(date -d "$START_DATE 00:00:00" +%s)
END_SECONDS=$(date -d "$END_DATE 23:59:59" +%s)

if [ "$START_SECONDS" -ge "$END_SECONDS" ]; then
    echo "ERROR: START_DATE must be before END_DATE."
    rm -rf "$SOURCE_DIR"
    exit 1
fi

TOTAL_SECONDS=$((END_SECONDS - START_SECONDS))

# ============================================================
# Generate unique random timestamps
# ============================================================

declare -A USED_TIMES
TIMESTAMPS=()

echo "Generating $COMMIT_COUNT random timestamps..."

while [ "${#TIMESTAMPS[@]}" -lt "$COMMIT_COUNT" ]; do

    # Random position across the complete date range
    RANDOM_OFFSET=$((RANDOM * RANDOM % TOTAL_SECONDS))

    TIMESTAMP_SECONDS=$((START_SECONDS + RANDOM_OFFSET))

    # Convert to a readable timestamp.
    # Using UTC avoids DST-related date calculation problems.
    TIMESTAMP=$(TZ=UTC date -d "@$TIMESTAMP_SECONDS" "+%Y-%m-%d %H:%M:%S +0000")

    if [[ -z "${USED_TIMES[$TIMESTAMP]+exists}" ]]; then
        USED_TIMES["$TIMESTAMP"]=1
        TIMESTAMPS+=("$TIMESTAMP")
    fi
done

# ============================================================
# Sort timestamps chronologically
# ============================================================

mapfile -t TIMESTAMPS < <(
    printf '%s\n' "${TIMESTAMPS[@]}" | sort
)

# ============================================================
# Create commits
# ============================================================

echo
echo "Creating commits..."
echo

for ((i=0; i<COMMIT_COUNT; i++)); do

    TIMESTAMP="${TIMESTAMPS[$i]}"
    MESSAGE="${COMMIT_MESSAGES[$i]}"

    echo "[$((i + 1))/$COMMIT_COUNT] $TIMESTAMP"
    echo "    $MESSAGE"

    # Create a unique file change for every commit
    echo "Synthetic commit $((i + 1))" > synthetic-commit.txt

    git add synthetic-commit.txt

    GIT_AUTHOR_DATE="$TIMESTAMP" \
    GIT_COMMITTER_DATE="$TIMESTAMP" \
    git commit --quiet -m "$MESSAGE"
done

# ============================================================
# Cleanup
# ============================================================

rm -rf "$SOURCE_DIR"
rm -f synthetic-commit.txt

# ============================================================
# Verification
# ============================================================

echo
echo "=============================================="
echo " Verification"
echo "=============================================="

ACTUAL_COUNT=$(git rev-list --count HEAD)

echo "Total commits in current history: $ACTUAL_COUNT"
echo

echo "First generated commits:"
git log --format="%ad | %s" --date=iso-strict --reverse -n 5

echo
echo "Last generated commits:"
git log --format="%ad | %s" --date=iso-strict -n 5

echo
echo "=============================================="
echo " Done"
echo "=============================================="
echo
echo "Generated exactly $COMMIT_COUNT synthetic commits."
echo "Date range: $START_DATE through $END_DATE"
echo
echo "To push:"
echo "  git push -u origin main"
echo
#!/usr/bin/env bash
#
# commit_data.sh -- commit and push generated data, surviving a concurrent
# push to the same branch.
#
# WHY THIS EXISTS
# ===============
# The daily job spends ~30 minutes talking to the MLB API and then commits
# what it computed. On 2026-08-23 that push was rejected -- code had been
# pushed to main while the job ran -- and the whole half hour of work was
# thrown away. The job is expensive and its output is not reproducible on
# demand (the API's answer moves), so losing it to a race is not acceptable.
#
# A plain `git pull --rebase` is the obvious fix and it is not safe here:
# docs/data/service_time.json is a single 20MB line-oriented blob that both
# jobs rewrite wholesale, so a rebase across two runs conflicts and stops.
#
# What is true instead, and what this script encodes: the generated paths
# are DERIVED OUTPUTS, not edits. The run that finished last read the API
# most recently, so its copy wins outright -- there is nothing to merge.
# Everything else on the branch is somebody's real work and must be kept.
#
# So on a rejection: stash our generated files aside, hard-reset onto the
# remote tip, put them back, recommit, push again. Ours wins for the paths
# we generated; theirs wins for every other file in the tree.
#
# Usage:
#   scripts/commit_data.sh "commit message" path [path...]

set -euo pipefail

MESSAGE="$1"
shift
PATHS=("$@")

ATTEMPTS=5

git config user.name "mlb-service-time-bot"
git config user.email "actions@users.noreply.github.com"

branch="$(git rev-parse --abbrev-ref HEAD)"

stage() {
  local p
  for p in "${PATHS[@]}"; do
    if [ -e "$p" ]; then
      git add -A "$p"
    fi
  done
}

stage
if git diff --cached --quiet; then
  echo "No data changes -- nothing to commit."
  exit 0
fi
git commit -m "$MESSAGE"

for attempt in $(seq 1 "$ATTEMPTS"); do
  if git push origin "HEAD:$branch"; then
    echo "Pushed on attempt $attempt."
    exit 0
  fi

  echo "Push rejected (attempt $attempt) -- rebuilding on top of the remote tip."

  keep="$(mktemp -d)"
  for p in "${PATHS[@]}"; do
    if [ -e "$p" ]; then
      mkdir -p "$keep/$(dirname "$p")"
      cp -a "$p" "$keep/$(dirname "$p")/"
    fi
  done

  git fetch origin "$branch"
  git reset --hard "origin/$branch"

  for p in "${PATHS[@]}"; do
    if [ -e "$keep/$p" ]; then
      rm -rf "$p"
      mkdir -p "$(dirname "$p")"
      cp -a "$keep/$p" "$p"
    fi
  done
  rm -rf "$keep"

  stage
  if git diff --cached --quiet; then
    echo "The remote tip already carries this data -- nothing left to commit."
    exit 0
  fi
  git commit -m "$MESSAGE"
done

echo "Could not push after $ATTEMPTS attempts." >&2
exit 1

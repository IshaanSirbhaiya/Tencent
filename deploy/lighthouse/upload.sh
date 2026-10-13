#!/usr/bin/env bash
# Ship the committed source to the VM and deploy, with no GitHub credentials on the VM.
# Run from your laptop inside the repo (Git Bash on Windows, macOS or Linux):
#
#   bash deploy/lighthouse/upload.sh ubuntu@43.134.10.20            # deploys the current commit (HEAD)
#   bash deploy/lighthouse/upload.sh ubuntu@43.134.10.20 origin/Tencent
#
# Only COMMITTED files are sent (git archive): uncommitted work, .env files, .venv and node_modules
# never leave your machine. deploy.sh then runs on the VM exactly as in the README.
set -euo pipefail

TARGET="${1:?usage: upload.sh ubuntu@IP [git-ref]}"
REF="${2:-HEAD}"
ROOT="$(git rev-parse --show-toplevel)"

git -C "$ROOT" cat-file -e "$REF:deploy/lighthouse/deploy.sh" 2>/dev/null \
  || { echo "deploy/lighthouse is not committed at $REF; commit it first" >&2; exit 1; }
echo "uploading $(git -C "$ROOT" rev-parse --short "$REF") to $TARGET:/opt/walnut/repo"

git -C "$ROOT" archive --format=tar "$REF" | gzip -c | ssh "$TARGET" \
  'sudo sh -c "set -e; mkdir -p /opt/walnut; rm -rf /opt/walnut/repo.new; mkdir /opt/walnut/repo.new;
   tar -xzf - -C /opt/walnut/repo.new; rm -rf /opt/walnut/repo; mv /opt/walnut/repo.new /opt/walnut/repo"'

# NO_BUILD=1 (images already sent with `docker save | docker load`, see README) passes through.
ssh "$TARGET" "sudo NO_BUILD=${NO_BUILD:-0} bash /opt/walnut/repo/deploy/lighthouse/deploy.sh"

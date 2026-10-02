#!/bin/sh
# Publish a clean snapshot of HEAD to the PUBLIC repo happybryan/quant-betting (main). Full history stays local:
# pre-registration records cite local commit hashes, so it is never rewritten. Personal handle is scrubbed.
set -e
cd "$(dirname "$0")"
PUB=../quant-betting-public
GH=~/.local/bin/gh
[ -d "$PUB/.git" ] || { mkdir -p "$PUB"; git -C "$PUB" init -q -b main; git -C "$PUB" remote add origin https://github.com/Happybryan/quant-betting.git; }
find "$PUB" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git archive HEAD | tar -x -C "$PUB"
grep -rlI "<account>" "$PUB" --exclude-dir=.git | while read -r f; do sed -i '' 's/<account>/<account>/g' "$f"; done
if grep -rqI "<account>" "$PUB" --exclude-dir=.git; then echo "handle still present, aborting"; exit 1; fi
cd "$PUB"
git add -A
git -c user.name=happybryan -c user.email=happybryan@users.noreply.github.com commit -qm "snapshot $(git -C ../quant-betting rev-parse --short HEAD)" || { echo "nothing to publish"; exit 0; }
$GH auth setup-git >/dev/null 2>&1 || true
git push -q origin main
echo "published"

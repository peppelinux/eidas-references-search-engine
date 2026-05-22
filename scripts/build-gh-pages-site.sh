#!/usr/bin/env bash
# Build a static tree for GitHub Pages deployment.
#
#   ./scripts/build-gh-pages-site.sh main       # mirror main branch as-is (site root)
#   ./scripts/build-gh-pages-site.sh night      # run pipeline → ./_site/nightbuilds/
#
# CI publishes ./_site with peaceiris/actions-gh-pages (keep_files: true) so main and
# night jobs can update root and /nightbuilds/ independently.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-main}"
SITE="${SITE_DIR:-$ROOT/_site}"
WORKERS="${WORKERS:-10}"

log() { printf '==> %s\n' "$*"; }

rsync_site() {
  local src="$1" dest="$2"
  mkdir -p "$dest"
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '_site/' \
    --exclude '.github/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.venv/' \
    --exclude 'venv/' \
    "$src/" "$dest/"
}

write_landing_index() {
  local out="$1"
  cat >"$out/index.html" <<'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>eIDAS legal &amp; technical references</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
    h1 { font-size: 1.35rem; }
    ul { padding-left: 1.25rem; }
    a { color: #0b57d0; }
    .meta { color: #555; font-size: 0.9rem; }
  </style>
</head>
<body>
  <h1>eIDAS legal &amp; technical references</h1>
  <p class="meta">Static mirror of the repository (main branch).</p>
  <ul>
    <li><a href="report/index.html">Interactive references report</a> (committed on main)</li>
    <li><a href="nightbuilds/report/index.html">Nightly build report</a> (refreshed by scheduled CI)</li>
    <li><a href="README.md">README</a></li>
  </ul>
</body>
</html>
EOF
}

write_build_info() {
  local dir="$1"
  local sha="${GITHUB_SHA:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo local)}"
  local ref="${GITHUB_REF_NAME:-$(git -C "$ROOT" branch --show-current 2>/dev/null || echo local)}"
  cat >"$dir/BUILD_INFO.json" <<EOF
{
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "git_sha": "$sha",
  "git_ref": "$ref",
  "mode": "night",
  "workflow": "${GITHUB_WORKFLOW:-local}"
}
EOF
}

build_main() {
  log "Building main site (as-is) → $SITE"
  rm -rf "$SITE"
  mkdir -p "$SITE"
  rsync_site "$ROOT" "$SITE"
  write_landing_index "$SITE"
}

build_night() {
  log "Running night pipeline in $ROOT"
  cd "$ROOT"

  export WORKERS
  make sync
  make markdown
  make specs
  make report

  local staging
  staging="$(mktemp -d)"
  # Expand path when trap is set: local `staging` is gone when EXIT runs (set -u).
  trap "rm -rf $(printf '%q' "$staging")" EXIT

  log "Assembling nightbuilds in staging"
  for path in regulation implementing-acts implementing-decisions referenced-standards report; do
    if [[ -e "$ROOT/$path" ]]; then
      rsync_site "$ROOT/$path" "$staging/$path"
    fi
  done

  cp "$ROOT/manifest.yaml" "$staging/"
  [[ -f "$ROOT/manifest.lock.json" ]] && cp "$ROOT/manifest.lock.json" "$staging/"
  [[ -f "$ROOT/README.md" ]] && cp "$ROOT/README.md" "$staging/"
  write_build_info "$staging"

  log "Publishing nightbuilds only → $SITE/nightbuilds (site root left to main deploy)"
  rm -rf "$SITE"
  mkdir -p "$SITE/nightbuilds"
  rsync_site "$staging" "$SITE/nightbuilds"
}

case "$MODE" in
  main) build_main ;;
  night) build_night ;;
  *)
    echo "Usage: $0 {main|night}" >&2
    exit 1
    ;;
esac

log "Done ($MODE) → $SITE"

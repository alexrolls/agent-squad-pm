#!/usr/bin/env bash
# update-installed-skill.sh — refresh an installed Startup Factory skill from GitHub.
set -euo pipefail

REMOTE_URL="${STARTUP_FACTORY_REMOTE_URL:-https://github.com/alexrolls/startup-factory.git}"
REMOTE_REF="${STARTUP_FACTORY_REF:-main}"
SKILL_NAME="${STARTUP_FACTORY_SKILL_NAME:-startup-factory}"

install_dir=""
overwrite_config=false
dry_run=false

die() {
  echo "update-installed-skill: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: update-installed-skill.sh [options]

Fetch the latest Startup Factory bundle and sync it into the current repository's
.claude/skills/startup-factory directory.

Options:
  --install-dir PATH     Update this skill directory instead of auto-detecting.
  --remote-url URL       Git remote to fetch from.
                         Default: $REMOTE_URL
  --ref REF              Branch or tag to fetch.
                         Default: $REMOTE_REF
  --overwrite-config     Replace local config files with the upstream defaults.
  --dry-run              Show rsync changes without writing them.
  -h, --help             Show this help.

Environment overrides:
  STARTUP_FACTORY_REMOTE_URL
  STARTUP_FACTORY_REF
  STARTUP_FACTORY_SKILL_NAME
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      [ "$#" -ge 2 ] || die "--install-dir requires a path"
      install_dir="$2"
      shift 2
      ;;
    --remote-url)
      [ "$#" -ge 2 ] || die "--remote-url requires a URL"
      REMOTE_URL="$2"
      shift 2
      ;;
    --ref)
      [ "$#" -ge 2 ] || die "--ref requires a branch or tag"
      REMOTE_REF="$2"
      shift 2
      ;;
    --overwrite-config)
      overwrite_config=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git is required"
command -v rsync >/dev/null 2>&1 || die "rsync is required"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
script_skill_dir="$(cd "$script_dir/.." && pwd -P)"

if [ -z "$install_dir" ]; then
  case "$script_skill_dir" in
    */.claude/skills/"$SKILL_NAME")
      install_dir="$script_skill_dir"
      ;;
    *)
      repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
      [ -n "$repo_root" ] || die "not inside a git repository; pass --install-dir"
      install_dir="$repo_root/.claude/skills/$SKILL_NAME"
      ;;
  esac
fi

case "$install_dir" in
  /*) ;;
  *) install_dir="$(pwd -P)/$install_dir" ;;
esac

tmp="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

checkout="$tmp/source"
preserve="$tmp/preserve"

echo "Fetching $REMOTE_URL ($REMOTE_REF)"
git clone --depth 1 --branch "$REMOTE_REF" "$REMOTE_URL" "$checkout" >/dev/null

if ! $overwrite_config && [ -d "$install_dir" ]; then
  for file in \
    config/project-management.config.md \
    config/team.config.md \
    config/statuses.config.json
  do
    if [ -f "$install_dir/$file" ]; then
      mkdir -p "$preserve/$(dirname "$file")"
      cp "$install_dir/$file" "$preserve/$file"
    fi
  done
fi

mkdir -p "$install_dir"

rsync_args=(-a --delete --exclude .git)
if $dry_run; then
  rsync_args+=(--dry-run --itemize-changes)
fi

rsync "${rsync_args[@]}" "$checkout"/ "$install_dir"/

if ! $overwrite_config && ! $dry_run && [ -d "$preserve" ]; then
  rsync -a "$preserve"/ "$install_dir"/
fi

echo "Updated Startup Factory skill at: $install_dir"
if ! $overwrite_config; then
  echo "Preserved existing local config files when present."
fi

target_repo="$(git -C "$install_dir" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$target_repo" ]; then
  case "$install_dir" in
    "$target_repo") rel_path="." ;;
    "$target_repo"/*) rel_path="${install_dir#"$target_repo"/}" ;;
    *) rel_path="$install_dir" ;;
  esac

  echo
  echo "Git status for $rel_path:"
  git -C "$target_repo" status --short -- "$rel_path" || true

  if ! $dry_run; then
    echo
    echo "Diff stat for $rel_path:"
    git -C "$target_repo" diff --stat -- "$rel_path" || true
  fi
fi

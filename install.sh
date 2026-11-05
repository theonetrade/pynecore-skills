#!/usr/bin/env bash
# Install the skills of this repository into the global skill folders of agent hosts.
#
#   ./install.sh                 # every host folder that already exists (~/.claude, ~/.cursor, ~/.agents)
#   ./install.sh --claude        # ~/.claude/skills
#   ./install.sh --cursor        # ~/.cursor/skills
#   ./install.sh --agents        # ~/.agents/skills
#   ./install.sh --target DIR    # any skills directory
#   ./install.sh --copy          # copy instead of symlink
#   ./install.sh --uninstall     # remove the links/copies this script creates
#
# Symlinks keep the installed skills in sync with `git pull`.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$REPO_DIR/skills"

MODE="link"
ACTION="install"
TARGETS=()

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --claude)   TARGETS+=("$HOME/.claude/skills") ;;
    --cursor)   TARGETS+=("$HOME/.cursor/skills") ;;
    --agents)   TARGETS+=("$HOME/.agents/skills") ;;
    --target)   shift; [[ $# -gt 0 ]] || { echo "--target needs a directory" >&2; exit 1; }; TARGETS+=("$1") ;;
    --copy)     MODE="copy" ;;
    --uninstall) ACTION="uninstall" ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  for host in "$HOME/.claude" "$HOME/.cursor" "$HOME/.agents"; do
    [[ -d "$host" ]] && TARGETS+=("$host/skills")
  done
  if [[ ${#TARGETS[@]} -eq 0 ]]; then
    TARGETS+=("$HOME/.claude/skills")
  fi
fi

for target in "${TARGETS[@]}"; do
  mkdir -p "$target"
  for skill_dir in "$SKILLS_SRC"/*/; do
    name="$(basename "$skill_dir")"
    dest="$target/$name"
    if [[ "$ACTION" == "uninstall" ]]; then
      if [[ -L "$dest" || -d "$dest" ]]; then
        rm -rf "$dest"
        echo "removed  $dest"
      fi
      continue
    fi
    if [[ -e "$dest" || -L "$dest" ]]; then
      if [[ -L "$dest" && "$(readlink "$dest")" == "${skill_dir%/}" ]]; then
        echo "exists   $dest (already linked)"
        continue
      fi
      echo "replacing $dest"
      rm -rf "$dest"
    fi
    if [[ "$MODE" == "link" ]]; then
      ln -s "${skill_dir%/}" "$dest"
      echo "linked   $dest -> ${skill_dir%/}"
    else
      cp -R "${skill_dir%/}" "$dest"
      echo "copied   $dest"
    fi
  done
done

if [[ "$ACTION" == "install" ]]; then
  echo
  echo "Done. Start a new agent session so the host picks up the skill list."
fi

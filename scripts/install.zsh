#!/usr/bin/env zsh

set -euo pipefail

repo_root="${0:A:h:h}"
dry_run=0

usage() {
  cat <<'EOF'
Usage: scripts/install.zsh [--dry-run]

Installs only the LiteLLM wrapper layer:
  - ~/litellm_config.yaml
  - ~/.config/ai-litellm
  - ~/.config/claude-litellm
  - ~/.config/codex-litellm
  - ~/.config/goose-litellm
  - ~/.config/opencode-litellm
  - ~/.local/bin/*-litellm and ai-litellm

It does not write ~/.claude or ~/.codex and does not replace native claude/codex.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --dry-run)
      dry_run=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

log() {
  print -r -- "$*"
}

run() {
  if (( dry_run )); then
    printf 'dry-run '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

backup_if_exists() {
  local target="$1"
  [[ -e "$target" || -L "$target" ]] || return 0
  local stamp backup
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup="${target}.bak.${stamp}"
  run mv "$target" "$backup"
}

install_rendered() {
  local src="$1"
  local dest="$2"
  run mkdir -p "${dest:h}"
  backup_if_exists "$dest"
  if (( dry_run )); then
    log "dry-run render ${src} -> ${dest} (__HOME__=${HOME})"
  else
    perl -pe "s#__HOME__#${HOME//\\/\\\\}#g" "$src" > "$dest"
  fi
}

install_executable() {
  local src="$1"
  local dest="$2"
  run mkdir -p "${dest:h}"
  backup_if_exists "$dest"
  run cp "$src" "$dest"
  run chmod 755 "$dest"
}

require_file() {
  local file_path="$1"
  [[ -f "$file_path" ]] || {
    echo "Missing repository file: $file_path" >&2
    exit 1
  }
}

for file in \
  "$repo_root/config/litellm_config.yaml" \
  "$repo_root/config/ai-litellm/lib.zsh" \
  "$repo_root/config/ai-litellm/settings.json" \
  "$repo_root/config/ai-litellm/harnesses/schema.json" \
  "$repo_root/config/ai-litellm/harnesses/claude.json" \
  "$repo_root/config/ai-litellm/harnesses/codex.json" \
  "$repo_root/config/ai-litellm/harnesses/goose.json" \
  "$repo_root/config/ai-litellm/harnesses/opencode.json" \
  "$repo_root/config/claude-litellm/settings.json" \
  "$repo_root/config/claude-litellm/shell.zsh" \
  "$repo_root/config/codex-litellm/settings.json" \
  "$repo_root/config/codex-litellm/shell.zsh"; do
  require_file "$file"
done

log "Installing ai-litellm-fabric from $repo_root"
(( dry_run )) && log "Dry run: no files will be changed"

install_rendered "$repo_root/config/litellm_config.yaml" "$HOME/litellm_config.yaml"

install_rendered "$repo_root/config/ai-litellm/lib.zsh" "$HOME/.config/ai-litellm/lib.zsh"
install_rendered "$repo_root/config/ai-litellm/settings.json" "$HOME/.config/ai-litellm/settings.json"
for descriptor in "$repo_root"/config/ai-litellm/harnesses/*.json(N); do
  install_rendered "$descriptor" "$HOME/.config/ai-litellm/harnesses/${descriptor:t}"
done

install_rendered "$repo_root/config/claude-litellm/settings.json" "$HOME/.config/claude-litellm/settings.json"
install_rendered "$repo_root/config/claude-litellm/shell.zsh" "$HOME/.config/claude-litellm/shell.zsh"
run mkdir -p "$HOME/.config/claude-litellm/claude-config"
backup_if_exists "$HOME/.config/claude-litellm/config.yaml"
run ln -s "$HOME/litellm_config.yaml" "$HOME/.config/claude-litellm/config.yaml"

install_rendered "$repo_root/config/codex-litellm/settings.json" "$HOME/.config/codex-litellm/settings.json"
install_rendered "$repo_root/config/codex-litellm/shell.zsh" "$HOME/.config/codex-litellm/shell.zsh"
run mkdir -p "$HOME/.config/codex-litellm/codex-home"

run mkdir -p "$HOME/.config/goose-litellm"
run mkdir -p "$HOME/.config/opencode-litellm"

for script in ai-litellm claude-litellm codex-litellm goose-litellm opencode-litellm openrouter-key-status litellm-master-key-status; do
  install_executable "$repo_root/bin/$script" "$HOME/.local/bin/$script"
done

log "Installed LiteLLM wrapper layer."
log "Next:"
log "  ai-litellm proxy doctor"
log "  ai-litellm context doctor"
log "  ai-litellm sync"

#!/usr/bin/env zsh

set -euo pipefail

prefix="${CLAUDE_LITELLM_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-litellm}"
bin_dir="$HOME/.local/bin"
user_config_root="${XDG_CONFIG_HOME:-$HOME/.config}/claude-litellm"
dry_run=0
remove_legacy=0
purge_keychain=0
purge_state=0
install_lock=""
install_lock_fd=""
mutation_lock=""
mutation_lock_fd=""

usage() {
  cat <<'EOF'
Usage: scripts/uninstall.zsh [--dry-run] [--prefix PATH] [--legacy]
                             [--purge-keychain] [--purge-state]

Removes the claude-litellm package and its single public shim after stopping a
proxy owned by that package.

Default removal:
  ~/.local/share/claude-litellm
  ~/.local/bin/claude-litellm

With --legacy, recognized ai-litellm and ai-litellm-fabric package roots and
their owned shims are also removed. This is intentionally explicit because an
unmigrated legacy package may still contain Claude transcripts.

Native ~/.claude, ~/.codex, native claude/codex commands, and Keychain entries
are never removed by default. --purge-keychain is the only operation that
deletes the built-in LiteLLM master/OpenRouter Keychain entries. Package state
(OAuth files, isolated Claude history/transcripts, logs) is moved to a private
backup under ~/.local/state/claude-litellm unless --purge-state is explicit.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --dry-run)
      dry_run=1
      ;;
    --legacy)
      remove_legacy=1
      ;;
    --purge-keychain)
      purge_keychain=1
      ;;
    --purge-state)
      purge_state=1
      ;;
    --prefix)
      shift
      [[ $# -gt 0 ]] || { echo "--prefix requires a path" >&2; exit 1; }
      prefix="$1"
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

# Keep the caller's lexical path. Resolving with `:A` first would turn a
# symlinked --prefix into its target before the safety checks could reject it.
prefix="${prefix:a}"
bin_dir="${bin_dir:a}"
user_config_root="${user_config_root:a}"

cleanup() {
  if [[ -n "$mutation_lock" && -d "$mutation_lock" && \
        "$(<"$mutation_lock/pid" 2>/dev/null || true)" == "$$" ]]; then
    rm -rf "$mutation_lock"
  fi
  if [[ "$mutation_lock_fd" == <-> ]]; then
    zmodload zsh/system 2>/dev/null || true
    zsystem flock -u "$mutation_lock_fd" 2>/dev/null || true
  fi
  if [[ -n "$install_lock" && -d "$install_lock" && \
        "$(<"$install_lock/pid" 2>/dev/null || true)" == "$$" ]]; then
    rm -rf "$install_lock"
  fi
  if [[ "$install_lock_fd" == <-> ]]; then
    zmodload zsh/system 2>/dev/null || true
    zsystem flock -u "$install_lock_fd" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

assert_no_symlink_ancestors() {
  local target="$1" component current="/"
  local -a components
  components=("${(@s:/:)target}")
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="${current%/}/$component"
    if [[ -L "$current" ]]; then
      # macOS intentionally exposes these root-owned compatibility links.
      # Permit only their exact stock targets; every later/user-controlled
      # symlink component (including the requested prefix itself) still fails.
      case "$current:$(readlink "$current" 2>/dev/null || true)" in
        /var:private/var|/tmp:private/tmp) ;;
        *)
          echo "Refusing path with a symlink component: $current" >&2
          return 1
          ;;
      esac
    fi
    if [[ "$current" != "$target" && ( -e "$current" || -L "$current" ) ]]; then
      [[ -d "$current" ]] || {
        echo "Refusing path with a non-directory ancestor: $current" >&2
        return 1
      }
    fi
  done
}

acquire_install_lock() {
  install_lock="${prefix:h}/.${prefix:t}.install.lock"
  assert_no_symlink_ancestors "${install_lock:h}" || return 1
  mkdir -p "${install_lock:h}"
  local lock_file="${install_lock}.flock"
  if [[ ! -e "$lock_file" && ! -L "$lock_file" ]]; then
    ( umask 077; setopt noclobber; : > "$lock_file" ) 2>/dev/null || true
  fi
  [[ -f "$lock_file" && ! -L "$lock_file" ]] || {
    echo "Refusing unsafe install lock file: $lock_file" >&2
    return 1
  }
  zmodload zsh/system || return 1
  local fd owner
  zsystem flock -t 0 -f fd "$lock_file" || {
    echo "Refusing uninstall while claude-litellm installation/uninstall is active." >&2
    return 1
  }
  chmod 600 "$lock_file" 2>/dev/null || true
  install_lock_fd="$fd"
  if [[ -d "$install_lock" ]]; then
    owner="$(<"$install_lock/pid" 2>/dev/null || printf '?')"
    if [[ "$owner" != '?' ]] && kill -0 "$owner" 2>/dev/null; then
      echo "Refusing uninstall while legacy installer pid $owner is active." >&2
      return 1
    fi
    rm -rf "$install_lock"
  elif [[ -e "$install_lock" || -L "$install_lock" ]]; then
    echo "Refusing unsafe install lock path: $install_lock" >&2
    return 1
  fi
  mkdir "$install_lock"
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$install_lock/started_at"
  printf '%s\n' "$$" > "$install_lock/pid"
}

acquire_mutation_lock() {
  local mutation_root="${XDG_CONFIG_HOME:-$HOME/.config}/claude-litellm"
  mutation_root="${mutation_root:a}"
  assert_no_symlink_ancestors "$mutation_root" || return 1
  mutation_lock="$mutation_root/.mutation.lock"
  mkdir -p "${mutation_lock:h}"
  chmod 700 "${mutation_lock:h}"
  local lock_file="${mutation_lock}.flock"
  if [[ ! -e "$lock_file" && ! -L "$lock_file" ]]; then
    ( umask 077; setopt noclobber; : > "$lock_file" ) 2>/dev/null || true
  fi
  [[ -f "$lock_file" && ! -L "$lock_file" ]] || {
    echo "Refusing unsafe mutation lock file: $lock_file" >&2
    return 1
  }
  zmodload zsh/system || return 1
  local fd owner
  zsystem flock -t 0 -f fd "$lock_file" || {
    echo "Refusing uninstall while a model/alias/sync mutation is active." >&2
    return 1
  }
  chmod 600 "$lock_file" 2>/dev/null || true
  mutation_lock_fd="$fd"
  if [[ -d "$mutation_lock" ]]; then
    owner="$(<"$mutation_lock/pid" 2>/dev/null || printf '?')"
    if [[ "$owner" != '?' ]] && kill -0 "$owner" 2>/dev/null; then
      echo "Refusing uninstall while legacy mutation pid $owner is active." >&2
      return 1
    fi
    rm -rf "$mutation_lock"
  elif [[ -e "$mutation_lock" || -L "$mutation_lock" ]]; then
    echo "Refusing unsafe mutation lock path: $mutation_lock" >&2
    return 1
  fi
  mkdir "$mutation_lock"
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$mutation_lock/started_at"
  printf '%s\n' "$$" > "$mutation_lock/pid"
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

package_manifest_identity_valid() {
  local target="$1" manifest="$1/install-manifest.json"
  [[ -f "$manifest" && ! -L "$manifest" ]] || return 1
  node -e '
const fs = require("fs");
const [manifest, expectedPrefix] = process.argv.slice(1);
try {
  const payload = JSON.parse(fs.readFileSync(manifest, "utf8"));
  const ok = payload && typeof payload === "object" && !Array.isArray(payload)
    && payload.product === "claude-litellm"
    && payload.prefix === expectedPrefix
    && (payload.schemaVersion === 1 || payload.schemaVersion === 2);
  process.exit(ok ? 0 : 1);
} catch (_) { process.exit(1); }
' "$manifest" "$target"
}

package_manifest_user_config_disjoint() {
  local target="$1" manifest="$1/install-manifest.json"
  node -e '
const fs = require("fs");
const path = require("path");
const [manifest, expectedPrefix] = process.argv.slice(1);
const contains = (root, candidate) =>
  candidate === root || candidate.startsWith(root + path.sep);
const canonicalThroughExistingAncestor = (raw) => {
  let cursor = path.resolve(raw);
  const missing = [];
  for (;;) {
    try {
      fs.lstatSync(cursor);
      break;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      const parent = path.dirname(cursor);
      if (parent === cursor) throw error;
      missing.unshift(path.basename(cursor));
      cursor = parent;
    }
  }
  return path.resolve(fs.realpathSync(cursor), ...missing);
};
try {
  const payload = JSON.parse(fs.readFileSync(manifest, "utf8"));
  if (payload.schemaVersion === 1) process.exit(0);
  const userConfig = payload.userConfig;
  if (payload.schemaVersion !== 2 || !userConfig || typeof userConfig !== "object"
      || Array.isArray(userConfig)) process.exit(1);
  const prefix = path.resolve(expectedPrefix);
  const canonicalPrefix = canonicalThroughExistingAncestor(prefix);
  for (const key of ["models", "claudeSettings"]) {
    const raw = userConfig[key];
    if (typeof raw !== "string" || !path.isAbsolute(raw)) process.exit(1);
    const candidate = path.resolve(raw);
    const canonicalCandidate = canonicalThroughExistingAncestor(candidate);
    if (contains(prefix, candidate) || contains(canonicalPrefix, canonicalCandidate)) {
      console.error(`Refusing removal: manifest-recorded userConfig.${key} resolves inside package prefix: ${canonicalCandidate}`);
      process.exit(2);
    }
  }
  process.exit(0);
} catch (_) { process.exit(1); }
' "$manifest" "$target"
}

assert_prefix_safe() {
  local target="$1"
  local home_abs="${HOME:a}"
  local data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
  local protected_native_root
  local target_canonical="${target:A}"
  local bin_dir_canonical="${bin_dir:A}"
  local user_config_canonical="${user_config_root:A}"
  data_root="${data_root:a}"
  [[ "$target" != "/" && "$target" != "$home_abs" && "$target" != "$data_root" ]] || {
    echo "Refusing unsafe package prefix: $target" >&2
    exit 1
  }
  [[ "$target" != "$bin_dir" && "$bin_dir" != "$target"/* ]] || {
    echo "Refusing package prefix that owns the public command directory: $target" >&2
    exit 1
  }
  [[ "$target_canonical" != "$bin_dir_canonical" && \
     "$bin_dir_canonical" != "$target_canonical"/* ]] || {
    echo "Refusing package prefix that resolves over the public command directory: $target" >&2
    exit 1
  }
  [[ "$target" != "$user_config_root" && "$user_config_root" != "$target"/* ]] || {
    echo "Refusing package prefix that owns durable user configuration: $target" >&2
    exit 1
  }
  [[ "$target_canonical" != "$user_config_canonical" && \
     "$user_config_canonical" != "$target_canonical"/* ]] || {
    echo "Refusing package prefix that resolves over durable user configuration: $target" >&2
    exit 1
  }
  for protected_native_root in "$home_abs/.claude" "$home_abs/.codex"; do
    if [[ "$target" == "$protected_native_root" || "$target" == "$protected_native_root"/* ]]; then
      echo "Refusing package prefix inside protected native state: $target" >&2
      exit 1
    fi
  done
  assert_no_symlink_ancestors "$target" || exit 1
  [[ ! -L "$target" ]] || {
    echo "Refusing symlink package prefix: $target" >&2
    exit 1
  }
  if [[ -e "$target" ]]; then
    [[ -d "$target" ]] || { echo "Refusing non-directory package prefix: $target" >&2; exit 1; }
    package_manifest_identity_valid "$target" || {
      echo "Refusing directory without a valid claude-litellm ownership manifest: $target" >&2
      exit 1
    }
    package_manifest_user_config_disjoint "$target" || {
      echo "Refusing package whose recorded user configuration overlaps its removal root: $target" >&2
      exit 1
    }
  fi
}

proxy_command_owned_by_prefix() {
  local command_line="$1"
  local owner_prefix="$2"
  local allow_external_runtime="${3:-0}"
  local process_executable="${4:-}"
  local config="$owner_prefix/config/litellm_config.yaml"
  local venv="$owner_prefix/runtime/venv"
  local bootstrap="$owner_prefix/config/ai_litellm_callbacks/proxy_bootstrap.py"
  local executable_name="${process_executable:t:l}"
  [[ "$command_line" == *"--config $config"* || "$command_line" == *"--config=$config"* ]] || return 1
  if [[ "$executable_name" == python* ]]; then
    [[ "$command_line" == "$process_executable $venv/bin/litellm "* || \
       "$command_line" == "$process_executable $venv/bin/litellm-proxy "* || \
       ( -f "$bootstrap" && ! -L "$bootstrap" && "$command_line" == "$process_executable $bootstrap --config $config"* ) || \
       ( -f "$bootstrap" && ! -L "$bootstrap" && "$command_line" == "$venv/bin/python $bootstrap --config $config"* ) || \
       ( -f "$bootstrap" && ! -L "$bootstrap" && "$command_line" == "$process_executable -sP -B $bootstrap --config $config"* ) || \
       ( -f "$bootstrap" && ! -L "$bootstrap" && "$command_line" == "$venv/bin/python -sP -B $bootstrap --config $config"* ) || \
       "$command_line" == "$process_executable $venv/"*"/litellm/proxy/"* ]] && return 0
  elif [[ "$process_executable" == "$venv/bin/litellm" || \
          "$process_executable" == "$venv/bin/litellm-proxy" ]]; then
    [[ "$command_line" == "$process_executable "* ]] && return 0
  fi
  (( allow_external_runtime )) || return 1
  case "$executable_name" in
    litellm|litellm-proxy)
      [[ "$command_line" == "$process_executable "* ]]
      ;;
    python*)
      [[ "$command_line" == "$process_executable "*/bin/litellm" --config $config"* || \
         "$command_line" == "$process_executable "*/bin/litellm-proxy" --config $config"* || \
         "$command_line" == "$process_executable $bootstrap --config $config"* || \
         "$command_line" == "$process_executable -sP -B $bootstrap --config $config"* || \
         "$command_line" == "$process_executable -m litellm --config $config"* ]]
      ;;
    *) return 1 ;;
  esac
}

stop_running_proxy() {
  local target_prefix="$1"
  local validate_only="${2:-0}"
  [[ -d "$target_prefix" ]] || return 0
  local pid_file="$target_prefix/state/ai-litellm/litellm.pid"
  local pid command_line process_executable line candidate
  typeset -A owned_pids
  owned_pids=()

  if [[ -e "$pid_file" || -L "$pid_file" ]]; then
    [[ -f "$pid_file" && ! -L "$pid_file" ]] || {
      echo "Refusing removal: proxy PID file is not a regular file: $pid_file" >&2
      return 1
    }
    pid="$(<"$pid_file")"
    [[ "$pid" == <-> ]] || {
      echo "Refusing removal: invalid proxy PID in $pid_file" >&2
      return 1
    }
    if kill -0 "$pid" 2>/dev/null; then
      command_line="$(ps -ww -o command= -p "$pid" 2>/dev/null || true)"
      process_executable="$(ps -ww -o comm= -p "$pid" 2>/dev/null || true)"
      proxy_command_owned_by_prefix "$command_line" "$target_prefix" 1 "$process_executable" || {
        echo "Refusing to signal pid $pid from $pid_file: process is not owned by $target_prefix" >&2
        return 1
      }
      owned_pids[$pid]=1
    fi
  fi

  while IFS= read -r line; do
    [[ "$line" =~ '^[[:space:]]*([0-9]+)[[:space:]]+(.*)$' ]] || continue
    candidate="${match[1]}"
    command_line="${match[2]}"
    [[ "$candidate" != "$$" ]] || continue
    [[ "$command_line" == *"--config $target_prefix/config/litellm_config.yaml"* || \
       "$command_line" == *"--config=$target_prefix/config/litellm_config.yaml"* ]] || continue
    [[ -z "${owned_pids[$candidate]:-}" ]] || continue
    process_executable="$(ps -ww -o comm= -p "$candidate" 2>/dev/null || true)"
    if proxy_command_owned_by_prefix "$command_line" "$target_prefix" 0 "$process_executable"; then
      owned_pids[$candidate]=0
    elif proxy_command_owned_by_prefix "$command_line" "$target_prefix" 1 "$process_executable"; then
      echo "Refusing removal: external LiteLLM pid $candidate uses $target_prefix config without an owned PID file." >&2
      return 1
    fi
  done < <(ps -axo pid=,command= 2>/dev/null)

  (( validate_only )) && return 0

  for pid in ${(k)owned_pids}; do
    command_line="$(ps -ww -o command= -p "$pid" 2>/dev/null || true)"
    process_executable="$(ps -ww -o comm= -p "$pid" 2>/dev/null || true)"
    proxy_command_owned_by_prefix "$command_line" "$target_prefix" "${owned_pids[$pid]}" "$process_executable" || {
      echo "Refusing to signal pid $pid: ownership changed during verification." >&2
      return 1
    }
    if (( dry_run )); then
      echo "dry-run stop proxy owned by $target_prefix (pid $pid)"
      continue
    fi
    echo "Stopping proxy owned by $target_prefix (pid $pid)."
    kill -TERM "$pid" 2>/dev/null || true
    local attempt
    for attempt in {1..30}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
      command_line="$(ps -ww -o command= -p "$pid" 2>/dev/null || true)"
      process_executable="$(ps -ww -o comm= -p "$pid" 2>/dev/null || true)"
      if proxy_command_owned_by_prefix "$command_line" "$target_prefix" "${owned_pids[$pid]}" "$process_executable"; then
        kill -KILL "$pid" 2>/dev/null || true
      fi
    fi
  done

  (( dry_run )) && return 0
  while IFS= read -r line; do
    [[ "$line" =~ '^[[:space:]]*([0-9]+)[[:space:]]+(.*)$' ]] || continue
    [[ "${match[2]}" == *"--config $target_prefix/config/litellm_config.yaml"* || \
       "${match[2]}" == *"--config=$target_prefix/config/litellm_config.yaml"* ]] || continue
    process_executable="$(ps -ww -o comm= -p "${match[1]}" 2>/dev/null || true)"
    if proxy_command_owned_by_prefix "${match[2]}" "$target_prefix" 0 "$process_executable"; then
      echo "Refusing removal: owned proxy pid ${match[1]} is still running." >&2
      return 1
    elif proxy_command_owned_by_prefix "${match[2]}" "$target_prefix" 1 "$process_executable"; then
      echo "Refusing removal: external LiteLLM pid ${match[1]} still uses $target_prefix config." >&2
      return 1
    fi
  done < <(ps -axo pid=,command= 2>/dev/null)
}

remove_shim_if_owned() {
  local target="$1"
  local expected_prefix="${2:-}"
  [[ -e "$target" || -L "$target" ]] || return 0
  if [[ -L "$target" ]]; then
    local link_target="$(readlink "$target" 2>/dev/null || true)"
    [[ "$link_target" == *litellm* && ( -z "$expected_prefix" || "$link_target" == *"$expected_prefix"* ) ]] || {
      echo "Leaving unrelated command in place: $target" >&2
      return 0
    }
  else
    grep -Eq 'claude-litellm|ai-litellm(-fabric)?|AI_LITELLM_HOME|CLAUDE_LITELLM_HOME' "$target" 2>/dev/null || {
      echo "Leaving unrelated command in place: $target" >&2
      return 0
    }
    if [[ -n "$expected_prefix" ]] && ! grep -Fq -- "$expected_prefix" "$target" 2>/dev/null; then
      echo "Leaving shim for a different package prefix in place: $target" >&2
      return 0
    fi
  fi
  run rm -f "$target"
}

recognized_legacy_prefix() {
  local target="$1"
  assert_no_symlink_ancestors "$target" || return 1
  [[ ! -L "$target" ]] || return 1
  [[ -d "$target" ]] || return 1
  [[ "${target:t}" == "ai-litellm" || "${target:t}" == "ai-litellm-fabric" ]] || return 1
  [[ -f "$target/config/ai-litellm/lib.zsh" ]] || return 1
}

handle_keychain() {
  (( purge_keychain )) || {
    echo "Keychain entries were left unchanged. Use --purge-keychain only if their credentials are not shared." >&2
    return 0
  }
  command -v security >/dev/null 2>&1 || return 0
  local -a services=(litellm-master-key openrouter-api-key)
  local service
  for service in "${services[@]}"; do
    security find-generic-password -s "$service" >/dev/null 2>&1 || continue
    run security delete-generic-password -a "${USER:-}" -s "$service"
  done
}

validate_package_state() {
  local state="$prefix/state"
  [[ -e "$state" || -L "$state" ]] || return 0
  if [[ ! -d "$state" || -L "$state" ]]; then
    (( purge_state )) && return 0
    echo "Refusing default uninstall: package state is not a regular directory: $state" >&2
    return 1
  fi
}

prepare_backup_root() {
  local removal_root="$1"
  local backup_root="${XDG_STATE_HOME:-$HOME/.local/state}/claude-litellm/uninstall-backups"
  local canonical_backup canonical_removal canonical_prefix
  backup_root="${backup_root:a}"
  # Validate before and after creation so mkdir cannot traverse a pre-existing
  # user-controlled symlink. Canonical containment closes lexical aliases too.
  assert_no_symlink_ancestors "$backup_root" || return 1
  case "$backup_root" in
    "$prefix"|"$prefix"/*|"$removal_root"|"$removal_root"/*)
      echo "Refusing state backup inside a package being removed: $backup_root" >&2
      return 1
      ;;
  esac
  if (( dry_run )); then
    run mkdir -p "$backup_root"
    REPLY="$backup_root"
    return 0
  fi
  ( umask 077; mkdir -p "$backup_root" ) || return 1
  assert_no_symlink_ancestors "$backup_root" || return 1
  [[ -d "$backup_root" && ! -L "$backup_root" ]] || {
    echo "Refusing unsafe uninstall backup root: $backup_root" >&2
    return 1
  }
  chmod 700 "${backup_root:h}" "$backup_root" 2>/dev/null || return 1
  canonical_backup="${backup_root:A}"
  canonical_removal="${removal_root:A}"
  canonical_prefix="${prefix:A}"
  case "$canonical_backup" in
    "$canonical_prefix"|"$canonical_prefix"/*|"$canonical_removal"|"$canonical_removal"/*)
      echo "Refusing canonical state backup inside a package being removed: $canonical_backup" >&2
      return 1
      ;;
  esac
  REPLY="$backup_root"
}

preserve_package_state() {
  local state="$prefix/state"
  validate_package_state || return $?
  [[ -e "$state" || -L "$state" ]] || return 0
  (( purge_state )) && return 0
  local backup_root backup_container backup
  prepare_backup_root "$prefix" || return 1
  backup_root="$REPLY"
  if (( dry_run )); then
    backup="$backup_root/<private-backup>/state"
    run mv "$state" "$backup"
    echo "Preserved package state at: $backup" >&2
    return 0
  fi
  backup_container="$(mktemp -d "$backup_root/$(date -u +%Y%m%dT%H%M%SZ)-state.XXXXXX")" || return 1
  chmod 700 "$backup_container" || return 1
  backup="$backup_container/state"
  mv "$state" "$backup" || return 1
  echo "Preserved package state at: $backup" >&2
}

remove_or_preserve_legacy_root() {
  local target="$1"
  if (( purge_state )); then
    run rm -rf "$target"
    return 0
  fi
  local backup_root backup_container backup
  prepare_backup_root "$target" || return 1
  backup_root="$REPLY"
  if (( dry_run )); then
    backup="$backup_root/<private-backup>/${target:t}"
    run mv "$target" "$backup"
    echo "Preserved legacy package at: $backup" >&2
    return 0
  fi
  backup_container="$(mktemp -d "$backup_root/$(date -u +%Y%m%dT%H%M%SZ)-legacy.XXXXXX")" || return 1
  chmod 700 "$backup_container" || return 1
  backup="$backup_container/${target:t}"
  mv "$target" "$backup" || return 1
  echo "Preserved legacy package at: $backup" >&2
}

typeset -a legacy_prefixes active_legacy_prefixes
legacy_data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
legacy_data_root="${legacy_data_root:a}"
legacy_prefixes=(
  "$legacy_data_root/ai-litellm-fabric"
  "$legacy_data_root/ai-litellm"
)
active_legacy_prefixes=()

# Validate every requested target and every process ownership claim before the
# first state move, shim deletion, or package removal. This prevents a bad
# legacy target from aborting only after the primary package is already gone.
assert_prefix_safe "$prefix"
if (( remove_legacy )); then
  local_prefix=""
  for local_prefix in "${legacy_prefixes[@]}"; do
    [[ -e "$local_prefix" ]] || continue
    recognized_legacy_prefix "$local_prefix" || {
      echo "Refusing unrecognized legacy directory: $local_prefix" >&2
      exit 1
    }
    active_legacy_prefixes+=("$local_prefix")
  done
fi
stop_running_proxy "$prefix" 1
for local_prefix in "${active_legacy_prefixes[@]}"; do
  stop_running_proxy "$local_prefix" 1
done
validate_package_state
if (( ! purge_state )) && [[ -e "$prefix/state" || -L "$prefix/state" ]]; then
  prepare_backup_root "$prefix" >/dev/null
fi
if (( remove_legacy && ! purge_state )); then
  for local_prefix in "${active_legacy_prefixes[@]}"; do
    prepare_backup_root "$local_prefix" >/dev/null
  done
fi

if (( ! dry_run )); then
  acquire_install_lock
  acquire_mutation_lock
fi

# Revalidate and stop every target while both package lifecycle and user
# mutation locks are held. Only after all stops succeed do removals begin.
assert_prefix_safe "$prefix"
validate_package_state
if (( ! purge_state )) && [[ -e "$prefix/state" || -L "$prefix/state" ]]; then
  prepare_backup_root "$prefix" >/dev/null
fi
for local_prefix in "${active_legacy_prefixes[@]}"; do
  recognized_legacy_prefix "$local_prefix" || {
    echo "Refusing legacy path that changed during uninstall preflight: $local_prefix" >&2
    exit 1
  }
done
stop_running_proxy "$prefix"
for local_prefix in "${active_legacy_prefixes[@]}"; do
  stop_running_proxy "$local_prefix"
done

preserve_package_state
if (( remove_legacy && ! purge_state )); then
  for local_prefix in "${active_legacy_prefixes[@]}"; do
    remove_or_preserve_legacy_root "$local_prefix"
  done
fi

remove_shim_if_owned "$bin_dir/claude-litellm" "$prefix"
run rm -rf "$prefix"

if (( remove_legacy )); then
  if (( purge_state )); then
    for local_prefix in "${active_legacy_prefixes[@]}"; do
      remove_or_preserve_legacy_root "$local_prefix"
    done
  fi
  for shim in ai-litellm codex-litellm opencode-litellm goose-litellm fabric \
    openrouter-key-status litellm-master-key-status; do
    remove_shim_if_owned "$bin_dir/$shim"
  done
fi

handle_keychain

echo "Removed claude-litellm package and owned shim."
if (( remove_legacy )); then
  echo "Removed recognized legacy packages/shims. Native ~/.claude and ~/.codex were untouched."
fi

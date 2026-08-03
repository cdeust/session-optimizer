#!/usr/bin/env bash
set -euo pipefail

bundle="${1:-}"
bundle_sum="${2:-}"
exec_manifest="${3:-}"

if [[ -z "$bundle" || -z "$bundle_sum" ]]; then
  echo "usage: $0 <bundle.tar.gz> <bundle.tar.gz.sha256> [executable-manifest]" >&2
  exit 2
fi
[[ -f "$bundle" ]] || { echo "error: bundle not found: $bundle" >&2; exit 2; }
[[ -f "$bundle_sum" ]] || { echo "error: checksum not found: $bundle_sum" >&2; exit 2; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

expected="$(awk '{print $1}' "$bundle_sum" | head -1 | tr '[:upper:]' '[:lower:]')"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo "error: invalid SHA-256 checksum" >&2; exit 2; }
actual="$(sha256_file "$bundle" | tr '[:upper:]' '[:lower:]')"
[[ "$actual" == "$expected" ]] || { echo "INTEGRITY FAILURE: bundle checksum mismatch" >&2; exit 1; }

[[ -z "$exec_manifest" ]] && { echo "OK: bundle checksum matches"; exit 0; }
[[ -f "$exec_manifest" ]] || { echo "error: manifest not found: $exec_manifest" >&2; exit 2; }

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# Reject path traversal before extraction even when the archive checksum is
# internally consistent. Release verification is a trust boundary, not a
# convenience untar command.
while IFS= read -r member; do
  case "$member" in
    /*|../*|*/../*|*/..) echo "INTEGRITY FAILURE: unsafe archive path: $member" >&2; exit 1 ;;
  esac
done < <(tar -tzf "$bundle")
tar -xzf "$bundle" -C "$workdir"

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  want="$(awk '{print $1}' <<<"$line" | tr '[:upper:]' '[:lower:]')"
  rel="$(awk '{print $2}' <<<"$line")"
  [[ "$want" =~ ^[0-9a-f]{64}$ ]] || { echo "error: invalid executable digest" >&2; exit 2; }
  [[ -n "$rel" && "$rel" != /* && "$rel" != ../* && "$rel" != */../* ]] \
    || { echo "INTEGRITY FAILURE: unsafe manifest path" >&2; exit 1; }
  target="$workdir/$rel"
  [[ -f "$target" ]] || { echo "INTEGRITY FAILURE: missing executable: $rel" >&2; exit 1; }
  got="$(sha256_file "$target" | tr '[:upper:]' '[:lower:]')"
  [[ "$got" == "$want" ]] || { echo "INTEGRITY FAILURE: executable mismatch: $rel" >&2; exit 1; }
done < "$exec_manifest"

echo "OK: bundle and executable manifest verified"

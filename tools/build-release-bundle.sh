#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"
out_dir="${1:-dist}"
bundle_name="session-optimizer"
bundle_paths=(
  .claude-plugin
  .agents
  hooks
  plugins
  README.md
  LICENSE
  PRIVACY.md
  SECURITY.md
  CHANGELOG.md
)

mkdir -p "$out_dir"

tar_args=(--create --gzip)
if tar --version 2>/dev/null | grep -qi "gnu tar"; then
  tar_args+=(--sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner)
fi
tar "${tar_args[@]}" -f "$out_dir/$bundle_name.tar.gz" "${bundle_paths[@]}"

find hooks plugins -type f \( -name "*.sh" -o -name "*.py" \) -print \
  | LC_ALL=C sort \
  | while IFS= read -r file; do
      if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file"
      else
        shasum -a 256 "$file"
      fi
    done > "$out_dir/EXECUTABLE-MANIFEST.sha256"

version="$(python3 -c 'import json; print(json.load(open(".claude-plugin/marketplace.json"))["metadata"]["version"])')"
python3 tools/gen-bundle-sbom.py \
  --version "$version" \
  --output "$out_dir/$bundle_name.cdx.json" \
  "${bundle_paths[@]}"

for file in "$bundle_name.tar.gz" "$bundle_name.cdx.json" EXECUTABLE-MANIFEST.sha256; do
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$out_dir" && sha256sum "$file" > "$file.sha256")
  else
    (cd "$out_dir" && shasum -a 256 "$file" > "$file.sha256")
  fi
done

printf 'Release bundle assembled in %s\n' "$out_dir"

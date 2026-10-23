#!/bin/sh
set -eu

version=0.10.0
archive="prime-agent-${version}-linux-x64.tar.gz"
url="https://github.com/PrimeIntellect-ai/prime-agent/releases/download/v${version}/${archive}"
archive_sha=c16bd2af5e77b53f49b914a44742c4cf6a67c5ed5000041430b78dbd4c3bcbec
binary_sha=546e71921bb0fa278c9d50dd8c61a27b835413552cb08a9359d0e320848e39d6
prefix=${PRIME_AGENT_PILOT_PREFIX:-"${HOME:?HOME is required}/.local"}
root="$prefix/share/factory/prime-agent"
destination="$root/$version"
launcher="$prefix/bin/factory-prime-agent"

case "$prefix" in
  /*) ;;
  *) printf 'PRIME_AGENT_PILOT_PREFIX must be absolute: %s\n' "$prefix" >&2; exit 1 ;;
esac
for command in curl sha256sum tar mktemp; do
  command -v "$command" >/dev/null || { printf 'missing required command: %s\n' "$command" >&2; exit 1; }
done

work=$(mktemp -d)
stage=""
trap 'rm -rf "$work" ${stage:+"$stage"}' EXIT HUP INT TERM
curl -fL --retry 2 --output "$work/$archive" "$url"
printf '%s  %s\n' "$archive_sha" "$work/$archive" | sha256sum --check --status

mkdir -p "$root" "$prefix/bin"
if [ -e "$destination" ]; then
  printf '%s  %s\n' "$binary_sha" "$destination/prime-agent" | sha256sum --check --status || {
    printf 'existing pinned install failed verification: %s\n' "$destination" >&2
    exit 1
  }
else
  stage=$(mktemp -d "$root/.${version}.XXXXXX")
  tar -xzf "$work/$archive" -C "$stage"
  printf '%s  %s\n' "$binary_sha" "$stage/prime-agent" | sha256sum --check --status
  [ "$("$stage/prime-agent" --version)" = "$version" ]
  mv "$stage" "$destination"
  stage=""
fi

if [ -e "$launcher" ] && [ ! -L "$launcher" ]; then
  printf 'refusing to replace non-symlink launcher: %s\n' "$launcher" >&2
  exit 1
fi
link="$work/factory-prime-agent"
ln -s "$destination/prime-agent" "$link"
mv -f "$link" "$launcher"
printf 'installed factory-prime-agent %s at %s\n' "$version" "$launcher"

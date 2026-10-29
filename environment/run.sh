#!/usr/bin/env bash
# Host: run a command in the EIC container. Arguments are passed without shell eval.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ $# == 0 ]]; then echo 'Usage: environment/run.sh COMMAND [ARG ...]' >&2; exit 2; fi
# Isolate links created by the container login environment from the repository root.
mkdir -p "$repo/result/.environment"
if [[ -n ${TESTBENCH_CONTAINER:-} ]]; then
  mounted=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/work"}}{{.Source}}{{end}}{{end}}' "$TESTBENCH_CONTAINER")
  if [[ -z $mounted || ! -d $mounted || $(cd "$mounted" && pwd -P) != "$repo" ]]; then
    echo 'The selected container does not mount this checkout at /work. Unset TESTBENCH_CONTAINER to launch the pinned image for this checkout.' >&2
    exit 2
  fi
  if [[ -f $repo/.git ]]; then
    echo 'Use the default launcher for an isolated Git worktree; unset TESTBENCH_CONTAINER.' >&2
    exit 2
  fi
  image_id=$(docker inspect --format '{{.Image}}' "$TESTBENCH_CONTAINER")
  exec docker exec -e TESTBENCH_IMAGE="$image_id" -w /work/result/.environment "$TESTBENCH_CONTAINER" bash -lc 'cd /work; exec "$@"' testbench "$@"
fi
image_ref=${TESTBENCH_IMAGE:-eicweb/eic_xl@sha256:67c914295a92396040c3051360d70786df9c1ee8458264ad0b83cd1a8ccf3d05}
docker_args=(run --rm -e TESTBENCH_IMAGE="$image_ref" --platform "${TESTBENCH_PLATFORM:-linux/amd64}")
if [[ -f $repo/.git ]]; then
  # A worktree's .git file points outside its checkout. Expose only the common
  # Git metadata, read-only, so source fingerprints work inside the container.
  common=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)
  docker_args+=(--mount "type=bind,source=$common,target=$common,readonly")
fi
exec docker "${docker_args[@]}" \
  -v "$repo:/work" -w /work/result/.environment "$image_ref" \
  bash -lc 'cd /work; exec "$@"' testbench "$@"

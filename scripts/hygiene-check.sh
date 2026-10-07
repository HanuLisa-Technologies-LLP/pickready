#!/usr/bin/env bash
# Lightweight workspace hygiene check (docs/development-storage-policy.md).
#
#   scripts/hygiene-check.sh                 report only, changes nothing
#   scripts/hygiene-check.sh --clean-temp    also remove Docker resources that
#                                            carry the label readypick.temporary=true
#
# Temporary Docker resources a task creates MUST carry that label, for example
#   docker run --label readypick.temporary=true --name pickready-tmp-<task> ...
# so this script can remove exactly them and nothing else. It never touches
# volumes without the label, tfstate, tfvars, secrets or any worktree.
set -u

echo "== git worktrees (expect only the canonical checkout)"
git worktree list
echo "prunable: $(git worktree list --porcelain | grep -c '^prunable')"

echo
echo "== docker disk use"
docker system df 2>/dev/null || echo "docker unavailable"

echo
echo "== compose projects (expect pickready; readypick-test only while a test run is live)"
docker ps -a --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null | grep -v '^$' | sort | uniq -c

echo
echo "== ReadyPick containers started from another path (stale)"
docker ps -a --format '{{.Names}} {{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
  | grep -i -E 'pickready|readypick' | grep -v -i 'C:\\pickready' || echo "none"

echo
echo "== temporary-labelled Docker resources"
docker ps -a --filter label=readypick.temporary=true --format '{{.Names}}' 2>/dev/null
docker network ls --filter label=readypick.temporary=true --format '{{.Name}}' 2>/dev/null
docker volume ls --filter label=readypick.temporary=true --format '{{.Name}}' 2>/dev/null

if [ "${1:-}" = "--clean-temp" ]; then
  echo
  echo "== removing temporary-labelled resources"
  docker ps -aq --filter label=readypick.temporary=true | xargs -r docker rm -f
  docker network ls -q --filter label=readypick.temporary=true | xargs -r docker network rm
  docker volume ls -q --filter label=readypick.temporary=true | xargs -r docker volume rm
fi

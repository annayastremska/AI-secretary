#!/usr/bin/env bash
# Selftest for bones-check.sh — the council's game-day negative tests, made permanent.
# Every secret-shaped string is CONSTRUCTED AT RUNTIME so this file never trips the
# scanner that scans it. Runs on macOS bash 3.2 and CI bash alike.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
SUT="$HERE/bones-check.sh"

PASS=0; FAIL=0
t() { # t <name> <expected-exit> [--grep <pattern>] -- cmd...
  local name="$1" want="$2"; shift 2
  local pat=""
  if [ "$1" = "--grep" ]; then pat="$2"; shift 2; fi
  [ "$1" = "--" ] && shift
  local out got
  out="$("$@" 2>&1)"; got=$?
  if [ "$got" -ne "$want" ]; then
    FAIL=$((FAIL+1)); echo "FAIL $name (exit $got, want $want)"
    echo "$out" | sed 's/^/     /' | head -6; return
  fi
  if [ -n "$pat" ] && ! printf '%s' "$out" | grep -q "$pat"; then
    FAIL=$((FAIL+1)); echo "FAIL $name (missing output: $pat)"
    echo "$out" | sed 's/^/     /' | head -6; return
  fi
  PASS=$((PASS+1)); echo "ok   $name"
}

# pristine fixture = the template itself (keeps tests honest as the template evolves)
mk_tree() {
  local d; d="$(mktemp -d)"
  ( cd "$REPO_ROOT" && find . -path ./.git -prune -o -type f -print ) | while read -r f; do
    mkdir -p "$d/$(dirname "$f")"; cp "$REPO_ROOT/$f" "$d/$f"
  done
  echo "$d"
}
run_in() { # run_in <dir> [env VAR=..] — run SUT inside fixture with clean CI env
  local d="$1"; shift
  ( cd "$d" && env -u GITHUB_STEP_SUMMARY -u GITHUB_EVENT_NAME -u GITHUB_BASE_REF "$@" bash "$SUT" )
}

# runtime-built secret shapes (never literal in this file)
GHP="ghp_$(printf 'A%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22)"
AWS="AKIA$(printf 'Q%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16)"
SLK="xoxb-$(printf '1%.0s' 1 2 3 4 5 6)-$(printf 'a%.0s' 1 2 3 4 5 6 7 8 9 10)"
KEY="-----BEGIN $(printf 'RSA ')PRIVATE KEY-----"
SKKEY="sk-$(printf 'B%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24)"
# false-positive fixtures (2026-08 repo adoptions): loopback dev URL, prose slug
LOCALURL="postgresql+asyncpg://app:$(printf 'p%.0s' 1 2 3 4 5 6 7 8)@127.0.0.1:5432/app"
REMOTEURL="postgres://deploy:$(printf 's%.0s' 1 2 3 4 5 6 7 8 9 10)@db.example.com/prod"
SLUG="https://news.example/kursk-$(printf 'a%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22)-hit"

echo "== bones-check selftest =="

D=$(mk_tree); t "pristine template passes" 0 --grep "bones-check: OK" -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); rm -rf "$D/agent-sessions"
t "missing agent-sessions/ fails" 1 -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); mkdir -p "$D/agent-sessions/somestudent"; : > "$D/agent-sessions/somestudent/2026-08-01-x-session.jsonl"
t "orphan session on push = warning only" 0 -- run_in "$D" env GITHUB_EVENT_NAME=push
t "orphan session on PR = error" 1 -- run_in "$D" env GITHUB_EVENT_NAME=pull_request
rm -rf "$D"

D=$(mk_tree); printf 'token=%s\n' "$GHP" > "$D/src/config.txt"
t "GitHub PAT shape fails" 1 --grep "ROTATE" -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf '\000\000%s\n' "$GHP" > "$D/src/blob.bin"
t "NUL-prefixed (binary) secret still caught" 1 -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf 'key=%s\n' "$SKKEY" > "$D/src/config.txt"
t "standalone sk- key still fails" 1 --grep "ROTATE" -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf 'source: %s\n' "$SLUG" > "$D/src/notes.md"
t "sk- inside a hyphenated URL slug passes" 0 -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf 'DB_URL=%s\n' "$LOCALURL" > "$D/src/dev-env.txt"
t "loopback dev URL cred passes" 0 -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf 'DB_URL=%s\n' "$REMOTEURL" > "$D/src/dev-env.txt"
t "remote URL cred still fails" 1 --grep "ROTATE" -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); mkdir -p "$D/src/tools"; printf '%s\n' "$AWS" > "$D/src/tools/bones-check.sh"
t "decoy filename is still scanned" 1 -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf '%s\n' "$SLK" > "$D/agent-sessions/_example-student/note.txt"
t "secrets in exempt example folder still fail" 1 -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf '%s\nx\n' "$KEY" > "$D/src/k.pem.txt"
t "private key header fails" 1 -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); printf '[submodule "x"]\n\tpath = x\n\turl = https://example.com/x.git\n' > "$D/.gitmodules"
t "nested .gitmodules fails" 1 --grep "gitmodules" -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); python3 -c "f=open('$D/src/big.jsonl','wb'); f.truncate(53477377)"
t "file >50MB fails with remedy" 1 --grep "50MB" -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree); mkdir -p "$D/agent-sessions/someone"; : > "$D/agent-sessions/someone/notes.txt"
t "off-convention filename warns, passes" 0 --grep "naming convention" -- run_in "$D" env
rm -rf "$D"

D=$(mk_tree)
t "scope states tree-only off PRs" 0 --grep "tree only" -- run_in "$D" env
rm -rf "$D"

# history scan: secret committed then scrubbed inside the PR range must still fail
D=$(mk_tree)
(
  cd "$D"
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  git init -q -b main . && git add -A && git commit -qm base
  git update-ref refs/remotes/origin/main main
  git checkout -qb feat
  printf '%s\n' "$GHP" > src/leak.txt && git add -A && git commit -qm "oops"
  rm src/leak.txt && git add -A && git commit -qm "scrub"
)
t "scrubbed secret in PR history still fails" 1 --grep "history" -- \
  run_in "$D" env GITHUB_EVENT_NAME=pull_request GITHUB_BASE_REF=main
t "history-mode scope says PR range" 1 --grep "ROTATE" -- \
  run_in "$D" env GITHUB_EVENT_NAME=pull_request GITHUB_BASE_REF=main
rm -rf "$D"

echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]

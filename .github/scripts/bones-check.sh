#!/usr/bin/env bash
# The skeleton is part of the course contract. Structure failures are errors;
# hygiene issues are warnings (missing summaries become errors on PRs — the review
# boundary); credential-shaped strings are errors.
set -uo pipefail

fail=0
need() { [ -e "$1" ] || { echo "::error::missing required path: $1 — see README.md (the contract)"; fail=1; }; }

need README.md
need AGENTS.md
need src
need agent-sessions
need reports

# nested submodules: hard fail — a pin here would make every practitioner clone fetch
# a remote nobody reviewed (same blast-radius rule as the cohort repo's pin-bump).
if [ -e .gitmodules ]; then
  echo "::error::.gitmodules found — nested submodules are not part of the team contract: they make every practitioner clone fetch arbitrary remotes. Vendor the code instead, or ask a practitioner."
  fail=1
fi

# the Charter is the students' rights contract; adopted repos may predate it — warn,
# never fail students for a practitioner's omission
[ -e docs/CHARTER.md ] || echo "::warning::docs/CHARTER.md is missing — that file is the students' rights contract; copy it from student-project-template (practitioner action)"
if [ -e docs/CHARTER.md ] && grep -q 'DECIDE-BEFORE-COHORT-START' docs/CHARTER.md; then
  echo "::warning::docs/CHARTER.md still contains DECIDE-BEFORE-COHORT-START — steward and retention fields must be real before enrollment (practitioner action)"
fi

summary() { [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "$*" >> "$GITHUB_STEP_SUMMARY" || true; }

summary "## bones-check"
summary ""
summary "| student | sessions | latest | summaries missing |"
summary "|---|---:|---|---:|"

total=0
for d in agent-sessions/*/; do
  [ -d "$d" ] || continue
  b=$(basename "$d")
  case "$b" in _example-student) continue ;; esac # only the shipped example is exempt

  n=0 miss=0
  for raw in "$d"*-session.jsonl "$d"*-session.json "$d"*-session.md; do
    [ -e "$raw" ] || continue
    n=$((n + 1)); total=$((total + 1))
    base="${raw%-session.*}"
    if [ ! -f "${base}-summary.md" ]; then
      if [ "${GITHUB_EVENT_NAME:-}" = "pull_request" ]; then
        echo "::error file=$raw::session log has no $(basename "$base")-summary.md sibling — required before review"
        fail=1
      else
        echo "::warning file=$raw::session log has no $(basename "$base")-summary.md sibling"
      fi
      miss=$((miss + 1))
    fi
  done

  # files that match neither naming convention would silently read as zero sessions
  for f in "$d"*; do
    [ -f "$f" ] || continue
    case "$(basename "$f")" in
      README*) : ;;
      *-session.*|*-summary.md) : ;;
      *) echo "::warning file=$f::not counted — rename to the naming convention (YYYY-MM-DD-<slug>-session.<ext> / …-summary.md)" ;;
    esac
  done

  latest=$(find "$d" -maxdepth 1 -name '20*' | sed 's|.*/||' | sort | tail -1)
  summary "| $b | $n | ${latest:-—} | $miss |"
done

summary ""
summary "_Counts are hygiene signals, not grades — one honest session beats five thin ones (docs/CHARTER.md)._"

if [ "$total" -eq 0 ]; then
  echo "::warning::no session logs yet under agent-sessions/<handle>/ — expected once work starts"
fi

# file sizes: warn >10MB, fail >50MB — GitHub hard-blocks pushes at 100MB, so catch
# runaway raw exports here while they're still fixable.
size_remedy="export the markdown via /export instead of raw JSONL, or split the session — ask in Slack before force-fixing; GitHub hard-blocks pushes at 100MB"
while IFS= read -r -d '' f; do
  size=$(wc -c < "$f" 2>/dev/null || echo 0); size=$((size))
  if [ "$size" -gt 52428800 ]; then
    echo "::error file=$f::file is $size bytes (>50MB) — $size_remedy"
    fail=1
  elif [ "$size" -gt 10485760 ]; then
    echo "::warning file=$f::file is $size bytes (>10MB) — $size_remedy"
  fi
done < <(find . -path ./.git -prune -o -type f -print0)

# credential-shaped strings anywhere in the tree = hard fail.
# --binary-files=text so a NUL byte can't blind the scan; this script filters itself
# out by exact path, not by name — any other file called bones-check.sh is still scanned.
# The scan itself fails closed: "could not check" must never read as "clean".
#
# Pattern notes (false-positive classes found during the 2026-08 repo adoptions):
#   - `sk-…` requires a boundary before it — bare hyphenated words in article URLs
#     ("…kursk-oil-refinery-struck…") are prose, not OpenAI keys. A real key after
#     `=`, `"`, `:` or start-of-line still matches.
#   - URL creds pointing at loopback (127.0.0.1/localhost) are the standard local-dev
#     convention (docker-compose, bootstrap scripts) — allowlisted below, AFTER the
#     match, so the allowlist can only ever narrow what fails, never what is scanned.
cred_pattern='gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^A-Za-z0-9-])sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[A-Za-z0-9-]{10,}|hf_[A-Za-z0-9]{30,}|eyJ[A-Za-z0-9_-]{20,}\.eyJ|[a-z][a-z0-9+.-]*://[^/[:space:]:@]+:[^/[:space:]:@]{6,}@|-----BEGIN [A-Z ]*PRIVATE KEY-----'
# lines whose only offense is a loopback dev URL (or this script itself) are allowed.
# Known tradeoff: a line carrying BOTH a real token AND a loopback URL would be
# dropped — contrived enough to accept; the history scan still sees every commit.
scan_allow='^\./\.github/scripts/bones-check\.sh:|://[^@[:space:]]*@(127\.0\.0\.1|localhost)([:/[:space:]]|$)'
scan_raw="$(mktemp)" && scan_out="$(mktemp)" || { echo "::error::credential scan could not start (mktemp failed) — treating as FAILED, not clean"; exit 1; }
grep -rnE --binary-files=text --exclude-dir=.git "$cred_pattern" . > "$scan_raw"
rc_scan=$?
grep --binary-files=text -vE "$scan_allow" "$scan_raw" > "$scan_out"
rc_filter=$?
matches="$(cat "$scan_out")"
rm -f "$scan_raw" "$scan_out"
if [ "$rc_scan" -ge 2 ] || [ "$rc_filter" -ge 2 ]; then
  echo "::error::credential scan did not complete (grep exit $rc_scan/$rc_filter) — treating as FAILED, not clean"
  fail=1
elif [ "$rc_scan" -eq 0 ] && [ -n "$matches" ]; then
  printf '%s\n' "$matches"
  echo "::error::credential-shaped string found — ROTATE THAT CREDENTIAL NOW (treat it as burned), then ask a practitioner in Slack to help rewrite history. Rotating is the fix; scrubbing is cleanup. This happens to everyone."
  fail=1
fi
# rc_scan=1 (no match anywhere), or matches empty after the allowlist (self-hits /
# loopback dev URLs only) — clean. Both greps run standalone on regular files with
# --binary-files=text, so there is no pipeline stage left that could swallow lines.

# history scan (PR range only): a secret scrubbed from the tree can still live in a
# pushed commit — scan this PR's commits so a clean tree can't hide burned history.
# Same fail-closed rule: "could not check" must never read as "clean".
scope="tree only — history not scanned (shallow checkout or non-PR event)"
if [ "${GITHUB_EVENT_NAME:-}" = "pull_request" ] \
  && base=$(git rev-parse -q --verify "origin/${GITHUB_BASE_REF:-}" 2>/dev/null); then
  hist_raw="$(mktemp)" && hist_grep="$(mktemp)" && hist_out="$(mktemp)" || { echo "::error::history scan could not start (mktemp failed) — treating as FAILED, not clean"; exit 1; }
  git log -p "$base"..HEAD -- . ':(exclude).github/scripts/bones-check.sh' > "$hist_raw"
  rc_log=$?
  grep -nE --binary-files=text "$cred_pattern" "$hist_raw" > "$hist_grep"
  rc_hgrep=$?
  # same allowlist as the tree scan: loopback dev URLs are convention, not leaks
  grep --binary-files=text -vE '://[^@[:space:]]*@(127\.0\.0\.1|localhost)([:/[:space:]]|$)' "$hist_grep" > "$hist_out"
  rc_hfilter=$?
  hist_matches="$(cat "$hist_out")"
  rm -f "$hist_raw" "$hist_grep" "$hist_out"
  if [ "$rc_log" -ne 0 ] || [ "$rc_hgrep" -ge 2 ] || [ "$rc_hfilter" -ge 2 ]; then
    echo "::error::history scan did not complete (git log exit $rc_log, grep exit $rc_hgrep/$rc_hfilter) — treating as FAILED, not clean"
    fail=1
  elif [ "$rc_hgrep" -eq 0 ] && [ -n "$hist_matches" ]; then
    printf '%s\n' "$hist_matches"
    echo "::error::credential found in this PR's history even though the tree is clean — scrubbing did not kill it; ROTATE IT NOW (treat it as burned) and a practitioner will help rewrite history. Rotating is the fix."
    fail=1
  fi
  scope="tree + PR range"
fi

[ "$fail" -eq 0 ] && echo "bones-check: OK — scanned: $scope"
exit "$fail"

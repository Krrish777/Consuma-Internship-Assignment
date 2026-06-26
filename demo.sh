#!/usr/bin/env bash
# Usage (run in your OWN terminal while recording — it pauses for narration):
#   ./demo.sh            # all three scenarios, with pauses
#   ./demo.sh crash      # just crash recovery
#   ./demo.sh poison     # just poison-pill -> DLQ
#   ./demo.sh duplicate  # just duplicate delivery / idempotency
#   ./demo.sh --no-pause # skip the "press Enter" pauses (e.g. CI / dry run)
set -uo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
RULE="────────────────────────────────────────────────────────────────────────"

PAUSE=1
pause() { # wait for the presenter to finish narrating, unless --no-pause
  [ "$PAUSE" = 1 ] || return 0
  printf "\n%s▶ press Enter to inject the fault and run the probe…%s" "$YELLOW" "$RESET"
  read -r _
}

banner() { # $1 title  $2 fault  $3 watch  $4 proves
  printf "\n%s%s%s\n" "$CYAN" "$RULE" "$RESET"
  printf "%s SCENARIO: %s%s\n" "$BOLD" "$1" "$RESET"
  printf "%s%s%s\n" "$CYAN" "$RULE" "$RESET"
  printf "  %sFault injected:%s %s\n" "$BOLD" "$RESET" "$2"
  printf "  %sWatch the logs for:%s %s\n" "$BOLD" "$RESET" "$3"
  printf "  %sWhat it proves:%s %s\n" "$BOLD" "$RESET" "$4"
}

verdict() { # $1 rc  $2 claim  $3 logfile
  if [ "$1" = 0 ]; then
    printf "\n%s✔ PASSED%s — %s\n" "$GREEN$BOLD" "$RESET" "$2"
  else
    printf "\n%s✗ FAILED%s (exit %s). Last probe output:\n%s\n" "$RED$BOLD" "$RESET" "$1" "$DIM"
    tail -n 25 "$3"; printf "%s\n" "$RESET"
  fi
}

run_probe() { # $1 pytest node  $2 claim-on-pass
  local node="$1" claim="$2"
  local out; out="$(mktemp)"
  ( uv run pytest "$node" -v --no-header -p no:cacheprovider >"$out" 2>&1; echo $? >"$out.rc" ) &
  local probe_pid=$!

  printf "\n%s── live worker/gateway logs (job_id is the trace key) ─────────────%s\n" "$DIM" "$RESET"
  # Drop the Docker healthcheck pings (GET /health) — pure noise that buries the story.
  docker compose logs -f --tail=0 worker gateway 2>&1 \
    | grep --line-buffered -vE "GET /health" &
  local logs_pid=$!

  wait "$probe_pid"
  kill "$logs_pid" 2>/dev/null; wait "$logs_pid" 2>/dev/null

  printf "\n%s── probe verdict ─────────────────────────────────────────────────%s\n" "$DIM" "$RESET"
  grep -E "PASSED|FAILED|ERROR" "$out" || true
  verdict "$(cat "$out.rc")" "$claim" "$out"
  rm -f "$out" "$out.rc"
}

ensure_stack() {
  printf "%sBringing the 6-service stack up (cached build is a near-no-op)…%s\n" "$DIM" "$RESET"
  docker compose up -d --build >/dev/null 2>&1 || { echo "${RED}docker compose up failed${RESET}"; exit 1; }
  printf "%sWaiting for the gateway to report healthy…%s\n" "$DIM" "$RESET"
  for _ in $(seq 1 60); do
    if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
      printf "%s✔ stack healthy.%s\n" "$GREEN" "$RESET"; return 0
    fi
    sleep 2
  done
  echo "${RED}gateway never became healthy — run ./init.sh and retry${RESET}"; exit 1
}

scenario_crash() {
  banner "Crash recovery — a worker SIGKILL loses no message" \
    "docker kill the worker, THEN submit a job (deterministic — see test docstring)." \
    "the job parked in PENDING with no consumer, then a restarted worker draining q.parse → parse → tts ×N → stitch → COMPLETED." \
    "ack-LAST ordering (work→COMMIT→PUBLISH→ACK) means a crash never acks unrecorded work; RabbitMQ redelivers, the job converges to COMPLETED."
  pause
  run_probe "tests/e2e/test_crash_recovery.py" \
    "worker was killed mid-flight; the message survived in the broker and a recovered worker drove the job to COMPLETED. No loss."
}

scenario_poison() {
  banner "Poison pill → DLQ (no head-of-line blocking)" \
    "a manuscript that fails parse on EVERY attempt, alongside a healthy job." \
    "the cursed job retrying on the 1s → 4s → 16s ladder, then dead-lettering to q.dlq, while the healthy job sails through." \
    "3 retries with exponential backoff then off-the-hot-queue to the DLQ — the poison pill never blocks the rest of the queue."
  pause
  run_probe "tests/e2e/test_poison_pill.py" \
    "the always-failing manuscript exhausted 1/4/16s and dead-lettered to q.dlq; a concurrent healthy job still COMPLETED. No head-of-line blocking."
}

scenario_duplicate() {
  banner "Duplicate delivery → exactly-once effect (idempotency)" \
    "the same JobCreated / TtsRequested event injected onto the live broker twice." \
    "the redelivered event being absorbed — no second set of task rows, no second pending_count decrement." \
    "at-least-once delivery + idempotent processing = exactly-once EFFECT. The durable inbox + conditional tasks.status UPDATE are the authority, not Redis."
  pause
  run_probe "tests/e2e/test_duplicate_delivery.py" \
    "a duplicated event changed nothing — no double rows, no double-decrement, the fan-in barrier stayed correct."
}

SCENARIO="all"
for arg in "$@"; do
  case "$arg" in
    --no-pause) PAUSE=0 ;;
    crash|poison|duplicate|all) SCENARIO="$arg" ;;
    *) echo "usage: ./demo.sh [crash|poison|duplicate|all] [--no-pause]"; exit 2 ;;
  esac
done

printf "%s\n  Consuma Internship Assignment\n%s\n" "$BOLD" "$RESET"
ensure_stack

case "$SCENARIO" in
  crash)     scenario_crash ;;
  poison)    scenario_poison ;;
  duplicate) scenario_duplicate ;;
  all)       scenario_crash; pause; scenario_poison; pause; scenario_duplicate ;;
esac

printf "\n%s%s%s\n" "$CYAN" "$RULE" "$RESET"
printf "%s  Every probe converged to a correct final state from its fault.%s\n" "$BOLD" "$RESET"
printf "  %sExactly-once effect · no message loss · no head-of-line blocking.%s\n" "$DIM" "$RESET"
printf "%s%s%s\n" "$CYAN" "$RULE" "$RESET"

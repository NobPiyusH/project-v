"""In-pod layer of the GPU safety net: works with the laptop off and the control plane down. Port of PodSelfStop.

Every studio pod must be created with its command wrapped by wrap(). The wrapper starts a guard loop next to the job that
terminates the pod (via its own RUNPOD_POD_ID) when the absolute deadline passes (creation time + max runtime, baked in as
epoch seconds, so a container restart cannot reset the clock), when the pod is idle for the idle limit (the heartbeat file
was not touched and the GPU is under 5 % busy), or when the job command exits (the job must upload its results first).

Terminate, not stop: a stopped pod still bills its disk. It tries runpodctl (new and old syntax), then the REST API with the
pod's own RUNPOD_API_KEY, and stop only as a last resort; the guard repeats every minute until the pod is gone.
"""

from __future__ import annotations

from datetime import datetime

HEARTBEAT = "/tmp/projectv-heartbeat"
LOG = "/tmp/projectv-guard.log"

_EXIT_SCRIPT = r"""DEADLINE=@DEADLINE@; IDLE=@IDLE@; HB=@HB@
touch "$HB"
"$@" &
JOB=$!
endjob() {
  echo "$(date -u +%FT%TZ) projectv guard: $1, ending the worker (the control plane destroys the instance)"
  kill -TERM "$JOB" 2>/dev/null; sleep 20; kill -KILL "$JOB" 2>/dev/null
}
gpu_busy() {
  u=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1)
  [ -n "$u" ] && [ "$u" -ge 5 ]
}
(
  while kill -0 "$JOB" 2>/dev/null; do
    now=$(date +%s)
    if [ "$now" -ge "$DEADLINE" ]; then endjob "max runtime reached"; break; fi
    if gpu_busy; then touch "$HB"; fi
    last=$(stat -c %Y "$HB" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge "$IDLE" ]; then endjob "idle for $((now - last)) s"; break; fi
    sleep 30
  done
) >>@LOG@ 2>&1 &
wait "$JOB"
rc=$?
echo "$(date -u +%FT%TZ) projectv guard: worker ended (exit $rc)" >>@LOG@ 2>&1
exit "$rc"
"""

_TERMINATE_SCRIPT = r"""DEADLINE=@DEADLINE@; IDLE=@IDLE@; HB=@HB@
selfterm() {
  echo "$(date -u +%FT%TZ) projectv guard: $1, terminating pod $RUNPOD_POD_ID"
  runpodctl pod delete "$RUNPOD_POD_ID" \
    || runpodctl remove pod "$RUNPOD_POD_ID" \
    || curl -fsS -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID" \
    || runpodctl pod stop "$RUNPOD_POD_ID" \
    || runpodctl stop pod "$RUNPOD_POD_ID"
}
gpu_busy() {
  u=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1)
  [ -n "$u" ] && [ "$u" -ge 5 ]
}
touch "$HB"
(
  while :; do
    now=$(date +%s)
    if [ "$now" -ge "$DEADLINE" ]; then selfterm "max runtime reached"; sleep 60; continue; fi
    if gpu_busy; then touch "$HB"; fi
    last=$(stat -c %Y "$HB" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge "$IDLE" ]; then selfterm "idle for $((now - last)) s"; sleep 60; continue; fi
    sleep 30
  done
) >>@LOG@ 2>&1 &
"$@"
rc=$?
selfterm "job finished (exit $rc)" >>@LOG@ 2>&1
while :; do sleep 60; selfterm "still running after job end" >>@LOG@ 2>&1; done
"""


def _fill(template: str, deadline: int, idle_seconds: int) -> str:
    return (template.replace("@DEADLINE@", str(deadline)).replace("@IDLE@", str(idle_seconds))
            .replace("@HB@", HEARTBEAT).replace("@LOG@", LOG))


def _check(job_argv: list[str], max_runtime_minutes: int, idle_minutes: int) -> None:
    if not job_argv:
        raise ValueError("job command is empty")
    if max_runtime_minutes <= 0 or idle_minutes <= 0:
        raise ValueError("max runtime and idle minutes must be positive")


def wrap(job_argv: list[str], max_runtime_minutes: int, idle_minutes: int, created_at: datetime) -> list[str]:
    """The pod's container command in exec form (cmd of REST v2 create): bash -c <guard script> followed by the job's own
    argv, which the script runs as "$@" (no quoting of the job command needed)."""
    _check(job_argv, max_runtime_minutes, idle_minutes)
    deadline = int(created_at.timestamp()) + max_runtime_minutes * 60
    return ["bash", "-c", _fill(_TERMINATE_SCRIPT, deadline, idle_minutes * 60), "projectv-guard", *job_argv]


def wrap_exit(job_argv: list[str], max_runtime_minutes: int, idle_minutes: int, created_at: datetime) -> list[str]:
    """The guard for providers where the pod must hold no account key (Vast.ai). Same triggers (deadline, idle, job exit), but
    the action only ends the worker process, so the container exits. The real kill is the control plane's destroy call (broker
    release, PodWatchdog); an exited Vast instance still bills its disk until then."""
    _check(job_argv, max_runtime_minutes, idle_minutes)
    deadline = int(created_at.timestamp()) + max_runtime_minutes * 60
    return ["bash", "-c", _fill(_EXIT_SCRIPT, deadline, idle_minutes * 60), "projectv-guard", *job_argv]


def as_shell_string(argv: list[str]) -> str:
    """The same command as one shell string (for tools that take a single start-command string)."""
    return " ".join("'" + a.replace("'", "'\"'\"'") + "'" for a in argv)

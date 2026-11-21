package com.projectv.gpu;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

/**
 * In-pod layer of the GPU safety net: works with the laptop off and the control plane down. Every studio pod must be
 * created with its command wrapped by {@link #wrap}. The wrapper starts a guard loop next to the job that terminates
 * the pod (via its own {@code RUNPOD_POD_ID}) when:
 * <ul>
 *   <li>the absolute deadline passes (creation time + max runtime, baked in as epoch seconds, so a container restart
 *       cannot reset the clock),</li>
 *   <li>the pod is idle for the idle limit: the heartbeat file ({@value #HEARTBEAT}) was not touched and the GPU is
 *       under 5 % busy,</li>
 *   <li>the job command exits (the job must upload its results before it exits).</li>
 * </ul>
 * Terminate, not stop: a stopped pod still bills its disk. It tries runpodctl (new and old syntax), then the REST API
 * with the pod's own {@code RUNPOD_API_KEY}, and stop only as a last resort; the guard repeats every minute until the
 * pod is gone. RunPod's docs advise testing the command on the image first (older images ship an older runpodctl).
 */
public final class PodSelfStop {

    public static final String HEARTBEAT = "/tmp/projectv-heartbeat";
    static final String LOG = "/tmp/projectv-guard.log";

    private PodSelfStop() {
    }

    /**
     * The pod's container command in exec form ({@code cmd} of REST v2 create): {@code bash -c <guard script>} followed
     * by the job's own argv, which the script runs as {@code "$@"} (no quoting of the job command needed).
     */
    public static List<String> wrap(List<String> jobArgv, int maxRuntimeMinutes, int idleMinutes, Instant createdAt) {
        if (jobArgv == null || jobArgv.isEmpty()) {
            throw new IllegalArgumentException("job command is empty");
        }
        if (maxRuntimeMinutes <= 0 || idleMinutes <= 0) {
            throw new IllegalArgumentException("max runtime and idle minutes must be positive");
        }
        long deadline = createdAt.getEpochSecond() + maxRuntimeMinutes * 60L;
        List<String> argv = new ArrayList<>(List.of("bash", "-c", script(deadline, idleMinutes * 60L), "projectv-guard"));
        argv.addAll(jobArgv);
        return List.copyOf(argv);
    }

    /**
     * The guard for providers where the pod must hold no account key (Vast.ai). Same triggers (deadline, idle, job exit),
     * but the action only ends the worker process, so the container exits. The real kill is the control plane's destroy
     * call ({@code GpuBroker} release, {@code PodWatchdog}); an exited Vast instance still bills its disk until then.
     */
    public static List<String> wrapExit(List<String> jobArgv, int maxRuntimeMinutes, int idleMinutes, Instant createdAt) {
        if (jobArgv == null || jobArgv.isEmpty()) {
            throw new IllegalArgumentException("job command is empty");
        }
        if (maxRuntimeMinutes <= 0 || idleMinutes <= 0) {
            throw new IllegalArgumentException("max runtime and idle minutes must be positive");
        }
        long deadline = createdAt.getEpochSecond() + maxRuntimeMinutes * 60L;
        List<String> argv = new ArrayList<>(List.of("bash", "-c", exitScript(deadline, idleMinutes * 60L), "projectv-guard"));
        argv.addAll(jobArgv);
        return List.copyOf(argv);
    }

    static String exitScript(long deadlineEpoch, long idleSeconds) {
        return """
                DEADLINE=%d; IDLE=%d; HB=%s
                touch "$HB"
                "$@" &
                JOB=$!
                endjob() {
                  echo "$(date -u +%%FT%%TZ) projectv guard: $1, ending the worker (the control plane destroys the instance)"
                  kill -TERM "$JOB" 2>/dev/null; sleep 20; kill -KILL "$JOB" 2>/dev/null
                }
                gpu_busy() {
                  u=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1)
                  [ -n "$u" ] && [ "$u" -ge 5 ]
                }
                (
                  while kill -0 "$JOB" 2>/dev/null; do
                    now=$(date +%%s)
                    if [ "$now" -ge "$DEADLINE" ]; then endjob "max runtime reached"; break; fi
                    if gpu_busy; then touch "$HB"; fi
                    last=$(stat -c %%Y "$HB" 2>/dev/null || echo 0)
                    if [ $((now - last)) -ge "$IDLE" ]; then endjob "idle for $((now - last)) s"; break; fi
                    sleep 30
                  done
                ) >>%s 2>&1 &
                wait "$JOB"
                rc=$?
                echo "$(date -u +%%FT%%TZ) projectv guard: worker ended (exit $rc)" >>%s 2>&1
                exit "$rc"
                """.formatted(deadlineEpoch, idleSeconds, HEARTBEAT, LOG, LOG);
    }

    /** The same command as one shell string (for tools that take a single start-command string). */
    public static String asShellString(List<String> argv) {
        StringBuilder sb = new StringBuilder();
        for (String a : argv) {
            if (!sb.isEmpty()) {
                sb.append(' ');
            }
            sb.append('\'').append(a.replace("'", "'\"'\"'")).append('\'');
        }
        return sb.toString();
    }

    static String script(long deadlineEpoch, long idleSeconds) {
        return """
                DEADLINE=%d; IDLE=%d; HB=%s
                selfterm() {
                  echo "$(date -u +%%FT%%TZ) projectv guard: $1, terminating pod $RUNPOD_POD_ID"
                  runpodctl pod delete "$RUNPOD_POD_ID" \\
                    || runpodctl remove pod "$RUNPOD_POD_ID" \\
                    || curl -fsS -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID" \\
                    || runpodctl pod stop "$RUNPOD_POD_ID" \\
                    || runpodctl stop pod "$RUNPOD_POD_ID"
                }
                gpu_busy() {
                  u=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1)
                  [ -n "$u" ] && [ "$u" -ge 5 ]
                }
                touch "$HB"
                (
                  while :; do
                    now=$(date +%%s)
                    if [ "$now" -ge "$DEADLINE" ]; then selfterm "max runtime reached"; sleep 60; continue; fi
                    if gpu_busy; then touch "$HB"; fi
                    last=$(stat -c %%Y "$HB" 2>/dev/null || echo 0)
                    if [ $((now - last)) -ge "$IDLE" ]; then selfterm "idle for $((now - last)) s"; sleep 60; continue; fi
                    sleep 30
                  done
                ) >>%s 2>&1 &
                "$@"
                rc=$?
                selfterm "job finished (exit $rc)" >>%s 2>&1
                while :; do sleep 60; selfterm "still running after job end" >>%s 2>&1; done
                """.formatted(deadlineEpoch, idleSeconds, HEARTBEAT, LOG, LOG, LOG);
    }
}

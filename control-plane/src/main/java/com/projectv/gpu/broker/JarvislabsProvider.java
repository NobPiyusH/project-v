package com.projectv.gpu.broker;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/**
 * JarvisLabs adapter. JarvisLabs documents no REST API for instances, only the {@code jarvislabs} Python SDK, so every call goes
 * through {@code workers/jarvislabs_helper.py} (one JSON command per process; the key travels in the {@code JL_API_KEY}
 * environment variable of that process only). Same safety design as {@link E2eProvider} (docs/jarvislabs-provider-notes.md):
 * <ul>
 *   <li><b>No secret goes to the instance.</b> The startup script holds the worker env only (boot payload, port, root, per-rental worker
 *       token). Never the JarvisLabs key or any other key.</li>
 *   <li><b>Teardown is ours.</b> {@link #terminate} destroys the instance (pause would keep billing storage, and a resumed instance can
 *       get a new id). The in-instance guard only ends the worker ({@link com.projectv.gpu.PodSelfStop#wrapExit}). The watchdog lists
 *       instances by the studio name prefix.</li>
 * </ul>
 * NOT verified live (no key was used): the helper's output fields, GPU type names, how the instance endpoint is addressed, whether a
 * container template can run the worker, the startup-script size limit. The adapter stays {@code proven=false} until the canary passes.
 */
@Component
public class JarvislabsProvider implements GpuProvider {

    private static final Logger log = LoggerFactory.getLogger(JarvislabsProvider.class);
    private static final Pattern SAFE_ENV = Pattern.compile("[A-Za-z0-9+/=._:@-]*");

    /** Runs one helper command; returns its stdout (JSON). Tests replace it, so nothing touches the network. */
    @FunctionalInterface
    interface Runner {
        String run(List<String> args, String stdin);
    }

    private final JarvislabsConfig cfg;
    private final String apiKey;
    private final Runner runner;
    private final ObjectMapper json = JsonMapper.builder().build();
    private final Map<String, Instant> firstSeen = new ConcurrentHashMap<>();
    private final Map<String, Double> hourly = new ConcurrentHashMap<>();
    private volatile String lastWhy = "no GPU passes the filter";
    private volatile long cachedAt;
    private volatile List<Quote> cached = List.of();

    @Autowired
    public JarvislabsProvider(JarvislabsConfig cfg, @Value("${projectv.jarvislabs.api-key:}") String apiKey,
                              @Value("${projectv.jarvislabs.command:uv run --with jarvislabs python}") String command,
                              @Value("${projectv.jarvislabs.helper:workers/jarvislabs_helper.py}") String helper) {
        this(cfg, apiKey, processRunner(apiKey, command, helper, cfg.startTimeoutSeconds() + 60));
    }

    JarvislabsProvider(JarvislabsConfig cfg, String apiKey, Runner runner) {
        this.cfg = cfg;
        this.apiKey = apiKey == null ? "" : apiKey.strip();
        this.runner = runner;
    }

    @Override
    public String provider() {
        return "jarvislabs";
    }

    @Override
    public boolean configured() {
        return !apiKey.isEmpty();
    }

    /** Off until the canary has run a real worker. */
    @Override
    public boolean enabledByDefault() {
        return false;
    }

    @Override
    public List<String> guard(List<String> jobArgv, int maxRuntimeMinutes, int idleMinutes, Instant createdAt) {
        return com.projectv.gpu.PodSelfStop.wrapExit(jobArgv, maxRuntimeMinutes, idleMinutes, createdAt);
    }

    // ------------------------------------------------------------------------------------------------ preflight

    @Override
    public Preflight preflight(GpuSpec spec, CreateRequest probe) {
        if (!configured()) {
            return new Preflight(false, List.of("not configured (JARVISLABS_API_KEY missing)"));
        }
        List<String> checks = new ArrayList<>();
        boolean ok = true;
        try {
            String script = startScript(probe);
            checks.add("startup script " + script.length() + " bytes (limit used here " + cfg.scriptMaxBytes() + "; JarvisLabs publishes none)");
            if (script.length() > cfg.scriptMaxBytes()) {
                ok = false;
                checks.add("FAIL: startup script too large");
            }
        } catch (RuntimeException e) {
            ok = false;
            checks.add("FAIL: startup script cannot be built: " + e.getMessage());
        }
        if (probe.name().length() > 40) {
            ok = false;
            checks.add("FAIL: instance name longer than 40 characters");
        }
        try {
            Map<?, ?> b = asMap(call(List.of("balance"), null));
            double bal = num(b.get("balance"));
            checks.add("read-only balance: " + bal + " " + str(b.get("currency")) + " (min " + cfg.minBalance() + ")");
            if (bal < cfg.minBalance()) {
                ok = false;
                checks.add("FAIL: balance below the minimum; a zero balance pauses instances and can delete their data");
            }
        } catch (RuntimeException e) {
            ok = false;
            checks.add("FAIL: balance check: " + e.getMessage());
        }
        List<Quote> q = quotes(spec);
        if (q.isEmpty()) {
            ok = false;
            checks.add("FAIL: read-only GPU list shows nothing rentable: " + lastWhy);
        } else {
            checks.add("read-only GPU list: " + q.size() + " option(s), cheapest " + q.getFirst().gpu() + " $%.2f/h".formatted(q.getFirst().hourlyUsd()));
        }
        return new Preflight(ok, checks);
    }

    // ------------------------------------------------------------------------------------------------ quote

    @Override
    public Quote quote(GpuSpec spec) {
        List<Quote> all = quotes(spec);
        return all.isEmpty() ? Quote.unavailable("jarvislabs", lastWhy) : all.getFirst();
    }

    @Override
    public List<Quote> quotes(GpuSpec spec) {
        long now = System.currentTimeMillis();
        if (now - cachedAt < cfg.cacheSeconds() * 1000L && !cached.isEmpty()) {
            return cached;
        }
        List<Quote> fresh = search(spec);
        cached = fresh;
        cachedAt = fresh.isEmpty() ? 0 : now;
        return fresh;
    }

    private List<Quote> search(GpuSpec spec) {
        List<?> rows = asList(call(List.of("gpus"), null));
        List<Quote> out = new ArrayList<>();
        Map<String, String> why = new LinkedHashMap<>();
        for (Object o : rows) {
            if (!(o instanceof Map<?, ?> row)) {
                continue;
            }
            String type = str(row.get("gpu_type"));
            JarvislabsConfig.Gpu allowed = cfg.gpus().stream().filter(g -> g.type().equalsIgnoreCase(type)).findFirst().orElse(null);
            if (allowed == null) {
                continue;
            }
            String kind = str(row.get("workload_type"));
            double price = toUsd(num(row.get("price_per_hour")), str(row.get("currency")));
            String reject = allowed.vramGb() < spec.minVramGb() ? "too little VRAM" : num(row.get("free")) < 1 ? "none free"
                    : price <= 0 ? "no live price" : !kind.isEmpty() && !kind.equalsIgnoreCase("container") ? "not a container template GPU" : null;
            if (reject != null) {
                why.put(type + ": " + reject, "");
                continue;
            }
            String region = str(row.get("region"));
            out.add(new Quote("jarvislabs", true, "FREE " + (int) num(row.get("free")), type + "|" + region, price,
                    "%s in %s, $%.2f/h (live list converted from the account currency; GST not confirmed in it)".formatted(type, region, price)));
        }
        out.sort(Comparator.comparingDouble(Quote::hourlyUsd));
        lastWhy = !out.isEmpty() ? "" : why.isEmpty() ? "no allowed GPU in the JarvisLabs list" : "every GPU was rejected: " + why.keySet();
        return out;
    }

    // ------------------------------------------------------------------------------------------------ create

    @Override
    public Instance create(CreateRequest req) {
        if (!req.name().startsWith(E2eProvider.LABEL_PREFIX)) {
            throw new IllegalArgumentException("instance name must carry the studio prefix so the watchdog can see it");
        }
        String[] g = req.quote().gpu().split("\\|");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", req.name());
        body.put("gpu_type", g[0]);
        if (g.length > 1 && !g[1].isBlank()) {
            body.put("region", g[1]);
        }
        body.put("storage", Math.max(req.diskGb(), 40));
        body.put("template", cfg.template());
        body.put("http_ports", Integer.toString(cfg.servicePort()));
        body.put("script", startScript(req));
        Map<?, ?> r = asMap(call(List.of("create"), json.writeValueAsString(body)));
        String id = idOf(r);
        if (id == null) {
            throw new IllegalStateException("JarvisLabs create returned no machine_id");
        }
        firstSeen.put(id, Instant.now());
        hourly.put(id, req.quote().hourlyUsd());
        return new Instance("jarvislabs", id, req.name(), req.quote().hourlyUsd(), g[0], endpointOf(r));
    }

    /**
     * Bash startup script: exports the worker env (checked against a safe character set) and starts the worker in the background.
     * The worker is told to listen on the service port JarvisLabs publishes.
     */
    String startScript(CreateRequest req) {
        StringBuilder sb = new StringBuilder("#!/bin/bash\numask 077\n");
        if (req.env() != null) {
            for (Map.Entry<String, String> e : req.env().entrySet()) {
                if (e.getKey().isEmpty() || !SAFE_ENV.matcher(e.getKey()).matches() || !SAFE_ENV.matcher(e.getValue()).matches()) {
                    throw new IllegalArgumentException("env " + e.getKey() + " has characters that cannot go into the startup script");
                }
                if (!e.getKey().equals("SV_PORT")) {
                    sb.append("export ").append(e.getKey()).append('=').append(e.getValue()).append('\n');
                }
            }
        }
        sb.append("export SV_PORT=").append(cfg.servicePort()).append('\n').append("nohup setsid");
        for (String a : req.bootArgv()) {
            sb.append(' ').append(E2eProvider.shellQuote(a));
        }
        return sb.append(" > /tmp/projectv-worker.log 2>&1 &\n").toString();
    }

    // ------------------------------------------------------------------------------------------------ status / list / delete

    @Override
    public InstanceStatus status(String instanceId) {
        Map<?, ?> m;
        try {
            m = asMap(call(List.of("get", instanceId), null));
        } catch (NotFound e) {
            return new InstanceStatus(State.GONE, null, "not found");
        }
        String st = str(m.get("status"));
        String ep = endpointOf(m);
        if (st.equalsIgnoreCase("Running")) {
            return ep == null ? new InstanceStatus(State.STARTING, null, "running, no endpoint yet") : new InstanceStatus(State.RUNNING, ep, st);
        }
        String low = st.toLowerCase();
        if (low.contains("fail") || low.contains("destroy") || low.contains("delet") || low.contains("pause")) {
            return new InstanceStatus(State.GONE, null, st); // a paused instance still bills storage: the broker destroys it
        }
        Instant born = firstSeen.computeIfAbsent(instanceId, k -> Instant.now());
        if (Instant.now().isAfter(born.plusSeconds(cfg.startTimeoutSeconds() * 3L))) {
            return new InstanceStatus(State.GONE, null, "never became running (" + st + ")");
        }
        return new InstanceStatus(State.STARTING, null, st);
    }

    @Override
    public String endpointFor(String instanceId, int port) {
        try {
            return endpointOf(asMap(call(List.of("get", instanceId), null)));
        } catch (RuntimeException e) {
            log.warn("JarvisLabs: could not read the address of instance {}: {}", instanceId, e.getMessage());
            return null;
        }
    }

    /** All instances of the account (the watchdog filters by the studio name prefix). */
    @Override
    public List<Pod> listPods() {
        List<Pod> out = new ArrayList<>();
        for (Object o : asList(call(List.of("list"), null))) {
            if (o instanceof Map<?, ?> m) {
                String id = idOf(m);
                if (id == null) {
                    continue;
                }
                Instant born = firstSeen.computeIfAbsent(id, k -> Instant.now()); // the SDK has no creation time
                Double known = hourly.get(id);
                out.add(new Pod(id, str(m.get("name")), str(m.get("status")).isEmpty() ? "LOADING" : str(m.get("status")).toUpperCase(),
                        known != null ? known : num(m.get("cost")), born, born));
            }
        }
        return out;
    }

    @Override
    public void terminate(String podId) {
        try {
            call(List.of("destroy", Long.toString(Long.parseLong(podId))), null);
        } catch (NotFound e) {
            // already gone
        }
        firstSeen.remove(podId);
        hourly.remove(podId);
    }

    // ------------------------------------------------------------------------------------------------ helpers

    private static final class NotFound extends RuntimeException {
        NotFound(String m) {
            super(m);
        }
    }

    private Object call(List<String> args, String stdin) {
        String out;
        try {
            out = runner.run(args, stdin);
        } catch (RuntimeException e) {
            throw new IllegalStateException("JarvisLabs helper failed: " + scrub(String.valueOf(e.getMessage())));
        }
        Object parsed;
        try {
            parsed = json.readValue(out == null ? "" : out.strip(), Object.class);
        } catch (RuntimeException e) {
            throw new IllegalStateException("JarvisLabs helper returned no JSON: " + scrub(abbreviate(out)));
        }
        if (parsed instanceof Map<?, ?> m && m.get("error") != null) {
            String type = str(m.get("type"));
            String msg = "JarvisLabs refused the call (" + type + "): " + scrub(abbreviate(str(m.get("error"))));
            throw type.equals("NotFoundError") ? new NotFound(msg) : new IllegalStateException(msg);
        }
        return parsed;
    }

    private static List<?> asList(Object o) {
        if (o instanceof List<?> l) {
            return l;
        }
        throw new IllegalStateException("JarvisLabs helper returned an object where a list was expected");
    }

    private static Map<?, ?> asMap(Object o) {
        if (o instanceof Map<?, ?> m) {
            return m;
        }
        throw new IllegalStateException("JarvisLabs helper returned a list where an object was expected");
    }

    private static String idOf(Map<?, ?> m) {
        return m.get("machine_id") instanceof Number n ? Long.toString(n.longValue()) : null;
    }

    /** The first listed endpoint, else null. The format is not documented: a bare host gets https://. */
    private String endpointOf(Map<?, ?> m) {
        if (m.get("endpoints") instanceof List<?> l && !l.isEmpty() && l.getFirst() != null) {
            String e = l.getFirst().toString().strip().replaceAll("/+$", "");
            return e.isEmpty() ? null : e.contains("://") ? e : "https://" + e;
        }
        return null;
    }

    private String scrub(String s) {
        return apiKey.isEmpty() ? s : s.replace(apiKey, "***");
    }

    /** The live list is in the account currency; INR (the owner's account) is converted with the configured rate. */
    private double toUsd(double amount, String currency) {
        return "INR".equalsIgnoreCase(currency) ? amount / cfg.inrPerUsd() : amount;
    }

    private static String abbreviate(String s) {
        return s == null ? "" : s.length() > 200 ? s.substring(0, 200) : s;
    }

    private static double num(Object o) {
        return o instanceof Number n ? n.doubleValue() : o instanceof String s && s.matches("[0-9.]+") ? Double.parseDouble(s) : 0;
    }

    private static String str(Object o) {
        return o == null ? "" : o.toString();
    }

    /** Real runner: {@code <command> <helper> <args...>}, key in the child's environment only, killed after the timeout. */
    static Runner processRunner(String apiKey, String command, String helper, int timeoutSeconds) {
        return (args, stdin) -> {
            Path script = Path.of(helper);
            if (!Files.isRegularFile(script) && Files.isRegularFile(Path.of("..").resolve(helper))) {
                script = Path.of("..").resolve(helper);
            }
            List<String> cmd = new ArrayList<>(List.of(command.strip().split("\\s+")));
            cmd.add(script.toString());
            cmd.addAll(args);
            ProcessBuilder pb = new ProcessBuilder(cmd).redirectError(ProcessBuilder.Redirect.DISCARD);
            pb.environment().put("JL_API_KEY", apiKey == null ? "" : apiKey.strip());
            try {
                Process p = pb.start();
                if (stdin != null) {
                    p.getOutputStream().write(stdin.getBytes(StandardCharsets.UTF_8));
                }
                p.getOutputStream().close();
                if (!p.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
                    p.destroyForcibly();
                    throw new IllegalStateException("helper timed out after " + timeoutSeconds + " s");
                }
                return new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            } catch (IOException e) {
                throw new IllegalStateException("helper could not start: " + e.getMessage());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("interrupted");
            }
        };
    }
}

package com.projectv.gpu;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.regex.Pattern;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * One bounded read of a pod's log stream for the dashboard's side panel. RunPod serves logs as Server-Sent Events
 * ({@code GET /v2/pods/{id}/logs}, {@code data: {"ts","source","line"}}, checked 20 Nov 2026 against the v2 OpenAPI
 * document). The stream never ends by itself and the endpoint sometimes stalls, so each call reads for at most
 * {@code maxWait}, stops early once the stream goes quiet, and never blocks longer. The key stays on the server.
 */
@Component
public class PodLogs {

    public record Line(String ts, String source, String line) {
    }

    /** {@code next} is the timestamp to pass as {@code since} on the following call (null when nothing came back). */
    public record Page(List<Line> lines, String next, String error) {
    }

    private static final Pattern POD_ID = Pattern.compile("[A-Za-z0-9_-]{4,64}");
    static final int MAX_LINE_CHARS = 2000;
    static final int MAX_LINES = 400;

    private final String baseUrl;
    private final String apiKey;
    private final HttpClient http;
    private final ObjectMapper json = new ObjectMapper();
    private final long maxWaitMs;
    private final long quietMs;

    @Autowired
    public PodLogs(@Value("${projectv.runpod.base-url:https://api.runpod.io/v2}") String baseUrl,
                   @Value("${projectv.runpod.api-key:}") String apiKey) {
        this(baseUrl, apiKey, 4000, 700);
    }

    PodLogs(String baseUrl, String apiKey, long maxWaitMs, long quietMs) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey == null ? "" : apiKey.strip();
        this.maxWaitMs = maxWaitMs;
        this.quietMs = quietMs;
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    }

    public boolean configured() {
        return !apiKey.isEmpty();
    }

    public static boolean validPodId(String id) {
        return id != null && POD_ID.matcher(id).matches();
    }

    /** @param since RFC3339 timestamp from the previous page, or null for the last {@code tail} lines */
    public Page read(String podId, String since, int tail) {
        if (!configured()) {
            return new Page(List.of(), null, "No RunPod key is configured on the control plane");
        }
        if (!validPodId(podId)) {
            return new Page(List.of(), null, "Not a valid pod id");
        }
        String query = since != null && !since.isBlank()
                ? "since=" + URLEncoder.encode(since, StandardCharsets.UTF_8)
                : "tail=" + Math.max(0, Math.min(tail, 1000));
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + "/pods/" + podId + "/logs?" + query))
                .header("Authorization", "Bearer " + apiKey)
                .header("Accept", "text/event-stream")
                .timeout(Duration.ofMillis(maxWaitMs))
                .GET().build();

        List<Line> lines = new CopyOnWriteArrayList<>();
        long[] lastData = {System.nanoTime()};
        boolean[] failed = {false};
        String[] error = {null};
        InputStream[] stream = {null};
        Thread reader = Thread.ofVirtual().start(() -> {
            try {
                HttpResponse<InputStream> r = http.send(req, HttpResponse.BodyHandlers.ofInputStream());
                if (r.statusCode() / 100 != 2) {
                    error[0] = "RunPod answered " + r.statusCode();
                    failed[0] = true;
                    r.body().close();
                    return;
                }
                stream[0] = r.body();
                try (BufferedReader in = new BufferedReader(new InputStreamReader(r.body(), StandardCharsets.UTF_8))) {
                    String l;
                    while ((l = in.readLine()) != null && lines.size() < MAX_LINES) {
                        if (l.startsWith("data:")) {
                            Line parsed = parse(l.substring(5).strip());
                            if (parsed != null) {
                                lines.add(parsed);
                                lastData[0] = System.nanoTime();
                            }
                        }
                    }
                }
            } catch (IOException e) {
                if (lines.isEmpty() && error[0] == null) {
                    error[0] = "RunPod log stream: " + e.getClass().getSimpleName();
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        });

        long start = System.nanoTime();
        try {
            while (reader.isAlive()) {
                Thread.sleep(50);
                long now = System.nanoTime();
                boolean quiet = !lines.isEmpty() && (now - lastData[0]) / 1_000_000 > quietMs;
                if (quiet || (now - start) / 1_000_000 > maxWaitMs) {
                    break;
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            reader.interrupt();
            try {
                if (stream[0] != null) {
                    stream[0].close(); // unblocks the reader's readLine
                }
            } catch (IOException ignored) {
                // closing a stream we are done with
            }
        }
        List<Line> out = new ArrayList<>(lines);
        if (out.isEmpty()) {
            // A quiet pod is normal; only report a failure the reader actually saw, or the wait running out with no data
            return new Page(out, since, failed[0] || error[0] != null ? error[0] : null);
        }
        return new Page(out, out.get(out.size() - 1).ts(), null);
    }

    private Line parse(String data) {
        try {
            JsonNode n = json.readTree(data);
            String line = n.path("line").asText("");
            if (line.length() > MAX_LINE_CHARS) {
                line = line.substring(0, MAX_LINE_CHARS) + "…";
            }
            return new Line(n.path("ts").asText(""), n.path("source").asText("container"), line);
        } catch (RuntimeException e) { // a malformed event is skipped, not fatal
            return null;
        }
    }
}

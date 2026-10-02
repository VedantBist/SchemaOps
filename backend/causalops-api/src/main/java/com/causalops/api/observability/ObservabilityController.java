package com.causalops.api.observability;

import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;
import java.time.Instant;
import java.util.*;

/** Live logs (Loki) and traces (Tempo) of an environment, read from the source on each request. */
@RestController
@RequestMapping("/api")
public class ObservabilityController {

    private final EnvironmentService environments;
    private final RestClient http;

    public ObservabilityController(EnvironmentService environments, RestClient http) {
        this.environments = environments;
        this.http = http;
    }

    @GetMapping("/logs")
    public List<Map<String, Object>> logs(@RequestParam(required = false) UUID environmentId,
                                          @RequestParam(required = false) String service,
                                          @RequestParam(required = false) String contains,
                                          @RequestParam(required = false) String traceId,
                                          @RequestParam(defaultValue = "15") int minutes,
                                          @RequestParam(defaultValue = "200") int limit) {
        Environment env = environments.resolve(environmentId);
        String loki = require(env.config().endpoints().lokiUrl(), "lokiUrl");
        String selector = service == null ? "{service_name=~\".+\"}" : "{service_name=\"" + logQlString(service) + "\"}";
        StringBuilder query = new StringBuilder(selector);
        if (contains != null && !contains.isBlank()) query.append(" |= \"").append(logQlString(contains)).append('"');
        if (traceId != null && !traceId.isBlank()) query.append(" | trace_id=\"").append(logQlString(traceId)).append('"');
        long end = Instant.now().toEpochMilli() * 1_000_000L;
        long start = end - Math.min(Math.max(minutes, 1), 24 * 60) * 60_000_000_000L;
        URI uri = UriComponentsBuilder.fromUriString(loki).path("/loki/api/v1/query_range")
                .queryParam("query", "{q}").queryParam("start", start).queryParam("end", end)
                .queryParam("limit", Math.min(Math.max(limit, 1), 5000)).queryParam("direction", "backward")
                .buildAndExpand(query.toString()).encode().toUri();
        JsonNode body = get(uri, "Loki");

        List<Map<String, Object>> out = new ArrayList<>();
        for (JsonNode stream : body.path("data").path("result")) {
            JsonNode labels = stream.path("stream");
            for (JsonNode v : stream.path("values")) {
                Map<String, Object> line = new LinkedHashMap<>();
                long ns = Long.parseLong(v.path(0).asText());
                line.put("timestamp", Instant.ofEpochSecond(0, ns).toString());
                line.put("service", labels.path("service_name").asText(null));
                line.put("level", firstText(labels, "severity_text", "detected_level", "level"));
                line.put("traceId", firstText(labels, "trace_id", "traceid"));
                line.put("spanId", firstText(labels, "span_id", "spanid"));
                line.put("message", v.path(1).asText());
                out.add(line);
            }
        }
        out.sort(Comparator.comparing((Map<String, Object> m) -> (String) m.get("timestamp")).reversed());
        return out.size() > limit ? out.subList(0, limit) : out;
    }

    @GetMapping("/traces")
    public List<Map<String, Object>> traces(@RequestParam(required = false) UUID environmentId,
                                            @RequestParam(required = false) String service,
                                            @RequestParam(required = false) Integer minDurationMs,
                                            @RequestParam(defaultValue = "15") int minutes,
                                            @RequestParam(defaultValue = "50") int limit) {
        Environment env = environments.resolve(environmentId);
        String tempo = require(env.config().endpoints().tempoUrl(), "tempoUrl");
        long end = Instant.now().getEpochSecond();
        long start = end - Math.min(Math.max(minutes, 1), 24 * 60) * 60L;
        var b = UriComponentsBuilder.fromUriString(tempo).path("/api/search")
                .queryParam("start", start).queryParam("end", end).queryParam("limit", Math.min(Math.max(limit, 1), 500));
        if (service != null) b.queryParam("tags", "service.name=" + service);
        if (minDurationMs != null) b.queryParam("minDuration", minDurationMs + "ms");
        JsonNode body = get(b.encode().build().toUri(), "Tempo");

        List<Map<String, Object>> out = new ArrayList<>();
        for (JsonNode t : body.path("traces")) {
            Map<String, Object> trace = new LinkedHashMap<>();
            trace.put("traceId", t.path("traceID").asText());
            trace.put("rootService", t.path("rootServiceName").asText(null));
            trace.put("rootOperation", t.path("rootTraceName").asText(null));
            trace.put("startTime", Instant.ofEpochSecond(0, Long.parseLong(t.path("startTimeUnixNano").asText("0"))).toString());
            trace.put("durationMs", t.path("durationMs").isMissingNode() ? 0 : t.path("durationMs").asInt());
            out.add(trace);
        }
        return out;
    }

    /** The full trace in OTLP JSON, as Tempo returns it. */
    @GetMapping(value = "/traces/{traceId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> trace(@RequestParam(required = false) UUID environmentId, @PathVariable String traceId) {
        if (!traceId.matches("[0-9a-fA-F]{16,32}")) throw new IllegalArgumentException("traceId must be 16-32 hex characters");
        Environment env = environments.resolve(environmentId);
        String tempo = require(env.config().endpoints().tempoUrl(), "tempoUrl");
        return ResponseEntity.ok(get(URI.create(tempo + "/api/traces/" + traceId), "Tempo"));
    }

    private JsonNode get(URI uri, String system) {
        try {
            JsonNode body = http.get().uri(uri).accept(MediaType.APPLICATION_JSON).retrieve().body(JsonNode.class);
            if (body == null) throw new UpstreamException(system + " returned an empty response");
            return body;
        } catch (RestClientException e) {
            throw new UpstreamException(system + " request failed: " + e.getMessage());
        }
    }

    private static String require(String url, String name) {
        if (url == null || url.isBlank()) throw new IllegalStateException("Environment has no " + name + " configured");
        return url;
    }

    private static String logQlString(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static String firstText(JsonNode node, String... names) {
        for (String n : names) {
            JsonNode v = node.path(n);
            if (!v.isMissingNode() && !v.asText().isEmpty()) return v.asText();
        }
        return null;
    }

    public static class UpstreamException extends RuntimeException {
        public UpstreamException(String message) {
            super(message);
        }
    }
}

package com.causalops.api.service;

import com.causalops.api.dto.FaultRequest;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Applies faults to the reference system through real mechanisms:
 * <ul>
 *   <li>DB_LATENCY / NETWORK_LATENCY: a Toxiproxy latency toxic on the real network link,</li>
 *   <li>SERVICE_LATENCY / ERROR_RATE / SERVICE_FAILURE / CONNECTION_POOL_SATURATION: the target
 *       service's token-protected chaos endpoint, which affects its actual request handling.</li>
 * </ul>
 * Targets and links come from configuration ({@code causalops.chaos.*}); nothing is simulated here.
 */
@Component
public class FaultInjector {

    private final RestClient http;
    private final ChaosProperties props;

    public FaultInjector(RestClient http, ChaosProperties props) {
        this.http = http;
        this.props = props;
    }

    public void inject(UUID faultId, FaultRequest r) {
        Map<String, Object> p = r.parameters() == null ? Map.of() : r.parameters();
        switch (r.type()) {
            case "DB_LATENCY" -> addLatencyToxic(requireLink(props.dbLinks(), r.target(), r.type()), faultId,
                    requireNumber(p, "latencyMs"), optionalNumber(p, "jitterMs"));
            case "NETWORK_LATENCY" -> addLatencyToxic(requireLink(props.inboundLinks(), r.target(), r.type()), faultId,
                    requireNumber(p, "latencyMs"), optionalNumber(p, "jitterMs"));
            case "SERVICE_LATENCY", "ERROR_RATE", "SERVICE_FAILURE", "CONNECTION_POOL_SATURATION" -> {
                Map<String, Object> body = new LinkedHashMap<>();
                body.put("type", r.type());
                body.put("durationSeconds", r.durationSeconds());
                if ("SERVICE_LATENCY".equals(r.type())) body.put("latencyMs", (long) requireNumber(p, "latencyMs"));
                if ("ERROR_RATE".equals(r.type())) {
                    // "errorRate" is the key used by the existing experiment configs.
                    body.put("errorRatePct", requireNumber(p, p.containsKey("errorRatePct") ? "errorRatePct" : "errorRate"));
                }
                if (p.containsKey("holdConnections")) body.put("holdConnections", (int) requireNumber(p, "holdConnections"));
                String url = serviceUrl(r.target());
                call(() -> http.post().uri(url + "/internal/chaos")
                        .header("X-Chaos-Token", props.token())
                        .contentType(MediaType.APPLICATION_JSON).body(body)
                        .retrieve().toBodilessEntity(), r.target());
            }
            default -> throw new IllegalArgumentException("Unsupported fault type " + r.type());
        }
    }

    public void stop(UUID faultId, String type, String target) {
        switch (type) {
            case "DB_LATENCY" -> removeToxic(requireLink(props.dbLinks(), target, type), faultId);
            case "NETWORK_LATENCY" -> removeToxic(requireLink(props.inboundLinks(), target, type), faultId);
            default -> {
                String url = serviceUrl(target);
                call(() -> http.delete().uri(url + "/internal/chaos")
                        .header("X-Chaos-Token", props.token())
                        .retrieve().toBodilessEntity(), target);
            }
        }
    }

    private void addLatencyToxic(String proxy, UUID faultId, double latencyMs, Double jitterMs) {
        Map<String, Object> attributes = new LinkedHashMap<>();
        attributes.put("latency", (long) latencyMs);
        attributes.put("jitter", jitterMs == null ? 0 : jitterMs.longValue());
        Map<String, Object> toxic = Map.of(
                "name", toxicName(faultId), "type", "latency", "stream", "downstream",
                "toxicity", 1.0, "attributes", attributes);
        call(() -> http.post().uri(props.toxiproxyUrl() + "/proxies/{proxy}/toxics", proxy)
                .contentType(MediaType.APPLICATION_JSON).body(toxic)
                .retrieve().toBodilessEntity(), proxy);
    }

    private void removeToxic(String proxy, UUID faultId) {
        try {
            http.delete().uri(props.toxiproxyUrl() + "/proxies/{proxy}/toxics/{name}", proxy, toxicName(faultId))
                    .retrieve().toBodilessEntity();
        } catch (RestClientResponseException e) {
            if (e.getStatusCode().value() != 404) throw new IllegalStateException("Toxiproxy refused to remove toxic on " + proxy, e);
        }
    }

    private static String toxicName(UUID faultId) {
        return "causalops-" + faultId;
    }

    private String serviceUrl(String target) {
        String url = props.serviceUrls().get(target);
        if (url == null) throw new IllegalArgumentException("No chaos control configured for target " + target);
        return url;
    }

    private static String requireLink(Map<String, String> links, String target, String type) {
        String proxy = links.get(target);
        if (proxy == null) throw new IllegalArgumentException(type + " is not configured for target " + target);
        return proxy;
    }

    private static double requireNumber(Map<String, Object> p, String key) {
        Object v = p.get(key);
        if (!(v instanceof Number n) || n.doubleValue() <= 0) {
            throw new IllegalArgumentException("parameters." + key + " must be a positive number");
        }
        return n.doubleValue();
    }

    private static Double optionalNumber(Map<String, Object> p, String key) {
        return p.get(key) instanceof Number n ? n.doubleValue() : null;
    }

    private static void call(Runnable request, String where) {
        try {
            request.run();
        } catch (RestClientResponseException e) {
            if (e.getStatusCode().is4xxClientError()) {
                throw new IllegalArgumentException("Fault rejected by " + where + ": " + e.getResponseBodyAsString(), e);
            }
            throw new IllegalStateException("Fault control failed at " + where + ": HTTP " + e.getStatusCode().value(), e);
        } catch (IllegalArgumentException e) {
            throw e;
        } catch (RuntimeException e) {
            throw new IllegalStateException("Fault control unreachable at " + where + ": " + e.getMessage(), e);
        }
    }

    @ConfigurationProperties(prefix = "causalops.chaos")
    public record ChaosProperties(
            String token,
            String toxiproxyUrl,
            Map<String, String> serviceUrls,
            Map<String, String> dbLinks,
            Map<String, String> inboundLinks) {
        public ChaosProperties {
            serviceUrls = serviceUrls == null ? Map.of() : serviceUrls;
            dbLinks = dbLinks == null ? Map.of() : dbLinks;
            inboundLinks = inboundLinks == null ? Map.of() : inboundLinks;
        }
    }
}

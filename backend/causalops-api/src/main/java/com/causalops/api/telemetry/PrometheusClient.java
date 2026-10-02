package com.causalops.api.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Minimal Prometheus HTTP API client for instant vector queries. */
@Component
public class PrometheusClient {

    public record Sample(Map<String, String> labels, double value) {
    }

    private final RestClient http;

    public PrometheusClient(RestClient http) {
        this.http = http;
    }

    /** Runs an instant query; NaN and infinite values are dropped (they carry no measurement). */
    public List<Sample> query(String baseUrl, String promql) {
        URI uri = UriComponentsBuilder.fromUriString(baseUrl).path("/api/v1/query")
                .queryParam("query", "{q}").buildAndExpand(promql).encode().toUri();
        JsonNode body;
        try {
            body = http.get().uri(uri).retrieve().body(JsonNode.class);
        } catch (RestClientException e) {
            throw new PrometheusException("Prometheus query failed at " + baseUrl + ": " + e.getMessage(), e);
        }
        if (body == null || !"success".equals(body.path("status").asText())) {
            throw new PrometheusException("Prometheus rejected query: " + (body == null ? "empty response" : body.path("error").asText()), null);
        }
        return parse(body.path("data"));
    }

    static List<Sample> parse(JsonNode data) {
        List<Sample> out = new ArrayList<>();
        if (!"vector".equals(data.path("resultType").asText())) return out;
        for (JsonNode series : data.path("result")) {
            double v;
            try {
                v = Double.parseDouble(series.path("value").path(1).asText());
            } catch (NumberFormatException e) {
                continue;
            }
            if (Double.isNaN(v) || Double.isInfinite(v)) continue;
            Map<String, String> labels = new LinkedHashMap<>();
            series.path("metric").fields().forEachRemaining(f -> labels.put(f.getKey(), f.getValue().asText()));
            out.add(new Sample(labels, v));
        }
        return out;
    }

    public boolean ready(String baseUrl) {
        try {
            http.get().uri(baseUrl + "/-/ready").retrieve().toBodilessEntity();
            return true;
        } catch (RestClientException e) {
            return false;
        }
    }

    public static class PrometheusException extends RuntimeException {
        public PrometheusException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}

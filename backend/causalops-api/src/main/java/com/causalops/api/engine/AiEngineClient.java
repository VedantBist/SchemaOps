package com.causalops.api.engine;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

import java.net.URI;
import java.util.Map;

/** The single gateway from the platform API to the AI engine. */
@Component
public class AiEngineClient {

    private final RestClient http;
    private final String baseUrl;

    public AiEngineClient(RestClient http, @Value("${causalops.ai-url}") String baseUrl) {
        this.http = http;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
    }

    public String baseUrl() {
        return baseUrl;
    }

    public Map<String, Object> post(String path, Object body) {
        try {
            return http.post().uri(baseUrl + path).contentType(MediaType.APPLICATION_JSON).body(body)
                    .retrieve().body(new ParameterizedTypeReference<>() {});
        } catch (RestClientResponseException e) {
            throw new EngineException(e.getStatusCode().value(), "AI engine returned HTTP " + e.getStatusCode().value()
                    + " for " + path + ": " + e.getResponseBodyAsString());
        } catch (RestClientException e) {
            throw new EngineException(503, "AI engine unreachable at " + baseUrl + ": " + e.getMessage());
        }
    }

    /** Forwards a request unchanged and returns the engine's status, content type and body. */
    public ResponseEntity<byte[]> forward(HttpMethod method, String pathAndQuery, MediaType contentType, byte[] body) {
        try {
            RestClient.RequestBodySpec spec = http.method(method).uri(URI.create(baseUrl + pathAndQuery))
                    .headers(h -> {
                        if (contentType != null) h.setContentType(contentType);
                        h.setAccept(java.util.List.of(MediaType.ALL));
                    });
            // Only attach a body when there is one: writing an empty body turns a GET into a POST.
            if (body != null && body.length > 0) spec.body(body);
            return spec.exchange((req, res) -> {
                        HttpHeaders headers = new HttpHeaders();
                        if (res.getHeaders().getContentType() != null) headers.setContentType(res.getHeaders().getContentType());
                        return ResponseEntity.status(res.getStatusCode()).headers(headers).body(res.getBody().readAllBytes());
                    });
        } catch (RestClientException e) {
            throw new EngineException(503, "AI engine unreachable at " + baseUrl + ": " + e.getMessage());
        }
    }

    public boolean ready() {
        try {
            http.get().uri(baseUrl + "/ready").retrieve().toBodilessEntity();
            return true;
        } catch (RestClientException e) {
            return false;
        }
    }

    public static class EngineException extends RuntimeException {
        private final int status;

        public EngineException(int status, String message) {
            super(message);
            this.status = status;
        }

        public int status() {
            return status;
        }
    }
}

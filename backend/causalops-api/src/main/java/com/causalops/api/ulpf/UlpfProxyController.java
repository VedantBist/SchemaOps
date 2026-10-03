package com.causalops.api.ulpf;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.net.URI;
import java.util.List;

/**
 * Exposes the ULPF service (log sources, OCSF events, vault, lineage proofs, parser test bench)
 * under {@code /api/ulpf/**}, so the console keeps talking to a single origin. Status codes and
 * bodies pass through unchanged.
 */
@RestController
public class UlpfProxyController {

    private static final String PREFIX = "/api/ulpf";

    private final RestClient http;
    private final String baseUrl;

    public UlpfProxyController(RestClient http, @Value("${causalops.ulpf-url}") String baseUrl) {
        this.http = http;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
    }

    @RequestMapping(PREFIX + "/**")
    public ResponseEntity<byte[]> proxy(HttpServletRequest request, @RequestBody(required = false) byte[] body) {
        String path = request.getRequestURI().substring(request.getContextPath().length() + PREFIX.length());
        if (path.isEmpty()) path = "/";
        if (request.getQueryString() != null) path += "?" + request.getQueryString();
        MediaType type = request.getContentType() == null ? null : MediaType.parseMediaType(request.getContentType());
        try {
            RestClient.RequestBodySpec spec = http.method(HttpMethod.valueOf(request.getMethod()))
                    .uri(URI.create(baseUrl + path))
                    .headers(h -> {
                        if (type != null) h.setContentType(type);
                        h.setAccept(List.of(MediaType.ALL));
                        String source = request.getHeader("X-Source");
                        if (source != null) h.set("X-Source", source);
                    });
            if (body != null && body.length > 0) spec.body(body);
            return spec.exchange((req, res) -> {
                HttpHeaders headers = new HttpHeaders();
                if (res.getHeaders().getContentType() != null) headers.setContentType(res.getHeaders().getContentType());
                return ResponseEntity.status(res.getStatusCode()).headers(headers).body(res.getBody().readAllBytes());
            });
        } catch (RestClientException e) {
            return ResponseEntity.status(503).contentType(MediaType.APPLICATION_JSON)
                    .body(("{\"message\":\"ULPF service unreachable at " + baseUrl + "\"}").getBytes());
        }
    }
}

package com.causalops.demo;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

/**
 * Fault-injection control plane. Disabled unless CHAOS_TOKEN is set, and every
 * call must present the same token in the X-Chaos-Token header.
 */
@RestController
@RequestMapping("/internal/chaos")
public class ChaosController {

    private final ChaosState chaos;
    private final byte[] token;

    public ChaosController(ChaosState chaos, @Value("${chaos.token:}") String token) {
        this.chaos = chaos;
        this.token = token.getBytes(StandardCharsets.UTF_8);
    }

    @GetMapping
    public Map<String, Object> status(@RequestHeader(value = "X-Chaos-Token", required = false) String t) {
        authorize(t);
        return chaos.status();
    }

    @PostMapping
    public Map<String, Object> inject(@RequestHeader(value = "X-Chaos-Token", required = false) String t,
                                      @RequestBody ChaosRequest request) {
        authorize(t);
        if (request.type() == null || request.type().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "type is required");
        }
        chaos.apply(request);
        return chaos.status();
    }

    @DeleteMapping
    public Map<String, Object> clear(@RequestHeader(value = "X-Chaos-Token", required = false) String t) {
        authorize(t);
        chaos.clear();
        return chaos.status();
    }

    private void authorize(String presented) {
        if (token.length == 0) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "chaos control is disabled (CHAOS_TOKEN not set)");
        }
        byte[] p = presented == null ? new byte[0] : presented.getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(token, p)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid chaos token");
        }
    }
}

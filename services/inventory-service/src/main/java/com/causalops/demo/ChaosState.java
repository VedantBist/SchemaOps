package com.causalops.demo;

import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;

/**
 * In-process fault state for the reference service. Faults are real: they delay,
 * fail or error actual requests, so every effect is observed through OTel telemetry
 * rather than computed. Every fault expires on its own after durationSeconds.
 */
@Component
public class ChaosState {

    private final List<ChaosExtension> extensions;

    private volatile String type;
    private volatile long latencyMs;
    private volatile double errorRatePct;
    private volatile boolean failure;
    private volatile Instant expiresAt;

    public ChaosState(ObjectProvider<ChaosExtension> extensions) {
        this.extensions = extensions.orderedStream().toList();
    }

    public synchronized void apply(ChaosRequest r) {
        clear();
        int duration = r.durationSeconds() == null ? 60 : r.durationSeconds();
        if (duration < 1 || duration > 3600) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "durationSeconds must be between 1 and 3600");
        }
        switch (r.type()) {
            case "SERVICE_LATENCY" -> latencyMs = requirePositive(r.latencyMs(), "latencyMs");
            case "ERROR_RATE" -> {
                double pct = r.errorRatePct() == null ? 0 : r.errorRatePct();
                if (pct <= 0 || pct > 100) {
                    throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "errorRatePct must be in (0, 100]");
                }
                errorRatePct = pct;
            }
            case "SERVICE_FAILURE" -> failure = true;
            default -> {
                ChaosExtension ext = extensions.stream().filter(e -> e.supports(r.type())).findFirst()
                        .orElseThrow(() -> new ResponseStatusException(HttpStatus.BAD_REQUEST,
                                "Fault type " + r.type() + " is not handled in-process by this service"));
                ext.start(r, duration);
            }
        }
        type = r.type();
        expiresAt = Instant.now().plusSeconds(duration);
    }

    public synchronized void clear() {
        type = null;
        latencyMs = 0;
        errorRatePct = 0;
        failure = false;
        expiresAt = null;
        extensions.forEach(ChaosExtension::stop);
    }

    /** Applies the active fault to a real business request. */
    public void beforeRequest() throws InterruptedException {
        expireIfDue();
        if (failure) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "chaos: service failure");
        }
        if (latencyMs > 0) {
            Thread.sleep(latencyMs);
        }
        if (errorRatePct > 0 && ThreadLocalRandom.current().nextDouble(100.0) < errorRatePct) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "chaos: injected error");
        }
    }

    @Scheduled(fixedDelay = 1000)
    public void expireIfDue() {
        Instant exp = expiresAt;
        if (exp != null && Instant.now().isAfter(exp)) {
            clear();
        }
    }

    public Map<String, Object> status() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("active", type != null);
        m.put("type", type);
        m.put("latencyMs", latencyMs);
        m.put("errorRatePct", errorRatePct);
        m.put("failure", failure);
        m.put("expiresAt", expiresAt == null ? null : expiresAt.toString());
        return m;
    }

    private static long requirePositive(Long v, String field) {
        if (v == null || v <= 0 || v > 60_000) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, field + " must be between 1 and 60000");
        }
        return v;
    }
}

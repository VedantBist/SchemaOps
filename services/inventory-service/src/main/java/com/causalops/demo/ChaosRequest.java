package com.causalops.demo;

public record ChaosRequest(
        String type,
        Long latencyMs,
        Double errorRatePct,
        Integer durationSeconds,
        Integer holdConnections) {
}

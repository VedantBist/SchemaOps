package com.causalops.demo;

/** A fault that needs service-specific resources (for example a DB connection pool). */
public interface ChaosExtension {
    boolean supports(String type);

    void start(ChaosRequest request, int durationSeconds);

    void stop();
}

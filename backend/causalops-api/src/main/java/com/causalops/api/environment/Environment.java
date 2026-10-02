package com.causalops.api.environment;

import java.time.Instant;
import java.util.UUID;

public record Environment(UUID id, String name, String status, String statusReason, EnvironmentConfig config,
                          Instant learningStartedAt, Instant calibratedAt, Instant createdAt, Instant updatedAt) {
}

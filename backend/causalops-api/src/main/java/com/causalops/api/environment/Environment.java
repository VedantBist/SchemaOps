package com.causalops.api.environment;

import java.time.Instant;
import java.util.UUID;

public record Environment(UUID id, String name, String status, EnvironmentConfig config,
                          Instant learningStartedAt, Instant createdAt, Instant updatedAt) {
}

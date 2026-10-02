package com.causalops.api.incident;

import java.util.UUID;

/** Published after a root-cause analysis has been stored for an incident. */
public record RootCauseIdentified(UUID incidentId, UUID analysisId) {
}

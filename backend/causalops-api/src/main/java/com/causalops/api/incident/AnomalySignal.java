package com.causalops.api.incident;

import java.util.List;
import java.util.Map;

/**
 * The AI engine's verdict for one service in one evaluation: its anomaly score against the
 * calibrated baselines, whether the gate flagged it, and the variables that drove the score.
 */
public record AnomalySignal(String service, double anomaly, boolean flagged, List<Map<String, Object>> signals) {
}

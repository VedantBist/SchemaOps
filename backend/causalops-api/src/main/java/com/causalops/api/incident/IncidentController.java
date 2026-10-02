package com.causalops.api.incident;

import com.causalops.api.environment.EnvironmentService;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api/incidents")
public class IncidentController {

    private final IncidentRepository incidents;
    private final IncidentAnalysisService analysis;
    private final EnvironmentService environments;

    public IncidentController(IncidentRepository incidents, IncidentAnalysisService analysis, EnvironmentService environments) {
        this.incidents = incidents;
        this.analysis = analysis;
        this.environments = environments;
    }

    @GetMapping
    public List<Map<String, Object>> list(@RequestParam(required = false) UUID environmentId,
                                          @RequestParam(defaultValue = "all") String state,
                                          @RequestParam(defaultValue = "200") int limit) {
        IncidentRepository.Filter filter = switch (state.toLowerCase()) {
            case "active" -> IncidentRepository.Filter.ACTIVE;
            case "resolved" -> IncidentRepository.Filter.RESOLVED;
            case "all" -> IncidentRepository.Filter.ALL;
            default -> throw new IllegalArgumentException("state must be active, resolved or all");
        };
        return incidents.list(environments.resolve(environmentId).id(), filter, Math.min(Math.max(limit, 1), 1000));
    }

    @GetMapping("/active")
    public List<Map<String, Object>> active(@RequestParam(required = false) UUID environmentId) {
        return list(environmentId, "active", 200);
    }

    @GetMapping("/history")
    public List<Map<String, Object>> history(@RequestParam(required = false) UUID environmentId) {
        return list(environmentId, "resolved", 200);
    }

    @GetMapping("/{id}")
    public Map<String, Object> get(@PathVariable UUID id) {
        return incidents.get(id);
    }

    @GetMapping("/{id}/timeline")
    public List<Map<String, Object>> timeline(@PathVariable UUID id) {
        incidents.get(id);
        return incidents.timeline(id);
    }

    /** The latest stored analysis. Never triggers a new one. */
    @GetMapping("/{id}/root-cause")
    public Map<String, Object> rootCause(@PathVariable UUID id) {
        return analysis.stored(id);
    }

    public record AnalyzeRequest(String mode, Integer lookbackMinutes) {
    }

    /** Runs root-cause analysis on the incident's measured telemetry window. */
    @PostMapping("/{id}/rca")
    public Map<String, Object> analyze(@PathVariable UUID id, @RequestBody(required = false) AnalyzeRequest body) {
        String mode = body == null ? null : body.mode();
        int lookback = body == null || body.lookbackMinutes() == null ? 10 : Math.min(Math.max(body.lookbackMinutes(), 1), 120);
        return analysis.analyze(id, mode, lookback);
    }
}

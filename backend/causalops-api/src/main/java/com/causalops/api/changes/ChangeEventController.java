package com.causalops.api.changes;

import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Record deployments, restarts and maintenance (e.g. from CI/CD) and list them. */
@RestController
@RequestMapping("/api/changes")
public class ChangeEventController {

    private final ChangeEventRepository changes;
    private final EnvironmentService environments;

    public ChangeEventController(ChangeEventRepository changes, EnvironmentService environments) {
        this.changes = changes;
        this.environments = environments;
    }

    public record NewChange(UUID environmentId, String kind, String target, Instant startedAt, Instant endedAt,
                            String source, String description) {
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Map<String, Object> record(@RequestBody NewChange body) {
        Environment env = environments.resolve(body.environmentId());
        Instant started = body.startedAt() == null ? Instant.now() : body.startedAt();
        UUID id = changes.record(env.id(), body.kind(), body.target(), started, body.endedAt(), body.source(),
                body.description(), null);
        return Map.of("id", id, "environmentId", env.id());
    }

    @GetMapping
    public List<Map<String, Object>> list(@RequestParam(required = false) UUID environmentId,
                                          @RequestParam(required = false) Instant from,
                                          @RequestParam(required = false) Instant to,
                                          @RequestParam(defaultValue = "200") int limit) {
        Environment env = environments.resolve(environmentId);
        Instant end = to == null ? Instant.now() : to;
        Instant start = from == null ? end.minus(Duration.ofDays(7)) : from;
        return changes.list(env.id(), start, end, Math.min(Math.max(limit, 1), 1000));
    }
}

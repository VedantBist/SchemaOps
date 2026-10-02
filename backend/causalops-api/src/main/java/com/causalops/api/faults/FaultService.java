package com.causalops.api.faults;

import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.events.EventBus;
import com.causalops.api.telemetry.TopologyRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;

/** Lifecycle of injected faults: record, apply through {@link FaultInjector}, stop, expire. */
@Service
public class FaultService {

    private static final Logger log = LoggerFactory.getLogger(FaultService.class);
    private static final String COLUMNS = """
            id, environment_id AS "environmentId", type, target, severity, duration_seconds AS "durationSeconds",
            parameters::text AS parameters, status, started_at AS "startedAt", stopped_at AS "stoppedAt"
            """;

    private final JdbcTemplate db;
    private final FaultInjector injector;
    private final EnvironmentService environments;
    private final TopologyRepository topology;
    private final EventBus events;
    private final ObjectMapper json;

    public FaultService(JdbcTemplate db, FaultInjector injector, EnvironmentService environments,
                        TopologyRepository topology, EventBus events, ObjectMapper json) {
        this.db = db;
        this.injector = injector;
        this.environments = environments;
        this.topology = topology;
        this.events = events;
        this.json = json;
    }

    public Map<String, Object> inject(UUID environmentId, FaultRequest r) {
        UUID env = environments.resolve(environmentId).id();
        boolean known = topology.nodes(env).stream().anyMatch(n -> n.name().equals(r.target()));
        if (!known) throw new NoSuchElementException("Target " + r.target() + " is not part of this environment's topology");
        UUID id = UUID.randomUUID();
        db.update("""
                INSERT INTO fault_injections (id, environment_id, type, target, severity, duration_seconds, parameters)
                VALUES (?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
                """, id, env, r.type(), r.target(), r.severity(), r.durationSeconds(),
                write(r.parameters() == null ? Map.of() : r.parameters()));
        try {
            injector.inject(id, r);
        } catch (RuntimeException e) {
            db.update("UPDATE fault_injections SET status = 'FAILED', stopped_at = now() WHERE id = ?", id);
            log.error("Fault {} could not be applied to {}", id, r.target(), e);
            throw e;
        }
        events.emit("fault.started", id, Map.of("target", r.target(), "type", r.type()));
        return get(id);
    }

    public List<Map<String, Object>> list(UUID environmentId) {
        UUID env = environments.resolve(environmentId).id();
        return db.queryForList("SELECT " + COLUMNS + " FROM fault_injections WHERE environment_id = ? ORDER BY started_at DESC", env);
    }

    public Map<String, Object> get(UUID id) {
        return db.queryForList("SELECT " + COLUMNS + " FROM fault_injections WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("Unknown fault " + id));
    }

    public void stop(UUID id) {
        var f = get(id);
        if (!"ACTIVE".equals(f.get("status"))) return;
        end(id, (String) f.get("type"), (String) f.get("target"), "STOPPED");
    }

    public void clear(UUID environmentId) {
        UUID env = environments.resolve(environmentId).id();
        for (var f : db.queryForList("SELECT id, type, target FROM fault_injections WHERE status = 'ACTIVE' AND environment_id = ?", env)) {
            end((UUID) f.get("id"), (String) f.get("type"), (String) f.get("target"), "STOPPED");
        }
        events.emit("faults.cleared", env, Map.of());
    }

    /** Faults expire after durationSeconds; the services also enforce this locally. */
    @Scheduled(fixedDelay = 1000)
    public void expire() {
        var due = db.queryForList("""
                SELECT id, type, target FROM fault_injections
                WHERE status = 'ACTIVE' AND started_at + make_interval(secs => duration_seconds) < now()
                """);
        for (var f : due) {
            end((UUID) f.get("id"), (String) f.get("type"), (String) f.get("target"), "EXPIRED");
        }
    }

    private void end(UUID id, String type, String target, String status) {
        try {
            injector.stop(id, type, target);
        } catch (RuntimeException e) {
            log.error("Could not remove fault {} ({} on {}); marking it {} anyway", id, type, target, status, e);
        }
        db.update("UPDATE fault_injections SET status = ?, stopped_at = now() WHERE id = ? AND status = 'ACTIVE'", status, id);
        events.emit("fault.stopped", id, Map.of("status", status));
    }

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Fault parameters are not serializable", e);
        }
    }
}

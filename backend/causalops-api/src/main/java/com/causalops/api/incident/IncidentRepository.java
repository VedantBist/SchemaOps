package com.causalops.api.incident;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.UUID;

/** Incidents and their timeline. All SQL is parameterized. */
@Repository
public class IncidentRepository {

    private static final String SELECT = """
            SELECT id, incident_key AS "incidentKey", environment_id AS "environmentId", title, severity, status,
                   detection_source AS "detectionSource", opened_at AS "openedAt", resolved_at AS "resolvedAt",
                   updated_at AS "updatedAt", summary, affected_services::text AS "affectedServices",
                   evidence::text AS evidence
            FROM incidents
            """;

    private final JdbcTemplate db;
    private final ObjectMapper json;

    public IncidentRepository(JdbcTemplate db, ObjectMapper json) {
        this.db = db;
        this.json = json;
    }

    public enum Filter { ALL, ACTIVE, RESOLVED }

    public List<Map<String, Object>> list(UUID env, Filter filter, int limit) {
        String where = switch (filter) {
            case ALL -> "WHERE environment_id = ?";
            case ACTIVE -> "WHERE environment_id = ? AND status <> 'RESOLVED'";
            case RESOLVED -> "WHERE environment_id = ? AND status = 'RESOLVED'";
        };
        return db.queryForList(SELECT + where + " ORDER BY opened_at DESC LIMIT ?", env, limit);
    }

    public Map<String, Object> get(UUID id) {
        return db.queryForList(SELECT + "WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("Unknown incident " + id));
    }

    public Optional<Map<String, Object>> openFor(UUID env) {
        return db.queryForList(SELECT + "WHERE environment_id = ? AND status <> 'RESOLVED' ORDER BY opened_at DESC LIMIT 1", env)
                .stream().findFirst();
    }

    public UUID create(UUID env, String title, String severity, String status, String detectionSource,
                       String summary, List<String> affected, List<?> evidence) {
        return db.queryForObject("""
                INSERT INTO incidents (environment_id, title, severity, status, detection_source, summary,
                                       affected_services, evidence)
                VALUES (?, ?, ?, ?, ?, ?, CAST(? AS jsonb), CAST(? AS jsonb))
                RETURNING id
                """, UUID.class, env, title, severity, status, detectionSource, summary, write(affected), write(evidence));
    }

    public void update(UUID id, String title, String severity, String summary, List<String> affected, List<?> evidence) {
        db.update("""
                UPDATE incidents SET title = ?, severity = ?, summary = ?, affected_services = CAST(? AS jsonb),
                                     evidence = CAST(? AS jsonb), updated_at = now()
                WHERE id = ?
                """, title, severity, summary, write(affected), write(evidence), id);
    }

    /** Status changes are not content changes, so updated_at (used to decide when to re-run RCA) is left alone. */
    public void setStatus(UUID id, String status) {
        db.update("""
                UPDATE incidents SET status = ?,
                       resolved_at = CASE WHEN ? = 'RESOLVED' THEN now() ELSE resolved_at END
                WHERE id = ?
                """, status, status, id);
    }

    public void addEvent(UUID incident, String type, Object payload) {
        db.update("INSERT INTO incident_events (incident_id, event_type, payload) VALUES (?, ?, CAST(? AS jsonb))",
                incident, type, write(payload));
    }

    public List<Map<String, Object>> timeline(UUID incident) {
        return db.queryForList("""
                SELECT id, occurred_at AS "occurredAt", event_type AS "eventType", payload::text AS payload
                FROM incident_events WHERE incident_id = ? ORDER BY occurred_at
                """, incident);
    }

    public <T> T read(String raw, Class<T> type) {
        try {
            return json.readValue(raw, type);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Stored incident JSON is invalid", e);
        }
    }

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Cannot serialize incident data", e);
        }
    }
}

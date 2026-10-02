package com.causalops.api.environment;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public class EnvironmentRepository {

    private static final String COLUMNS =
            "id, name, status, status_reason, config::text AS config, learning_started_at, calibrated_at, created_at, updated_at";

    private final JdbcTemplate db;
    private final ObjectMapper json;
    private final RowMapper<Environment> mapper;

    public EnvironmentRepository(JdbcTemplate db, ObjectMapper json) {
        this.db = db;
        this.json = json;
        this.mapper = (rs, i) -> new Environment(
                rs.getObject("id", UUID.class),
                rs.getString("name"),
                rs.getString("status"),
                rs.getString("status_reason"),
                read(rs.getString("config")),
                rs.getTimestamp("learning_started_at").toInstant(),
                rs.getTimestamp("calibrated_at") == null ? null : rs.getTimestamp("calibrated_at").toInstant(),
                rs.getTimestamp("created_at").toInstant(),
                rs.getTimestamp("updated_at").toInstant());
    }

    public List<Environment> findAll() {
        return db.query("SELECT " + COLUMNS + " FROM environments ORDER BY created_at", mapper);
    }

    public List<Environment> findMonitored() {
        return db.query("SELECT " + COLUMNS + " FROM environments WHERE status <> 'DISABLED' ORDER BY created_at", mapper);
    }

    public Optional<Environment> findById(UUID id) {
        return db.query("SELECT " + COLUMNS + " FROM environments WHERE id = ?", mapper, id).stream().findFirst();
    }

    public Optional<Environment> findByName(String name) {
        return db.query("SELECT " + COLUMNS + " FROM environments WHERE name = ?", mapper, name).stream().findFirst();
    }

    public Environment create(String name, EnvironmentConfig config) {
        UUID id = db.queryForObject(
                "INSERT INTO environments(name, config) VALUES (?, CAST(? AS jsonb)) RETURNING id",
                UUID.class, name, write(config));
        return findById(id).orElseThrow();
    }

    public Environment updateConfig(UUID id, EnvironmentConfig config) {
        int n = db.update("UPDATE environments SET config = CAST(? AS jsonb), updated_at = now() WHERE id = ?",
                write(config), id);
        if (n == 0) throw new java.util.NoSuchElementException("Unknown environment " + id);
        return findById(id).orElseThrow();
    }

    public Environment updateStatus(UUID id, String status) {
        int n = db.update("UPDATE environments SET status = ?, updated_at = now() WHERE id = ?", status, id);
        if (n == 0) throw new java.util.NoSuchElementException("Unknown environment " + id);
        return findById(id).orElseThrow();
    }

    /** Records the outcome of a calibration run on the environment's lifecycle. */
    public void restartLearning(UUID id, String reason) {
        db.update("""
                UPDATE environments SET status = 'LEARNING', learning_started_at = now(), status_reason = ?, updated_at = now()
                WHERE id = ?
                """, reason, id);
    }

    public void updateLifecycle(UUID id, String status, Timestamp calibratedAt, String reason) {
        db.update("""
                UPDATE environments SET status = ?, calibrated_at = COALESCE(?, calibrated_at), status_reason = ?,
                                        updated_at = now()
                WHERE id = ?
                """, status, calibratedAt, reason, id);
    }

    public Optional<Timestamp> lastSampleAt(UUID environmentId) {
        return Optional.ofNullable(db.queryForObject(
                "SELECT max(captured_at) FROM telemetry_snapshots WHERE environment_id = ?", Timestamp.class, environmentId));
    }

    private EnvironmentConfig read(String raw) {
        try {
            return json.readValue(raw, EnvironmentConfig.class);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Stored environment config is not valid JSON", e);
        }
    }

    private String write(EnvironmentConfig config) {
        try {
            return json.writeValueAsString(config);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Environment config cannot be serialized", e);
        }
    }
}

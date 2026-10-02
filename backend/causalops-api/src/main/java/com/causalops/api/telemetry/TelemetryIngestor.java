package com.causalops.api.telemetry;

import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.events.EventBus;
import com.causalops.api.incident.AnomalySignal;
import com.causalops.api.incident.IncidentDetector;
import com.causalops.api.incident.PipelineCoordinator;
import com.causalops.api.remediation.RemediationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Reads measured telemetry for every node of every monitored environment from Prometheus
 * and stores it. Nothing is computed or simulated: a metric Prometheus has no data for is stored as NULL.
 */
@Component
public class TelemetryIngestor {

    private static final Logger log = LoggerFactory.getLogger(TelemetryIngestor.class);

    private final EnvironmentService environments;
    private final TopologyRepository topology;
    private final MeasurementCollector collector;
    private final TelemetryRepository telemetry;
    private final IncidentDetector detector;
    private final PipelineCoordinator pipeline;
    private final EventBus events;
    private final RemediationService remediation;
    private final int retentionDays;

    public TelemetryIngestor(EnvironmentService environments, TopologyRepository topology, MeasurementCollector collector,
                             TelemetryRepository telemetry, IncidentDetector detector, PipelineCoordinator pipeline,
                             EventBus events, RemediationService remediation,
                             @Value("${causalops.telemetry.retention-days:14}") int retentionDays) {
        this.remediation = remediation;
        this.environments = environments;
        this.topology = topology;
        this.collector = collector;
        this.telemetry = telemetry;
        this.detector = detector;
        this.pipeline = pipeline;
        this.events = events;
        this.retentionDays = retentionDays;
    }

    @Scheduled(initialDelayString = "${causalops.telemetry.initial-delay-ms:10000}",
               fixedDelayString = "${causalops.telemetry.ingest-interval-ms:5000}")
    public void ingestAll() {
        for (Environment env : environments.monitored()) {
            try {
                ingest(env, Instant.now());
            } catch (RuntimeException e) {
                log.warn("Telemetry ingestion failed for environment {}: {}", env.name(), e.getMessage());
            }
        }
    }

    public Map<String, NodeMeasurement> ingest(Environment env, Instant at) {
        Map<String, String> nodes = new LinkedHashMap<>();
        for (var n : topology.nodes(env.id())) nodes.put(n.name(), n.kind());
        if (nodes.isEmpty()) return Map.of();

        Map<String, NodeMeasurement> measured = collector.collect(env.config(), nodes);
        List<Map<String, Object>> summary = new ArrayList<>();
        Map<String, SloEvaluator.Result> evaluations = new LinkedHashMap<>();
        measured.forEach((name, m) -> {
            if (m.metrics().isEmpty()) return;
            SloEvaluator.Result result = SloEvaluator.evaluate(m, SloEvaluator.sloFor(env.config(), name));
            evaluations.put(name, result);
            telemetry.insertSnapshot(env.id(), at, m);
            telemetry.updateServiceState(env.id(), name, m, result.status(), at);
            Map<String, Object> row = new LinkedHashMap<>(m.metrics());
            row.put("service", name);
            row.put("status", result.status());
            summary.add(row);
        });
        for (EdgeMeasurement edge : collector.collectEdges(env.config())) {
            if (!edge.metrics().isEmpty()) telemetry.insertEdgeSnapshot(env.id(), at, edge);
        }
        Map<String, AnomalySignal> anomalies = pipeline.evaluate(env, at);
        detector.evaluate(env, evaluations, anomalies, at);
        try {
            remediation.verify(env, evaluations, anomalies);
        } catch (RuntimeException e) {
            log.warn("Remediation verification failed for {}: {}", env.name(), e.getMessage());
        }
        pipeline.scheduleRootCauseAnalysis(env);
        events.emit("telemetry.ingested", env.id(), Map.of("at", at.toString(), "services", summary));
        return measured;
    }

    @Scheduled(cron = "${causalops.telemetry.purge-cron:0 15 3 * * *}")
    public void purge() {
        int n = telemetry.purgeOlderThan(retentionDays);
        if (n > 0) log.info("Purged {} telemetry snapshots older than {} days", n, retentionDays);
    }
}

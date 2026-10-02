package com.causalops.api.remediation;

import com.causalops.api.environment.EnvironmentConfig;
import com.causalops.api.environment.EnvironmentConfig.Action;

import java.util.List;
import java.util.Map;

final class Fixtures {

    static final Action RESTART = new Action("docker-restart", "Restart", "docker", "restart", 1, List.of("service"),
            List.of("error", "latency", "pool_util"), Map.of(), "");
    static final Action LIMITS = new Action("docker-raise-limits", "Raise limits", "docker", "update_resources", 2,
            List.of("service"), List.of("latency"), Map.of("cpuFactor", 1.5), "");
    static final Action K8S_SCALE = new Action("k8s-scale-out", "Add a replica", "kubernetes", "scale", 1,
            List.of("service"), List.of("latency"), Map.of("delta", 1), "");
    static final Action DB_RUNBOOK = new Action("runbook-database", "DB runbook", "webhook", "runbook", 3,
            List.of("database"), List.of("latency", "db_latency"), Map.of(), "");

    static EnvironmentConfig.Remediation config(int maxTier, Map<String, Map<String, Object>> executors) {
        return new EnvironmentConfig.Remediation(false, false, maxTier, 0.3, 30, 4, 10, 30, 0.5,
                new EnvironmentConfig.Verification(20, 180, 4), executors, Map.of("orders", Map.of("docker", "orders-svc")),
                List.of(RESTART, LIMITS, K8S_SCALE, DB_RUNBOOK));
    }

    static Map<String, Map<String, Object>> dockerOnly() {
        return Map.of("docker", Map.of("enabled", true, "project", "shop"), "kubernetes", Map.of("enabled", false));
    }

    private Fixtures() {
    }
}

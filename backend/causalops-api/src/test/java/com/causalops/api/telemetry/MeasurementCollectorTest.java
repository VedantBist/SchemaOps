package com.causalops.api.telemetry;

import com.causalops.api.environment.EnvironmentConfig;
import com.causalops.api.environment.EnvironmentConfig.MetricTemplate;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class MeasurementCollectorTest {

    /** Answers each query with fixed samples and records what was asked. */
    static class FakePrometheus extends PrometheusClient {
        final Map<String, List<Sample>> answers;
        final List<String> asked = new ArrayList<>();

        FakePrometheus(Map<String, List<Sample>> answers) {
            super(null);
            this.answers = answers;
        }

        @Override
        public List<Sample> query(String baseUrl, String promql) {
            asked.add(promql);
            return answers.entrySet().stream().filter(e -> promql.startsWith(e.getKey()))
                    .findFirst().map(Map.Entry::getValue).orElse(List.of());
        }
    }

    private static PrometheusClient.Sample s(String label, String node, double v) {
        return new PrometheusClient.Sample(Map.of(label, node), v);
    }

    private static EnvironmentConfig config() {
        return new EnvironmentConfig(
                new EnvironmentConfig.Endpoints("http://prom", null, null, null),
                new EnvironmentConfig.Telemetry("30s", "5m", 120,
                        Map.of("requestRate", new MetricTemplate("rps[${window}]", "service_name", null),
                               "errorRatePct", new MetricTemplate("err[${window}]", "service_name", null)),
                        Map.of("latencyP99", new MetricTemplate("dbp99[${window}]", "server", 1000.0)),
                        null, "graph"),
                new EnvironmentConfig.Slo(500, 5, 2), Map.of(), new EnvironmentConfig.Detection(3, 6),
                List.of("user"), new EnvironmentConfig.Calibration(24, 7, 30), null);
    }

    @Test
    void readsServiceAndDependencyMetricsWithWindowAndScale() {
        var prom = new FakePrometheus(Map.of(
                "rps", List.of(s("service_name", "api", 4.0), s("service_name", "idle", 0.0)),
                "err", List.of(),
                "dbp99", List.of(s("server", "db", 0.25))));
        var out = new MeasurementCollector(prom).collect(config(), Map.of("api", "service", "db", "database", "ghost", "service"));

        assertTrue(prom.asked.contains("rps[30s]"), "window placeholder is substituted");
        assertEquals(4.0, out.get("api").get("requestRate"));
        assertEquals(0.0, out.get("api").get("errorRatePct"), "traffic without error series means zero errors");
        assertEquals(250.0, out.get("db").get("latencyP99"), 1e-9, "seconds scaled to ms");
        assertTrue(out.get("ghost").metrics().isEmpty(), "a node without data gets no invented values");
    }

    @Test
    void parsesPrometheusVectorAndDropsNaN() throws Exception {
        var data = new ObjectMapper().readTree("""
                {"resultType":"vector","result":[
                  {"metric":{"service_name":"a"},"value":[1,"2.5"]},
                  {"metric":{"service_name":"b"},"value":[1,"NaN"]}]}
                """);
        var samples = PrometheusClient.parse(data);
        assertEquals(1, samples.size());
        assertEquals(2.5, samples.get(0).value());
    }
}

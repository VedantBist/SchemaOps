package com.causalops.api.remediation;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class RecommenderTest {

    private static final Recommender.Candidate ORDERS = new Recommender.Candidate("orders", "service", "orders", 0.6,
            Set.of("latency", "pool_util"), "latency");
    private static final Recommender.Candidate DB = new Recommender.Candidate("orders-db", "database", "orders-db", 0.3,
            Set.of("db_latency"), "db_latency");

    @Test
    void proposesOnlyEnabledActionsThatMatchKindAndDeviation() {
        var out = Recommender.propose(List.of(ORDERS, DB), Fixtures.config(1, Fixtures.dockerOnly()),
                Map.of(), (a, m) -> new Recommender.Effectiveness(0, 0));
        // Kubernetes is disabled and no webhook executor is configured for the database runbook.
        assertEquals(List.of("docker-restart", "docker-raise-limits"), out.stream().map(p -> p.action().id()).toList());
        assertEquals("orders-svc", out.get(0).binding(), "target mapping from the environment is used");
        assertTrue(out.get(0).stateless() && !out.get(0).reversible());
        assertTrue(out.get(1).reversible());
    }

    @Test
    void learnedSuccessRateAndCounterfactualBenefitDriveTheRanking() {
        var benefits = Map.of("orders", new Recommender.Benefit(400.0, 900.0, null, 3, "PASS"));
        // Restart passed verification 1 of 5 times here for latency deviations; raising limits 3 of 3 times.
        var out = Recommender.propose(List.of(ORDERS), Fixtures.config(1, Fixtures.dockerOnly()), benefits,
                (a, m) -> a.equals("docker-restart") ? new Recommender.Effectiveness(1, 5) : new Recommender.Effectiveness(3, 3));
        assertEquals("docker-raise-limits", out.get(0).action().id());
        assertEquals(0.6 * (4.0 / 5.0) * 1.0, out.get(0).score(), 1e-9);
        assertTrue(out.get(0).rationale().contains("400 ms"), out.get(0).rationale());
        assertTrue(out.get(1).rationale().contains("1 of 5"), out.get(1).rationale());
    }

    @Test
    void benefitShareIsRelativeToTheBestCandidateAndUnknownCountsAsHalf() {
        assertEquals(0.5, Recommender.benefitShare(new Recommender.Benefit(null, null, null, 0, "UNAVAILABLE"), 300, 0));
        assertEquals(0.5, Recommender.benefitShare(new Recommender.Benefit(150.0, 200.0, null, 0, "PASS"), 300, 0), 1e-9);
        assertEquals(0.1, Recommender.benefitShare(new Recommender.Benefit(0.0, 0.0, null, 0, "PASS"), 300, 0), 1e-9);
    }

    @Test
    void nothingIsProposedWhenNoEnabledExecutorFits() {
        var out = Recommender.propose(List.of(DB), Fixtures.config(1, Fixtures.dockerOnly()), Map.of(),
                (a, m) -> new Recommender.Effectiveness(0, 0));
        assertTrue(out.isEmpty());
    }
}

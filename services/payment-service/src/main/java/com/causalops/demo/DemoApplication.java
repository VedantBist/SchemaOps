package com.causalops.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.web.bind.annotation.*;

import java.util.Map;
import java.util.UUID;

/** Payment authorization endpoint of the reference system. */
@SpringBootApplication
@EnableScheduling
@RestController
public class DemoApplication {

    private final ChaosState chaos;

    public DemoApplication(ChaosState chaos) {
        this.chaos = chaos;
    }

    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }

    @PostMapping("/payments")
    public Map<String, Object> pay(@RequestBody Map<String, Object> request) throws InterruptedException {
        chaos.beforeRequest();
        return Map.of("paymentId", UUID.randomUUID().toString(), "orderId", String.valueOf(request.get("orderId")),
                "status", "AUTHORIZED");
    }
}

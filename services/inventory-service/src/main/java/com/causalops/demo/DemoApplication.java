package com.causalops.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;

/** Stock lookups backed by the inventory-db PostgreSQL database. */
@SpringBootApplication
@EnableScheduling
@RestController
public class DemoApplication {

    private final JdbcTemplate db;
    private final ChaosState chaos;

    public DemoApplication(JdbcTemplate db, ChaosState chaos) {
        this.db = db;
        this.chaos = chaos;
    }

    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }

    @GetMapping("/inventory/{sku}")
    public Map<String, Object> inventory(@PathVariable String sku) throws InterruptedException {
        chaos.beforeRequest();
        List<Integer> rows = db.queryForList("select quantity from inventory where sku = ?", Integer.class, sku);
        if (rows.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown sku " + sku);
        }
        return Map.of("sku", sku, "available", rows.get(0));
    }

    @GetMapping("/business")
    public Map<String, Object> business() throws InterruptedException {
        return inventory("sku-demo");
    }
}

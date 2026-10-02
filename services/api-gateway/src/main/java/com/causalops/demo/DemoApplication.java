package com.causalops.demo;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;

import java.util.Map;

/** Edge gateway of the reference system: forwards order lookups to order-service. */
@SpringBootApplication
@EnableScheduling
@RestController
public class DemoApplication {

    private final RestClient orders;
    private final ChaosState chaos;

    public DemoApplication(RestClient.Builder builder, ChaosState chaos,
                           @Value("${downstream.order-url}") String orderUrl) {
        SimpleClientHttpRequestFactory timeouts = new SimpleClientHttpRequestFactory();
        timeouts.setConnectTimeout(2_000);
        timeouts.setReadTimeout(10_000);
        this.orders = builder.baseUrl(orderUrl).requestFactory(timeouts).build();
        this.chaos = chaos;
    }

    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }

    @GetMapping("/orders/{id}")
    public Map<?, ?> order(@PathVariable String id) throws InterruptedException {
        chaos.beforeRequest();
        return orders.get().uri("/orders/{id}", id).retrieve().body(Map.class);
    }

    @GetMapping("/business")
    public Map<?, ?> business() throws InterruptedException {
        return order("demo");
    }
}

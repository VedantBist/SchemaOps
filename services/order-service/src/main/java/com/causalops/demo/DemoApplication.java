package com.causalops.demo;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;

import java.util.Map;

/** Order orchestration: checks stock in inventory-service, then authorizes payment. */
@SpringBootApplication
@EnableScheduling
@RestController
public class DemoApplication {

    private final RestClient inventory;
    private final RestClient payments;
    private final ChaosState chaos;

    public DemoApplication(RestClient.Builder builder, ChaosState chaos,
                           @Value("${downstream.inventory-url}") String inventoryUrl,
                           @Value("${downstream.payment-url}") String paymentUrl) {
        SimpleClientHttpRequestFactory timeouts = new SimpleClientHttpRequestFactory();
        timeouts.setConnectTimeout(2_000);
        timeouts.setReadTimeout(8_000);
        this.inventory = builder.clone().baseUrl(inventoryUrl).requestFactory(timeouts).build();
        this.payments = builder.clone().baseUrl(paymentUrl).requestFactory(timeouts).build();
        this.chaos = chaos;
    }

    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }

    @GetMapping("/orders/{id}")
    public Map<String, Object> order(@PathVariable String id) throws InterruptedException {
        chaos.beforeRequest();
        Map<?, ?> stock = inventory.get().uri("/inventory/{sku}", "sku-demo").retrieve().body(Map.class);
        Map<?, ?> payment = payments.post().uri("/payments").body(Map.of("orderId", id)).retrieve().body(Map.class);
        return Map.of("id", id, "inventory", stock, "payment", payment);
    }

    @GetMapping("/business")
    public Map<String, Object> business() throws InterruptedException {
        return order("demo");
    }
}

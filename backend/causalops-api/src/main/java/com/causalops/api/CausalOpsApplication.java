package com.causalops.api;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import com.causalops.api.service.FaultInjector;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
@EnableConfigurationProperties(FaultInjector.ChaosProperties.class)
public class CausalOpsApplication {
    public static void main(String[] args) {
        SpringApplication.run(CausalOpsApplication.class, args);
    }
}

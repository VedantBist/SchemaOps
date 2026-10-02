package com.causalops.api;

import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.faults.FaultInjector;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
@EnableConfigurationProperties({FaultInjector.ChaosProperties.class, EnvironmentService.BootstrapProperties.class})
public class CausalOpsApplication {
    public static void main(String[] args) {
        SpringApplication.run(CausalOpsApplication.class, args);
    }
}

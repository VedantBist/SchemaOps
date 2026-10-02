package com.causalops.api.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.Arrays;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final String[] allowedOrigins;

    public WebConfig(@Value("${causalops.cors.allowed-origins:}") String allowedOrigins) {
        this.allowedOrigins = Arrays.stream(allowedOrigins.split(","))
                .map(String::trim).filter(s -> !s.isEmpty()).toArray(String[]::new);
    }

    /** Only the explicitly configured origins (CAUSALOPS_CORS_ORIGINS) may call the API from a browser. */
    @Override
    public void addCorsMappings(CorsRegistry registry) {
        if (allowedOrigins.length == 0) return;
        registry.addMapping("/api/**")
                .allowedOrigins(allowedOrigins)
                .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                .allowedHeaders("Content-Type", "Authorization", "X-Correlation-ID");
    }

    /** Outbound HTTP (Prometheus, Loki, Tempo, AI engine, fault controls) with bounded timeouts. */
    @Bean
    RestClient restClient(RestClient.Builder builder,
                          @Value("${causalops.http.connect-timeout-ms:2000}") int connectTimeout,
                          @Value("${causalops.http.read-timeout-ms:30000}") int readTimeout) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeout);
        factory.setReadTimeout(readTimeout);
        return builder.requestFactory(factory).build();
    }
}

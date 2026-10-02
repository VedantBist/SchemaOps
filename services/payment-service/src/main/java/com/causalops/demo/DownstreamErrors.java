package com.causalops.demo;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientResponseException;

import java.util.Map;

/** A failing dependency surfaces as 502 (error) or 504 (timeout), so failures propagate realistically. */
@RestControllerAdvice
public class DownstreamErrors {

    @ExceptionHandler(RestClientResponseException.class)
    ResponseEntity<Map<String, Object>> downstreamError(RestClientResponseException e) {
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                .body(Map.of("error", "downstream returned " + e.getStatusCode().value()));
    }

    @ExceptionHandler(ResourceAccessException.class)
    ResponseEntity<Map<String, Object>> downstreamTimeout(ResourceAccessException e) {
        return ResponseEntity.status(HttpStatus.GATEWAY_TIMEOUT)
                .body(Map.of("error", "downstream unreachable or timed out"));
    }
}

package com.causalops.api.controller;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.observability.ObservabilityController;
import com.causalops.api.telemetry.PrometheusClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.NoSuchElementException;

/** Every error is returned with a real HTTP status and a JSON body describing it. */
@RestControllerAdvice
public class ApiExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler(NoSuchElementException.class)
    ResponseEntity<Map<String, Object>> notFound(Exception e) {
        return error(HttpStatus.NOT_FOUND, e.getMessage());
    }

    @ExceptionHandler({IllegalArgumentException.class, MethodArgumentTypeMismatchException.class,
            HttpMessageNotReadableException.class})
    ResponseEntity<Map<String, Object>> badRequest(Exception e) {
        return error(HttpStatus.BAD_REQUEST, e.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<Map<String, Object>> invalid(MethodArgumentNotValidException e) {
        String detail = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + " " + f.getDefaultMessage()).reduce((a, b) -> a + "; " + b).orElse("invalid request");
        return error(HttpStatus.BAD_REQUEST, detail);
    }

    @ExceptionHandler(AiEngineClient.EngineException.class)
    ResponseEntity<Map<String, Object>> engine(AiEngineClient.EngineException e) {
        HttpStatus status = HttpStatus.resolve(e.status());
        return error(status == null ? HttpStatus.BAD_GATEWAY : status, e.getMessage());
    }

    @ExceptionHandler({PrometheusClient.PrometheusException.class, ObservabilityController.UpstreamException.class})
    ResponseEntity<Map<String, Object>> upstream(RuntimeException e) {
        return error(HttpStatus.BAD_GATEWAY, e.getMessage());
    }

    @ExceptionHandler(IllegalStateException.class)
    ResponseEntity<Map<String, Object>> conflict(IllegalStateException e) {
        return error(HttpStatus.CONFLICT, e.getMessage());
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<Map<String, Object>> unexpected(Exception e) {
        log.error("Unhandled API error", e);
        return error(HttpStatus.INTERNAL_SERVER_ERROR, "Internal error: " + e.getClass().getSimpleName());
    }

    private static ResponseEntity<Map<String, Object>> error(HttpStatus status, String message) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("timestamp", Instant.now().toString());
        body.put("status", status.value());
        body.put("error", status.getReasonPhrase());
        body.put("message", message);
        return ResponseEntity.status(status).body(body);
    }
}

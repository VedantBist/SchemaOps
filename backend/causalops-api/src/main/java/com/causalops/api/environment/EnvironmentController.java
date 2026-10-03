package com.causalops.api.environment;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api/environments")
public class EnvironmentController {

    private final EnvironmentService environments;
    private final EnvironmentValidator validator;

    public EnvironmentController(EnvironmentService environments, EnvironmentValidator validator) {
        this.environments = environments;
        this.validator = validator;
    }

    /** Tries a proposed configuration against the real backends and previews the discovered topology; saves nothing. */
    @PostMapping("/validate")
    public Map<String, Object> validate(@RequestBody EnvironmentConfig config) {
        return validator.validate(config);
    }

    @GetMapping
    public List<Environment> list() {
        return environments.all();
    }

    @GetMapping("/defaults")
    public EnvironmentConfig defaults() {
        return environments.defaults();
    }

    @GetMapping("/{id}")
    public Environment get(@PathVariable UUID id) {
        return environments.get(id);
    }

    public record CreateEnvironment(String name, EnvironmentConfig config) {
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Environment create(@RequestBody CreateEnvironment body) {
        return environments.create(body.name(), body.config());
    }

    @PutMapping("/{id}/config")
    public Environment updateConfig(@PathVariable UUID id, @RequestBody EnvironmentConfig config) {
        return environments.updateConfig(id, config);
    }

    @PutMapping("/{id}/status")
    public Environment updateStatus(@PathVariable UUID id, @RequestBody Map<String, String> body) {
        return environments.updateStatus(id, body.get("status"));
    }
}

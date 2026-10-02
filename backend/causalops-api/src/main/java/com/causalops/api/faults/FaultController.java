package com.causalops.api.faults;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api/faults")
public class FaultController {

    private final FaultService faults;

    public FaultController(FaultService faults) {
        this.faults = faults;
    }

    @GetMapping
    public List<Map<String, Object>> list(@RequestParam(required = false) UUID environmentId) {
        return faults.list(environmentId);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Map<String, Object> inject(@RequestParam(required = false) UUID environmentId, @Valid @RequestBody FaultRequest request) {
        return faults.inject(environmentId, request);
    }

    @PostMapping("/{id}/stop")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void stop(@PathVariable UUID id) {
        faults.stop(id);
    }

    @PostMapping("/clear")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void clear(@RequestParam(required = false) UUID environmentId) {
        faults.clear(environmentId);
    }
}

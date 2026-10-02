package com.causalops.api.calibration;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api")
public class CalibrationController {

    private final CalibrationService calibration;

    public CalibrationController(CalibrationService calibration) {
        this.calibration = calibration;
    }

    /** Lifecycle state, learning progress, recent runs and the champion model. */
    @GetMapping("/calibration/status")
    public Map<String, Object> status(@RequestParam(required = false) UUID environmentId) {
        return calibration.status(environmentId);
    }

    /** Starts a calibration now (needs minLearningMinutes of telemetry). The run continues in the background. */
    @PostMapping("/calibration/run")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public Map<String, Object> run(@RequestParam(required = false) UUID environmentId) {
        return calibration.runNow(environmentId);
    }

    @GetMapping("/models")
    public List<Map<String, Object>> models(@RequestParam(required = false) UUID environmentId) {
        return calibration.models(environmentId);
    }
}

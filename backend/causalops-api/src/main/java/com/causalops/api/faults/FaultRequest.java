package com.causalops.api.faults;
import jakarta.validation.constraints.*; import java.util.*;
public record FaultRequest(@NotBlank @Pattern(regexp="DB_LATENCY|SERVICE_LATENCY|SERVICE_FAILURE|NETWORK_LATENCY|ERROR_RATE|CONNECTION_POOL_SATURATION") String type,@NotBlank String target,@NotBlank @Pattern(regexp="LOW|MEDIUM|HIGH|CRITICAL") String severity,@Min(1) @Max(3600) int durationSeconds,Map<String,Object> parameters){}

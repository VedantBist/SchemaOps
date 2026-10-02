package com.causalops.api.engine;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Exposes the AI engine under {@code /api/engine/**}, so browsers and integrations talk to a
 * single origin and the engine never needs to be published. Status codes and bodies pass
 * through unchanged, including errors.
 */
@RestController
public class EngineProxyController {

    private static final String PREFIX = "/api/engine";

    private final AiEngineClient engine;

    public EngineProxyController(AiEngineClient engine) {
        this.engine = engine;
    }

    @RequestMapping(PREFIX + "/**")
    public ResponseEntity<byte[]> proxy(HttpServletRequest request, @RequestBody(required = false) byte[] body) {
        String path = request.getRequestURI().substring(request.getContextPath().length() + PREFIX.length());
        if (path.isEmpty()) path = "/";
        if (request.getQueryString() != null) path += "?" + request.getQueryString();
        MediaType type = request.getContentType() == null ? null : MediaType.parseMediaType(request.getContentType());
        return engine.forward(HttpMethod.valueOf(request.getMethod()), path, type, body);
    }
}

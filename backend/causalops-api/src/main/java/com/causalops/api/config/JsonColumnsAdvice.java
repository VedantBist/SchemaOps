package com.causalops.api.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.core.MethodParameter;
import org.springframework.http.MediaType;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice;

import java.util.*;

/**
 * JSONB columns are read as text inside the application; this turns them back into JSON in API
 * responses, so clients get {@code "affectedServices": ["a", "b"]} rather than a string holding JSON.
 */
@RestControllerAdvice
public class JsonColumnsAdvice implements ResponseBodyAdvice<Object> {

    static final Set<String> JSON_FIELDS = Set.of("affectedServices", "evidence", "payload", "factors", "parameters",
            "metrics", "components", "detail", "result", "intervention", "signals", "config");

    private final ObjectMapper json;

    public JsonColumnsAdvice(ObjectMapper json) {
        this.json = json;
    }

    @Override
    public boolean supports(MethodParameter returnType, Class<? extends HttpMessageConverter<?>> converterType) {
        return MappingJackson2HttpMessageConverter.class.isAssignableFrom(converterType);
    }

    @Override
    public Object beforeBodyWrite(Object body, MethodParameter returnType, MediaType contentType,
                                  Class<? extends HttpMessageConverter<?>> converterType,
                                  ServerHttpRequest request, ServerHttpResponse response) {
        return convert(body, null);
    }

    Object convert(Object value, String key) {
        if (value instanceof Map<?, ?> map) {
            Map<Object, Object> out = new LinkedHashMap<>();
            map.forEach((k, v) -> out.put(k, convert(v, String.valueOf(k))));
            return out;
        }
        if (value instanceof List<?> list) {
            List<Object> out = new ArrayList<>(list.size());
            for (Object v : list) out.add(convert(v, null));
            return out;
        }
        if (value instanceof String s && key != null && JSON_FIELDS.contains(key)) {
            String t = s.trim();
            if (t.startsWith("{") || t.startsWith("[")) {
                try {
                    return convert(json.readValue(t, Object.class), null);
                } catch (Exception e) {
                    return s;
                }
            }
        }
        return value;
    }
}

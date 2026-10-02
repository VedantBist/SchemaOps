package com.causalops.api.events;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.time.Instant;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Server-sent events to connected clients. Every event is sent with its type as the SSE
 * event name (clients subscribe per name, for example "incident.created").
 */
@Service
public class EventBus {

    private final Set<SseEmitter> clients = ConcurrentHashMap.newKeySet();
    private final ObjectMapper json;

    public EventBus(ObjectMapper json) {
        this.json = json;
    }

    public SseEmitter subscribe() {
        SseEmitter emitter = new SseEmitter(0L);
        clients.add(emitter);
        emitter.onCompletion(() -> clients.remove(emitter));
        emitter.onTimeout(() -> clients.remove(emitter));
        emitter.onError(e -> clients.remove(emitter));
        return emitter;
    }

    public void emit(String type, Object entityId, Object payload) {
        if (clients.isEmpty()) return;
        String data;
        try {
            data = json.writeValueAsString(Map.of(
                    "eventType", type,
                    "timestamp", Instant.now().toString(),
                    "entityId", String.valueOf(entityId),
                    "payload", payload));
        } catch (Exception e) {
            throw new IllegalStateException("Event payload is not serializable: " + type, e);
        }
        for (SseEmitter emitter : clients) {
            try {
                emitter.send(SseEmitter.event().name(type).data(data));
            } catch (Exception e) {
                clients.remove(emitter);
            }
        }
    }
}

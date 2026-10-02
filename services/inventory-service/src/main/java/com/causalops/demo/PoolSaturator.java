package com.causalops.demo;

import com.zaxxer.hikari.HikariDataSource;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * CONNECTION_POOL_SATURATION: borrows real pool connections and parks them on
 * pg_sleep, so business requests genuinely wait for (or time out on) the pool.
 */
@Component
public class PoolSaturator implements ChaosExtension {

    private final HikariDataSource pool;
    private final List<Statement> running = new CopyOnWriteArrayList<>();
    private final List<Thread> holders = new ArrayList<>();

    public PoolSaturator(HikariDataSource pool) {
        this.pool = pool;
    }

    @Override
    public boolean supports(String type) {
        return "CONNECTION_POOL_SATURATION".equals(type);
    }

    @Override
    public synchronized void start(ChaosRequest request, int durationSeconds) {
        int max = pool.getMaximumPoolSize();
        int hold = request.holdConnections() == null ? max : request.holdConnections();
        if (hold < 1 || hold > max) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "holdConnections must be between 1 and " + max);
        }
        for (int i = 0; i < hold; i++) {
            Thread t = Thread.ofVirtual().name("pool-saturator-" + i).start(() -> holdConnection(durationSeconds));
            holders.add(t);
        }
    }

    private void holdConnection(int seconds) {
        try (Connection c = pool.getConnection(); Statement s = c.createStatement()) {
            running.add(s);
            try {
                s.execute("select pg_sleep(" + seconds + ")");
            } finally {
                running.remove(s);
            }
        } catch (SQLException e) {
            // Cancelled by stop() or pool shutdown: the connection is returned to the pool.
        }
    }

    @Override
    public synchronized void stop() {
        for (Statement s : running) {
            try {
                s.cancel();
            } catch (SQLException ignored) {
                // Statement already finished.
            }
        }
        holders.forEach(Thread::interrupt);
        holders.clear();
    }
}

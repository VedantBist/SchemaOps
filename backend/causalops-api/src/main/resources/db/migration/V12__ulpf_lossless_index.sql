-- Exact "proven lossless" figure: count the (normally zero) events whose proof failed, via a tiny partial index,
-- instead of summing per-source counters that a concurrent re-normalization can race.
CREATE INDEX ulpf_events_not_lossless ON ulpf_events (uid) WHERE NOT lossless;

/** One request at a time; polling starts after completion, and disposal ignores late responses. */
export function requestLoop<T>(load: () => Promise<T>, callbacks: {
  start: () => void; success: (value: T) => void; error: (error: unknown) => void; settled: () => void;
}, intervalMs?: number) {
  let disposed = false;
  let running = false;
  let queued = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const run = async () => {
    if (disposed) return;
    if (running) { queued = true; return; }
    clearTimeout(timer);
    running = true;
    callbacks.start();
    try {
      const result = await load();
      if (!disposed) callbacks.success(result);
    } catch (error) {
      if (!disposed) callbacks.error(error);
    } finally {
      running = false;
      if (!disposed) {
        callbacks.settled();
        if (queued) { queued = false; void run(); }
        else if (intervalMs) timer = setTimeout(run, intervalMs);
      }
    }
  };
  return {
    reload: () => { void run(); },
    dispose: () => { disposed = true; clearTimeout(timer); },
  };
}

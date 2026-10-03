import { useEffect, useRef } from 'react';
import { apiUrl, getToken } from '../api/client';

/** Named server-sent events the platform emits (see EventBus on the API). */
export const EVENT_TYPES = [
  'telemetry.ingested', 'topology.changed', 'incident.created', 'incident.updated', 'incident.resolved', 'rca.completed',
  'remediation.recommended', 'remediation.approval_required', 'remediation.executing', 'remediation.executed',
  'remediation.verified', 'remediation.failed', 'remediation.rolled_back', 'remediation.escalated', 'remediation.dry_run',
  'calibration.started', 'calibration.completed', 'calibration.failed', 'fault.started', 'fault.stopped', 'faults.cleared',
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

/**
 * Subscribes to the platform's event stream and calls ``onEvent`` for the given event types.
 * EventSource cannot set headers, so the session token travels as ``access_token``.
 */
export function useEvents(types: readonly string[], onEvent: (type: string, data: unknown) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const key = types.join(',');

  useEffect(() => {
    const source = new EventSource(apiUrl('/events/stream', { access_token: getToken() }));
    const listeners = types.map((type) => {
      const l = (e: MessageEvent) => {
        let data: unknown = e.data;
        try {
          data = JSON.parse(e.data);
        } catch {
          /* plain text payload */
        }
        handler.current(type, data);
      };
      source.addEventListener(type, l as EventListener);
      return [type, l] as const;
    });
    return () => {
      listeners.forEach(([type, l]) => source.removeEventListener(type, l as EventListener));
      source.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

import { Redis } from "ioredis";

const LIVE_CHANNEL = "brain:live";

export interface LiveEvent {
  id: string;
  type: string;
  source: string;
  severity: string;
  orgId: string;
  flowId: string;
  at: string;
  payload: unknown;
}

/**
 * Fan-out for SSE clients. The worker (packages/brain) and the HTTP API
 * (apps/api) are separate processes/containers (see infra/docker-compose*),
 * so an in-memory subscriber registry in one process is invisible to
 * clients connected to the other — that was a real bug in an earlier
 * draft of this feature. Redis Pub/Sub is the actual cross-process fan-out:
 * the worker PUBLISHes after a durably-processed event; apps/api's SSE
 * route SUBSCRIBEs on its own connection (a connection in subscribe mode
 * can only do pub/sub, so it must be separate from the Streams consumer
 * connection in bus.ts) and forwards matching messages to connected clients.
 *
 * Pub/Sub delivery is best-effort (no replay, no ack) — correct for a live
 * "what's happening now" view. Durable history stays in the event lake via
 * /v1/brain/flows/:flowId and /v1/brain/health/history.
 */
export function publishLive(evt: LiveEvent, publisher: Redis): void {
  void publisher.publish(LIVE_CHANNEL, JSON.stringify(evt)).catch(() => {
    // a dropped live-view update must never affect durable ingestion
  });
}

/** Returns an unsubscribe function that closes the dedicated connection. */
export function subscribeLive(onEvent: (evt: LiveEvent) => void): () => void {
  const sub = new Redis(process.env.REDIS_URL ?? "redis://localhost:6379");
  void sub.subscribe(LIVE_CHANNEL);
  sub.on("message", (_channel, message) => {
    try {
      onEvent(JSON.parse(message) as LiveEvent);
    } catch {
      // malformed message on the live channel — drop it, don't crash the subscriber
    }
  });
  return () => {
    void sub.unsubscribe(LIVE_CHANNEL).finally(() => sub.disconnect());
  };
}

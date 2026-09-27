import type { RawEvent } from "./bus.js";

export interface WebhookSubscription {
  url: string;
  secret: string;
  eventTypes: string[]; // "*" matches everything
}

export type WebhookSubscriptionProvider = (orgId: string) => Promise<WebhookSubscription[]>;

/**
 * The module-10 webhook store (org-configured subscriptions + HMAC
 * signing) isn't part of this consolidation, so this defaults to a no-op
 * provider. Call `setWebhookSubscriptionProvider()` once that store
 * exists instead of editing this file — keeps the ingest loop working
 * either way instead of crashing on an undefined import.
 */
let provider: WebhookSubscriptionProvider = async () => [];

export function setWebhookSubscriptionProvider(p: WebhookSubscriptionProvider) {
  provider = p;
}

export async function fanOutWebhooks(e: RawEvent): Promise<void> {
  const orgId = e.fields.orgId;
  if (!orgId) return; // platform-level events don't fan out to org webhooks
  const subs = await provider(orgId);
  if (!subs.length) return;

  const matching = subs.filter(
    (s) => s.eventTypes.includes("*") || s.eventTypes.includes(e.fields.type),
  );
  await Promise.allSettled(matching.map((s) => deliver(s, e)));
}

async function deliver(sub: WebhookSubscription, e: RawEvent): Promise<void> {
  try {
    await fetch(sub.url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: e.id, type: e.fields.type, payload: e.fields.payload }),
    });
  } catch {
    // delivery failures are the webhook subsystem's concern (retries, dead
    // lettering) once it exists — the ingest loop must not block on them
  }
}

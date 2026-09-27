import { randomUUID } from "node:crypto";
import { trace } from "@opentelemetry/api";
import { publish } from "./bus.js";
import { EVENT_CATALOG, type BrainEventType } from "./catalog.js";

export type BrainSource =
  | "api"
  | "mcp"
  | "workers"
  | "check-runner"
  | "connector"
  | "agent"
  | "review"
  | "ethics"
  | "sla"
  | "brain";

export interface EmitInput {
  type: BrainEventType;
  source: BrainSource;
  orgId?: string;
  traceId?: string;
  flowId?: string;
  payload?: Record<string, unknown>;
  /** severity derives from the catalog; only a downward override is meaningful */
  severityOverride?: "WARN" | "ERROR" | "CRITICAL";
}

/** THE call site API. Fire-and-forget: never throws into the caller's flow. */
export function emit(input: EmitInput): void {
  void safeEmit(input).catch(() => {
    // the bus being down must never take down the flow that's calling emit()
  });
}

async function safeEmit(input: EmitInput) {
  const id = randomUUID();
  const severity = input.severityOverride ?? EVENT_CATALOG[input.type];
  const span = trace.getActiveSpan();
  if (span) span.addEvent(input.type, { ...toAttrs(input.payload), "brain.severity": severity });

  await publish({
    id,
    at: new Date().toISOString(),
    type: input.type,
    source: input.source,
    orgId: input.orgId ?? "",
    severity,
    traceId: input.traceId ?? span?.spanContext().traceId ?? "",
    flowId: input.flowId ?? "",
    payload: JSON.stringify(input.payload ?? {}),
  });
}

// OTel span attributes must be primitives/arrays-of-primitives, not nested
// objects — the original draft spread arbitrary payload values straight in,
// which throws for anything with an object/array-of-object value.
function toAttrs(payload?: Record<string, unknown>): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const [k, v] of Object.entries(payload ?? {})) {
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") out[k] = v;
    else if (v !== undefined) out[k] = JSON.stringify(v);
  }
  return out;
}

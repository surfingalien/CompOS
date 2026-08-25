import { runAgent } from "@compos/agents";
import { brainDb } from "../db.js";
import { emit } from "../emit.js";
import type { BrainEvent } from "../generated/index.js";

export interface DiagnosisResult {
  answer: string;
  root_causes: Array<{
    hypothesis: string;
    confidence: number;
    supporting_events: string[];
    disconfirming_check: string;
  }>;
  recommended_actions: string[];
  data_gaps: string[];
}

export interface DiagnoseInput {
  question: string;
  /** Scopes retrieval to one org's events. Omit only for platform-team callers. */
  orgId?: string;
  flowId?: string;
  timeRangeHours?: number;
}

export async function gatherContext(input: DiagnoseInput) {
  const since = new Date(Date.now() - (input.timeRangeHours ?? 24) * 3_600_000);
  const events = await brainDb.brainEvent.findMany({
    where: {
      at: { gte: since },
      ...(input.orgId ? { orgId: input.orgId } : {}),
      ...(input.flowId ? { flowId: input.flowId } : {}),
    },
    orderBy: { at: "desc" },
    take: 200,
  });
  const anomalies = await brainDb.anomaly.findMany({
    where: { status: "OPEN", at: { gte: since } },
    take: 50,
  });
  const flow = input.flowId
    ? await brainDb.flowRun.findUnique({ where: { flowId: input.flowId } })
    : null;
  return { events, anomalies, flow };
}

function summarizeEvent(e: BrainEvent) {
  return {
    id: e.id,
    at: e.at,
    type: e.type,
    source: e.source,
    severity: e.severity,
    flowId: e.flowId,
    payload: e.payload,
  };
}

/** Guards the shape of whatever JSON the model produced — runAgent() guarantees valid JSON, not this shape. */
function normalizeDiagnosis(raw: unknown): DiagnosisResult {
  const parsed = (raw ?? {}) as Partial<DiagnosisResult>;
  return {
    answer: typeof parsed.answer === "string" ? parsed.answer : "",
    root_causes: Array.isArray(parsed.root_causes) ? parsed.root_causes : [],
    recommended_actions: Array.isArray(parsed.recommended_actions) ? parsed.recommended_actions : [],
    data_gaps: Array.isArray(parsed.data_gaps) ? parsed.data_gaps : [],
  };
}

/**
 * Retrieval-augmented root-cause diagnosis over the event lake, via the
 * shared @compos/agents runtime (LLM call, JSON parse + repair retry,
 * GenerationJob cost/latency logging). This module owns retrieval
 * (gatherContext) and DiagnosisResult shape-guarding; the runtime owns the
 * model call itself — see packages/agents/src/runtime.ts for why the
 * dependency runs brain -> agents and not the other way around.
 */
export async function diagnose(input: DiagnoseInput): Promise<DiagnosisResult> {
  const { events, anomalies, flow } = await gatherContext(input);

  if (!process.env.ANTHROPIC_API_KEY) {
    return {
      answer:
        `ANTHROPIC_API_KEY is not configured, so the diagnostician cannot reason over the ` +
        `${events.length} retrieved events. Returning raw context only.`,
      root_causes: [],
      recommended_actions: ["Set ANTHROPIC_API_KEY to enable narrative diagnosis."],
      data_gaps: events.length === 0 ? ["No events found in the requested time range."] : [],
    };
  }

  const raw = await runAgent("ops_diagnostician", {
    question: input.question,
    events: events.map(summarizeEvent),
    anomalies,
    flow,
  }, {
    orgId: input.orgId,
    onTelemetry: (info) => {
      emit({
        type: info.status === "SUCCEEDED" ? "llm.generation.completed" : "llm.generation.failed",
        source: "agent",
        orgId: input.orgId,
        severityOverride: info.status === "FAILED" ? "ERROR" : undefined,
        payload: {
          agent: "ops_diagnostician",
          costUsd: info.costUsd,
          latencyMs: info.latencyMs,
          ...(info.error ? { error: info.error } : {}),
        },
      });
    },
  });

  return normalizeDiagnosis(raw);
}

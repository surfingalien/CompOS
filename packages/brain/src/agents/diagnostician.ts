import Anthropic from "@anthropic-ai/sdk";
import { brainDb } from "../db.js";
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

const SYSTEM_PROMPT = `You are the operations diagnostician for a compliance platform. You investigate
platform health questions using ONLY the event-lake context provided in the user message.

RULES:
1. Every claim cites event IDs or flow IDs actually present in the supplied context.
2. Form a ranked root-cause hypothesis list. State what evidence would DISCONFIRM each —
   a diagnosis that can't be wrong is worthless.
3. Recommend runbook actions. NEVER recommend modifying compliance data (controls,
   evidence, policies, framework content) — only operational remediation.
4. If the supplied context is insufficient, say so in data_gaps. "Unknown" is a valid answer.

Respond with ONLY a JSON object of this exact shape, no prose outside it:
{ "answer": "<=5 sentences, plain English",
  "root_causes": [{ "hypothesis": string, "confidence": 0-1,
                    "supporting_events": ["id"...], "disconfirming_check": string }],
  "recommended_actions": [string],
  "data_gaps": [string] }`;

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

function extractJson(text: string): string {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end === -1) throw new Error("no JSON object found in model output");
  return text.slice(start, end + 1);
}

function parseDiagnosis(text: string, eventCount: number): DiagnosisResult {
  try {
    const parsed = JSON.parse(extractJson(text));
    return {
      answer: typeof parsed.answer === "string" ? parsed.answer : "",
      root_causes: Array.isArray(parsed.root_causes) ? parsed.root_causes : [],
      recommended_actions: Array.isArray(parsed.recommended_actions)
        ? parsed.recommended_actions
        : [],
      data_gaps: Array.isArray(parsed.data_gaps) ? parsed.data_gaps : [],
    };
  } catch {
    return {
      answer: text.slice(0, 2000),
      root_causes: [],
      recommended_actions: [],
      data_gaps: [
        `Model response was not valid JSON; ${eventCount} events were available as context.`,
      ],
    };
  }
}

/**
 * Retrieval-augmented root-cause diagnosis over the event lake.
 *
 * This is a single-shot RAG call, not an agentic tool loop — the design
 * draft this was built from assumed a `defineTool`/`runAgent` framework
 * (packages/mcp) that doesn't exist in this repo yet. When that framework
 * lands, wrap this function as its `ask_brain` tool handler rather than
 * reimplementing retrieval there.
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

  const client = new Anthropic();
  const context = JSON.stringify(
    { events: events.map(summarizeEvent), anomalies, flow },
    null,
    2,
  );

  const msg = await client.messages.create({
    model: "claude-sonnet-5",
    max_tokens: 1500,
    system: SYSTEM_PROMPT,
    messages: [
      {
        role: "user",
        content:
          `Question: ${input.question}\n\n` +
          `Event-lake context (${events.length} events, ${anomalies.length} open anomalies):\n` +
          context,
      },
    ],
  });

  const text = msg.content.find((b) => b.type === "text")?.text ?? "{}";
  return parseDiagnosis(text, events.length);
}

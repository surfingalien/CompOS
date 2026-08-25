import Anthropic from "@anthropic-ai/sdk";
import { prisma } from "@compos/db";

export interface AgentDef {
  name: string;
  promptKey: string;
  /** Used when no ACTIVE PromptTemplate row exists for promptKey. */
  fallbackSystem: string;
  model: string;
  maxTokens: number;
  budget: { maxUsdPerRun: number };
}

// Pricing per Anthropic's published rates (verified via the claude-api skill,
// not recalled from training — check there before changing these).
const PRICING: Record<string, { in: number; out: number }> = {
  "claude-sonnet-5": { in: 2 / 1e6, out: 10 / 1e6 },
};

/**
 * One agent is registered here for real: ops_diagnostician, the only AI
 * feature that actually exists in this repo (packages/brain's
 * diagnostician). The other agents referenced in the platform design —
 * policy_drafter, questionnaire_composer, impact_analyst, etc. — belong to
 * domains (policies, questionnaires, regulatory change) that have no
 * schema or package in this repo yet. Register them here when their owning
 * module is actually built, not before — an agent def with no caller and
 * no domain model behind it is exactly the "referenced but never
 * implemented" pattern this codebase has been fixing, not repeating.
 */
export const AGENTS: Record<string, AgentDef> = {
  ops_diagnostician: {
    name: "ops_diagnostician",
    promptKey: "ops_diagnostician",
    fallbackSystem:
      "You are the operations diagnostician for a compliance platform. " +
      "Investigate platform health questions using ONLY the event-lake context " +
      "provided in the user message. Cite event or flow IDs actually present in " +
      "that context. Output ONLY valid JSON, no prose outside it.",
    model: "claude-sonnet-5",
    maxTokens: 1500,
    budget: { maxUsdPerRun: 0.15 },
  },
};

async function resolveSystemPrompt(
  promptKey: string,
  fallback: string,
): Promise<{ system: string; version: number }> {
  const row = await prisma.promptTemplate.findFirst({
    where: { key: promptKey, status: "ACTIVE" },
    orderBy: { version: "desc" },
  });
  return row ? { system: row.systemPrompt, version: row.version } : { system: fallback, version: 0 };
}

function extractJson(text: string): unknown | null {
  const cleaned = text.replace(/^```(?:json)?\s*/m, "").replace(/```\s*$/m, "").trim();
  try {
    return JSON.parse(cleaned);
  } catch {
    return null;
  }
}

function costUsd(model: string, promptTokens: number, completionTokens: number): number {
  const p = PRICING[model] ?? PRICING["claude-sonnet-5"];
  return promptTokens * p.in + completionTokens * p.out;
}

export interface TelemetryInfo {
  status: "SUCCEEDED" | "FAILED";
  costUsd: number;
  latencyMs: number;
  error?: string;
}

export interface RunAgentOptions {
  orgId?: string;
  /**
   * packages/agents intentionally has no dependency on packages/brain (that
   * would make the two packages depend on each other — brain already
   * depends on agents for the diagnostician). Pass a callback here to emit
   * brain telemetry from the call site instead.
   */
  onTelemetry?: (info: TelemetryInfo) => void;
}

/** THE entry point every AI feature in this repo calls. */
export async function runAgent<T = unknown>(
  agentName: string,
  input: unknown,
  opts: RunAgentOptions = {},
): Promise<T> {
  const def = AGENTS[agentName];
  if (!def) throw new Error(`Unknown agent: ${agentName}`);
  if (!process.env.ANTHROPIC_API_KEY) {
    throw new Error(`ANTHROPIC_API_KEY is not configured — agent "${agentName}" cannot run`);
  }

  const { system, version } = await resolveSystemPrompt(def.promptKey, def.fallbackSystem);
  const userContent = typeof input === "string" ? input : JSON.stringify(input, null, 2);

  const job = await prisma.generationJob.create({
    data: {
      orgId: opts.orgId,
      promptKey: def.promptKey,
      promptVersion: version,
      model: def.model,
      inputContext: input as object,
      status: "RUNNING",
    },
  });

  const client = new Anthropic();
  const t0 = Date.now();

  try {
    const first = await client.messages.create({
      model: def.model,
      max_tokens: def.maxTokens,
      system,
      messages: [{ role: "user", content: userContent }],
    });
    const firstText = first.content.filter((b) => b.type === "text").map((b) => b.text).join("");
    let parsed = extractJson(firstText);
    let promptTokens = first.usage.input_tokens;
    let completionTokens = first.usage.output_tokens;

    // One repair retry on parse failure — never more; a model that can't
    // produce valid JSON twice needs a prompt fix, not a longer retry loop.
    if (parsed === null) {
      const repaired = await client.messages.create({
        model: def.model,
        max_tokens: def.maxTokens,
        temperature: 0,
        system,
        messages: [
          { role: "user", content: userContent },
          { role: "assistant", content: firstText },
          {
            role: "user",
            content:
              "Your previous output was not valid JSON. Output ONLY valid JSON, " +
              "no prose, no markdown code fences.",
          },
        ],
      });
      const repairedText = repaired.content
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join("");
      parsed = extractJson(repairedText);
      promptTokens += repaired.usage.input_tokens;
      completionTokens += repaired.usage.output_tokens;
    }

    if (parsed === null) {
      throw new Error(`Agent "${agentName}" produced no valid JSON after a repair retry`);
    }

    const cost = costUsd(def.model, promptTokens, completionTokens);
    const latencyMs = Date.now() - t0;
    if (cost > def.budget.maxUsdPerRun) {
      console.warn(
        `[agents] ${agentName} exceeded its per-run budget: $${cost.toFixed(4)} > $${def.budget.maxUsdPerRun}`,
      );
    }

    await prisma.generationJob.update({
      where: { id: job.id },
      data: {
        output: parsed as object,
        status: "SUCCEEDED",
        completedAt: new Date(),
        promptTokens,
        completionTokens,
        costUsd: cost,
        latencyMs,
      },
    });
    opts.onTelemetry?.({ status: "SUCCEEDED", costUsd: cost, latencyMs });

    return parsed as T;
  } catch (err) {
    const latencyMs = Date.now() - t0;
    const message = err instanceof Error ? err.message : String(err);
    await prisma.generationJob.update({
      where: { id: job.id },
      data: { status: "FAILED", error: message.slice(0, 500), completedAt: new Date() },
    });
    opts.onTelemetry?.({ status: "FAILED", costUsd: 0, latencyMs, error: message });
    throw err;
  }
}

// Named import, not default: ioredis ships CJS at runtime with ESM-shaped
// type declarations, which under NodeNext module resolution makes a
// default import resolve to the whole module namespace (not constructable)
// rather than the Redis class. The named export doesn't have that problem.
import { Redis } from "ioredis";

const STREAM = "brain:events";
const GROUP = "brain-workers";

export const bus = new Redis(process.env.REDIS_URL ?? "redis://localhost:6379", {
  maxRetriesPerRequest: 3,
});
const sub = new Redis(process.env.REDIS_URL ?? "redis://localhost:6379"); // dedicated consumer connection

export async function ensureGroup() {
  try {
    await bus.xgroup("CREATE", STREAM, GROUP, "$", "MKSTREAM");
  } catch (e) {
    if (!(e instanceof Error) || !e.message.includes("BUSYGROUP")) throw e;
  }
}

export async function publish(evt: Record<string, string>) {
  const args: (string | number)[] = [STREAM, "MAXLEN", "~", 1_000_000, "*"];
  for (const [k, v] of Object.entries(evt)) args.push(k, v);
  await bus.xadd(...(args as [string, ...string[]]));
}

export interface RawEvent {
  id: string;
  fields: Record<string, string>;
  streamId: string;
}

type XEntry = [string, string[]];

function toBatch(messages: XEntry[]): RawEvent[] {
  return messages
    .filter((m): m is XEntry => Array.isArray(m) && m[1] != null)
    .map(([streamId, flat]) => {
      const fields: Record<string, string> = {};
      for (let i = 0; i < flat.length; i += 2) fields[flat[i]] = flat[i + 1];
      return { id: fields.id, fields, streamId };
    });
}

/**
 * Long-poll consumer. Yields batches; caller ACKs after durable handling.
 *
 * Fixes vs. the original draft:
 *  - `xautoclaim` requires the literal `"COUNT"` keyword before the count
 *    argument — passing the number positionally silently mis-parses under
 *    ioredis's command builder.
 *  - `xautoclaim`'s reply is a tuple `[nextCursor, entries, deletedIds]`,
 *    not an object with a `.messages` property.
 */
export async function* consume(consumerName: string): AsyncGenerator<RawEvent[]> {
  await ensureGroup();
  for (;;) {
    // reclaim messages stuck >5min with a dead consumer (at-least-once delivery)
    const claimed = (await bus.xautoclaim(
      STREAM,
      GROUP,
      consumerName,
      300_000,
      "0",
      "COUNT",
      50,
    )) as [string, XEntry[], string[]?];
    const claimedEntries = claimed?.[1] ?? [];
    if (claimedEntries.length) yield toBatch(claimedEntries);

    const res = (await sub.xreadgroup(
      "GROUP",
      GROUP,
      consumerName,
      "COUNT",
      50,
      "BLOCK",
      5000,
      "STREAMS",
      STREAM,
      ">",
    )) as [string, XEntry[]][] | null;
    if (res) yield toBatch(res[0][1]);
  }
}

export async function ack(ids: string[]) {
  if (ids.length) await bus.xack(STREAM, GROUP, ...ids);
}

import { PrismaClient } from "../src/generated/index.js";

/**
 * Every org-scoped query must run inside a transaction that has set
 * `app.current_org` for the session — Postgres RLS policies (rls.sql,
 * not yet written) key off that setting to enforce tenant isolation at
 * the database layer, not just in application code.
 *
 * withOrgContext() is the one sanctioned way to get an org-scoped
 * client: it opens a transaction, sets the session var, runs the
 * callback, and lets the transaction close (which drops the setting).
 */
export async function withOrgContext<T>(
  prisma: PrismaClient,
  orgId: string,
  fn: (tx: Omit<PrismaClient, "$transaction">) => Promise<T>,
): Promise<T> {
  return prisma.$transaction(async (tx) => {
    await tx.$executeRawUnsafe(`SET LOCAL app.current_org = '${orgId.replace(/'/g, "''")}'`);
    return fn(tx as unknown as Omit<PrismaClient, "$transaction">);
  });
}

/** Convenience factory — same client, just named for call-site clarity. */
export function rlsClient(prisma: PrismaClient) {
  return {
    forOrg: <T>(orgId: string, fn: Parameters<typeof withOrgContext<T>>[2]) =>
      withOrgContext(prisma, orgId, fn),
  };
}

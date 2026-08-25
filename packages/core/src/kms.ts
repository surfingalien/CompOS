/**
 * Envelope-decryption surface for org-scoped integration credentials.
 *
 * NOT YET WIRED to a real KMS provider — that lands with the connectors
 * module. This exists so callers (packages/skills, connectors) have a
 * stable import today instead of each inventing their own credential
 * shape later. Calling it before a provider is configured throws a
 * loud, specific error rather than silently returning garbage.
 */
export async function decryptCredentials<T = unknown>(
  orgId: string,
  integrationType: string,
): Promise<T> {
  throw new Error(
    `decryptCredentials() has no KMS provider configured yet ` +
      `(org=${orgId}, integration=${integrationType}). ` +
      `Wire packages/core/src/kms.ts to AWS/GCP KMS before enabling connectors.`,
  );
}

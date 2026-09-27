export interface RetentionConfig {
  /** Jurisdiction-dependent; default 7y in most US financial/HR contexts. */
  statutoryRetentionDays: number;
  /** Must match the infra backup retention (Terraform var.backup_retention_days). */
  backupRetentionDays: number;
  /** Safety margin appended after both conditions clear. Default 7. */
  marginDays: number;
}

function addDays(date: Date, days: number): Date {
  const result = new Date(date.getTime());
  result.setUTCDate(result.getUTCDate() + days);
  return result;
}

/**
 * Crypto-shred date for a closed ethics case. BOTH conditions must hold
 * before the DEK is destroyed:
 *
 *  1. Statutory retention has elapsed since the case closed, AND
 *  2. every backup that could contain the wrapped DEK has itself aged out
 *     of the backup retention window — otherwise restoring a backup taken
 *     before the shred date resurrects content the case owner believed
 *     was destroyed.
 *
 * Condition 2 is evaluated from *today*, not from case-close time, because
 * a backup can be taken any day up to the shred date and each one carries
 * its own `backupRetentionDays` window forward.
 */
export function computeShredDate(cfg: RetentionConfig, caseClosedAt: Date): Date {
  if (cfg.statutoryRetentionDays < 0 || cfg.backupRetentionDays < 0 || cfg.marginDays < 0) {
    throw new Error("RetentionConfig values must be non-negative");
  }
  const statutoryOk = addDays(caseClosedAt, cfg.statutoryRetentionDays);
  const backupOk = addDays(new Date(), cfg.backupRetentionDays);
  const shred = statutoryOk.getTime() > backupOk.getTime() ? statutoryOk : backupOk;
  return addDays(shred, cfg.marginDays);
}

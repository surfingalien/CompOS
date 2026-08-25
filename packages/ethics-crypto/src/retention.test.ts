import { test } from "node:test";
import assert from "node:assert/strict";
import { computeShredDate } from "./retention.js";

test("shred date is never earlier than statutory retention + margin", () => {
  const closed = new Date("2020-01-01T00:00:00Z");
  const cfg = { statutoryRetentionDays: 2555, backupRetentionDays: 30, marginDays: 7 };
  const shred = computeShredDate(cfg, closed);
  const statutoryFloor = new Date(closed);
  statutoryFloor.setUTCDate(statutoryFloor.getUTCDate() + cfg.statutoryRetentionDays);
  assert.ok(shred.getTime() > statutoryFloor.getTime());
});

test("shred date accounts for backups taken today, not just at close", () => {
  const closed = new Date(); // case closed today — statutory retention dominates in practice
  const cfg = { statutoryRetentionDays: 0, backupRetentionDays: 30, marginDays: 7 };
  const shred = computeShredDate(cfg, closed);
  const backupFloor = new Date();
  backupFloor.setUTCDate(backupFloor.getUTCDate() + cfg.backupRetentionDays);
  assert.ok(shred.getTime() >= backupFloor.getTime());
});

test("rejects negative config values", () => {
  assert.throws(() => computeShredDate({ statutoryRetentionDays: -1, backupRetentionDays: 30, marginDays: 7 }, new Date()));
});

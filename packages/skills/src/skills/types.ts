import { z } from "zod";
import { decryptCredentials } from "@compos/core";

export interface SkillContext {
  orgId: string;
  getOrgCredentials<T = unknown>(integrationType: string): Promise<T>;
}

export interface Skill {
  id: string;
  description: string;
  inputSchema: z.ZodSchema;
  execute(ctx: SkillContext, input: unknown): Promise<unknown>;
}

const registry = new Map<string, Skill>();

export const skillRegistry = {
  register: (s: Skill) => {
    if (registry.has(s.id)) {
      throw new Error(`Skill "${s.id}" is already registered`);
    }
    registry.set(s.id, s);
  },

  async invoke<T = unknown>(id: string, orgId: string, input: unknown): Promise<T> {
    const s = registry.get(id);
    if (!s) throw new Error(`Unknown skill: ${id}`);
    const ctx: SkillContext = {
      orgId,
      getOrgCredentials: <U>(integrationType: string) =>
        decryptCredentials<U>(orgId, integrationType),
    };
    return s.execute(ctx, s.inputSchema.parse(input ?? {})) as Promise<T>;
  },

  list: () => [...registry.values()],
};

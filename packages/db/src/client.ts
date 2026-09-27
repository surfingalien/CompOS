import { PrismaClient } from "./generated/index.js";

export const prisma = new PrismaClient({
  log: process.env.NODE_ENV === "production" ? ["error"] : ["warn", "error"],
});

export { rlsClient, withOrgContext } from "./rlsClient.js";

import { PrismaClient } from "./generated/index.js";

export const brainDb = new PrismaClient({
  log: process.env.NODE_ENV === "production" ? ["error"] : ["warn", "error"],
});

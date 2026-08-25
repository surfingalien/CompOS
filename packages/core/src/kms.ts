import { KMSClient, GenerateDataKeyCommand, DecryptCommand } from "@aws-sdk/client-kms";
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { prisma, type Prisma } from "@compos/db";

const kms = new KMSClient({});

interface Sealed {
  iv: string;
  ct: string;
  tag: string;
  /** The data-encryption key, itself encrypted under the KMS master key (envelope encryption). */
  dekCt: string;
}

function requireKeyId(): string {
  const keyId = process.env.AWS_KMS_MAIN_KEY_ID;
  if (!keyId) {
    throw new Error(
      "AWS_KMS_MAIN_KEY_ID is not configured — credential storage/decryption is unavailable until it is set.",
    );
  }
  return keyId;
}

async function seal(plaintext: string): Promise<Sealed> {
  const keyId = requireKeyId();
  const dk = await kms.send(new GenerateDataKeyCommand({ KeyId: keyId, KeySpec: "AES_256" }));
  if (!dk.Plaintext || !dk.CiphertextBlob) {
    throw new Error("KMS GenerateDataKey returned no key material");
  }
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", dk.Plaintext, iv);
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return {
    iv: iv.toString("base64"),
    ct: ct.toString("base64"),
    tag: cipher.getAuthTag().toString("base64"),
    dekCt: Buffer.from(dk.CiphertextBlob).toString("base64"),
  };
}

async function unseal(sealed: Sealed): Promise<string> {
  const keyId = requireKeyId();
  const dk = await kms.send(
    new DecryptCommand({ CiphertextBlob: Buffer.from(sealed.dekCt, "base64"), KeyId: keyId }),
  );
  if (!dk.Plaintext) throw new Error("KMS Decrypt returned no key material");
  const decipher = createDecipheriv("aes-256-gcm", dk.Plaintext, Buffer.from(sealed.iv, "base64"));
  decipher.setAuthTag(Buffer.from(sealed.tag, "base64"));
  return Buffer.concat([
    decipher.update(Buffer.from(sealed.ct, "base64")),
    decipher.final(),
  ]).toString("utf8");
}

/** Connects an integration: seals credentials under KMS envelope encryption and stores them. */
export async function storeCredentials(
  orgId: string,
  integrationType: string,
  credentials: unknown,
  config: Record<string, unknown> = {},
): Promise<string> {
  const sealed = await seal(JSON.stringify(credentials));
  const integration = await prisma.integration.create({
    data: {
      orgId,
      // Cast at the boundary: this package intentionally takes a plain string
      // (not @compos/db's generated enum) so packages/skills stays decoupled
      // from the db package's generated types — Prisma rejects an invalid
      // value at the query, which is the right place for that check.
      type: integrationType as never,
      config: config as Prisma.InputJsonValue,
      encryptedCredentials: sealed as unknown as Prisma.InputJsonValue,
      status: "CONNECTED",
    },
  });
  return integration.id;
}

/** The function every SkillContext.getOrgCredentials() call resolves to. */
export async function decryptCredentials<T = unknown>(
  orgId: string,
  integrationType: string,
): Promise<T> {
  const integration = await prisma.integration.findFirst({
    where: { orgId, type: integrationType as never, status: { not: "DISCONNECTED" } },
    orderBy: { createdAt: "desc" },
  });
  if (!integration) {
    throw new Error(`No ${integrationType} integration connected for org ${orgId}`);
  }
  try {
    const plaintext = await unseal(integration.encryptedCredentials as unknown as Sealed);
    return JSON.parse(plaintext) as T;
  } catch (err) {
    await prisma.integration.update({
      where: { id: integration.id },
      data: {
        status: "ERROR",
        syncError: `Credential decrypt failed: ${String(err).slice(0, 200)}`,
      },
    });
    throw new Error(`${integrationType} credentials unreadable — re-connect the integration`);
  }
}

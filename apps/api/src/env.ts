import { z } from "zod";

const schema = z.object({
  NODE_ENV: z.enum(["development", "production", "test"]).default("development"),
  PORT: z.coerce.number().default(4000),
  HOST: z.string().default("0.0.0.0"),
  DATABASE_URL: z.string().min(1),
  REDIS_URL: z.string().default("redis://localhost:6379"),
  // Shared secret between the API and the Python worker for /internal routes.
  INTERNAL_API_TOKEN: z.string().min(16),
  // Optional bearer token protecting the public API. The web app's server-side proxy injects it.
  API_AUTH_TOKEN: z.string().optional(),
  // 32-byte key (64 hex chars or base64) used to encrypt OAuth tokens at rest.
  ENCRYPTION_KEY: z.string().min(32),
  S3_ENDPOINT: z.string().default("http://localhost:9000"),
  S3_PUBLIC_ENDPOINT: z.string().default("http://localhost:9000"),
  S3_REGION: z.string().default("us-east-1"),
  S3_BUCKET: z.string().default("freeclip"),
  S3_ACCESS_KEY: z.string().default("minioadmin"),
  S3_SECRET_KEY: z.string().default("minioadmin"),
  S3_FORCE_PATH_STYLE: z
    .string()
    .default("true")
    .transform((v) => v === "true"),
  CORS_ORIGIN: z.string().default("http://localhost:3000"),
  DEFAULT_USER_EMAIL: z.string().email().default("owner@freeclip.local"),
  SCHEDULER_SWEEP_CRON: z.string().default("*/5 * * * *"),
});

export const env = schema.parse(process.env);
export type Env = typeof env;

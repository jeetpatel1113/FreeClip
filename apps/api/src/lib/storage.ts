import {
  CreateBucketCommand,
  DeleteObjectsCommand,
  GetObjectCommand,
  HeadBucketCommand,
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { env } from "../env.js";

const base = {
  region: env.S3_REGION,
  forcePathStyle: env.S3_FORCE_PATH_STYLE,
  credentials: { accessKeyId: env.S3_ACCESS_KEY, secretAccessKey: env.S3_SECRET_KEY },
};

/** Talks to object storage from inside the docker network. */
export const s3 = new S3Client({ ...base, endpoint: env.S3_ENDPOINT });
/** Only used to sign URLs that browsers and social platforms fetch. */
const s3Public = new S3Client({ ...base, endpoint: env.S3_PUBLIC_ENDPOINT });

export async function ensureBucket(): Promise<void> {
  try {
    await s3.send(new HeadBucketCommand({ Bucket: env.S3_BUCKET }));
  } catch {
    await s3.send(new CreateBucketCommand({ Bucket: env.S3_BUCKET }));
  }
}

export function presignGet(key: string, expiresIn = 3600): Promise<string> {
  return getSignedUrl(s3Public, new GetObjectCommand({ Bucket: env.S3_BUCKET, Key: key }), { expiresIn });
}

export async function presignGetOpt(key: string | null | undefined): Promise<string | null> {
  return key ? presignGet(key) : null;
}

export function presignPut(key: string, contentType: string, expiresIn = 3600): Promise<string> {
  return getSignedUrl(
    s3Public,
    new PutObjectCommand({ Bucket: env.S3_BUCKET, Key: key, ContentType: contentType }),
    { expiresIn },
  );
}

export async function deletePrefix(prefix: string): Promise<void> {
  let token: string | undefined;
  do {
    const page = await s3.send(
      new ListObjectsV2Command({ Bucket: env.S3_BUCKET, Prefix: prefix, ContinuationToken: token }),
    );
    const objects = (page.Contents ?? []).flatMap((o) => (o.Key ? [{ Key: o.Key }] : []));
    if (objects.length) {
      await s3.send(new DeleteObjectsCommand({ Bucket: env.S3_BUCKET, Delete: { Objects: objects } }));
    }
    token = page.IsTruncated ? page.NextContinuationToken : undefined;
  } while (token);
}

# syntax=docker/dockerfile:1
FROM node:20-bookworm-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app

FROM base AS deps
COPY package.json package-lock.json ./
COPY apps/api/package.json apps/api/
COPY apps/web/package.json apps/web/
RUN npm ci --workspace apps/api --include-workspace-root=false

FROM deps AS build
COPY apps/api apps/api
RUN npx --workspace apps/api prisma generate --schema apps/api/prisma/schema.prisma \
 && npm run build --workspace apps/api \
 && npm prune --omit=dev --workspace apps/api

FROM base AS runtime
ENV NODE_ENV=production
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/apps/api/dist ./apps/api/dist
COPY --from=build /app/apps/api/prisma ./apps/api/prisma
COPY --from=build /app/apps/api/package.json ./apps/api/package.json
WORKDIR /app/apps/api
EXPOSE 4000
# Apply the schema on boot (idempotent), then start the API + scheduler.
CMD ["sh", "-c", "npx prisma db push --skip-generate && node dist/index.js"]

import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const API_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:4000").replace(/\/$/, "");
const API_TOKEN = process.env.API_AUTH_TOKEN;

/** Server-side proxy to the Fastify API so the API token never reaches the browser. */
async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path[0] === "internal") return Response.json({ error: "Not found" }, { status: 404 });

  const url = `${API_URL}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  if (API_TOKEN) headers.set("authorization", `Bearer ${API_TOKEN}`);

  const hasBody = !["GET", "HEAD"].includes(req.method);
  try {
    const upstream = await fetch(url, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      cache: "no-store",
    });
    const out = new Headers();
    const uct = upstream.headers.get("content-type");
    if (uct) out.set("content-type", uct);
    return new Response(upstream.status === 204 ? null : upstream.body, { status: upstream.status, headers: out });
  } catch {
    return Response.json({ error: "API is unreachable. Is the api container running?" }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };

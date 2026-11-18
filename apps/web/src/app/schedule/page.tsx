"use client";

import { Bot, ExternalLink, RotateCcw, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card } from "@/components/ui/primitives";
import { api, fetcher, PLATFORMS, type Post, type PostStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<PostStatus, string> = {
  SCHEDULED: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  QUEUED: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  PUBLISHING: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  PUBLISHED: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  FAILED: "border-red-500/30 bg-red-500/10 text-red-300",
  CANCELLED: "text-muted-foreground",
};

export default function SchedulePage() {
  const { data, error, mutate } = useSWR<Post[]>("/posts", fetcher, { refreshInterval: 5000 });
  const [expanded, setExpanded] = useState<string | null>(null);

  async function act(id: string, action: "cancel" | "retry") {
    await api(`/posts/${id}${action === "retry" ? "/retry" : ""}`, { method: action === "retry" ? "POST" : "DELETE" });
    mutate();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Publishing schedule</h1>
        <p className="text-sm text-muted-foreground">Manual and Auto-Pilot posts, with the publishing agent&apos;s log for each.</p>
      </div>
      {error && <Alert>{error.message}</Alert>}
      {data?.length === 0 && <p className="text-sm text-muted-foreground">Nothing scheduled yet.</p>}
      <div className="space-y-2">
        {data?.map((p) => (
          <Card key={p.id} className="p-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge className={STATUS_STYLE[p.status]}>{p.status.toLowerCase()}</Badge>
              <span className="w-32 text-sm font-medium">{PLATFORMS.find((x) => x.id === p.platform)?.label}</span>
              <Link
                href={p.clip ? `/projects/${p.clip.projectId}` : "#"}
                className="min-w-0 flex-1 truncate text-sm hover:underline"
              >
                {p.clip?.title ?? p.clipId}
              </Link>
              {p.source === "AUTOPILOT" && (
                <Badge className="text-violet-300">
                  <Bot className="h-3 w-3" /> auto-pilot
                </Badge>
              )}
              <span className="text-xs tabular-nums text-muted-foreground">
                {new Date(p.publishedAt ?? p.scheduledFor).toLocaleString()}
              </span>
              <div className="flex gap-1">
                {p.externalUrl && (
                  <Button variant="ghost" size="icon" asChild aria-label="Open post">
                    <a href={p.externalUrl} target="_blank" rel="noreferrer">
                      <ExternalLink />
                    </a>
                  </Button>
                )}
                {p.status === "FAILED" && (
                  <Button variant="ghost" size="icon" onClick={() => act(p.id, "retry")} aria-label="Retry">
                    <RotateCcw />
                  </Button>
                )}
                {(p.status === "SCHEDULED" || p.status === "QUEUED") && (
                  <Button variant="ghost" size="icon" onClick={() => act(p.id, "cancel")} aria-label="Cancel">
                    <X />
                  </Button>
                )}
                {p.agentLog?.steps?.length ? (
                  <Button variant="ghost" size="sm" onClick={() => setExpanded(expanded === p.id ? null : p.id)}>
                    Agent log
                  </Button>
                ) : null}
              </div>
            </div>
            {p.error && <p className="mt-2 text-xs text-red-300">{p.error}</p>}
            {expanded === p.id && (
              <ol className="mt-3 space-y-2 border-l pl-4 text-xs">
                {p.agentLog?.steps?.map((s, i) => (
                  <li key={i} className={cn(s.type === "tool" ? "font-mono text-muted-foreground" : "")}>
                    {s.type === "tool" ? (
                      <>
                        <span className="text-violet-300">{s.name}</span> → {s.result?.slice(0, 300)}
                      </>
                    ) : (
                      s.text
                    )}
                  </li>
                ))}
              </ol>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

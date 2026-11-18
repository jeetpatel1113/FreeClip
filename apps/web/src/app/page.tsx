"use client";

import { Film, Trash2 } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { NewProjectForm } from "@/components/new-project-form";
import { ProgressTracker } from "@/components/progress-tracker";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, Progress } from "@/components/ui/primitives";
import { api, fetcher, type Project } from "@/lib/api";
import { formatDuration, timeAgo } from "@/lib/utils";

export default function Dashboard() {
  const { data, error, mutate } = useSWR<Project[]>("/projects", fetcher, {
    // Poll quickly while anything is processing.
    refreshInterval: (d) => (d?.some((p) => p.status !== "READY" && p.status !== "FAILED") ? 2000 : 15000),
  });

  async function remove(id: string) {
    if (!confirm("Delete this project and all its clips?")) return;
    await api(`/projects/${id}`, { method: "DELETE" });
    mutate();
  }

  return (
    <div className="space-y-10">
      <div className="mx-auto max-w-2xl">
        <NewProjectForm onCreated={() => mutate()} />
      </div>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Projects</h2>
        {error && <Alert>{error.message}</Alert>}
        {data?.length === 0 && (
          <p className="text-sm text-muted-foreground">No projects yet. Paste a link above to get started.</p>
        )}
        <div className="grid gap-3">
          {data?.map((p) => (
            <Card key={p.id} className="group flex items-center gap-4 p-3 transition-colors hover:border-violet-500/40">
              <Link href={`/projects/${p.id}`} className="flex min-w-0 flex-1 items-center gap-4">
                <div className="flex h-16 w-28 shrink-0 items-center justify-center overflow-hidden rounded-md bg-secondary">
                  {p.thumbnailUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={p.thumbnailUrl} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <Film className="h-5 w-5 text-muted-foreground" />
                  )}
                </div>
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex items-center gap-2">
                    <p className="truncate font-medium">{p.title}</p>
                    {p.sourcePlatform && <Badge className="capitalize text-muted-foreground">{p.sourcePlatform}</Badge>}
                    {p.status === "READY" && (
                      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300">{p.clipCount} clips</Badge>
                    )}
                    {p.status === "FAILED" && (
                      <Badge className="border-red-500/30 bg-red-500/10 text-red-300">Failed</Badge>
                    )}
                  </div>
                  {p.status !== "READY" && p.status !== "FAILED" ? (
                    <div className="space-y-1.5">
                      <ProgressTracker status={p.status} compact />
                      <div className="flex items-center gap-3">
                        <Progress value={p.progress} className="h-1.5" />
                        <span className="w-48 shrink-0 truncate text-xs text-muted-foreground">{p.statusMessage}</span>
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {formatDuration(p.durationMs)} source · {timeAgo(p.createdAt)}
                    </p>
                  )}
                </div>
              </Link>
              <Button
                variant="ghost"
                size="icon"
                className="opacity-0 group-hover:opacity-100"
                onClick={() => remove(p.id)}
                aria-label="Delete project"
              >
                <Trash2 />
              </Button>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}

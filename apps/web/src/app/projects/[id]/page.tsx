"use client";

import { ArrowLeft, Flame, RotateCcw } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";
import useSWR from "swr";
import { ClipStudio, ClipVideo } from "@/components/clip-studio";
import { ProgressTracker } from "@/components/progress-tracker";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, Progress } from "@/components/ui/primitives";
import { api, fetcher, type Clip, type Project } from "@/lib/api";
import { formatDuration, scoreColor } from "@/lib/utils";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: project, error, mutate } = useSWR<Project>(`/projects/${id}`, fetcher, {
    refreshInterval: (p) =>
      !p || p.status !== "READY" || p.clips?.some((c) => c.status === "RENDERING" || c.status === "PENDING")
        ? 2000
        : 0,
  });
  const [openClip, setOpenClip] = useState<string | null>(null);

  if (error) return <Alert>{error.message}</Alert>;
  if (!project) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const processing = project.status !== "READY" && project.status !== "FAILED";
  const selected = project.clips?.find((c) => c.id === openClip);

  async function retry() {
    await api(`/projects/${id}/retry`, { method: "POST" });
    mutate();
  }

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Link href="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> Projects
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">{project.title}</h1>
            <p className="text-sm text-muted-foreground">
              {formatDuration(project.durationMs)} source
              {project.sourceUrl && (
                <>
                  {" · "}
                  <a href={project.sourceUrl} target="_blank" rel="noreferrer" className="underline">
                    original
                  </a>
                </>
              )}
            </p>
          </div>
          {project.status === "FAILED" && (
            <Button variant="outline" onClick={retry}>
              <RotateCcw /> Retry
            </Button>
          )}
        </div>

        <Card className="space-y-4 p-5">
          <ProgressTracker status={project.status} />
          {processing && (
            <div className="flex items-center gap-3">
              <Progress value={project.progress} />
              <span className="w-12 text-right text-xs tabular-nums text-muted-foreground">{project.progress}%</span>
            </div>
          )}
          {project.statusMessage && <p className="text-sm text-muted-foreground">{project.statusMessage}</p>}
          {project.error && <Alert className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-xs">{project.error}</Alert>}
        </Card>
      </div>

      {!!project.clips?.length && (
        <section className="space-y-4">
          <h2 className="text-lg font-semibold">Clip studio · {project.clips.length} clips</h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {project.clips.map((clip: Clip) => (
              <Card
                key={clip.id}
                className="group cursor-pointer overflow-hidden transition-colors hover:border-violet-500/50"
                onClick={() => setOpenClip(clip.id)}
              >
                <div className="relative">
                  <ClipVideo clip={clip} />
                  <Badge className={`absolute left-2 top-2 backdrop-blur ${scoreColor(clip.viralityScore)}`}>
                    <Flame className="h-3 w-3" /> {clip.viralityScore}
                  </Badge>
                  <span className="absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] tabular-nums">
                    {formatDuration(clip.durationMs)}
                  </span>
                </div>
                <div className="space-y-1 p-3">
                  <p className="line-clamp-2 text-sm font-medium">{clip.title}</p>
                  <p className="line-clamp-2 text-xs text-muted-foreground">“{clip.hook}”</p>
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}

      {selected && (
        <ClipStudio
          clip={selected}
          open={!!selected}
          onOpenChange={(o) => !o && setOpenClip(null)}
          onChanged={() => mutate()}
        />
      )}
    </div>
  );
}

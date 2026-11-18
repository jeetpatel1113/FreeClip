"use client";

import { Download, Flame, Loader2, RefreshCw, Save, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Alert, Badge, Input, Label } from "@/components/ui/primitives";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PublishDialog } from "@/components/publish-dialog";
import { api, CAPTION_STYLES, type CaptionStyle, type Clip, type Word } from "@/lib/api";
import { cn, formatDuration, scoreColor } from "@/lib/utils";

export function ClipVideo({ clip, className }: { clip: Clip; className?: string }) {
  if (clip.status !== "READY" || !clip.videoUrl) {
    return (
      <div className={cn("flex aspect-[9/16] flex-col items-center justify-center gap-2 bg-secondary text-xs text-muted-foreground", className)}>
        {clip.status === "FAILED" ? (
          <span className="px-4 text-center text-red-300">Render failed</span>
        ) : (
          <>
            <Loader2 className="h-5 w-5 animate-spin" /> Rendering…
          </>
        )}
      </div>
    );
  }
  return (
    <video
      key={clip.videoUrl}
      src={clip.videoUrl}
      poster={clip.thumbnailUrl ?? undefined}
      controls
      playsInline
      preload="metadata"
      className={cn("aspect-[9/16] w-full bg-black object-cover", className)}
    />
  );
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span>{value}</span>
      </div>
      <div className="h-1.5 rounded-full bg-secondary">
        <div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500" style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

function TranscriptEditor({ words, onChange }: { words: Word[]; onChange: (w: Word[]) => void }) {
  return (
    <div className="max-h-[50vh] overflow-y-auto rounded-md border bg-background/40 p-3 leading-8">
      {words.map((w, i) => (
        <input
          key={i}
          value={w.word}
          title={`${(w.start / 1000).toFixed(2)}s`}
          onChange={(e) => onChange(words.map((x, j) => (j === i ? { ...x, word: e.target.value } : x)))}
          style={{ width: `${Math.max(2, w.word.length + 1)}ch` }}
          className="mx-0.5 rounded bg-transparent px-0.5 text-sm hover:bg-secondary focus:bg-secondary focus:outline-none focus:ring-1 focus:ring-ring"
        />
      ))}
    </div>
  );
}

export function ClipStudio({
  clip,
  open,
  onOpenChange,
  onChanged,
}: {
  clip: Clip;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onChanged: () => void;
}) {
  const [title, setTitle] = useState(clip.title);
  const [hook, setHook] = useState(clip.hook);
  const [words, setWords] = useState<Word[]>(clip.words);
  const [style, setStyle] = useState<CaptionStyle>(clip.captionStyle);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);

  useEffect(() => {
    if (open) {
      setTitle(clip.title);
      setHook(clip.hook);
      setWords(clip.words);
      setStyle(clip.captionStyle);
      setError(null);
    }
    // Only reset when the dialog opens or a different clip is shown, not on every background poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, clip.id]);

  const dirty =
    title !== clip.title ||
    hook !== clip.hook ||
    style !== clip.captionStyle ||
    JSON.stringify(words) !== JSON.stringify(clip.words);

  async function save(rerender?: boolean) {
    setBusy(true);
    setError(null);
    try {
      const wordsChanged = JSON.stringify(words) !== JSON.stringify(clip.words);
      await api(`/clips/${clip.id}`, {
        method: "PATCH",
        json: {
          title,
          hook,
          captionStyle: style,
          words: wordsChanged ? words.filter((w) => w.word.trim()) : undefined,
          rerender,
        },
      });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const bd = clip.hookBreakdown;
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-5xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3 pr-8">
              <Badge className={scoreColor(clip.viralityScore)}>
                <Flame className="h-3 w-3" /> {clip.viralityScore}
              </Badge>
              <span className="truncate">{clip.title}</span>
            </DialogTitle>
            <DialogDescription>
              {formatDuration(clip.durationMs)} · from {formatDuration(clip.startMs)} in the source · layout {clip.layout ?? "…"}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-6 md:grid-cols-[300px_1fr]">
            <div className="space-y-3">
              <ClipVideo clip={clip} className="rounded-lg" />
              <div className="flex gap-2">
                <Button className="flex-1" onClick={() => setPublishOpen(true)} disabled={clip.status !== "READY"}>
                  <Send /> Publish
                </Button>
                {clip.videoUrl && (
                  <Button variant="outline" size="icon" asChild aria-label="Download">
                    <a href={clip.videoUrl} download target="_blank" rel="noreferrer">
                      <Download />
                    </a>
                  </Button>
                )}
              </div>
              {clip.error && <Alert className="whitespace-pre-wrap text-xs">{clip.error}</Alert>}
            </div>

            <Tabs defaultValue="hook">
              <TabsList>
                <TabsTrigger value="hook">AI hook breakdown</TabsTrigger>
                <TabsTrigger value="transcript">Transcript</TabsTrigger>
                <TabsTrigger value="style">Captions style</TabsTrigger>
              </TabsList>

              <TabsContent value="hook" className="space-y-4">
                <div className="space-y-2">
                  <Label>On-screen hook</Label>
                  <Input value={hook} onChange={(e) => setHook(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Title</Label>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} />
                </div>
                {bd && (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-3 rounded-lg border p-3">
                      <ScoreBar label="Hook strength" value={bd.scores.hook} />
                      <ScoreBar label="Flow" value={bd.scores.flow} />
                      <ScoreBar label="Value" value={bd.scores.value} />
                      <ScoreBar label="Trend fit" value={bd.scores.trend} />
                    </div>
                    <dl className="space-y-2 text-sm">
                      {[
                        ["Hook type", bd.hook_type],
                        ["First 3 seconds", bd.first_3_seconds],
                        ["Emotional trigger", bd.emotional_trigger],
                        ["Payoff", bd.payoff],
                      ].map(([k, v]) => (
                        <div key={k}>
                          <dt className="text-xs text-muted-foreground">{k}</dt>
                          <dd>{v}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}
                <div className="rounded-lg border bg-secondary/40 p-3 text-sm">
                  <p className="mb-1 text-xs text-muted-foreground">Why this clip</p>
                  {clip.reasoning}
                  {bd?.retention_notes && <p className="mt-2 text-muted-foreground">{bd.retention_notes}</p>}
                </div>
                <div className="flex flex-wrap gap-1">
                  {clip.hashtags.map((h) => (
                    <Badge key={h} className="text-muted-foreground">
                      {h}
                    </Badge>
                  ))}
                </div>
              </TabsContent>

              <TabsContent value="transcript" className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  Fix any word inline. Timing is kept, and saving re-renders the captions. Clear a word to remove it.
                </p>
                <TranscriptEditor words={words} onChange={setWords} />
              </TabsContent>

              <TabsContent value="style" className="grid gap-3 sm:grid-cols-3">
                {CAPTION_STYLES.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setStyle(s.id)}
                    className={cn(
                      "rounded-lg border p-4 text-left transition-colors hover:border-violet-400/60",
                      style === s.id && "border-violet-400 bg-violet-500/10",
                    )}
                  >
                    <div
                      className={cn(
                        "mb-3 flex h-20 items-center justify-center rounded-md bg-black text-lg",
                        s.id === "HORMOZI" && "font-black uppercase [text-shadow:_0_0_4px_#000,_3px_3px_0_#000]",
                        s.id === "MINIMALIST" && "font-medium",
                        s.id === "NEON" && "font-extrabold uppercase [text-shadow:_0_0_8px_#00f0ff,_0_0_16px_#00f0ff]",
                      )}
                    >
                      <span>
                        {s.id === "MINIMALIST" ? <span className="opacity-50">this is </span> : "THIS IS "}
                        <span
                          className={cn(
                            s.id === "HORMOZI" && "text-yellow-300",
                            s.id === "NEON" && "text-fuchsia-400",
                            s.id === "MINIMALIST" && "text-white",
                          )}
                        >
                          {s.id === "MINIMALIST" ? "viral" : "VIRAL"}
                        </span>
                      </span>
                    </div>
                    <p className="font-medium">{s.label}</p>
                    <p className="text-xs text-muted-foreground">{s.description}</p>
                  </button>
                ))}
              </TabsContent>

              {error && <Alert className="mt-4">{error}</Alert>}
              <div className="mt-6 flex justify-end gap-2">
                <Button variant="outline" onClick={() => save(true)} disabled={busy || clip.status === "RENDERING"}>
                  <RefreshCw /> Re-render
                </Button>
                <Button onClick={() => save()} disabled={busy || !dirty}>
                  {busy ? <Loader2 className="animate-spin" /> : <Save />} Save changes
                </Button>
              </div>
            </Tabs>
          </div>
        </DialogContent>
      </Dialog>
      <PublishDialog clip={clip} open={publishOpen} onOpenChange={setPublishOpen} onDone={onChanged} />
    </>
  );
}

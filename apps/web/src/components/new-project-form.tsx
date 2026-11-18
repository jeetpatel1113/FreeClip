"use client";

import { Link2, Loader2, Sparkles, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Select } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { api, CAPTION_STYLES, type CaptionStyle, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";

function uploadWithProgress(url: string, file: File, onProgress: (pct: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress((e.loaded / e.total) * 100);
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`Upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("Upload failed: storage is unreachable from the browser (check S3_PUBLIC_ENDPOINT)"));
    xhr.send(file);
  });
}

export function NewProjectForm({ onCreated }: { onCreated?: (p: Project) => void }) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [style, setStyle] = useState<CaptionStyle>("HORMOZI");
  const [broll, setBroll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!url && !file) return setError("Paste a link or choose a video file.");
    setBusy(true);
    try {
      let sourceKey: string | undefined;
      if (file) {
        const { key, url: putUrl } = await api<{ key: string; url: string }>("/uploads/presign", {
          method: "POST",
          json: { filename: file.name, contentType: file.type || "video/mp4" },
        });
        setUploadPct(0);
        await uploadWithProgress(putUrl, file, setUploadPct);
        sourceKey = key;
      }
      const project = await api<Project>("/projects", {
        method: "POST",
        json: {
          sourceUrl: file ? undefined : url.trim(),
          sourceKey,
          title: file ? file.name.replace(/\.[^.]+$/, "") : undefined,
          captionStyle: style,
          brollEnabled: broll,
        },
      });
      onCreated?.(project);
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setUploadPct(null);
    }
  }

  return (
    <Card className="border-violet-500/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-xl">
          <Sparkles className="h-5 w-5 text-violet-400" /> Create viral clips
        </CardTitle>
        <CardDescription>Paste a YouTube, Twitch or Kick link, or upload an MP4. You get 5-10 captioned 9:16 clips.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          <div className="relative">
            <Link2 className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-11 pl-9"
              placeholder="https://www.youtube.com/watch?v=…"
              value={url}
              disabled={!!file || busy}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const f = e.dataTransfer.files[0];
              if (f?.type.startsWith("video/")) {
                setFile(f);
                setUrl("");
              }
            }}
            onClick={() => inputRef.current?.click()}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-4 py-6 text-sm text-muted-foreground transition-colors hover:border-violet-400/60",
              dragging && "border-violet-400 bg-violet-500/5",
            )}
          >
            <Upload className="h-5 w-5" />
            {file ? (
              <span className="text-foreground">
                {file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB{" "}
                <button
                  type="button"
                  className="ml-2 text-xs text-violet-300 underline"
                  onClick={(e) => {
                    e.stopPropagation();
                    setFile(null);
                  }}
                >
                  remove
                </button>
              </span>
            ) : (
              <span>Drop an MP4 here or click to browse</span>
            )}
            <input
              ref={inputRef}
              type="file"
              accept="video/*"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) {
                  setFile(f);
                  setUrl("");
                }
              }}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="style">Caption style</Label>
              <Select id="style" value={style} onChange={(e) => setStyle(e.target.value as CaptionStyle)}>
                {CAPTION_STYLES.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}: {s.description}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex items-center justify-between rounded-md border px-3">
              <div>
                <p className="text-sm font-medium">B-roll overlays</p>
                <p className="text-xs text-muted-foreground">Pexels stock cutaways (needs PEXELS_API_KEY)</p>
              </div>
              <Switch checked={broll} onCheckedChange={setBroll} />
            </div>
          </div>

          {error && <Alert>{error}</Alert>}
          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}
            {uploadPct !== null ? `Uploading ${uploadPct.toFixed(0)}%` : busy ? "Starting…" : "Get clips"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

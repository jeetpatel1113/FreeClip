"use client";

import { CalendarClock, Loader2, Send } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Alert, Input, Label, Textarea } from "@/components/ui/primitives";
import { api, fetcher, PLATFORMS, type Clip, type Platform, type SocialAccount, type UserSettings } from "@/lib/api";
import { cn } from "@/lib/utils";

function localInputValue(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function PublishDialog({
  clip,
  open,
  onOpenChange,
  onDone,
}: {
  clip: Clip;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone?: () => void;
}) {
  const { data } = useSWR<{ user: UserSettings; accounts: SocialAccount[] }>(open ? "/settings" : null, fetcher);
  const connected = useMemo(
    () => new Set((data?.accounts ?? []).filter((a) => a.enabled && a.hasAccessToken).map((a) => a.platform)),
    [data],
  );
  const [selected, setSelected] = useState<Platform[]>([]);
  const [mode, setMode] = useState<"now" | "schedule">("now");
  const [when, setWhen] = useState(() => localInputValue(new Date(Date.now() + 3600_000)));
  const [captions, setCaptions] = useState<Record<string, string>>(clip.captions ?? {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  function toggle(p: Platform) {
    setSelected((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]));
  }

  async function submit() {
    setError(null);
    if (!selected.length) return setError("Pick at least one platform.");
    const scheduledFor = mode === "schedule" ? new Date(when) : null;
    if (scheduledFor && scheduledFor.getTime() < Date.now()) return setError("Pick a time in the future.");
    setBusy(true);
    try {
      await api(`/clips/${clip.id}/publish`, {
        method: "POST",
        json: {
          platforms: selected,
          scheduledFor: scheduledFor?.toISOString(),
          captions: Object.fromEntries(selected.map((p) => [p.toLowerCase(), captions[p.toLowerCase()] ?? ""])),
        },
      });
      setDone(
        scheduledFor
          ? `Scheduled for ${scheduledFor.toLocaleString()}. The publishing agent will tune each caption at post time.`
          : "Queued. The publishing agent is preparing your posts now.",
      );
      onDone?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setDone(null);
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Publish “{clip.title}”</DialogTitle>
          <DialogDescription>
            Claude reviews each post before it goes out: it adapts the hook for the platform and checks your recent
            posts for repeated topics.
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <div className="space-y-4">
            <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-200">{done}</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" asChild>
                <Link href="/schedule">View schedule</Link>
              </Button>
              <Button onClick={() => onOpenChange(false)}>Close</Button>
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="space-y-2">
              <Label>Platforms</Label>
              <div className="flex flex-wrap gap-2">
                {PLATFORMS.map((p) => {
                  const ok = connected.has(p.id);
                  return (
                    <button
                      key={p.id}
                      type="button"
                      disabled={!ok}
                      onClick={() => toggle(p.id)}
                      title={ok ? undefined : "Connect this account in Settings"}
                      className={cn(
                        "rounded-full border px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                        selected.includes(p.id) ? "border-violet-400 bg-violet-500/20 text-violet-100" : "hover:bg-accent",
                      )}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
              {data && connected.size === 0 && (
                <p className="text-xs text-muted-foreground">
                  No accounts connected yet.{" "}
                  <Link className="text-violet-300 underline" href="/settings">
                    Connect accounts
                  </Link>
                </p>
              )}
            </div>

            {selected.map((p) => (
              <div key={p} className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">{PLATFORMS.find((x) => x.id === p)?.label} caption</Label>
                <Textarea
                  rows={3}
                  value={captions[p.toLowerCase()] ?? ""}
                  onChange={(e) => setCaptions((c) => ({ ...c, [p.toLowerCase()]: e.target.value }))}
                />
              </div>
            ))}

            <div className="flex flex-wrap items-center gap-2">
              <Button variant={mode === "now" ? "secondary" : "ghost"} size="sm" onClick={() => setMode("now")}>
                <Send /> Publish now
              </Button>
              <Button variant={mode === "schedule" ? "secondary" : "ghost"} size="sm" onClick={() => setMode("schedule")}>
                <CalendarClock /> Schedule
              </Button>
              {mode === "schedule" && (
                <Input type="datetime-local" className="w-auto" value={when} onChange={(e) => setWhen(e.target.value)} />
              )}
            </div>

            {error && <Alert>{error}</Alert>}
            <div className="flex justify-end">
              <Button onClick={submit} disabled={busy || !selected.length}>
                {busy && <Loader2 className="animate-spin" />}
                {mode === "now" ? "Publish now" : "Schedule posts"}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

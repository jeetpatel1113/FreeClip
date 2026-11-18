import { Check, Loader2, X } from "lucide-react";
import type { ProjectStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

export const STEPS: { status: ProjectStatus; label: string }[] = [
  { status: "DOWNLOADING", label: "Downloading" },
  { status: "TRANSCRIBING", label: "Transcribing" },
  { status: "CURATING", label: "AI Viral Curation" },
  { status: "REFRAMING", label: "Active Speaker Cropping" },
  { status: "CAPTIONING", label: "Rendering Captions" },
  { status: "READY", label: "Ready" },
];

export function ProgressTracker({
  status,
  failedAt,
  compact = false,
}: {
  status: ProjectStatus;
  failedAt?: ProjectStatus | null;
  compact?: boolean;
}) {
  const failed = status === "FAILED";
  const current = failed ? STEPS.findIndex((s) => s.status === failedAt) : STEPS.findIndex((s) => s.status === status);
  return (
    <ol className={cn("flex w-full items-center", compact ? "gap-1" : "gap-2")}>
      {STEPS.map((step, i) => {
        const done = status === "READY" || (current >= 0 && i < current);
        const active = !failed && i === current && status !== "READY";
        const error = failed && (i === current || (current < 0 && i === 0));
        return (
          <li key={step.status} className="flex flex-1 items-center gap-2">
            <div
              className={cn(
                "flex shrink-0 items-center justify-center rounded-full border text-[10px]",
                compact ? "h-5 w-5" : "h-7 w-7",
                done && "border-emerald-500 bg-emerald-500/20 text-emerald-300",
                active && "border-violet-400 bg-violet-500/20 text-violet-200",
                error && "border-red-500 bg-red-500/20 text-red-300",
                !done && !active && !error && "border-border text-muted-foreground",
              )}
            >
              {done ? (
                <Check className="h-3 w-3" />
              ) : active ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : error ? (
                <X className="h-3 w-3" />
              ) : (
                i + 1
              )}
            </div>
            {!compact && (
              <span className={cn("hidden text-xs lg:inline", active || done ? "text-foreground" : "text-muted-foreground")}>
                {step.label}
              </span>
            )}
            {i < STEPS.length - 1 && <div className={cn("h-px flex-1", done ? "bg-emerald-500/50" : "bg-border")} />}
          </li>
        );
      })}
    </ol>
  );
}

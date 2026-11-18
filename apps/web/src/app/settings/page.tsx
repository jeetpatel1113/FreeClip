"use client";

import { Bot, CheckCircle2, Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Select } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { api, CAPTION_STYLES, fetcher, PLATFORMS, type Platform, type SocialAccount, type UserSettings } from "@/lib/api";
import { cn } from "@/lib/utils";

type FieldKey = "accountName" | "externalId" | "accessToken" | "refreshToken" | "clientId" | "clientSecret" | "meta.username" | "meta.categoryId";

const FIELDS: Record<Platform, { key: FieldKey; label: string; secret?: boolean; placeholder?: string }[]> = {
  INSTAGRAM: [
    { key: "accountName", label: "Account name", placeholder: "@yourbrand" },
    { key: "externalId", label: "Instagram user ID", placeholder: "17841400000000000" },
    { key: "accessToken", label: "Long-lived access token", secret: true },
  ],
  TIKTOK: [
    { key: "accountName", label: "Account name" },
    { key: "clientId", label: "Client key" },
    { key: "clientSecret", label: "Client secret", secret: true },
    { key: "accessToken", label: "Access token", secret: true },
    { key: "refreshToken", label: "Refresh token", secret: true },
  ],
  YOUTUBE: [
    { key: "accountName", label: "Channel name" },
    { key: "clientId", label: "OAuth client ID" },
    { key: "clientSecret", label: "OAuth client secret", secret: true },
    { key: "refreshToken", label: "Refresh token", secret: true },
    { key: "accessToken", label: "Access token (optional, refreshed automatically)", secret: true },
    { key: "meta.categoryId", label: "Category ID", placeholder: "22" },
  ],
  X: [
    { key: "meta.username", label: "Username (without @)" },
    { key: "clientId", label: "OAuth 2.0 client ID" },
    { key: "clientSecret", label: "Client secret (confidential apps)", secret: true },
    { key: "accessToken", label: "User access token", secret: true },
    { key: "refreshToken", label: "Refresh token", secret: true },
  ],
  LINKEDIN: [
    { key: "accountName", label: "Profile / page name" },
    { key: "externalId", label: "Author URN", placeholder: "urn:li:person:abc123" },
    { key: "accessToken", label: "Access token", secret: true },
  ],
};

function AccountCard({ platform, label, account, onSaved }: {
  platform: Platform;
  label: string;
  account?: SocialAccount;
  onSaved: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState(account?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    setValues({
      accountName: account?.accountName ?? "",
      externalId: account?.externalId ?? "",
      clientId: account?.clientId ?? "",
      "meta.username": String(account?.meta?.username ?? ""),
      "meta.categoryId": String(account?.meta?.categoryId ?? ""),
    });
    setEnabled(account?.enabled ?? true);
  }, [account]);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const meta: Record<string, string> = { ...(account?.meta as Record<string, string>) };
      for (const [k, v] of Object.entries(values)) if (k.startsWith("meta.")) meta[k.slice(5)] = v;
      await api(`/social-accounts/${platform}`, {
        method: "PUT",
        json: {
          accountName: values.accountName || null,
          externalId: values.externalId || null,
          clientId: values.clientId || null,
          // Blank secret fields keep what is already stored.
          accessToken: values.accessToken || "",
          refreshToken: values.refreshToken || "",
          clientSecret: values.clientSecret || "",
          meta,
          enabled,
        },
      });
      setValues((v) => ({ ...v, accessToken: "", refreshToken: "", clientSecret: "" }));
      setMsg({ ok: true, text: "Saved" });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!confirm(`Remove the ${label} credentials?`)) return;
    await api(`/social-accounts/${platform}`, { method: "DELETE" });
    onSaved();
  }

  const stored: Record<string, boolean> = {
    accessToken: !!account?.hasAccessToken,
    refreshToken: !!account?.hasRefreshToken,
    clientSecret: !!account?.hasClientSecret,
  };

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2">
            {label}
            {account?.hasAccessToken || account?.hasRefreshToken ? (
              <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300">
                <CheckCircle2 className="h-3 w-3" /> Connected
              </Badge>
            ) : (
              <Badge className="text-muted-foreground">Not connected</Badge>
            )}
          </CardTitle>
          {account?.accessTokenPreview && <CardDescription>Token {account.accessTokenPreview}</CardDescription>}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          Enabled <Switch checked={enabled} onCheckedChange={setEnabled} />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS[platform].map((f) => (
            <div key={f.key} className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">{f.label}</Label>
              <Input
                type={f.secret ? "password" : "text"}
                autoComplete="off"
                placeholder={f.secret && stored[f.key] ? "•••••• stored (leave blank to keep)" : f.placeholder}
                value={values[f.key] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
            </div>
          ))}
        </div>
        <div className="flex items-center justify-end gap-2">
          {msg && <span className={cn("text-xs", msg.ok ? "text-emerald-300" : "text-red-300")}>{msg.text}</span>}
          {account && (
            <Button variant="ghost" size="sm" onClick={disconnect}>
              <Trash2 /> Remove
            </Button>
          )}
          <Button size="sm" onClick={save} disabled={busy}>
            {busy && <Loader2 className="animate-spin" />} Save
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const { data, error, mutate } = useSWR<{ user: UserSettings; accounts: SocialAccount[] }>("/settings", fetcher);
  const [user, setUser] = useState<UserSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (data) setUser(data.user);
  }, [data]);

  async function saveSettings(patch: Partial<UserSettings>) {
    if (!user) return;
    const next = { ...user, ...patch };
    setUser(next);
    setSaving(true);
    setSaveError(null);
    try {
      await api("/settings", {
        method: "PATCH",
        json: {
          timezone: next.timezone,
          defaultCaptionStyle: next.defaultCaptionStyle,
          autopilotEnabled: next.autopilotEnabled,
          autopilotPlatforms: next.autopilotPlatforms,
          autopilotMinScore: next.autopilotMinScore,
          autopilotMaxPostsPerDay: next.autopilotMaxPostsPerDay,
          autopilotPostingHours: next.autopilotPostingHours,
        },
      });
      mutate();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  if (error) return <Alert>{error.message}</Alert>;
  if (!data || !user) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-2xl font-semibold">Accounts & Automation</h1>
        <p className="text-sm text-muted-foreground">
          Credentials are encrypted at rest (AES-256-GCM) and only decrypted by the publishing worker.
        </p>
      </div>

      <Card className={cn("border-violet-500/20", user.autopilotEnabled && "border-violet-400/60 shadow-[0_0_40px_-12px] shadow-violet-500/40")}>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Bot className="h-5 w-5 text-violet-400" /> Auto-Pilot Mode
              {saving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            </CardTitle>
            <CardDescription>
              When a project finishes, its best clips are scheduled into your posting slots automatically. Before each
              post, Claude rewrites the caption for the platform and holds back topics you posted recently.
            </CardDescription>
          </div>
          <Switch checked={user.autopilotEnabled} onCheckedChange={(v) => saveSettings({ autopilotEnabled: v })} />
        </CardHeader>
        <CardContent className="grid gap-5 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Platforms</Label>
            <div className="flex flex-wrap gap-2">
              {PLATFORMS.map((p) => {
                const on = user.autopilotPlatforms.includes(p.id);
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() =>
                      saveSettings({
                        autopilotPlatforms: on
                          ? user.autopilotPlatforms.filter((x) => x !== p.id)
                          : [...user.autopilotPlatforms, p.id],
                      })
                    }
                    className={cn(
                      "rounded-full border px-3 py-1 text-sm",
                      on ? "border-violet-400 bg-violet-500/20" : "text-muted-foreground hover:bg-accent",
                    )}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Min virality score</Label>
              <Input
                type="number"
                min={0}
                max={100}
                defaultValue={user.autopilotMinScore}
                onBlur={(e) => saveSettings({ autopilotMinScore: Number(e.target.value) })}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Max posts / day / platform</Label>
              <Input
                type="number"
                min={1}
                max={24}
                defaultValue={user.autopilotMaxPostsPerDay}
                onBlur={(e) => saveSettings({ autopilotMaxPostsPerDay: Number(e.target.value) })}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Posting hours (0-23)</Label>
              <Input
                defaultValue={user.autopilotPostingHours.join(", ")}
                onBlur={(e) =>
                  saveSettings({
                    autopilotPostingHours: e.target.value
                      .split(/[,\s]+/)
                      .map(Number)
                      .filter((n) => Number.isInteger(n) && n >= 0 && n <= 23),
                  })
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Timezone</Label>
              <Input defaultValue={user.timezone} onBlur={(e) => saveSettings({ timezone: e.target.value.trim() })} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Default caption style</Label>
            <Select
              value={user.defaultCaptionStyle}
              onChange={(e) => saveSettings({ defaultCaptionStyle: e.target.value as UserSettings["defaultCaptionStyle"] })}
            >
              {CAPTION_STYLES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </Select>
          </div>
          {saveError && <Alert className="md:col-span-2">{saveError}</Alert>}
        </CardContent>
      </Card>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Social accounts</h2>
        <p className="text-sm text-muted-foreground">
          See the README for how to get each token (Meta Graph API, TikTok Content Posting API, YouTube Data API v3, X
          API v2, LinkedIn Community Management API).
        </p>
        <div className="grid gap-4 lg:grid-cols-2">
          {PLATFORMS.map((p) => (
            <AccountCard
              key={p.id}
              platform={p.id}
              label={p.label}
              account={data.accounts.find((a) => a.platform === p.id)}
              onSaved={() => mutate()}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

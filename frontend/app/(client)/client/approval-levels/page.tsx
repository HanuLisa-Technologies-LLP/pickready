"use client";

// Approval levels config (FR-2.3): 4 levels, active toggle + approver select.

import * as React from "react";

import { apiGet, apiPut } from "@/lib/api";
import type {
  ApprovalLevelName,
  ApprovalLevelsConfig,
  HiringManager,
} from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const LEVELS: { key: ApprovalLevelName; label: string; hint: string }[] = [
  { key: "requested", label: "Requested", hint: "Initial request for the role" },
  { key: "recommended", label: "Recommended", hint: "Second-line recommendation" },
  { key: "approved", label: "Approved", hint: "Management approval" },
  { key: "ratified", label: "Ratified", hint: "Final ratification — unlocks HR access" },
];

const NONE = "__none__";

const DEFAULT_CONFIG: ApprovalLevelsConfig = {
  requested: { active: true, approver_user_id: null },
  recommended: { active: false, approver_user_id: null },
  approved: { active: false, approver_user_id: null },
  ratified: { active: true, approver_user_id: null },
};

export default function ApprovalLevelsPage() {
  const { toast } = useToast();
  const [config, setConfig] = React.useState<ApprovalLevelsConfig>(DEFAULT_CONFIG);
  const [managers, setManagers] = React.useState<HiringManager[]>([]);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    apiGet<HiringManager[] | { hiring_managers: HiringManager[] }>(
      "/companies/me/hiring-managers"
    )
      .then((res) =>
        setManagers(Array.isArray(res) ? res : res.hiring_managers ?? [])
      )
      .catch(() => {});
    apiGet<{ approval_levels?: ApprovalLevelsConfig; config?: ApprovalLevelsConfig } | ApprovalLevelsConfig>(
      "/companies/me"
    )
      .then((res) => {
        const cfg =
          (res as { approval_levels?: ApprovalLevelsConfig }).approval_levels ??
          (res as { config?: ApprovalLevelsConfig }).config;
        if (cfg && cfg.requested) {
          setConfig({ ...DEFAULT_CONFIG, ...cfg });
        }
      })
      .catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await apiPut("/companies/me/approval-levels", { config });
      toast({ title: "Approval chain saved" });
    } catch (e) {
      toast({
        title: "Save failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Approval Levels"
        description="Choose which of the 4 levels are mandatory and who approves each. Inactive levels are bypassed automatically."
        actions={
          <Button onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        }
      />
      <div className="space-y-4">
        {LEVELS.map((level, idx) => {
          const entry = config[level.key];
          return (
            <Card key={level.key}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="text-base">
                      <span className="mr-2 text-muted-foreground">
                        {idx + 1}.
                      </span>
                      {level.label}
                    </CardTitle>
                    <CardDescription>{level.hint}</CardDescription>
                  </div>
                  <div className="flex items-center gap-2">
                    <Label
                      htmlFor={`level-${level.key}`}
                      className="text-xs text-muted-foreground"
                    >
                      {entry.active ? "Active" : "Bypassed"}
                    </Label>
                    <Switch
                      id={`level-${level.key}`}
                      checked={entry.active}
                      onCheckedChange={(active) =>
                        setConfig({
                          ...config,
                          [level.key]: { ...entry, active },
                        })
                      }
                    />
                  </div>
                </div>
              </CardHeader>
              {entry.active ? (
                <CardContent>
                  <div className="space-y-2">
                    <Label>Approver</Label>
                    <Select
                      value={entry.approver_user_id ?? NONE}
                      onValueChange={(v) =>
                        setConfig({
                          ...config,
                          [level.key]: {
                            ...entry,
                            approver_user_id: v === NONE ? null : v,
                          },
                        })
                      }
                    >
                      <SelectTrigger className="w-full max-w-sm">
                        <SelectValue placeholder="Assign an approver" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>Unassigned</SelectItem>
                        {managers.map((m) => (
                          <SelectItem key={m.id} value={m.id}>
                            {m.full_name} ({m.email})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </CardContent>
              ) : null}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

"use client";

// Drishti: one strategic profile per FUNCTION, maintained by the functional
// head, updatable any time (vivekium feature 1 under C3). The five sections
// and their prompts come from the server; the critique probes under each
// box are the structured conversation's server half, one probe per claim
// that nobody could watch happen, and saving recompiles the artifact every
// assessment in the function reads. Absent, nothing changes: it is an
// enhancement layer, not a gate.

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { apiGet, apiPut } from "@/lib/api";

interface SectionDef {
  key: string;
  title: string;
  prompt: string;
}

interface ProfileOut {
  id: string;
  function_name: string;
  sections: Record<string, string>;
  critiques: Record<string, string[]>;
  updated_at: string | null;
}

export default function DrishtiPage() {
  const { toast } = useToast();
  const [sections, setSections] = React.useState<SectionDef[]>([]);
  const [profiles, setProfiles] = React.useState<ProfileOut[]>([]);
  const [functionName, setFunctionName] = React.useState("");
  const [draft, setDraft] = React.useState<Record<string, string>>({});
  const [critiques, setCritiques] = React.useState<Record<string, string[]>>({});
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    const [meta, list] = await Promise.all([
      apiGet<{ sections: SectionDef[] }>("/drishti/sections"),
      apiGet<{ profiles: ProfileOut[] }>("/drishti/functions"),
    ]);
    setSections(meta.sections);
    setProfiles(list.profiles);
  }, []);

  React.useEffect(() => {
    void load().catch(() => undefined);
  }, [load]);

  const edit = (profile: ProfileOut) => {
    setFunctionName(profile.function_name);
    setDraft(profile.sections);
    setCritiques(profile.critiques);
  };

  const save = async () => {
    setBusy(true);
    try {
      const saved = await apiPut<ProfileOut>("/drishti/functions", {
        function_name: functionName,
        ...draft,
      });
      setCritiques(saved.critiques);
      toast({ title: `Drishti saved for ${saved.function_name}` });
      await load();
    } catch (err) {
      toast({
        title: "Could not save",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Drishti, the function&apos;s strategic profile</CardTitle>
          <CardDescription>
            Once per function, by its functional head, in twenty to thirty
            minutes. It quietly informs every assessment for that function
            until you update it; without one, assessments run exactly as
            before. A leadership change does not update this by itself:
            record it here when it happens.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="max-w-sm space-y-1.5">
            <label htmlFor="fn" className="text-sm font-medium">
              Function
            </label>
            <Input
              id="fn"
              placeholder="Engineering"
              value={functionName}
              onChange={(e) => setFunctionName(e.target.value)}
            />
            <p className="text-xs">
              Matched against each job&apos;s department, so name it the way
              your jobs do.
            </p>
          </div>
          {sections.map((section) => (
            <div key={section.key} className="space-y-1.5">
              <label htmlFor={section.key} className="text-sm font-medium">
                {section.title}
              </label>
              <p className="text-xs">{section.prompt}</p>
              <Textarea
                id={section.key}
                rows={4}
                value={draft[section.key] ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, [section.key]: e.target.value }))
                }
              />
              {(critiques[section.key] ?? []).map((probe, i) => (
                <p key={i} className="text-xs font-medium">
                  {probe}
                </p>
              ))}
            </div>
          ))}
          <Button
            disabled={busy || functionName.trim().length < 2}
            onClick={() => void save()}
          >
            {busy ? "Saving" : "Save Drishti profile"}
          </Button>
        </CardContent>
      </Card>

      {profiles.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Functions with a profile</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {profiles.map((profile) => (
              <div
                key={profile.id}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div>
                  <p className="text-sm font-medium">{profile.function_name}</p>
                  <p className="text-xs">
                    {profile.updated_at
                      ? `Updated ${new Date(profile.updated_at).toLocaleDateString()}`
                      : "Saved"}
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={() => edit(profile)}>
                  Edit
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

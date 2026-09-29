"use client";

/**
 * The one department picker (the leadership release, 2026-09-29, spec 13).
 *
 * A department is a ROW now, not a string a person types: a Functional Head
 * is confined to one by its id, so a job typed "engineering " and a job typed
 * "Engineering" must land in the same department, and the way to make that
 * true is to pick from the company's list. Used by the Create Job form, the
 * job page's details editor and the Staff page's Functional Head invite.
 *
 * "Add a department" is offered to a holder of `create_job`, the capability
 * the server's POST /companies/departments asks. The server matches names
 * case-insensitively, so adding a name that exists selects the existing
 * department rather than minting a second one.
 */
import * as React from "react";

import { apiGet, apiPost } from "@/lib/api";
import type { Department } from "@/lib/types";
import { CAP } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function DepartmentPicker({
  id,
  value,
  onChange,
  required = false,
  label = "Department",
  hint,
  error,
}: {
  id: string;
  /** The selected department's id, or "" for none. */
  value: string;
  onChange: (department: Department) => void;
  required?: boolean;
  label?: string;
  hint?: string;
  /** A validation message from the form that owns the value. */
  error?: string | null;
}) {
  const { can } = usePermissions();
  const canAdd = can(CAP.createJob);
  const [departments, setDepartments] = React.useState<Department[]>([]);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [newName, setNewName] = React.useState("");
  const [adding, setAdding] = React.useState(false);
  const [addError, setAddError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let live = true;
    apiGet<Department[]>("/companies/departments").then(
      (rows) => {
        if (live) setDepartments(rows);
      },
      (error) => {
        if (live) {
          setLoadError(
            error instanceof Error ? error.message : "Could not load departments."
          );
        }
      }
    );
    return () => {
      live = false;
    };
  }, []);

  const add = async () => {
    const name = newName.trim();
    if (!name) return;
    setAdding(true);
    setAddError(null);
    try {
      const department = await apiPost<Department>("/companies/departments", {
        name,
      });
      setDepartments((current) =>
        current.some((row) => row.id === department.id)
          ? current
          : [...current, department].sort((a, b) => a.name.localeCompare(b.name))
      );
      setNewName("");
      onChange(department);
    } catch (error) {
      setAddError(
        error instanceof Error ? error.message : "Could not add the department."
      );
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="space-y-2">
      <FormField
        label={label}
        htmlFor={id}
        required={required}
        hint={hint}
        error={error ?? loadError ?? addError ?? undefined}
      >
        <Select
          value={value || undefined}
          onValueChange={(selected) => {
            const department = departments.find((row) => row.id === selected);
            if (department) onChange(department);
          }}
        >
          <SelectTrigger id={id}>
            <SelectValue
              placeholder={
                departments.length ? "Choose a department" : "No departments yet"
              }
            />
          </SelectTrigger>
          <SelectContent>
            {departments.map((department) => (
              <SelectItem key={department.id} value={department.id}>
                {department.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FormField>
      {canAdd ? (
        <div className="flex gap-2">
          <Input
            aria-label="New department name"
            placeholder="Add a department"
            value={newName}
            maxLength={255}
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void add();
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            disabled={adding || !newName.trim()}
            onClick={() => void add()}
          >
            {adding ? "Adding" : "Add"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

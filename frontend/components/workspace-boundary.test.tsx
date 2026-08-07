// @vitest-environment jsdom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { User } from "@/lib/types";
import { WorkspaceContentBoundary } from "./workspace-boundary";

afterEach(cleanup);

function user(id: string, tenantId: string, workspaceName: string): User {
  return {
    id,
    tenant_id: tenantId,
    workspace_name: workspaceName,
    role: "recruiter",
    full_name: "Shared Identity",
    email: "shared@example.test",
    email_verified: true,
    phone_verified: true,
  };
}

function StickyTenantPanel({ value }: { value: string }) {
  // Deliberately sticky: this models a page-local request/cache result that
  // does not react to prop changes. Only an actual remount clears tenant A.
  const [renderedValue] = React.useState(value);
  return <div>{renderedValue}</div>;
}

describe("WorkspaceContentBoundary", () => {
  it("removes tenant A DOM when the session switches to tenant B without a reload", () => {
    const tenantA = user("user-a", "tenant-a", "ACRM Corp");
    const tenantB = user("user-b", "tenant-b", "Specter & Co.");
    const view = render(
      <WorkspaceContentBoundary user={tenantA}>
        <StickyTenantPanel value="ACRM confidential candidate" />
      </WorkspaceContentBoundary>
    );

    expect(screen.getByText("ACRM confidential candidate")).toBeTruthy();

    view.rerender(
      <WorkspaceContentBoundary user={tenantB}>
        <StickyTenantPanel value="Specter candidate" />
      </WorkspaceContentBoundary>
    );

    expect(screen.queryByText("ACRM confidential candidate")).toBeNull();
    expect(screen.getByText("Specter candidate")).toBeTruthy();
  });
});

"use client";

import * as React from "react";

import { apiPost } from "@/lib/api";

/** Non-blocking observability only; landing rendering must not depend on it. */
export function LandingTelemetry() {
  React.useEffect(() => {
    void apiPost("/telemetry/landing-view").catch(() => {
      // The API logs failures; visitors should never see telemetry errors.
    });
  }, []);

  return null;
}

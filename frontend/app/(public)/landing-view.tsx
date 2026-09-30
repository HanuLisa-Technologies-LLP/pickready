"use client";

import { useEffect } from "react";

import { API_BASE } from "@/lib/api";

export function LandingView() {
  useEffect(() => {
    void fetch(`${API_BASE}/telemetry/landing-view`, {
      method: "POST",
      keepalive: true,
    }).catch(() => undefined);
  }, []);

  return null;
}

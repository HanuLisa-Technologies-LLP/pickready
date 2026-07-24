"use client";

import type { User as FirebaseUser } from "firebase/auth";

import { apiPost } from "@/lib/api";
import type { AuthSession } from "@/lib/types";

export async function exchangeFirebaseSession(user: FirebaseUser): Promise<AuthSession> {
  return apiPost<AuthSession>("/auth/firebase/session", {
    id_token: await user.getIdToken(),
  });
}

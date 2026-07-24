"use client";

import * as React from "react";
import Link from "next/link";
import { createUserWithEmailAndPassword, updateProfile } from "firebase/auth";
import { useRouter } from "next/navigation";

import { firebaseAuth } from "@/lib/firebase";
import { exchangeFirebaseSession } from "@/lib/firebase-session";
import { homePathForRole, useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function RegisterFlow() {
  const router = useRouter(); const { setSession } = useAuth();
  const [name, setName] = React.useState(""); const [email, setEmail] = React.useState(""); const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false); const [error, setError] = React.useState<string | null>(null);
  const submit = async (e: React.FormEvent) => { e.preventDefault(); setBusy(true); setError(null); try {
    const credential = await createUserWithEmailAndPassword(firebaseAuth, email, password);
    await updateProfile(credential.user, { displayName: name });
    const session = await exchangeFirebaseSession(credential.user);
    if (session.user.role !== "candidate") throw new Error("Candidate account required");
    setSession(session.user, session.capabilities ?? []); router.replace(homePathForRole(session.user.role));
  } catch { setError("We could not create your candidate account. Try another email or sign in instead."); } finally { setBusy(false); } };
  return <div className="flex min-h-screen items-center justify-center bg-background p-4"><Card className="w-full max-w-md"><CardHeader><CardTitle>Create your candidate account</CardTitle><CardDescription>Use an email and password to start. You can later link other Firebase sign-in methods.</CardDescription></CardHeader><CardContent><form className="space-y-4" onSubmit={submit}><div><Label htmlFor="name">Full name</Label><Input id="name" value={name} onChange={(e) => setName(e.target.value)} required /></div><div><Label htmlFor="email">Email address</Label><Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></div><div><Label htmlFor="password">Password</Label><Input id="password" type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} required /></div><Button className="w-full" disabled={busy}>{busy ? "Creating…" : "Create account"}</Button>{error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}<p className="text-center text-xs text-muted-foreground">Already registered? <Link className="underline" href="/login">Sign in</Link></p></form></CardContent></Card></div>;
}

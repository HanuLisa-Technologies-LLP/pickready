"use client";

import * as React from "react";
import { GoogleAuthProvider, RecaptchaVerifier, signInWithEmailAndPassword, signInWithPhoneNumber, signInWithPopup, type ConfirmationResult } from "firebase/auth";
import { useRouter } from "next/navigation";

import { firebaseAuth } from "@/lib/firebase";
import { exchangeFirebaseSession } from "@/lib/firebase-session";
import { homePathForRole, useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type Method = "password" | "phone";

export function LoginFlow({ title, description }: { title: string; description: string }) {
  const router = useRouter();
  const { setSession } = useAuth();
  const [candidate, setCandidate] = React.useState(true);
  const [method, setMethod] = React.useState<Method>("password");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [code, setCode] = React.useState("");
  const [confirmation, setConfirmation] = React.useState<ConfirmationResult | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const verifier = React.useRef<RecaptchaVerifier | null>(null);

  React.useEffect(() => () => verifier.current?.clear(), []);

  const finish = async (user: Parameters<typeof exchangeFirebaseSession>[0]) => {
    const session = await exchangeFirebaseSession(user);
    setSession(session.user, session.capabilities ?? []);
    router.replace(homePathForRole(session.user.role));
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await action(); } catch { setError("Sign-in could not be completed. Check your details and try again."); }
    finally { setBusy(false); }
  };

  const startPhone = () => run(async () => {
    verifier.current?.clear();
    verifier.current = new RecaptchaVerifier(firebaseAuth, "firebase-recaptcha", { size: "invisible" });
    setConfirmation(await signInWithPhoneNumber(firebaseAuth, phone, verifier.current));
  });

  return <div className="flex min-h-screen items-center justify-center bg-background p-4"><Card className="w-full max-w-md"><CardHeader className="text-center"><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader><CardContent className="space-y-4">
    <div className="grid grid-cols-2 gap-2"><Button type="button" variant={candidate ? "default" : "outline"} onClick={() => setCandidate(true)}>Candidate</Button><Button type="button" variant={!candidate ? "default" : "outline"} onClick={() => setCandidate(false)}>Team member</Button></div>
    <div className="grid grid-cols-2 gap-2"><Button type="button" variant={method === "password" ? "secondary" : "outline"} onClick={() => setMethod("password")}>Email & password</Button><Button type="button" variant={method === "phone" ? "secondary" : "outline"} onClick={() => setMethod("phone")}>Phone</Button></div>
    {method === "password" ? <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void run(async () => finish((await signInWithEmailAndPassword(firebaseAuth, email, password)).user)); }}><div><Label htmlFor="email">Email address</Label><Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></div><div><Label htmlFor="password">Password</Label><Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div><Button className="w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button></form> : !confirmation ? <div className="space-y-3"><div><Label htmlFor="phone">Mobile number</Label><Input id="phone" type="tel" placeholder="+91 98765 43210" value={phone} onChange={(e) => setPhone(e.target.value)} /></div><Button className="w-full" onClick={() => void startPhone()} disabled={busy || !phone}>Send verification code</Button></div> : <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void run(async () => finish((await confirmation.confirm(code)).user)); }}><div><Label htmlFor="code">Verification code</Label><Input id="code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} required /></div><Button className="w-full" disabled={busy}>Verify and sign in</Button></form>}
    {candidate ? <Button variant="outline" className="w-full" disabled={busy} onClick={() => void run(async () => finish((await signInWithPopup(firebaseAuth, new GoogleAuthProvider())).user))}>Continue with Google</Button> : <p className="text-center text-xs text-muted-foreground">Google sign-in is available to candidates only.</p>}
    <div id="firebase-recaptcha" />{error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
  </CardContent></Card></div>;
}

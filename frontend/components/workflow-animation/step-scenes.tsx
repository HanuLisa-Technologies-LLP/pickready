"use client";

import { motion } from "framer-motion";
import {
  Check,
  CheckCircle2,
  Circle,
  Mail,
  Send,
  Sparkles,
  Star,
} from "lucide-react";

import {
  AnimatedCursor,
  DemoWindow,
  EASE,
  GlowButton,
  MetricCard,
  SceneHeader,
  SPRING,
  TinyBadge,
} from "./workflow-frame";

const CANDIDATES = [
  { name: "Ananya Sharma", score: 94, label: "Top match" },
  { name: "Rahul Verma", score: 89, label: "Strong" },
  { name: "Meera Krishnan", score: 84, label: "Strong" },
  { name: "Arjun Kumar", score: 76, label: "Potential" },
];

export function Step1Login() {
  return (
    <div className="relative h-full bg-[radial-gradient(circle_at_50%_20%,rgba(124,58,237,.2),transparent_44%)] p-5 sm:p-8">
      <motion.div
        initial={{ opacity: 0, y: -28, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.6, ease: EASE }}
        className="mx-auto mt-3 max-w-xs rounded-2xl border border-white/10 bg-white/[0.055] p-5 shadow-2xl backdrop-blur"
      >
        <div className="mb-5">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-500/20 font-black text-violet-300">
            P
          </span>
          <h3 className="mt-3 text-base font-semibold">Welcome back</h3>
          <p className="mt-1 text-[10px] text-white/45">Continue to your PickReady workspace</p>
        </div>
        <AnimatedField label="Work email" text="hr@novacore.in" delay={0.35} />
        <AnimatedField label="Password" text="••••••••••" delay={0.95} secret />
        <motion.div
          className="mt-4"
          initial={{ opacity: 0.7 }}
          animate={{ opacity: 1, scale: [1, 1, 0.96, 1] }}
          transition={{ delay: 1.55, duration: 0.75 }}
        >
          <GlowButton>
            <Check className="h-3 w-3" /> Sign in
          </GlowButton>
        </motion.div>
      </motion.div>
      <AnimatedCursor
        points={[
          { x: 190, y: 98 },
          { x: 235, y: 148 },
          { x: 220, y: 235 },
        ]}
      />
    </div>
  );
}

function AnimatedField({
  label,
  text,
  delay,
  secret,
}: {
  label: string;
  text: string;
  delay: number;
  secret?: boolean;
}) {
  return (
    <div className="mb-3">
      <p className="mb-1 text-[9px] font-medium text-white/55">{label}</p>
      <motion.div
        initial={{ borderColor: "rgba(255,255,255,.1)" }}
        animate={{ borderColor: ["rgba(255,255,255,.1)", "rgba(167,139,250,.7)", "rgba(255,255,255,.1)"] }}
        transition={{ delay, duration: 0.85 }}
        className="h-9 rounded-lg border bg-black/20 px-3 py-2 text-[10px] text-white/80"
      >
        <motion.span
          initial={{ width: 0 }}
          animate={{ width: secret ? "5.5rem" : "7.1rem" }}
          transition={{ delay, duration: 0.75, ease: "linear" }}
          className="block overflow-hidden whitespace-nowrap"
        >
          {text}
        </motion.span>
      </motion.div>
    </div>
  );
}

export function Step2Dashboard() {
  return (
    <DemoWindow active="dashboard">
      <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} className="h-full p-4 sm:p-6">
        <SceneHeader
          eyebrow="Workspace overview"
          title="Good morning, Priya"
          action={<GlowButton>Create job</GlowButton>}
        />
        <div className="mt-4 grid grid-cols-3 gap-2">
          <MetricCard label="Open roles" value="08" />
          <MetricCard label="Profiles ready" value="126" delay={0.1} />
          <MetricCard label="Awaiting review" value="14" delay={0.2} />
        </div>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.32 }}
          className="mt-3 rounded-xl border border-white/10 bg-white/[0.035] p-3"
        >
          <p className="text-[9px] font-semibold text-white/65">Active companies</p>
          <div className="mt-2 flex gap-2">
            {["NovaCore", "FinAxis", "Trellis Labs"].map((name, index) => (
              <motion.div
                key={name}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.4 + index * 0.11 }}
                className="flex-1 rounded-lg bg-white/[0.04] p-2 text-[9px]"
              >
                <div className="mb-2 h-5 w-5 rounded-md bg-violet-400/15" />
                {name}
              </motion.div>
            ))}
          </div>
        </motion.div>
      </motion.div>
    </DemoWindow>
  );
}

export function Step3JobCreation() {
  const fields = [
    ["Job title", "Senior Backend Engineer"],
    ["Department", "Engineering"],
    ["Level", "Senior"],
    ["Location", "Bengaluru · Hybrid"],
  ];
  return (
    <DemoWindow active="jobs">
      <motion.div
        initial={{ opacity: 0, x: 45 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.55, ease: EASE }}
        className="h-full p-4 sm:p-6"
      >
        <SceneHeader eyebrow="New role" title="Create a job" action={<TinyBadge>AI-assisted draft</TinyBadge>} />
        <div className="mt-3 grid grid-cols-2 gap-2">
          {fields.map(([label, value], index) => (
            <div key={label} className="rounded-lg border border-white/10 bg-white/[0.035] p-2">
              <p className="text-[8px] text-white/40">{label}</p>
              <motion.p
                initial={{ clipPath: "inset(0 100% 0 0)" }}
                animate={{ clipPath: "inset(0 0% 0 0)" }}
                transition={{ delay: 0.2 + index * 0.22, duration: 0.48 }}
                className="mt-1 whitespace-nowrap text-[9px] font-medium"
              >
                {value}
              </motion.p>
            </div>
          ))}
        </div>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.05 }}
          className="mt-2 rounded-lg border border-white/10 bg-white/[0.035] p-2"
        >
          <p className="text-[8px] text-white/40">Job description</p>
          <div className="mt-2 space-y-1">
            {[92, 79, 86].map((width, index) => (
              <motion.div
                key={width}
                initial={{ width: 0 }}
                animate={{ width: `${width}%` }}
                transition={{ delay: 1.15 + index * 0.16, duration: 0.55, ease: EASE }}
                className="h-1.5 rounded-full bg-white/20"
              />
            ))}
          </div>
        </motion.div>
        <div className="mt-3 flex justify-end">
          <GlowButton>Publish job</GlowButton>
        </div>
      </motion.div>
    </DemoWindow>
  );
}

export function Step4Published() {
  return (
    <DemoWindow active="jobs">
      <div className="relative h-full p-4 sm:p-6">
        <SceneHeader eyebrow="Jobs" title="Your live roles" />
        <motion.div
          initial={{ opacity: 0, scale: 0.84, y: 25 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={SPRING}
          className="mt-5 rounded-xl border border-violet-400/30 bg-violet-400/[0.08] p-4"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold">Senior Backend Engineer</p>
              <p className="mt-1 text-[9px] text-white/45">Engineering · Bengaluru · Senior</p>
            </div>
            <TinyBadge tone="green">Live</TinyBadge>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: "100%" }}
                transition={{ duration: 1.35, ease: EASE }}
                className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-400"
              />
            </div>
            <span className="text-[9px] text-white/55">30 days</span>
          </div>
        </motion.div>
        <motion.div
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="absolute right-5 top-4 flex items-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-[9px] text-emerald-100"
        >
          <CheckCircle2 className="h-3.5 w-3.5" /> Job live for 30 days
        </motion.div>
      </div>
    </DemoWindow>
  );
}

export function Step5CandidateTable() {
  return (
    <DemoWindow active="candidates">
      <div className="h-full p-4 sm:p-6">
        <SceneHeader
          eyebrow="Senior Backend Engineer"
          title="Candidate workspace"
          action={<GlowButton><Sparkles className="h-3 w-3" /> Run AI matching</GlowButton>}
        />
        <div className="mt-4 overflow-hidden rounded-xl border border-white/10">
          <div className="grid grid-cols-[1.4fr_.7fr_.7fr] bg-white/[0.06] px-3 py-2 text-[8px] font-semibold uppercase tracking-wider text-white/35">
            <span>Candidate</span><span>Experience</span><span>Status</span>
          </div>
          {CANDIDATES.map((candidate, index) => (
            <motion.div
              key={candidate.name}
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.18 + index * 0.14, duration: 0.38 }}
              className="grid grid-cols-[1.4fr_.7fr_.7fr] items-center border-t border-white/[0.07] px-3 py-2.5 text-[9px]"
            >
              <span className="font-medium">{candidate.name}</span>
              <span className="text-white/50">{8 - index} years</span>
              <span><TinyBadge>Parsed</TinyBadge></span>
            </motion.div>
          ))}
        </div>
      </div>
    </DemoWindow>
  );
}

export function Step6AiMatching() {
  return (
    <DemoWindow active="candidates">
      <div className="h-full p-4 sm:p-6">
        <SceneHeader
          eyebrow="AI matching"
          title="Evidence settling into place"
          action={<TinyBadge tone="green"><Sparkles className="mr-1 h-2.5 w-2.5" /> Complete</TinyBadge>}
        />
        <div className="mt-4 space-y-2">
          {CANDIDATES.map((candidate, index) => (
            <motion.div
              layout
              key={candidate.name}
              initial={{ opacity: 0, x: index % 2 ? 16 : -16 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: index * 0.12, ...SPRING }}
              className="grid grid-cols-[1.25fr_1fr_auto] items-center gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-[9px] font-medium">{candidate.name}</p>
                {index === 0 ? <span className="mt-0.5 flex items-center gap-1 text-[7px] text-amber-200"><Star className="h-2.5 w-2.5 fill-current" /> Top match</span> : null}
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${candidate.score}%` }}
                  transition={{ delay: 0.35 + index * 0.13, duration: 1, ease: EASE }}
                  className="h-full rounded-full bg-gradient-to-r from-violet-500 via-indigo-400 to-emerald-400"
                />
              </div>
              <motion.span
                initial={{ opacity: 0, scale: 0.6 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.8 + index * 0.12, ...SPRING }}
                className="w-8 text-right text-[10px] font-bold text-violet-200"
              >
                {candidate.score}%
              </motion.span>
            </motion.div>
          ))}
        </div>
        <div className="mt-3 grid grid-cols-4 gap-1.5 text-center text-[7px] text-white/45">
          {["Skills", "Experience", "Role", "Education"].map((label, index) => (
            <motion.div
              key={label}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 1 + index * 0.1 }}
              className="rounded-md bg-white/[0.035] py-1.5"
            >
              {label}
            </motion.div>
          ))}
        </div>
      </div>
    </DemoWindow>
  );
}

export function Step7Invitations() {
  return (
    <DemoWindow active="candidates">
      <div className="h-full p-4 sm:p-6">
        <SceneHeader eyebrow="Selection" title="Invite the strongest evidence" />
        <div className="mt-3 space-y-1.5">
          {CANDIDATES.map((candidate, index) => {
            const selected = index < 3;
            return (
              <motion.div
                key={candidate.name}
                animate={{ backgroundColor: selected ? "rgba(124,58,237,.11)" : "rgba(255,255,255,.025)" }}
                transition={{ delay: 0.2 + index * 0.12 }}
                className="flex items-center gap-3 rounded-lg border border-white/10 px-3 py-2"
              >
                <motion.span
                  initial={{ scale: 0.7 }}
                  animate={{ scale: 1 }}
                  transition={{ delay: 0.15 + index * 0.13, ...SPRING }}
                  className="grid h-4 w-4 place-items-center rounded border border-white/20"
                >
                  {selected ? (
                    <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.3 + index * 0.13, ...SPRING }}>
                      <Check className="h-3 w-3 text-violet-300" />
                    </motion.span>
                  ) : null}
                </motion.span>
                <span className="flex-1 text-[9px] font-medium">{candidate.name}</span>
                <span className="text-[9px] text-white/45">{candidate.score}%</span>
              </motion.div>
            );
          })}
        </div>
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.85 }}
          className="mt-3 flex items-center justify-between gap-3"
        >
          <span className="text-[9px] text-white/45">3 candidates selected</span>
          <GlowButton success><CheckCircle2 className="h-3 w-3" /> Invitations sent</GlowButton>
        </motion.div>
      </div>
    </DemoWindow>
  );
}

export function Step8PfiReport() {
  const axes = [
    [80, 16],
    [137, 57],
    [116, 124],
    [44, 124],
    [23, 57],
  ];
  return (
    <DemoWindow active="candidates" compact>
      <motion.div
        initial={{ opacity: 0, x: 55 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.58, ease: EASE }}
        className="grid h-full gap-3 p-4 sm:grid-cols-[1.15fr_.85fr] sm:p-5"
      >
        <div className="min-w-0">
          <SceneHeader eyebrow="PickReady intelligence" title="PPI Assessment Report · Ananya Sharma" />
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="mt-3 rounded-lg border border-white/10 bg-white/[0.035] p-3"
          >
            <p className="text-[8px] font-semibold text-violet-200">Overall summary</p>
            <p className="mt-1.5 text-[8px] leading-4 text-white/55">
              Strong system design judgment, clear ownership and dependable collaboration. Interview for scale trade-offs and stakeholder influence.
            </p>
          </motion.div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {[
              ["Profile match", "Highly matching"],
              ["Behavioural competencies", "Highly matching"],
              ["Technical depth", "Matching"],
              ["Validation", "Verified"],
            ].map(([label, value], index) => (
              <motion.div
                key={label}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.38 + index * 0.1 }}
                className="rounded-lg border border-white/10 p-2"
              >
                <p className="text-[7px] text-white/35">{label}</p>
                <p className="mt-1 text-[8px] font-semibold">{value}</p>
              </motion.div>
            ))}
          </div>
        </div>
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.42, duration: 0.55, ease: EASE }}
          className="relative hidden place-items-center rounded-xl border border-violet-400/20 bg-violet-400/[0.055] sm:grid"
        >
          <div className="absolute left-3 top-3">
            <p className="text-[8px] font-semibold text-violet-200">Performance fingerprint</p>
            <p className="mt-1 text-[7px] text-white/35">Five evidence dimensions</p>
          </div>
          <svg viewBox="0 0 160 145" className="mt-6 h-40 w-40 overflow-visible">
            {[58, 43, 29].map((radius) => (
              <polygon
                key={radius}
                points={axes.map(([x, y]) => `${80 + ((x - 80) * radius) / 64},${72 + ((y - 72) * radius) / 64}`).join(" ")}
                fill="none"
                stroke="rgba(255,255,255,.10)"
                strokeWidth="1"
              />
            ))}
            {axes.map(([x, y], index) => (
              <line key={index} x1="80" y1="72" x2={x} y2={y} stroke="rgba(255,255,255,.09)" />
            ))}
            <motion.polygon
              points="80,23 127,59 108,112 49,116 30,59"
              fill="rgba(124,58,237,.28)"
              stroke="#a78bfa"
              strokeWidth="2"
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: 1, opacity: 1 }}
              transition={{ delay: 0.7, duration: 1.15, ease: EASE }}
            />
            {[[80,23],[127,59],[108,112],[49,116],[30,59]].map(([x,y], index) => (
              <motion.circle key={index} cx={x} cy={y} r="2.7" fill="#c4b5fd" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 1.25 + index * .08, ...SPRING }} />
            ))}
          </svg>
        </motion.div>
      </motion.div>
    </DemoWindow>
  );
}

export function Step9Shortlist() {
  return (
    <DemoWindow active="candidates" compact>
      <div className="relative h-full p-4 sm:p-5">
        <SceneHeader
          eyebrow="Decision"
          title="Ananya is ready for the next conversation"
          action={<TinyBadge tone="green">Shortlisted</TinyBadge>}
        />
        <motion.div
          initial={{ opacity: 0, y: 25 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: EASE }}
          className="mx-auto mt-3 max-w-md overflow-hidden rounded-xl border border-white/10 bg-white/[0.045]"
        >
          <div className="flex items-center gap-2 border-b border-white/10 px-4 py-3">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-violet-500/20">
              <Mail className="h-3.5 w-3.5 text-violet-200" />
            </span>
            <div>
              <p className="text-[9px] font-semibold">Shortlist email</p>
              <p className="text-[7px] text-white/35">To: ananya.sharma@example.com</p>
            </div>
          </div>
          <div className="p-4">
            <p className="text-[9px] font-semibold">You&apos;re shortlisted for Senior Backend Engineer</p>
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.4, duration: 0.8 }}
              className="mt-3 text-[8px] leading-4 text-white/50"
            >
              Hi Ananya, your experience and assessment show a strong fit for this role. We&apos;d like to invite you to the next conversation with the team.
            </motion.p>
            <motion.div
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.9, ...SPRING }}
              className="mt-4 flex justify-end"
            >
              <GlowButton success><Send className="h-3 w-3" /> Email sent</GlowButton>
            </motion.div>
          </div>
        </motion.div>
        <motion.div
          initial={{ opacity: 0, scale: 0.75 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 1.15, ...SPRING }}
          className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-full border border-emerald-400/25 bg-emerald-400/10 px-3 py-1.5 text-[8px] text-emerald-100"
        >
          <CheckCircle2 className="h-3 w-3" /> Workflow complete · every decision recorded
        </motion.div>
      </div>
    </DemoWindow>
  );
}

export const WORKFLOW_SCENES = [
  Step1Login,
  Step2Dashboard,
  Step3JobCreation,
  Step4Published,
  Step5CandidateTable,
  Step6AiMatching,
  Step7Invitations,
  Step8PfiReport,
  Step9Shortlist,
] as const;

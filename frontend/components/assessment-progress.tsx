import { Check, Circle } from "lucide-react";

export function AssessmentSteps({
  answered,
  total,
}: {
  answered: number;
  total: number;
}) {
  const ratio = total ? Math.min(1, answered / total) : 0;
  const current = Math.min(4, Math.floor(ratio * 5));
  const labels = ["Start", "Foundation", "Core", "Applied", "Complete"];

  return (
    <ol
      className="grid grid-cols-5 rounded-2xl border border-border bg-surface px-3 py-4 shadow-card"
      aria-label="Assessment stages"
    >
      {labels.map((label, index) => {
        const completed = ratio >= (index + 1) / labels.length;
        const active = !completed && index === current;
        return (
          <li key={label} className="relative flex flex-col items-center gap-2">
            {index ? (
              <span
                className={`absolute right-1/2 top-3 h-0.5 w-full ${
                  completed ? "bg-brand-600" : "bg-border"
                }`}
                aria-hidden="true"
              />
            ) : null}
            <span
              className={`relative z-10 grid h-6 w-6 place-items-center rounded-full ${
                completed
                  ? "bg-brand-600 text-white"
                  : active
                    ? "border-4 border-brand-100 bg-brand-600"
                    : "bg-surface text-muted-foreground"
              }`}
              aria-current={active ? "step" : undefined}
            >
              {completed ? (
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
              ) : active ? null : (
                <Circle className="h-5 w-5" aria-hidden="true" />
              )}
            </span>
            <span className="hidden text-[11px] font-medium sm:block">
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function AssessmentProgress({
  answered,
  total,
}: {
  answered: number;
  total: number;
}) {
  const percent = total ? Math.round((answered / total) * 100) : 0;
  return (
    <aside className="h-fit rounded-2xl border border-border bg-surface p-5 text-center shadow-card lg:sticky lg:top-24">
      <p className="text-sm font-semibold">Assessment Progress</p>
      <div
        className="mx-auto mt-4 grid h-32 w-32 place-items-center rounded-full"
        role="progressbar"
        aria-label="Assessment progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        style={{
          background: `conic-gradient(hsl(var(--brand-600)) ${percent}%, hsl(var(--border)) ${percent}% 100%)`,
        }}
      >
        <div className="grid h-24 w-24 place-items-center rounded-full bg-surface">
          <div>
            <p className="text-2xl font-bold">{percent}%</p>
            <p className="text-xs">Completed</p>
          </div>
        </div>
      </div>
      <p className="mt-4 text-sm font-semibold">
        {answered} / {total} Questions Answered
      </p>
      <p className="mt-1 text-xs">
        Re-asks and clarification probes do not change this count.
      </p>
    </aside>
  );
}

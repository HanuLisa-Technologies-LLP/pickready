/**
 * The batched event queue between the detectors and the ingestion route.
 *
 * Detectors call `enqueue`; the queue posts batches of at most
 * `event_batch_max` every `FLUSH_INTERVAL_MS`, and posts AT ONCE when a Path A
 * event arrives, because the session is ending and the candidate should learn
 * that from a screen rather than from a request that fails a minute later.
 *
 * The response decides everything. A `warning` on the response is shown; a
 * `termination` ends the session. The client never counts warnings and never
 * decides a consequence, which is what "the server is authoritative" has to
 * mean in the client.
 *
 * FAILURES ARE RETRIED, NOT DROPPED, with one exception. A transport failure,
 * a 5xx, a 503 from an unreachable Redis and a 429 all put the batch back and
 * retry with exponential backoff, because a dropped event is a monitoring gap
 * the report cannot see. The exception is a 4xx that says the request itself
 * is malformed, which no retry repairs; that batch is dropped and counted, and
 * a non-zero `rejected` is a defect in this client, not in the network.
 */
import type { ApiError } from "@/lib/api";

import { type ClientEventType, isImmediate } from "./catalog";
import { type EventIn, type IngestOut, type TerminationOut, type WarningOut, isClientFault, isSessionEnded } from "./api";

/**
 * Transport pacing, not a behavioural threshold: how long a Path B or C event
 * waits for company before it is posted. Short enough that a live warning
 * follows the thing that caused it; long enough that a burst of focus changes
 * is one request rather than ten.
 */
export const FLUSH_INTERVAL_MS = 3000;

export interface EventDraft {
  event_type: ClientEventType;
  occurred_at?: string;
  duration_ms?: number | null;
  confidence?: number | null;
  question_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface EventQueueOptions {
  batchMax: number;
  /** The ceiling on retry backoff. */
  maxBackoffMs: number;
  post: (events: EventIn[]) => Promise<IngestOut>;
  onWarning: (warning: WarningOut, warningsUsed: number) => void;
  onTermination: (termination: TerminationOut) => void;
  /** The server answered 409: the session is over. Plain-language message. */
  onSessionEnded: (message: string) => void;
  flushIntervalMs?: number;
  now?: () => number;
}

export class EventQueue {
  private pending: EventIn[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight = false;
  private stopped = false;
  private backoffMs = 0;
  private readonly flushIntervalMs: number;
  private readonly now: () => number;
  /** Batches refused as malformed. Exposed so the integrity self-check can
   *  see that events are being lost, which is a defect and not a gap. */
  rejected = 0;

  constructor(private readonly options: EventQueueOptions) {
    this.flushIntervalMs = options.flushIntervalMs ?? FLUSH_INTERVAL_MS;
    this.now = options.now ?? Date.now;
  }

  get pendingCount(): number {
    return this.pending.length;
  }

  enqueue(draft: EventDraft): void {
    if (this.stopped) return;
    this.pending.push({
      event_type: draft.event_type,
      occurred_at: draft.occurred_at ?? new Date(this.now()).toISOString(),
      duration_ms: draft.duration_ms ?? null,
      confidence: draft.confidence ?? null,
      question_id: draft.question_id ?? null,
      metadata: draft.metadata ?? {},
    });
    if (isImmediate(draft.event_type)) {
      this.clearTimer();
      void this.flush();
      return;
    }
    if (this.timer === null && !this.inFlight) {
      this.timer = setTimeout(() => {
        this.timer = null;
        void this.flush();
      }, this.flushIntervalMs);
    }
  }

  /** Post everything pending, in batches of `batchMax`. */
  async flush(): Promise<void> {
    if (this.inFlight || this.stopped || this.pending.length === 0) return;
    this.inFlight = true;
    const batch = this.pending.splice(0, this.options.batchMax);
    try {
      const result = await this.options.post(batch);
      this.backoffMs = 0;
      if (result.warning) {
        this.options.onWarning(result.warning, result.warnings_used);
      }
      if (result.termination) {
        this.stop();
        this.options.onTermination(result.termination);
        return;
      }
    } catch (error) {
      if (isSessionEnded(error)) {
        this.stop();
        this.options.onSessionEnded((error as ApiError).message);
        return;
      }
      if (isClientFault(error)) {
        this.rejected += 1;
      } else {
        this.pending.unshift(...batch);
        this.backoffMs = Math.min(
          this.options.maxBackoffMs,
          this.backoffMs === 0 ? this.flushIntervalMs : this.backoffMs * 2
        );
        this.inFlight = false;
        this.schedule(this.backoffMs);
        return;
      }
    } finally {
      this.inFlight = false;
    }
    if (this.pending.length > 0) {
      // More than one batch was waiting, or an event arrived mid-flight. A
      // Path A event in the remainder deserves the same immediacy it had when
      // it was queued.
      const urgent = this.pending.some((event) => isImmediate(event.event_type));
      this.schedule(urgent ? 0 : this.flushIntervalMs);
    }
  }

  stop(): void {
    this.stopped = true;
    this.clearTimer();
  }

  private schedule(delayMs: number): void {
    if (this.stopped || this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, delayMs);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

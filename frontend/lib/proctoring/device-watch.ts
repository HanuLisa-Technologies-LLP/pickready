/**
 * The camera and the microphone going away, and coming back (Phase 3,
 * 2026-09-24; master prompt Appendix B section 4).
 *
 * THE RULE THIS SERVES. A lost camera or microphone PAUSES the assessment:
 * an explicit warning, a two-minute grace to fix it, at most two pauses per
 * session, and a third loss or a grace that runs out ends it. Every part of
 * that rule is the SERVER's: it opens the pause, stamps the deadline, counts
 * the allowance and ends the session. What the browser owns is the one thing
 * only the browser can see, which is whether a track is live, and this module
 * turns what the monitors see into the events the server decides on.
 *
 * FOUR REPORTS, AND WHEN EACH IS SENT.
 *
 *   - A PERMISSION revoked is reported at once (`CAMERA_PERMISSION_LOST`,
 *     `MIC_PERMISSION_LOST`). It is a deliberate act and no amount of waiting
 *     brings the device back.
 *   - A STREAM that fails is given `device_glitch_seconds` to come back on its
 *     own. Back in time, it was a glitch and is logged as one
 *     (`CAMERA_STREAM_INTERRUPTED`, `MIC_STREAM_INTERRUPTED`) and nothing
 *     pauses. Still
 *     gone at the deadline, it is reported (`CAMERA_STREAM_FAILED`,
 *     `MIC_STREAM_FAILED`) with how long it has been down, and the server
 *     opens the pause. Reporting a USB hiccup as a pause would spend one of
 *     the candidate's two for a cable that reseated itself.
 *   - `DEVICE_RECOVERED` is sent ONCE, when EVERY device lost in the episode
 *     is live again, and only if something in the episode was reported. A
 *     camera back while the microphone is still gone is not a recovery, and
 *     telling the server it was would lift the pause over a session that is
 *     still half blind.
 *
 * WHAT THIS MODULE DOES NOT DO. It does not open a device (the monitors do,
 * and they call `lost` and `recovered`), it does not count pauses and it does
 * not decide an ending. A client that counted would one day disagree with the
 * server about whether this was the second pause or the third, and the
 * candidate would be told the wrong one.
 */
import type { ClientEventType } from "./catalog";
import type { EventDraft } from "./events";

export type Device = "camera" | "microphone";

/** `permission`: the browser says the site may no longer use the device.
 *  `stream`: the permission stands and the track ended anyway (unplugged,
 *  claimed by another application, a driver reset). */
export type LossKind = "permission" | "stream";

/**
 * How often a monitor retries a failed stream on its own. A measurement
 * resolution rather than a threshold: it is how finely a glitch can be told
 * from a failure, and asking a missing device every second costs nothing the
 * assessment would notice. Named here because both monitors use it.
 */
export const DEVICE_RETRY_MS = 1000;

const PERMISSION_EVENT: Record<Device, ClientEventType> = {
  camera: "CAMERA_PERMISSION_LOST",
  microphone: "MIC_PERMISSION_LOST",
};

const STREAM_EVENT: Record<Device, ClientEventType> = {
  camera: "CAMERA_STREAM_FAILED",
  microphone: "MIC_STREAM_FAILED",
};

const GLITCH_EVENT: Record<Device, ClientEventType> = {
  camera: "CAMERA_STREAM_INTERRUPTED",
  microphone: "MIC_STREAM_INTERRUPTED",
};

interface Outage {
  since: number;
  kind: LossKind;
  /** Whether a pause-opening event has been sent for this outage. */
  reported: boolean;
  timer: ReturnType<typeof setTimeout> | null;
}

export interface DeviceWatchOptions {
  /** `device_glitch_seconds`, in milliseconds. */
  glitchMs: number;
  emit: (draft: EventDraft) => void;
  /** Called whenever the set of lost devices changes, with the new set. */
  onChange?: (lost: readonly Device[]) => void;
  now?: () => number;
}

export class DeviceWatch {
  private readonly outages = new Map<Device, Outage>();
  /** When the current episode began: the first loss while nothing was lost. */
  private episodeStart: number | null = null;
  /** The devices reported during the current episode, in report order. */
  private readonly episodeReported: Device[] = [];
  private stopped = false;
  private readonly now: () => number;

  constructor(private readonly options: DeviceWatchOptions) {
    this.now = options.now ?? Date.now;
  }

  isLost(device: Device): boolean {
    return this.outages.has(device);
  }

  lostDevices(): Device[] {
    return [...this.outages.keys()];
  }

  /** The kind of the device's current outage, or null when it is live. */
  lossKind(device: Device): LossKind | null {
    return this.outages.get(device)?.kind ?? null;
  }

  lost(device: Device, kind: LossKind): void {
    if (this.stopped) return;
    const at = this.now();
    const existing = this.outages.get(device);
    if (existing) {
      // A stream failure that turns out to be a revoked permission is the
      // stronger fact, and it is reported the moment it is known: the glitch
      // allowance exists for hardware, not for a decision the candidate made.
      if (kind === "permission" && existing.kind === "stream") {
        existing.kind = "permission";
        this.clearTimer(existing);
        this.report(device, PERMISSION_EVENT[device], existing, at);
        this.changed();
      }
      return;
    }
    if (this.outages.size === 0) this.episodeStart = at;
    const outage: Outage = { since: at, kind, reported: false, timer: null };
    this.outages.set(device, outage);
    if (kind === "permission") {
      this.report(device, PERMISSION_EVENT[device], outage, at);
    } else {
      outage.timer = setTimeout(() => {
        outage.timer = null;
        if (this.stopped || this.outages.get(device) !== outage) return;
        this.report(device, STREAM_EVENT[device], outage, this.now());
        this.changed();
      }, this.options.glitchMs);
    }
    this.changed();
  }

  recovered(device: Device): void {
    if (this.stopped) return;
    const outage = this.outages.get(device);
    if (!outage) return;
    const at = this.now();
    this.clearTimer(outage);
    this.outages.delete(device);
    if (!outage.reported) {
      this.options.emit({
        event_type: GLITCH_EVENT[device],
        duration_ms: Math.round(at - outage.since),
        metadata: {},
      });
    }
    if (this.outages.size === 0) {
      if (this.episodeReported.length > 0 && this.episodeStart !== null) {
        this.options.emit({
          event_type: "DEVICE_RECOVERED",
          duration_ms: Math.round(at - this.episodeStart),
          metadata: { devices: [...this.episodeReported] },
        });
      }
      this.episodeStart = null;
      this.episodeReported.length = 0;
    }
    this.changed();
  }

  /** Whether any loss in the current episode has been reported to the server,
   *  which is what makes the pause the server's rather than a glitch. */
  reportedThisEpisode(): boolean {
    return this.episodeReported.length > 0;
  }

  stop(): void {
    this.stopped = true;
    for (const outage of this.outages.values()) this.clearTimer(outage);
  }

  private report(device: Device, eventType: ClientEventType, outage: Outage, at: number): void {
    outage.reported = true;
    if (!this.episodeReported.includes(device)) this.episodeReported.push(device);
    this.options.emit({
      event_type: eventType,
      // A permission revoked is reported the instant it is seen, so it has no
      // duration yet; a failed stream has been down for the glitch allowance.
      duration_ms: eventType === PERMISSION_EVENT[device] ? null : Math.round(at - outage.since),
      metadata: {},
    });
  }

  private clearTimer(outage: Outage): void {
    if (outage.timer !== null) clearTimeout(outage.timer);
    outage.timer = null;
  }

  private changed(): void {
    this.options.onChange?.(this.lostDevices());
  }
}

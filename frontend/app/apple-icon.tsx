import { ImageResponse } from "next/og";

/**
 * The iOS home-screen icon. Same monogram as `app/icon.tsx`, drawn at 180 so
 * the strokes stay proportional rather than being an upscaled 32.
 *
 * Apple renders this on a home screen with no white card around it, so the
 * tile paints its own white background for the same reason the header mark
 * sits on a white tile: the navy stroke would otherwise disappear against a
 * dark wallpaper.
 */
export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          background: "#ffffff",
        }}
      >
        <svg width="180" height="180" viewBox="0 0 48 48">
          <path
            d="M18 14 L24 4 L30 14 M34 18 L44 24 L34 30 M30 34 L24 44 L18 34 M14 30 L4 24 L14 18"
            stroke="#1B2B4B"
            strokeWidth="4"
            strokeLinecap="butt"
            strokeLinejoin="miter"
            fill="none"
          />
          <circle cx="24" cy="24" r="6" fill="#0F6B5C" />
        </svg>
      </div>
    ),
    size,
  );
}

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
        <svg width="180" height="180" viewBox="0 0 32 32">
          <path
            d="M7 7.5 L16 25"
            stroke="#012654"
            strokeWidth="5"
            strokeLinecap="square"
            fill="none"
          />
          <path
            d="M25 7.5 L16 25"
            stroke="#00888A"
            strokeWidth="5"
            strokeLinecap="square"
            fill="none"
          />
        </svg>
      </div>
    ),
    size,
  );
}

import { ImageResponse } from "next/og";

/**
 * The browser-tab icon, DRAWN rather than shipped as a raster.
 *
 * 512 square, which is the size `public/icon.png` was: the mark is two
 * strokes, so a browser downscaling it to 16 or 32 in a tab strip loses
 * nothing, and the one artefact doubles as the Organization logo in
 * `components/site-json-ld.tsx`, where Google wants at least 112.
 *
 * The three files this replaces (`favicon.ico`, `icon.png`,
 * `apple-touch-icon.png`) were the previous logo, so every tab of a product
 * named ReadyPick was flying the OLD NAME'S INITIALS. Rename change 01 covers
 * all screens, and a favicon is the one that follows the user into their tab
 * strip and their bookmarks.
 *
 * Generated from the SAME geometry as `components/brand/logo.tsx`: a navy
 * stroke and a teal stroke meeting at the foot of a V, which is the
 * navy-to-teal transition the wordmark makes across "Vivek" and "ium". Navy
 * is structure and teal is evidence (DESIGN.md), and teal-600 is right here
 * because this is a FILL rather than text.
 *
 * GEOMETRY, NOT IDENTITY, exactly as the logo component says: a plain
 * monogram standing in until the owner commissions a real mark.
 */
export const size = { width: 512, height: 512 };
export const contentType = "image/png";

export default function Icon() {
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
        <svg width="512" height="512" viewBox="0 0 48 48">
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

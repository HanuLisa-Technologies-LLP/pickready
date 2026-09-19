import { ImageResponse } from "next/og";

/**
 * The Open Graph card, generated rather than shipped as an asset.
 *
 * `public/` carries a favicon, an apple touch icon and two brand JPEGs, and
 * none of them is a 1200x630 social card. Pointing `openGraph.images` at a
 * path that does not exist is the failure this avoids: the metadata validates,
 * the tag renders, and every share shows a blank rectangle.
 *
 * File-based metadata, so Next wires `og:image`, its dimensions and its type
 * automatically for every route under the root layout. X falls back to
 * `og:image` when `twitter:image` is absent, so one card serves both.
 *
 * Dependency-free on purpose: no font fetch, no remote asset, no runtime
 * network call. The colours are the brand's navy canvas and teal accent,
 * written as literals because this file is rendered by Satori and never sees
 * the Tailwind token layer.
 */

export const alt = "ReadyPick, the candidate intelligence platform";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const NAVY = "#0A2540";
const TEAL = "#0D9688";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          backgroundColor: NAVY,
          padding: "96px",
        }}
      >
        <div
          style={{
            display: "flex",
            width: "96px",
            height: "10px",
            backgroundColor: TEAL,
          }}
        />
        <div
          style={{
            display: "flex",
            marginTop: "56px",
            fontSize: "132px",
            fontWeight: 700,
            letterSpacing: "-0.055em",
            color: "#FFFFFF",
          }}
        >
          <span>Ready</span>
          <span style={{ color: TEAL }}>Pick</span>
        </div>
        <div
          style={{
            display: "flex",
            marginTop: "36px",
            fontSize: "46px",
            fontWeight: 600,
            letterSpacing: "-0.02em",
            color: "#FFFFFF",
          }}
        >
          The candidate intelligence platform
        </div>
        <div
          style={{
            display: "flex",
            marginTop: "24px",
            fontSize: "30px",
            color: TEAL,
          }}
        >
          readypick.ai
        </div>
      </div>
    ),
    { ...size }
  );
}

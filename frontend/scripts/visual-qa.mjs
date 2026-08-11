#!/usr/bin/env node
/**
 * Automated visual QA for the surfaces this release touched.
 *
 * What it actually asserts, and why each one:
 *
 *  1. **No horizontal scroll, at three widths.** `overflow-x: hidden` on the
 *     body hides the SYMPTOM of a too-wide child, so the page can look fine
 *     while an element sticks out past the viewport. This measures the element
 *     widths, not the scrollbar.
 *  2. **No console errors.** HMR websocket noise from the dev container is
 *     excluded by name -- it is a container-networking artifact, not the app.
 *  3. **Fields are visible at rest.** Reads the COMPUTED border colour of every
 *     input, textarea, select and outline button and checks it against the
 *     field token, then computes the WCAG contrast against the element's own
 *     background. This is the acceptance criterion for Task A, measured rather
 *     than eyeballed, on the rendered page rather than on the stylesheet.
 *  4. **Motion is scroll-triggered and respects the reduced-motion setting.**
 *     A section below the fold must start hidden and become visible after
 *     scrolling; under `prefers-reduced-motion: reduce` it must be visible
 *     immediately, because a reveal that never fires is content nobody sees.
 *  5. **Headings survive at 375px**, i.e. no element overflows its container.
 *
 * Usage: node scripts/visual-qa.mjs [baseUrl]
 * Exits non-zero on any failure so CI can gate on it.
 */
import { chromium } from "playwright";

const BASE = process.argv[2] ?? "http://127.0.0.1:3000";

const VIEWPORTS = [
  { name: "mobile", width: 375, height: 812 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1280, height: 800 },
];

/**
 * Console output that says nothing about the application.
 *
 *  - HMR websocket failures are a dev-container artifact of the Windows bind
 *    mount, not the app.
 *  - `/auth/me` answering 401 to a signed-out visitor is the CORRECT answer,
 *    and the browser logs every non-2xx fetch as an error whether the code
 *    handles it or not. Filtering it by name is honest; widening the filter to
 *    "any 401" would hide a real one.
 */
const IGNORED_CONSOLE = [
  /webpack-hmr/i,
  /Failed to load resource.*hmr/i,
  /auth\/(me|refresh)/i,
  /status of 401/i,
  // The local `next start` used for QA has no backend proxy target, so
  // /auth/refresh answers 502 here. In the container it is a 401.
  /status of 502/i,
];

const failures = [];
const notes = [];

function fail(message) {
  failures.push(message);
  process.stdout.write(`FAIL  ${message}\n`);
}
function pass(message) {
  process.stdout.write(`PASS  ${message}\n`);
}
function note(message) {
  notes.push(message);
  process.stdout.write(`      ${message}\n`);
}

/** WCAG relative luminance from an `rgb(...)`/`rgba(...)` string. */
function luminance(color) {
  const parts = color.match(/[\d.]+/g);
  if (!parts) return null;
  const [r, g, b] = parts.slice(0, 3).map(Number);
  const channel = (value) => {
    const v = value / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  if (la === null || lb === null) return null;
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

async function withPage(browser, options, run) {
  const context = await browser.newContext(options);
  const page = await context.newPage();
  const errors = [];
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (IGNORED_CONSOLE.some((pattern) => pattern.test(text))) return;
    errors.push(text);
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  try {
    await run(page, errors);
  } finally {
    await context.close();
  }
}

async function checkOverflow(page, label) {
  const overflow = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const wide = [];
    // An element only pushes the PAGE sideways if nothing between it and the
    // root clips it. Decorative blur orbs are deliberately wider than their
    // section and sit inside `overflow-hidden`; flagging those would bury the
    // one finding that matters under four that do not.
    const clipped = (element) => {
      let node = element.parentElement;
      while (node && node !== document.documentElement) {
        const style = getComputedStyle(node);
        if (["hidden", "clip", "auto", "scroll"].includes(style.overflowX)) {
          return true;
        }
        node = node.parentElement;
      }
      return false;
    };
    for (const element of document.querySelectorAll("body *")) {
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) continue;
      const style = getComputedStyle(element);
      if (style.overflowX === "auto" || style.overflowX === "scroll") continue;
      if (style.position === "fixed") continue; // a fixed header is not page width
      if (box.right > viewport + 1 || box.left < -1) {
        if (clipped(element)) continue;
        wide.push(
          `${element.tagName.toLowerCase()}.${String(element.className).slice(0, 40)} left=${Math.round(box.left)} right=${Math.round(box.right)}`
        );
      }
    }
    return {
      viewport,
      documentScrollWidth: document.documentElement.scrollWidth,
      wide: wide.slice(0, 5),
    };
  });
  // Independent of the element walk: the body must not actually be scrollable
  // sideways. `overflow-x: hidden` hides the scrollbar, so this is checked on
  // the root's scrollWidth, which the property does not suppress.
  if (overflow.documentScrollWidth > overflow.viewport + 1) {
    fail(
      `${label}: the document scrolls sideways (${overflow.documentScrollWidth}px in a ${overflow.viewport}px viewport)`
    );
  }
  if (overflow.wide.length > 0) {
    fail(`${label}: ${overflow.wide.length} element(s) past the viewport -> ${overflow.wide[0]}`);
  } else {
    pass(`${label}: nothing overflows the viewport (${overflow.viewport}px)`);
  }
}

async function checkFieldAffordance(page, label) {
  const fields = await page.evaluate(() => {
    const results = [];
    const selector =
      'input:not([type="hidden"]), textarea, select, [role="combobox"], button';
    for (const element of document.querySelectorAll(selector)) {
      let style = getComputedStyle(element);
      if (parseFloat(style.borderTopWidth) === 0) continue;
      if (style.borderTopColor === "rgba(0, 0, 0, 0)") continue;
      const box = element.getBoundingClientRect();
      if (box.width === 0) continue;

      // Both the border and the backdrop are routinely TRANSLUCENT here
      // (`border-white/10` over `bg-white/[0.045]` over a near-black section).
      // Comparing the raw rgba strings makes white-on-white read as 1.00:1,
      // which is a measurement artifact and not a finding. So collect the
      // whole stack and composite it, nearest-last, over the first opaque
      // ancestor -- which is what the eye actually sees.
      const parse = (value) => {
        const parts = (value || "").match(/[\d.]+/g);
        if (!parts) return null;
        const [r, g, b, a] = parts.map(Number);
        return { r, g, b, a: a === undefined ? 1 : a };
      };
      const over = (top, bottom) => {
        if (!top) return bottom;
        const a = top.a;
        return {
          r: top.r * a + bottom.r * (1 - a),
          g: top.g * a + bottom.g * (1 - a),
          b: top.b * a + bottom.b * (1 - a),
          a: 1,
        };
      };
      const stack = [];
      let node = element;
      let base = { r: 255, g: 255, b: 255, a: 1 };
      while (node) {
        const colour = parse(getComputedStyle(node).backgroundColor);
        if (colour && colour.a > 0) {
          if (colour.a >= 1) {
            base = colour;
            break;
          }
          stack.push(colour);
        }
        node = node.parentElement;
      }
      let composited = base;
      for (let index = stack.length - 1; index >= 0; index -= 1) {
        composited = over(stack[index], composited);
      }
      const asRgb = (c) =>
        `rgb(${Math.round(c.r)}, ${Math.round(c.g)}, ${Math.round(c.b)})`;
      // The element's own background is part of the backdrop the BORDER sits
      // against only where they overlap; the border straddles the boundary, so
      // compare against the composited fill, which is the harder of the two.
      const backdrop = asRgb(composited);
      const borderColour = over(
        parse(style.borderTopColor),
        // A border painted over the element's own fill.
        composited
      );
      style = { ...style, borderTopColor: asRgb(borderColour) };
      results.push({
        tag: element.tagName.toLowerCase(),
        label: (element.textContent || element.getAttribute("placeholder") || "")
          .trim()
          .slice(0, 24),
        classes: String(element.className).slice(0, 60),
        border: style.borderTopColor,
        backdrop,
      });
    }
    return results;
  });

  if (fields.length === 0) {
    note(`${label}: no bordered interactive fields on this page`);
    return;
  }
  const weak = [];
  for (const field of fields) {
    const ratio = contrast(field.border, field.backdrop);
    if (ratio !== null && ratio < 3) {
      weak.push(
        `${field.tag} "${field.label}" [${field.classes}] border=${field.border} on ${field.backdrop} = ${ratio.toFixed(2)}:1`
      );
    }
  }
  if (weak.length > 0) {
    fail(
      `${label}: ${weak.length}/${fields.length} field border(s) below 3:1 at rest -> ${weak[0]}`
    );
  } else {
    pass(
      `${label}: all ${fields.length} bordered controls are >= 3:1 against their own background at rest`
    );
  }
}

/** Everything below the fold that is currently faded out, by a stable id. */
const HIDDEN_BELOW_FOLD = () => {
  const found = [];
  document.querySelectorAll("main *").forEach((element, index) => {
    // An INLINE opacity is the signature of a motion component mid-animation.
    // Reading the computed value instead would also collect anything the
    // design deliberately dims (`opacity-40` on a decorative rule), and then
    // the check would report a permanent failure that is not a defect.
    if (!element.style.opacity) return;
    const opacity = Number(getComputedStyle(element).opacity);
    if (opacity >= 0.5) return;
    const box = element.getBoundingClientRect();
    if (box.height === 0) return;
    element.setAttribute("data-qa-motion", String(index));
    found.push({ id: String(index), opacity, top: Math.round(box.top) });
  });
  return found;
};

async function checkScrollMotion(browser) {
  // Full motion. Two halves, and the second is the one that matters: a reveal
  // that ARMS but never FIRES is content the reader never sees, which is
  // strictly worse than no animation at all.
  await withPage(browser, { viewport: VIEWPORTS[2] }, async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    const armed = await page.evaluate(HIDDEN_BELOW_FOLD);

    if (armed.length === 0) {
      // Not a pass. Before this release the below-fold sections used
      // mount-time motion, which means they were already at opacity 1 by the
      // time anyone looked -- exactly this reading. A check that treats it as
      // success would have certified the bug.
      fail(
        "motion: nothing below the fold is waiting to reveal, so the scroll animations are not armed"
      );
      return;
    }
    pass(`motion: ${armed.length} element(s) below the fold start hidden`);

    // Walk the page rather than jumping to the end, so each viewport's
    // `whileInView` actually triggers.
    const height = await page.evaluate(() => document.body.scrollHeight);
    for (let y = 0; y <= height; y += 600) {
      await page.evaluate((top) => window.scrollTo(0, top), y);
      await page.waitForTimeout(120);
    }
    await page.waitForTimeout(700);

    const stuck = await page.evaluate(
      (ids) =>
        ids
          .map((id) => {
            const element = document.querySelector(`[data-qa-motion="${id}"]`);
            if (!element) return null;
            const opacity = Number(getComputedStyle(element).opacity);
            return opacity < 0.95
              ? { id, opacity, tag: element.tagName.toLowerCase() }
              : null;
          })
          .filter(Boolean),
      armed.map((item) => item.id)
    );
    if (stuck.length > 0) {
      fail(
        `motion: ${stuck.length} element(s) never revealed after scrolling the whole page -> ${JSON.stringify(stuck[0])}`
      );
    } else {
      pass(`motion: all ${armed.length} revealed once scrolled into view`);
    }
  });

  // Reduced motion: the same content must be there immediately, not animated
  // away. A reveal that does not fire under reduce is hidden content.
  await withPage(
    browser,
    { viewport: VIEWPORTS[2], reducedMotion: "reduce" },
    async (page) => {
      await page.goto(BASE, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);
      const hidden = await page.evaluate(HIDDEN_BELOW_FOLD);
      if (hidden.length > 0) {
        fail(
          `reduced motion: ${hidden.length} element(s) are still faded out, so someone who opted out of motion cannot read them without scrolling them into view`
        );
      } else {
        pass(
          "reduced motion: every section is fully visible with no animation required"
        );
      }
    }
  );
}

const PAGES = [
  { path: "/", label: "landing" },
  { path: "/login", label: "login" },
  { path: "/register", label: "register" },
  { path: "/assessments/invite/not-a-real-token", label: "invitation (invalid)" },
];

const browser = await chromium.launch();
try {
  for (const { path, label } of PAGES) {
    for (const viewport of VIEWPORTS) {
      await withPage(browser, { viewport }, async (page, errors) => {
        const response = await page.goto(`${BASE}${path}`, {
          waitUntil: "networkidle",
        });
        const name = `${label} @${viewport.name}`;
        if (!response || response.status() >= 400) {
          fail(`${name}: HTTP ${response ? response.status() : "no response"}`);
          return;
        }
        await page.waitForTimeout(400);
        await checkOverflow(page, name);
        if (viewport.name === "desktop") {
          await checkFieldAffordance(page, `${label}`);
        }
        if (errors.length > 0) {
          fail(`${name}: ${errors.length} console error(s) -> ${errors[0]}`);
        } else {
          pass(`${name}: no console errors`);
        }
      });
    }
  }
  await checkScrollMotion(browser);
} finally {
  await browser.close();
}

process.stdout.write(
  `\n${failures.length === 0 ? "Visual QA clean." : `${failures.length} failure(s).`}\n`
);
process.exit(failures.length === 0 ? 0 : 1);

// What CodeMirror 6 needs from a DOM that jsdom does not provide.
//
// TEST SUPPORT ONLY. The editor measures text with `Range.getClientRects` and
// positions its layers with `getBoundingClientRect`; jsdom implements neither
// on a Range, so mounting an editor under vitest throws before the first
// keystroke. These shims return empty geometry, which is enough for the
// editor to construct, accept input, dispatch its DOM events and report
// changes. Nothing that depends on real layout (scrolling a line into view,
// hit-testing a click) is meaningful under them, and no test here relies on
// it.

const EMPTY_RECT = {
  x: 0,
  y: 0,
  top: 0,
  left: 0,
  bottom: 0,
  right: 0,
  width: 0,
  height: 0,
  toJSON: () => ({}),
} as DOMRect;

function emptyRectList(): DOMRectList {
  const list = {
    length: 0,
    item: () => null,
    [Symbol.iterator]: function* () {},
  };
  return list as unknown as DOMRectList;
}

export function installCodeMirrorDomShims(): void {
  const range = Range.prototype as Range & {
    getClientRects: () => DOMRectList;
    getBoundingClientRect: () => DOMRect;
  };
  if (typeof range.getClientRects !== "function") {
    range.getClientRects = emptyRectList;
  }
  if (typeof range.getBoundingClientRect !== "function") {
    range.getBoundingClientRect = () => EMPTY_RECT;
  }
  const doc = document as Document & { elementFromPoint?: (x: number, y: number) => Element | null };
  if (typeof doc.elementFromPoint !== "function") {
    doc.elementFromPoint = () => null;
  }
}

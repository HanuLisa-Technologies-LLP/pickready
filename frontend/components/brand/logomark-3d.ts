/**
 * The ReadyPick R+P logomark, reconstructed as an animatable THREE.Group.
 *
 * spec-doc5 §C.2 asks for the mark rebuilt via `img2threejs` as "a code-only
 * procedural reconstruction (no downloaded mesh, no photogrammetry) -- the
 * output is a `THREE.Group` factory you can actually animate", used "for the
 * landing/login hero and nowhere else". That is what this is.
 *
 * WHY IT IS BUILT FROM SHAPES RATHER THAN FROM A TRACED OUTLINE
 * --------------------------------------------------------------
 * A traced SVG path extruded into a mesh gives you a picture of the mark and
 * nothing else: the R and the P are one blob of geometry, and the one thing the
 * brief actually wants to animate -- the shared stroke where they interlock --
 * is not addressable. Here the mark is composed of NAMED PARTS
 * (`group.getObjectByName("shared-stroke")`), so a light sweep along the shared
 * edge, or a scroll-triggered assembly of the R and the P from separate pieces,
 * is a transform on a node rather than a re-export.
 *
 * THE SHARED STROKE IS THE BRAND'S ONE GEOMETRIC IDEA
 * -----------------------------------------------------
 * spec-doc5 §C.1: "a merged R+P monogram, navy R with a teal P sharing a
 * stroke -- treat this as the brand's one distinctive geometric idea". So the
 * shared stem is a SEPARATE MESH sitting between the two letters rather than
 * being part of either, and it carries its own material. That is what lets it
 * be lit, swept or held while the letters move.
 *
 * COLOURS ARE THE SAMPLED ONES, and they are converted from sRGB rather than
 * assigned raw: three.js r152+ works in linear space, and passing a hex
 * straight to a material produces a visibly washed-out navy. `setStyle` with
 * an explicit "srgb" color space is the conversion.
 *
 * NO REACT, NO react-three-fiber. This is a plain factory returning a
 * `THREE.Group`, so it can be dropped into any renderer, unit-tested for its
 * part names without a DOM, and imported by a `"use client"` component that
 * owns the canvas. Keeping the geometry free of the framework is what makes
 * `logomark-3d.test.ts` possible at all.
 */
import * as THREE from "three";

/** The sampled brand colours. See DESIGN.md §2 for the measurement. */
export const NAVY = "#012654";
export const TEAL = "#00888A";

/** Every animatable part, by name. Exported so a caller does not stringly-type
 *  its way into a `getObjectByName` that silently returns undefined. */
export const PART = {
  R: "letter-r",
  P: "letter-p",
  SHARED_STROKE: "shared-stroke",
  R_BOWL: "r-bowl",
  R_LEG: "r-leg",
  P_BOWL: "p-bowl",
} as const;

export interface LogomarkOptions {
  /** Height of the letterforms in world units. Everything scales from this. */
  height?: number;
  /** Extrusion depth as a fraction of `height`. */
  depth?: number;
  /** Stroke weight as a fraction of `height`. The mark is a heavy geometric
   *  sans, so this is deliberately thick. */
  weight?: number;
}

const DEFAULTS: Required<LogomarkOptions> = {
  height: 1,
  depth: 0.18,
  weight: 0.2,
};

function srgb(hex: string): THREE.Color {
  // r152+ renders in linear space. A hex assigned directly is interpreted as
  // linear and comes out washed out -- a navy that reads as slate.
  return new THREE.Color().setStyle(hex, THREE.SRGBColorSpace);
}

function brandMaterial(hex: string): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: srgb(hex),
    // A printed mark, not a plastic toy. Low metalness and mid roughness keep
    // the navy reading as ink under a studio light rather than as a chrome
    // logo, which is the specific look the brand is positioned against.
    metalness: 0.05,
    roughness: 0.45,
  });
}

/** A rounded rectangle, the primitive both letters are built from. */
function bar(w: number, h: number, radius: number): THREE.Shape {
  const shape = new THREE.Shape();
  const r = Math.min(radius, w / 2, h / 2);
  shape.moveTo(-w / 2 + r, -h / 2);
  shape.lineTo(w / 2 - r, -h / 2);
  shape.quadraticCurveTo(w / 2, -h / 2, w / 2, -h / 2 + r);
  shape.lineTo(w / 2, h / 2 - r);
  shape.quadraticCurveTo(w / 2, h / 2, w / 2 - r, h / 2);
  shape.lineTo(-w / 2 + r, h / 2);
  shape.quadraticCurveTo(-w / 2, h / 2, -w / 2, h / 2 - r);
  shape.lineTo(-w / 2, -h / 2 + r);
  shape.quadraticCurveTo(-w / 2, -h / 2, -w / 2 + r, -h / 2);
  return shape;
}

/**
 * A letter bowl: the closed loop of an R or a P, as a shape with a hole.
 *
 * A HOLE RATHER THAN TWO ARCS, because an extruded shape with a hole produces
 * a single watertight mesh with correct side walls. Two concentric arcs would
 * leave the counter open at the extrusion depth, which reads as a hollow shell
 * the moment the mark rotates -- and the mark rotating is the whole point of
 * building it in 3D.
 */
function bowl(
  outerW: number,
  outerH: number,
  stroke: number
): THREE.Shape {
  const shape = bar(outerW, outerH, outerH / 2);
  const counter = bar(outerW - stroke * 2, outerH - stroke * 2, (outerH - stroke * 2) / 2);
  shape.holes.push(new THREE.Path(counter.getPoints(48)));
  return shape;
}

function extrude(shape: THREE.Shape, depth: number): THREE.ExtrudeGeometry {
  return new THREE.ExtrudeGeometry(shape, {
    depth,
    bevelEnabled: true,
    // A small bevel, not a chamfer. It catches the light along an edge, which
    // is what makes the shared stroke readable when it is swept; a larger one
    // would round the geometric sans into something softer than the brand.
    bevelThickness: depth * 0.08,
    bevelSize: depth * 0.06,
    bevelSegments: 2,
    curveSegments: 32,
  });
}

/**
 * Build the mark.
 *
 * The group is centred on its own bounding box, so a caller can rotate it about
 * its middle without first measuring it -- the mistake that makes a spinning
 * logo wobble.
 */
export function createLogomark(options: LogomarkOptions = {}): THREE.Group {
  const { height, depth, weight } = { ...DEFAULTS, ...options };
  const stroke = height * weight;
  const d = height * depth;

  const group = new THREE.Group();
  group.name = "readypick-logomark";

  const navy = brandMaterial(NAVY);
  const teal = brandMaterial(TEAL);

  // ── The shared stroke ────────────────────────────────────────────────────
  //
  // BUILT FIRST AND OWNED BY NEITHER LETTER. It is the vertical stem the R and
  // the P both hang off, and in the mark it is the single edge where navy meets
  // teal. Its own mesh, its own name, its own material -- which is what lets a
  // light sweep run along it, or a scroll-triggered assembly hold it in place
  // while the two letters separate.
  //
  // Its material is navy: at the join the darker colour has to carry the
  // silhouette, or the mark reads as a teal P with a navy fragment beside it.
  const sharedStroke = new THREE.Mesh(
    extrude(bar(stroke, height, stroke * 0.12), d),
    navy
  );
  sharedStroke.name = PART.SHARED_STROKE;
  sharedStroke.position.set(0, 0, 0);
  group.add(sharedStroke);

  // ── The R ────────────────────────────────────────────────────────────────
  const letterR = new THREE.Group();
  letterR.name = PART.R;

  const bowlH = height * 0.52;
  const bowlW = height * 0.46;

  const rBowl = new THREE.Mesh(extrude(bowl(bowlW, bowlH, stroke), d), navy);
  rBowl.name = PART.R_BOWL;
  // Hangs off the LEFT of the shared stroke, top-aligned.
  rBowl.position.set(-bowlW / 2 - stroke / 2 + stroke * 0.5, height / 2 - bowlH / 2, 0);
  letterR.add(rBowl);

  // The leg. A rotated bar rather than a curve: the mark is a geometric sans
  // and its leg is straight.
  const legLength = height * 0.5;
  const rLeg = new THREE.Mesh(extrude(bar(stroke, legLength, stroke * 0.12), d), navy);
  rLeg.name = PART.R_LEG;
  rLeg.rotation.z = Math.PI * 0.19;
  rLeg.position.set(
    -bowlW * 0.62,
    -height / 2 + legLength / 2 - height * 0.02,
    0
  );
  letterR.add(rLeg);
  group.add(letterR);

  // ── The P ────────────────────────────────────────────────────────────────
  const letterP = new THREE.Group();
  letterP.name = PART.P;

  const pBowl = new THREE.Mesh(extrude(bowl(bowlW, bowlH, stroke), d), teal);
  pBowl.name = PART.P_BOWL;
  // Hangs off the RIGHT of the same stroke, and slightly LOWER than the R's
  // bowl. The offset is what makes the two letters read as interlocking rather
  // than as mirrored -- a symmetric pair reads as a decorative monogram, and
  // the brand's idea is that they share an edge.
  pBowl.position.set(
    bowlW / 2 + stroke / 2 - stroke * 0.5,
    height / 2 - bowlH / 2 - height * 0.16,
    0
  );
  letterP.add(pBowl);
  group.add(letterP);

  // Centre on the bounding box so a caller can rotate about the middle without
  // measuring first -- otherwise a spinning logo wobbles around an origin that
  // is not its centre.
  const box = new THREE.Box3().setFromObject(group);
  const centre = box.getCenter(new THREE.Vector3());
  group.position.sub(centre);

  return group;
}

/**
 * The signature load animation: a light sweeping along the shared stroke.
 *
 * Returned as a step function taking elapsed seconds rather than owning a
 * clock, so the caller's render loop drives it and a reduced-motion caller can
 * simply never call it. THAT IS THE POINT of the shape: an animation that owns
 * its own timer cannot be switched off from outside without being torn down.
 */
export function sweepShared(
  group: THREE.Group,
  light: THREE.PointLight,
  { height = 1, seconds = 2.4 }: { height?: number; seconds?: number } = {}
): (elapsed: number) => void {
  const stroke = group.getObjectByName(PART.SHARED_STROKE);
  const travel = height * 1.4;
  return (elapsed: number) => {
    const t = (elapsed % seconds) / seconds;
    light.position.set(0, travel * (0.5 - t) * 2, height * 0.9);
    if (stroke) {
      // A very small counter-rotation while the light passes, so the bevel on
      // the shared edge actually catches it. Without it the sweep is a light
      // moving past a flat face and reads as nothing.
      (stroke as THREE.Mesh).rotation.y = Math.sin(t * Math.PI * 2) * 0.06;
    }
  };
}

/**
 * Scroll-triggered assembly: the R and the P arriving from opposite sides onto
 * the stroke that was already there.
 *
 * `progress` is 0..1 and is driven by the caller. Pure, so it can be scrubbed
 * backwards, and idempotent, so calling it repeatedly at the same value does
 * not accumulate.
 */
export function assemble(group: THREE.Group, progress: number): void {
  const t = Math.min(1, Math.max(0, progress));
  // Exponential ease-out. DESIGN.md §7 rules out spring overshoot, and this is
  // the alternative Impeccable's `bounce-easing` detector actually recommends:
  // real objects decelerate smoothly.
  const eased = 1 - Math.pow(2, -10 * t);
  const r = group.getObjectByName(PART.R);
  const p = group.getObjectByName(PART.P);
  if (r) {
    r.position.x = -(1 - eased) * 1.2;
    r.scale.setScalar(0.85 + eased * 0.15);
  }
  if (p) {
    p.position.x = (1 - eased) * 1.2;
    p.scale.setScalar(0.85 + eased * 0.15);
  }
  group.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    const material = mesh.material as THREE.MeshStandardMaterial;
    material.transparent = t < 1;
    material.opacity = eased;
  });
}

/** Release every geometry and material the factory allocated.
 *
 *  three.js does not garbage-collect GPU resources, so a component that mounts
 *  and unmounts the hero -- which the login page does on every navigation --
 *  leaks a mesh per mount without this. */
export function disposeLogomark(group: THREE.Group): void {
  group.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry?.dispose();
    const material = mesh.material;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material?.dispose();
  });
}

import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { api, fontUrl } from "./api";
import type { FontFamily, TextStyle } from "./types";

/* 字体库：列表只请求一次，各组件共用；字体文件按需加载到浏览器，预览用真实字体。 */

let cache: Promise<FontFamily[]> | null = null;
const listeners = new Set<(families: FontFamily[]) => void>();

export function loadFonts(force = false): Promise<FontFamily[]> {
  if (!cache || force) {
    cache = api.fonts().catch(() => []);
    cache.then((families) => listeners.forEach((listen) => listen(families)));
  }
  return cache;
}

export function useFonts(): FontFamily[] {
  const [families, setFamilies] = useState<FontFamily[]>([]);
  useEffect(() => {
    listeners.add(setFamilies);
    loadFonts().then(setFamilies);
    return () => {
      listeners.delete(setFamilies);
    };
  }, []);
  return families;
}

const loaded = new Map<string, Promise<void>>();
const ready = new Set<string>();

/** 预览里引用字体用的 CSS 名字：每个字体文件单独注册，加粗 / 常规用不同的文件。 */
const faceName = (file: string) => `sw-${file.replace(/[^\w-]/g, "_")}`;

function ensureFace(file: string): Promise<void> {
  let promise = loaded.get(file);
  if (!promise) {
    const face = new FontFace(faceName(file), `url(${fontUrl(file)})`);
    promise = face
      .load()
      .then((font) => {
        document.fonts.add(font);
        ready.add(file);
      })
      .catch(() => undefined);
    loaded.set(file, promise);
  }
  return promise;
}

/** 等字体加载好后重新渲染（第一次用某个字体时会闪一下系统字体）。 */
export function useFontReady(files: (string | null | undefined)[]): void {
  const [, setTick] = useState(0);
  const key = files.filter(Boolean).join("|");
  useEffect(() => {
    let alive = true;
    const pending = files.filter((file): file is string => !!file && !ready.has(file));
    if (pending.length) Promise.all(pending.map(ensureFace)).then(() => alive && setTick((n) => n + 1));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

export function findFamily(families: FontFamily[], file: string | null | undefined): FontFamily | undefined {
  if (!file) return undefined;
  return families.find((family) => family.files.includes(file));
}

/** 和后端 fonts.pick_weight 一致：加粗时用同族的粗体文件，没有就用现有文件（浏览器合成加粗）。 */
export function weightFile(families: FontFamily[], file: string, bold: boolean): string {
  const family = findFamily(families, file);
  if (!family || family.files.length < 2) return file;
  const target = bold ? 700 : 400;
  const distance = (name: string) => Math.abs((family.weights?.[name] ?? 400) - target);
  return [...family.files].sort((a, b) => distance(a) - distance(b) || Number(b === file) - Number(a === file))[0];
}

const hexAlpha = (hex: string, alpha: number) =>
  `${hex}${Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, "0")}`;

/**
 * 预览用的 CSS，尽量贴近 ASS 渲染的效果：描边用 text-stroke，阴影向右下偏移，
 * 发光用和描边同色的模糊阴影，底框用半透明背景加内边距。
 */
export function textCss(style: TextStyle, size: number, scale: number, file: string | null): CSSProperties {
  const px = (value: number) => value * scale;
  const boxed = style.background_opacity > 0;
  const shadows: string[] = [];
  const outline2 = !boxed ? style.outline2 ?? 0 : 0;
  if (outline2 > 0) {
    // 第二层描边：用一圈同色的硬阴影模拟（ASS 里是加粗的描边层）。
    const outer = px(style.outline + outline2) * 1.1;
    for (const radius of [outer, outer * 0.6]) {
      for (let i = 0; i < 24; i += 1) {
        const angle = (Math.PI * 2 * i) / 24;
        shadows.push(`${(Math.cos(angle) * radius).toFixed(2)}px ${(Math.sin(angle) * radius).toFixed(2)}px 0.5px ${style.outline2_color}`);
      }
    }
  }
  if (style.shadow > 0) {
    const angle = ((style.shadow_angle ?? 45) * Math.PI) / 180;
    const distance = px(style.shadow) * Math.SQRT2;
    const extra = outline2 > 0 ? px(style.outline + outline2) : 0;
    shadows.push(
      `${(Math.cos(angle) * (distance + extra)).toFixed(2)}px ${(Math.sin(angle) * (distance + extra)).toFixed(2)}px ${px(style.shadow_blur ?? 0) * 2}px ${hexAlpha(style.shadow_color, style.shadow_opacity)}`,
    );
  }
  if (style.glow > 0) {
    const glowColor = boxed || style.outline === 0 ? style.color : style.outline_color;
    shadows.push(`0 0 ${px(style.glow * 2.5)}px ${glowColor}`, `0 0 ${px(style.glow * 5)}px ${glowColor}`);
  }
  return {
    fontFamily: file ? `"${faceName(file)}", "Noto Sans SC", sans-serif` : undefined,
    fontSize: Math.max(9, size * scale),
    fontWeight: style.bold ? 700 : 400,
    fontStyle: style.italic ? "italic" : "normal",
    textDecoration: style.underline ? "underline" : undefined,
    letterSpacing: style.spacing ? `${px(style.spacing)}px` : undefined,
    color: style.color,
    opacity: style.opacity,
    WebkitTextStroke:
      !boxed && style.outline > 0 ? `${Math.max(0.5, px(style.outline) * 1.4)}px ${style.outline_color}` : undefined,
    paintOrder: "stroke fill",
    background: boxed ? hexAlpha(style.background, style.background_opacity) : undefined,
    padding: boxed ? `${px(size * 0.12)}px ${px(size * 0.18)}px` : undefined,
    boxDecorationBreak: "clone",
    WebkitBoxDecorationBreak: "clone",
    textShadow: shadows.length ? shadows.join(", ") : undefined,
    whiteSpace: "pre-line",
    lineHeight: style.line_spacing ? `${px(size * 1.3 + style.line_spacing)}px` : undefined,
    textAlign: style.align,
    ["--sw-highlight" as string]: style.highlight_color,
  };
}

/** 样式预设：对标剪映 / CapCut 的「花字」，只改颜色、描边、阴影、底框、发光这几项。 */
export type PresetId =
  | "classic"
  | "variety"
  | "boxed"
  | "news"
  | "neon"
  | "soft"
  | "candy"
  | "double"
  | "bold3d"
  | "dreamy"
  | "karaoke";

/** 预设之间切换时，把预设没写到的进阶效果恢复成默认。 */
const RESET: Partial<TextStyle> = { outline2: 0, shadow_blur: 0, shadow_angle: 45 };

export const PRESETS: { id: PresetId; patch: Partial<TextStyle> }[] = [
  {
    id: "classic",
    patch: { ...RESET, color: "#FFFFFF", outline: 3, outline_color: "#000000", shadow: 0, glow: 0, background_opacity: 0, opacity: 1 },
  },
  {
    id: "variety",
    patch: { ...RESET, color: "#FFE45C", outline: 5, outline_color: "#000000", shadow: 4, shadow_color: "#000000", shadow_opacity: 0.7, glow: 0, background_opacity: 0, opacity: 1, bold: true },
  },
  {
    id: "boxed",
    patch: { ...RESET, color: "#FFFFFF", outline: 0, shadow: 0, glow: 0, background: "#000000", background_opacity: 0.6, opacity: 1 },
  },
  {
    id: "news",
    patch: { ...RESET, color: "#FFFFFF", outline: 0, shadow: 0, glow: 0, background: "#C62828", background_opacity: 0.92, opacity: 1, bold: true },
  },
  {
    id: "neon",
    patch: { ...RESET, color: "#FFFFFF", outline: 4, outline_color: "#FF2BD6", shadow: 0, glow: 6, background_opacity: 0, opacity: 1 },
  },
  {
    id: "soft",
    patch: { ...RESET, color: "#FFFFFF", outline: 0, shadow: 3, shadow_color: "#000000", shadow_opacity: 0.75, glow: 0, background_opacity: 0, opacity: 1 },
  },
  {
    id: "candy",
    patch: { ...RESET, color: "#FFFFFF", outline: 6, outline_color: "#FF4F8B", shadow: 3, shadow_color: "#7A1F3D", shadow_opacity: 0.8, glow: 0, background_opacity: 0, opacity: 1, bold: true },
  },
  {
    id: "double",
    patch: { ...RESET, color: "#FFFFFF", outline: 4, outline_color: "#000000", outline2: 6, outline2_color: "#FFD60A", shadow: 0, glow: 0, background_opacity: 0, opacity: 1, bold: true },
  },
  {
    id: "bold3d",
    patch: { ...RESET, color: "#FFE45C", outline: 3, outline_color: "#1A1A1A", shadow: 5, shadow_color: "#1A1A1A", shadow_opacity: 1, shadow_angle: 90, glow: 0, background_opacity: 0, opacity: 1, bold: true },
  },
  {
    id: "dreamy",
    patch: { ...RESET, color: "#FFFFFF", outline: 0, shadow: 4, shadow_color: "#6A5CFF", shadow_opacity: 0.9, shadow_angle: 90, shadow_blur: 8, glow: 0, background_opacity: 0, opacity: 1 },
  },
  {
    id: "karaoke",
    patch: { ...RESET, color: "#FFFFFF", outline: 3, outline_color: "#000000", shadow: 0, glow: 0, background_opacity: 0, opacity: 1, animation: "karaoke", highlight_color: "#FFE45C" },
  },
];

/** 当前样式是否就是某个预设（预设里列出的字段都相同）。 */
export function matchesPreset(style: TextStyle, patch: Partial<TextStyle>): boolean {
  return (Object.keys(patch) as (keyof TextStyle)[]).every((key) => {
    if (key === "shadow_color" && !style.shadow) return true;
    if (key === "shadow_opacity" && !style.shadow) return true;
    if ((key === "shadow_angle" || key === "shadow_blur") && !style.shadow) return true;
    if (key === "outline2_color" && !style.outline2) return true;
    if (key === "outline_color" && !style.outline) return true;
    if (key === "background" && !style.background_opacity) return true;
    return style[key] === patch[key];
  });
}

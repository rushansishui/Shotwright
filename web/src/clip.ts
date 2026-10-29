import type { Asset, ShotVisual } from "./types";

/** 镜头实际播放的片段：入出点和中间剪掉的部分（只在入出点属于当前选中的素材时生效，和后端 effective_edit 一致）。 */
export interface ClipRange {
  start: number;
  end: number;
  skips: [number, number][];
}

export function clipRange(visual: ShotVisual, asset: Asset | undefined): ClipRange {
  const total = asset?.duration ?? 0;
  const own = !!asset && visual.edit_asset === asset.id;
  const start = own ? visual.trim_start : 0;
  const end = own && visual.trim_end !== null ? visual.trim_end : total;
  const skips = own ? [...(visual.skips ?? [])].filter(([a, b]) => b > start && a < end).sort((x, y) => x[0] - y[0]) : [];
  return { start, end: end || Infinity, skips };
}

/**
 * 播放时守住片段：落在剪掉的部分里就跳到它后面，到了出点回到入点（loop）或停下。
 * 返回值是要跳到的位置；不需要跳时返回 null。
 */
export function nextPosition(now: number, range: ClipRange, loop: boolean): number | null {
  if (now < range.start - 0.05) return range.start;
  for (const [a, b] of range.skips) {
    if (now >= a - 0.02 && now < b - 0.02) return b;
  }
  if (now >= range.end - 0.03) return loop ? range.start : null;
  return null;
}

/** 剪掉的部分扣除后，片段实际播放的秒数。 */
export function keptSeconds(range: ClipRange): number {
  const end = Number.isFinite(range.end) ? range.end : range.start;
  const cut = range.skips.reduce((sum, [a, b]) => sum + Math.max(0, Math.min(b, end) - Math.max(a, range.start)), 0);
  return Math.max(0, end - range.start - cut);
}

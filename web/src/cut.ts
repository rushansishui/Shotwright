/** 剪辑页的时间计算，和后端 services/transcribe.py 的 plan_ranges 保持一致（只用于预览和统计）。 */

export type WordTuple = [string, number, number];

const PAD = 0.08;
const PAUSE_KEEP = 0.25;
const MIN_RANGE = 0.3;

export function planRanges(
  words: WordTuple[],
  total: number,
  removed: Set<number>,
  maxPause: number | null,
): [number, number][] {
  const cuts: [number, number][] = [];
  let index = 0;
  while (index < words.length) {
    if (!removed.has(index)) {
      index++;
      continue;
    }
    const first = index;
    while (index < words.length && removed.has(index)) index++;
    const last = index - 1;
    const before = first > 0 ? words[first - 1][2] : null;
    const after = index < words.length ? words[index][1] : null;
    let begin = before === null ? words[first][1] : Math.min(before + PAD, words[first][1]);
    let end = after === null ? words[last][2] : Math.max(after - PAD, words[last][2]);
    if (before === null && maxPause !== null) begin = 0;
    if (after === null && maxPause !== null) end = total;
    cuts.push([Math.max(0, begin), Math.min(total, end)]);
  }
  if (maxPause !== null) {
    const kept = words.filter((_, i) => !removed.has(i));
    if (kept.length) {
      const edges: [number, number][] = [[0, kept[0][1]]];
      for (let i = 1; i < kept.length; i++) edges.push([kept[i - 1][2], kept[i][1]]);
      edges.push([kept[kept.length - 1][2], total]);
      edges.forEach(([a, b], position) => {
        if (b - a <= maxPause) return;
        const left = a + (position === 0 ? 0 : PAUSE_KEEP);
        const right = b - (position === edges.length - 1 ? 0 : PAUSE_KEEP);
        if (right > left) cuts.push([left, right]);
      });
    } else if (removed.size) {
      return [];
    }
  }
  cuts.sort((x, y) => x[0] - y[0]);
  const merged: [number, number][] = [];
  for (const [b, e] of cuts) {
    const prev = merged[merged.length - 1];
    if (prev && b <= prev[1] + 1e-3) prev[1] = Math.max(prev[1], e);
    else merged.push([b, e]);
  }
  const ranges: [number, number][] = [];
  let cursor = 0;
  for (const [b, e] of merged) {
    if (b - cursor >= MIN_RANGE) ranges.push([cursor, b]);
    cursor = Math.max(cursor, e);
  }
  if (total - cursor >= MIN_RANGE) ranges.push([cursor, total]);
  return ranges;
}

/** 把识别出的词按句子分组（句子的起止时间来自识别服务）；没有分句时每 20 个词一组。 */
export function groupSentences(words: WordTuple[], sentences: [number, number][]): number[][] {
  const groups: number[][] = [];
  if (sentences.length) {
    let cursor = 0;
    for (const [a, b] of sentences) {
      const members: number[] = [];
      while (cursor < words.length) {
        const mid = (words[cursor][1] + words[cursor][2]) / 2;
        if (mid > b + 0.05) break;
        if (mid >= a - 0.05 || members.length === 0) members.push(cursor);
        cursor++;
      }
      if (members.length) groups.push(members);
    }
    if (cursor < words.length) groups.push(words.slice(cursor).map((_, i) => cursor + i));
    return groups;
  }
  for (let i = 0; i < words.length; i += 20) groups.push(words.slice(i, i + 20).map((_, j) => i + j));
  return groups;
}

const CJK = /[぀-ヿ㐀-䶿一-鿿가-힯]/;

/** 词之间要不要加空格：中日韩不加，拼音文字加，标点前不加。 */
export function needsSpace(previous: string, next: string): boolean {
  if (!previous) return false;
  if (CJK.test(next[0] ?? "") || CJK.test(previous[previous.length - 1] ?? "")) return false;
  return /[\p{L}\p{N}]/u.test(next[0] ?? "");
}

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function guessAspect(width: number, height: number): "9:16" | "16:9" | "1:1" {
  if (!width || !height) return "9:16";
  const ratio = width / height;
  if (ratio < 0.85) return "9:16";
  if (ratio > 1.2) return "16:9";
  return "1:1";
}

/** 文稿里一处查找结果：涉及哪些词（下标），以及在第一个词里从第几个字开始、在最后一个词里到第几个字结束。 */
export interface TextMatch {
  tokens: number[];
  start: number;
  end: number;
}

/**
 * 在每句话里查找一段文字（不区分大小写）。识别结果里中文常常一个字一个词，
 * 所以按句子把词拼起来（和页面显示一样，拼音文字之间加空格）再找，一处结果可以跨好几个词。
 */
export function findMatches(words: WordTuple[], groups: number[][], query: string): TextMatch[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const matches: TextMatch[] = [];
  for (const group of groups) {
    let text = "";
    const owner: number[] = [];
    const offset: number[] = [];
    group.forEach((index, k) => {
      if (k > 0 && needsSpace(words[group[k - 1]][0], words[index][0])) {
        text += " ";
        owner.push(-1);
        offset.push(0);
      }
      const word = words[index][0];
      for (let c = 0; c < word.length; c++) {
        owner.push(index);
        offset.push(c);
      }
      text += word;
    });
    const hay = text.toLowerCase();
    let from = 0;
    while (from <= hay.length - needle.length) {
      const at = hay.indexOf(needle, from);
      if (at < 0) break;
      const last = at + needle.length - 1;
      const tokens = [...new Set(owner.slice(at, last + 1).filter((i) => i >= 0))];
      if (tokens.length) {
        let first = at;
        while (owner[first] < 0) first++;
        let end = last;
        while (owner[end] < 0) end--;
        matches.push({ tokens, start: offset[first], end: offset[end] + 1 });
      }
      from = at + needle.length;
    }
  }
  return matches;
}

/**
 * 把查找结果替换成新文字，返回要改的词（下标 → 新文字）。
 * 一处结果跨了几个词时，中文替换文字按字平均分给这几个词，字幕逐字高亮时节奏还对得上；
 * 分不开（字比词少、或者不是中日韩文字）时整段放进第一个词，其余的词留空。
 */
export function replaceMatches(words: WordTuple[], matches: TextMatch[], replacement: string): Record<number, string> {
  const result: Record<number, string> = {};
  const current = (i: number) => result[i] ?? words[i][0];
  for (const match of matches) {
    const firstIndex = match.tokens[0];
    const lastIndex = match.tokens[match.tokens.length - 1];
    const prefix = current(firstIndex).slice(0, match.start);
    const suffix = current(lastIndex).slice(match.end);
    if (match.tokens.length === 1) {
      result[firstIndex] = prefix + replacement + suffix;
      continue;
    }
    const chars = [...replacement];
    const parts: string[] = [];
    if (chars.length >= match.tokens.length && chars.every((c) => CJK.test(c))) {
      const base = Math.floor(chars.length / match.tokens.length);
      let extra = chars.length % match.tokens.length;
      let cursor = 0;
      for (let k = 0; k < match.tokens.length; k++) {
        const size = base + (extra > 0 ? 1 : 0);
        if (extra > 0) extra--;
        parts.push(chars.slice(cursor, cursor + size).join(""));
        cursor += size;
      }
    } else {
      parts.push(replacement, ...match.tokens.slice(1).map(() => ""));
    }
    match.tokens.forEach((index, k) => {
      let text = parts[k];
      if (k === 0) text = prefix + text;
      if (k === match.tokens.length - 1) text += suffix;
      result[index] = text;
    });
  }
  return result;
}

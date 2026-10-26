import { useEffect, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { Project, SubtitleStyle } from "../../types";
import { Slider } from "../Controls";

interface KeywordFieldProps {
  project: Project;
  canSuggest: boolean;
  update: (patch: Partial<SubtitleStyle>) => Promise<unknown>;
  run: (action: () => Promise<Project>) => Promise<Project | null>;
}

const split = (text: string) =>
  text
    .split(/[,，、;；\s]+/u)
    .map((word) => word.trim())
    .filter(Boolean);

/** 字幕关键词高亮（对标剪映的「关键词高亮」）：手动填写或让 AI 从文案里挑。 */
export function KeywordField({ project, canSuggest, update, run }: KeywordFieldProps) {
  const { t } = useI18n();
  const style = project.subtitle;
  const saved = (style.keywords ?? []).join(" ");
  const [text, setText] = useState(saved);
  const [scale, setScale] = useState<number | undefined>();
  const [busy, setBusy] = useState(false);
  useEffect(() => setText(saved), [saved]);

  function commit() {
    const words = split(text);
    if (words.join(" ") !== saved) update({ keywords: words });
  }

  async function suggest() {
    setBusy(true);
    try {
      await run(() => api.suggestKeywords(project.id));
    } finally {
      setBusy(false);
    }
  }

  const active = (style.keywords ?? []).length > 0;
  return (
    <div className="field keyword-field">
      <span className="label">{t("editor.keywords")}</span>
      <div className="row">
        <input
          className="input"
          value={text}
          placeholder={t("editor.keywordsPlaceholder")}
          aria-label={t("editor.keywords")}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
          }}
        />
        {canSuggest && (
          <button type="button" className="btn small" disabled={busy} onClick={suggest}>
            {busy ? t("editor.keywordsAiBusy") : t("editor.keywordsAi")}
          </button>
        )}
      </div>
      <p className="muted small">{t("editor.keywordsHint")}</p>
      {active && (
        <div className="row wrap keyword-style">
          <label className="color-field">
            <input
              type="color"
              aria-label={t("editor.keywordColor")}
              value={style.keyword_color.toLowerCase()}
              onChange={(e) => update({ keyword_color: e.target.value.toUpperCase() })}
            />
            {t("editor.keywordColor")}
          </label>
          <Slider
            id="kw-scale"
            label={t("editor.keywordScale")}
            value={scale ?? style.keyword_scale}
            min={1}
            max={1.6}
            step={0.05}
            format={(v) => `${Math.round(v * 100)}%`}
            onInput={setScale}
            onCommit={(keyword_scale) => {
              update({ keyword_scale }).then(() => setScale(undefined));
            }}
          />
        </div>
      )}
    </div>
  );
}

/** 预览里把关键词换色放大（和后端 subtitles.keyword_spans 一致：长词优先、英文按整词）。 */
export function highlightKeywords(text: string, style: SubtitleStyle): (string | { word: string })[] {
  const words = [...new Set((style.keywords ?? []).map((w) => w.trim()).filter(Boolean))].sort((a, b) => b.length - a.length);
  if (!words.length) return [text];
  const lower = text.toLowerCase();
  const taken = new Array<boolean>(text.length).fill(false);
  const spans: [number, number][] = [];
  const ascii = /[A-Za-z0-9]/;
  for (const word of words) {
    const needle = word.toLowerCase();
    const latin = ascii.test(needle[0]) || ascii.test(needle[needle.length - 1]);
    let from = 0;
    for (;;) {
      const i = lower.indexOf(needle, from);
      if (i < 0) break;
      const j = i + needle.length;
      from = i + 1;
      if (taken.slice(i, j).some(Boolean)) continue;
      if (latin && ((i > 0 && ascii.test(text[i - 1])) || (j < text.length && ascii.test(text[j])))) continue;
      spans.push([i, j]);
      for (let k = i; k < j; k += 1) taken[k] = true;
    }
  }
  spans.sort((a, b) => a[0] - b[0]);
  const out: (string | { word: string })[] = [];
  let pos = 0;
  for (const [i, j] of spans) {
    if (i > pos) out.push(text.slice(pos, i));
    out.push({ word: text.slice(i, j) });
    pos = j;
  }
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}

import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { Project, Pronunciation } from "../../types";

interface PronunciationSectionProps {
  project: Project;
  canSuggest: boolean;
  run: (action: () => Promise<Project>) => Promise<Project | null>;
  onError: (message: string) => void;
}

/** 去掉停顿、强调标注（和后端 speech.plain 一致）。 */
const plain = (text: string) => text.replace(/\[(?:停顿|pause)(?:\s+\d+(?:\.\d+)?)?\]/giu, "").replace(/\*([^*\n]{1,60})\*/gu, "$1");

/** 文案里包含这个词的那一句，试听时读整句更容易听出读音对不对。 */
function sentenceWith(project: Project, word: string): string {
  for (const shot of project.shots) {
    const text = plain(shot.text);
    const index = text.indexOf(word);
    if (index < 0) continue;
    const before = text.slice(0, index).search(/[^。！？!?；;\n]*$/u);
    const afterMatch = text.slice(index).search(/[。！？!?；;\n]/u);
    const end = afterMatch < 0 ? text.length : index + afterMatch + 1;
    return text.slice(Math.max(0, before), end).trim().slice(0, 120);
  }
  return word;
}

/** 读音纠正：配音读错字时，把词换成同音字再合成，字幕仍显示原文。 */
export function PronunciationSection({ project, canSuggest, run, onError }: PronunciationSectionProps) {
  const { t } = useI18n();
  const saved = project.pronunciations ?? [];
  const [rows, setRows] = useState<Pronunciation[]>(saved);
  const [word, setWord] = useState("");
  const [say, setSay] = useState("");
  const [checking, setChecking] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const savedKey = JSON.stringify(saved);
  useEffect(() => setRows(saved), [savedKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => audio.current?.pause(), []);

  function save(next: Pronunciation[]) {
    const clean = next.filter((item) => item.word.trim() && item.say.trim());
    setRows(clean);
    if (JSON.stringify(clean) !== savedKey) run(() => api.updateProject(project.id, { pronunciations: clean }));
  }

  function add() {
    if (!word.trim() || !say.trim()) return;
    const rest = rows.filter((item) => item.word !== word.trim());
    save([...rest, { word: word.trim(), say: say.trim(), note: "" }]);
    setWord("");
    setSay("");
  }

  async function listen(key: string, text: string) {
    audio.current?.pause();
    setPlaying(key);
    try {
      const blob = await api.previewVoice(project.id, text);
      const player = new Audio(URL.createObjectURL(blob));
      audio.current = player;
      player.onended = () => setPlaying(null);
      await player.play();
    } catch (exc) {
      setPlaying(null);
      onError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function check() {
    setChecking(true);
    try {
      await run(() => api.suggestPronunciations(project.id));
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="pronunciations">
      <div className="row between">
        <span className="label">{t("pron.title")}</span>
        {canSuggest && (
          <button type="button" className="btn small" disabled={checking || !project.shots.some((s) => s.text.trim())} onClick={check}>
            {checking ? t("pron.checking") : t("pron.check")}
          </button>
        )}
      </div>
      <p className="muted small">{t("pron.hint")}</p>
      {rows.length > 0 && (
        <ul className="pron-list">
          {rows.map((item, index) => {
            const sentence = sentenceWith(project, item.word);
            return (
              <li key={`${item.word}-${index}`} className="pron-row">
                <input
                  className="input"
                  aria-label={t("pron.word")}
                  defaultValue={item.word}
                  onBlur={(e) => {
                    if (e.target.value.trim() !== item.word) save(rows.map((row, i) => (i === index ? { ...row, word: e.target.value.trim() } : row)));
                  }}
                />
                <span className="pron-arrow" aria-hidden="true">
                  →
                </span>
                <input
                  className="input"
                  aria-label={t("pron.say")}
                  defaultValue={item.say}
                  onBlur={(e) => {
                    if (e.target.value.trim() !== item.say) save(rows.map((row, i) => (i === index ? { ...row, say: e.target.value.trim() } : row)));
                  }}
                />
                <button
                  type="button"
                  className="btn small"
                  disabled={playing !== null}
                  title={t("pron.listenHint", { text: sentence })}
                  onClick={() => listen(item.word, sentence)}
                >
                  {playing === item.word ? t("pron.playing") : t("pron.listen")}
                </button>
                <button
                  type="button"
                  className="btn icon ghost danger"
                  aria-label={t("pron.remove")}
                  title={t("pron.remove")}
                  onClick={() => save(rows.filter((_, i) => i !== index))}
                >
                  ×
                </button>
                {item.note && <span className="muted small pron-note">{item.note}</span>}
              </li>
            );
          })}
        </ul>
      )}
      <div className="pron-row pron-add">
        <input className="input" placeholder={t("pron.wordPlaceholder")} aria-label={t("pron.word")} value={word} onChange={(e) => setWord(e.target.value)} />
        <span className="pron-arrow" aria-hidden="true">
          →
        </span>
        <input
          className="input"
          placeholder={t("pron.sayPlaceholder")}
          aria-label={t("pron.say")}
          value={say}
          onChange={(e) => setSay(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
        />
        <button type="button" className="btn small" disabled={!word.trim() || !say.trim()} onClick={add}>
          {t("pron.add")}
        </button>
        <button
          type="button"
          className="btn small"
          disabled={!word.trim() || playing !== null}
          title={t("pron.listenHint", { text: word.trim() ? sentenceWith(project, word.trim()) : "" })}
          onClick={() => listen("__new", sentenceWith(project, word.trim()))}
        >
          {playing === "__new" ? t("pron.playing") : t("pron.listen")}
        </button>
      </div>
    </div>
  );
}

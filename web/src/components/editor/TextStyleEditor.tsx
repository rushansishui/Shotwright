import { useRef, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import { findFamily, loadFonts, matchesPreset, PRESETS, textCss, useFontReady, useFonts, weightFile } from "../../textStyle";
import type { TextAlign, TextAnimation, TextExit, TextLoop, TextStyle } from "../../types";
import { Slider } from "../Controls";

interface TextStyleEditorProps {
  id: string;
  value: TextStyle;
  /** commit 为 false 时只更新草稿（拖动滑块），为 true 时保存。 */
  onChange: (patch: Partial<TextStyle>, commit: boolean) => void;
  /** 标题用：font 为空表示跟随字幕字体，这里传字幕当前的字体文件。 */
  inheritFont?: string;
  allowKaraoke?: boolean;
  showAlign?: boolean;
  onError?: (message: string) => void;
}

const ANIMATIONS: TextAnimation[] = ["none", "fade", "pop", "slide_up", "typewriter", "karaoke"];
const EXITS: TextExit[] = ["none", "fade", "shrink", "slide_down"];
const LOOPS: TextLoop[] = ["none", "pulse", "swing"];

/** 文字样式面板：字体、加粗 / 倾斜 / 下划线、对齐、样式预设、颜色、入场 / 出场 / 循环效果和更多样式。 */
export function TextStyleEditor({ id, value, onChange, inheritFont, allowKaraoke = false, showAlign = true, onError }: TextStyleEditorProps) {
  const { t, lang } = useI18n();
  const families = useFonts();
  const fileInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState("");
  const font = value.font ?? inheritFont ?? null;
  const sampleFile = font ? weightFile(families, font, value.bold) : null;
  useFontReady([sampleFile]);
  const currentFamily = findFamily(families, value.font);
  const presets = PRESETS.filter((preset) => allowKaraoke || preset.id !== "karaoke");
  const boxed = value.background_opacity > 0;

  async function importFont(file: File) {
    setImporting(true);
    setNotice("");
    try {
      const result = await api.importFont(file);
      await loadFonts(true);
      onChange({ font: result.file }, true);
      setNotice(t("style.imported", { name: result.family }));
    } catch (exc) {
      onError?.(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setImporting(false);
    }
  }

  async function removeFont(file: string) {
    try {
      await api.deleteFont(file);
      await loadFonts(true);
      if (inheritFont !== undefined) onChange({ font: null }, true);
    } catch (exc) {
      onError?.(exc instanceof Error ? exc.message : String(exc));
    }
  }

  const toggle = (key: "bold" | "italic" | "underline", label: string, glyph: string) => (
    <button
      type="button"
      className={`style-toggle ${value[key] ? "on" : ""}`}
      aria-pressed={value[key]}
      title={label}
      aria-label={label}
      onClick={() => onChange({ [key]: !value[key] }, true)}
    >
      <span className={`glyph-${key}`}>{glyph}</span>
    </button>
  );

  const color = (key: keyof TextStyle, label: string) => (
    <label className="color-field">
      <input
        type="color"
        aria-label={label}
        value={String(value[key]).toLowerCase()}
        onChange={(e) => onChange({ [key]: e.target.value.toUpperCase() } as Partial<TextStyle>, true)}
      />
      {label}
    </label>
  );

  return (
    <div className="text-style">
      <div className="text-style-row">
        <label className="text-style-font">
          <span className="label">{t("style.font")}</span>
          <select
            className="input"
            value={currentFamily?.file ?? (value.font ? value.font : "")}
            onChange={(e) => onChange({ font: e.target.value || null }, true)}
          >
            {inheritFont !== undefined && <option value="">{t("style.fontFollow")}</option>}
            {families.map((family) => (
              <option key={family.family} value={family.file}>
                {(lang === "zh" ? family.name_zh : family.name_en) + (family.user ? t("style.myFont") : "")}
              </option>
            ))}
            {value.font && !currentFamily && <option value={value.font}>{value.font}</option>}
          </select>
        </label>
        <button type="button" className="btn small" disabled={importing} title={t("style.importHint")} onClick={() => fileInput.current?.click()}>
          {importing ? t("style.importing") : t("style.importFont")}
        </button>
        {currentFamily?.user && value.font && (
          <button type="button" className="linkish small" onClick={() => removeFont(value.font!)}>
            {t("style.deleteFont")}
          </button>
        )}
        <input
          ref={fileInput}
          type="file"
          accept=".ttf,.otf"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) importFont(file);
          }}
        />
      </div>
      {notice && <p className="muted small">{notice}</p>}

      <div className="text-style-row">
        <div className="style-toolbar" role="group" aria-label={t("style.font")}>
          {toggle("bold", t("style.bold"), "B")}
          {toggle("italic", t("style.italic"), "I")}
          {toggle("underline", t("style.underline"), "U")}
        </div>
        {showAlign && (
          <div className="style-toolbar" role="radiogroup" aria-label={t("style.align")}>
            {(["left", "center", "right"] as TextAlign[]).map((align) => (
              <button
                type="button"
                key={align}
                role="radio"
                aria-checked={value.align === align}
                className={`style-toggle ${value.align === align ? "on" : ""}`}
                title={t(`style.align.${align}` as MessageKey)}
                aria-label={t(`style.align.${align}` as MessageKey)}
                onClick={() => onChange({ align }, true)}
              >
                <AlignIcon align={align} />
              </button>
            ))}
          </div>
        )}
        <label className="text-style-anim">
          <span className="label">{t("style.animation")}</span>
          <select className="input" value={value.animation} onChange={(e) => onChange({ animation: e.target.value as TextAnimation }, true)}>
            {ANIMATIONS.filter((item) => allowKaraoke || item !== "karaoke").map((item) => (
              <option key={item} value={item}>
                {t(`style.anim.${item}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-style-anim">
          <span className="label">{t("style.exit")}</span>
          <select className="input" value={value.exit ?? "none"} onChange={(e) => onChange({ exit: e.target.value as TextExit }, true)}>
            {EXITS.map((item) => (
              <option key={item} value={item}>
                {t(`style.exit.${item}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-style-anim">
          <span className="label">{t("style.loop")}</span>
          <select className="input" value={value.loop ?? "none"} onChange={(e) => onChange({ loop: e.target.value as TextLoop }, true)}>
            {LOOPS.map((item) => (
              <option key={item} value={item}>
                {t(`style.loop.${item}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {value.animation === "karaoke" && <p className="muted small">{t("style.karaokeHint")}</p>}

      <div className="field">
        <span className="label">{t("style.presets")}</span>
        <div className="style-presets">
          {presets.map((preset) => {
            const sample = { ...value, ...preset.patch };
            const on = matchesPreset(value, preset.patch);
            return (
              <button
                type="button"
                key={preset.id}
                className={`style-preset ${on ? "on" : ""}`}
                aria-pressed={on}
                onClick={() => onChange(preset.patch, true)}
                title={t(`style.preset.${preset.id}` as MessageKey)}
              >
                <span className="style-preset-sample" style={{ ...textCss({ ...sample, animation: "none" }, 64, 30 / 64, sampleFile), opacity: sample.opacity }}>
                  {preset.id === "karaoke" ? (
                    <>
                      <span style={{ color: sample.highlight_color }}>字</span>幕
                    </>
                  ) : (
                    "字幕"
                  )}
                </span>
                <span className="style-preset-name">{t(`style.preset.${preset.id}` as MessageKey)}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="row wrap">
        {color("color", t("style.text"))}
        {!boxed && color("outline_color", t("style.outline"))}
        {!boxed && value.outline2 > 0 && color("outline2_color", t("style.outline2"))}
        {value.shadow > 0 && color("shadow_color", t("style.shadow"))}
        {boxed && color("background", t("style.box"))}
        {value.animation === "karaoke" && color("highlight_color", t("style.highlight"))}
      </div>

      <details className="text-style-more">
        <summary>{t("style.more")}</summary>
        <div className="grid-2">
          <Slider
            id={`${id}-outline`}
            label={t("style.outlineWidth")}
            value={value.outline}
            min={0}
            max={12}
            step={0.5}
            format={(v) => (v === 0 ? t("style.off") : `${v}px`)}
            onInput={(outline) => onChange({ outline }, false)}
            onCommit={(outline) => onChange({ outline }, true)}
          />
          <Slider
            id={`${id}-outline2`}
            label={t("style.outline2Width")}
            value={value.outline2 ?? 0}
            min={0}
            max={12}
            step={0.5}
            format={(v) => (v === 0 ? t("style.off") : `${v}px`)}
            onInput={(outline2) => onChange({ outline2 }, false)}
            onCommit={(outline2) => onChange({ outline2 }, true)}
          />
          <Slider
            id={`${id}-shadow`}
            label={t("style.shadowDistance")}
            value={value.shadow}
            min={0}
            max={12}
            step={0.5}
            format={(v) => (v === 0 ? t("style.off") : `${v}px`)}
            onInput={(shadow) => onChange({ shadow }, false)}
            onCommit={(shadow) => onChange({ shadow }, true)}
          />
          {value.shadow > 0 && (
            <>
              <Slider
                id={`${id}-shadow_angle`}
                label={t("style.shadowAngle")}
                value={value.shadow_angle ?? 0}
                min={0}
                max={360}
                step={15}
                format={(v) => `${v}°`}
                onInput={(shadow_angle) => onChange({ shadow_angle }, false)}
                onCommit={(shadow_angle) => onChange({ shadow_angle }, true)}
              />
              <Slider
                id={`${id}-shadow_blur`}
                label={t("style.shadowBlur")}
                value={value.shadow_blur ?? 0}
                min={0}
                max={12}
                step={0.5}
                format={(v) => (v === 0 ? t("style.off") : `${v}`)}
                onInput={(shadow_blur) => onChange({ shadow_blur }, false)}
                onCommit={(shadow_blur) => onChange({ shadow_blur }, true)}
              />
            </>
          )}
          <Slider
            id={`${id}-glow`}
            label={t("style.glow")}
            value={value.glow}
            min={0}
            max={12}
            step={0.5}
            format={(v) => (v === 0 ? t("style.off") : `${v}`)}
            onInput={(glow) => onChange({ glow }, false)}
            onCommit={(glow) => onChange({ glow }, true)}
          />
          <div>
            <Slider
              id={`${id}-box`}
              label={t("style.boxOpacity")}
              value={value.background_opacity}
              min={0}
              max={1}
              step={0.05}
              format={(v) => (v === 0 ? t("style.off") : `${Math.round(v * 100)}%`)}
              onInput={(background_opacity) => onChange({ background_opacity }, false)}
              onCommit={(background_opacity) => onChange({ background_opacity }, true)}
            />
            {boxed && <p className="muted small">{t("style.boxHint")}</p>}
          </div>
          <Slider
            id={`${id}-spacing`}
            label={t("style.spacing")}
            value={value.spacing}
            min={-4}
            max={30}
            step={1}
            format={(v) => `${v}px`}
            onInput={(spacing) => onChange({ spacing }, false)}
            onCommit={(spacing) => onChange({ spacing }, true)}
          />
          <Slider
            id={`${id}-line_spacing`}
            label={t("style.lineSpacing")}
            value={value.line_spacing ?? 0}
            min={0}
            max={80}
            step={2}
            format={(v) => (v === 0 ? t("style.off") : `${v}px`)}
            onInput={(line_spacing) => onChange({ line_spacing }, false)}
            onCommit={(line_spacing) => onChange({ line_spacing }, true)}
          />
          <Slider
            id={`${id}-opacity`}
            label={t("style.opacity")}
            value={value.opacity}
            min={0.1}
            max={1}
            step={0.05}
            format={(v) => `${Math.round(v * 100)}%`}
            onInput={(opacity) => onChange({ opacity }, false)}
            onCommit={(opacity) => onChange({ opacity }, true)}
          />
        </div>
      </details>
    </div>
  );
}

function AlignIcon({ align }: { align: TextAlign }) {
  const lines = { left: ["M4 6h16", "M4 10h10", "M4 14h16", "M4 18h10"], center: ["M4 6h16", "M7 10h10", "M4 14h16", "M7 18h10"], right: ["M4 6h16", "M10 10h10", "M4 14h16", "M10 18h10"] }[align];
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      {lines.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

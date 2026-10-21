import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { en, zh, type MessageKey } from "./messages";

export type UILang = "zh" | "en";
type Vars = Record<string, string | number>;
export type Translate = (key: MessageKey, vars?: Vars) => string;

const STORAGE_KEY = "shotwright.ui-lang";
const TABLES: Record<UILang, Record<MessageKey, string>> = { zh, en };

function initialLang(): UILang {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch {
    /* 隐私模式等情况下读不到，按浏览器语言 */
  }
  return (navigator.language || "zh").toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function format(text: string, vars?: Vars): string {
  return vars ? text.replace(/\{(\w+)\}/g, (all, name: string) => (name in vars ? String(vars[name]) : all)) : text;
}

// 组件外（例如 api.ts）也需要当前语言。
let current: UILang = initialLang();
export const currentLang = () => current;
export const translate: Translate = (key, vars) => format(TABLES[current][key] ?? zh[key], vars);

const I18nContext = createContext<{ lang: UILang; setLang: (lang: UILang) => void; t: Translate }>({
  lang: current,
  setLang: () => undefined,
  t: translate,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<UILang>(current);

  const setLang = useCallback((next: UILang) => {
    current = next;
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* 保存失败只影响下次打开 */
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang]);

  const value = useMemo(
    () => ({ lang, setLang, t: ((key, vars) => format(TABLES[lang][key] ?? zh[key], vars)) as Translate }),
    [lang, setLang],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export const useI18n = () => useContext(I18nContext);

/** 服务名称：英文界面优先用 name_en。 */
export const providerName = (p: { name: string; name_en?: string }, lang: UILang) =>
  (lang === "en" && p.name_en) || p.name;

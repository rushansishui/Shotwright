import { useEffect, useState } from "react";
import { Cut } from "./components/Cut";
import { Editor } from "./components/Editor";
import { Home } from "./components/Home";
import { Settings } from "./components/Settings";
import { useI18n } from "./i18n";

type Route =
  | { page: "home" }
  | { page: "settings" }
  | { page: "project"; id: string }
  | { page: "cut"; projectId?: string; assetId?: string };

function parseHash(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const match = hash.match(/^p\/([A-Za-z0-9_-]{1,64})$/);
  if (match) return { page: "project", id: match[1] };
  if (hash === "settings") return { page: "settings" };
  const cut = hash.match(/^cut(?:\/([A-Za-z0-9_-]{1,64})\/([A-Za-z0-9_-]{1,64}))?$/);
  if (cut) return { page: "cut", projectId: cut[1], assetId: cut[2] };
  return { page: "home" };
}

export function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const { t, lang, setLang } = useI18n();

  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#/">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">Shotwright</span>
            <span className="brand-cn">{t("app.brandSub")}</span>
          </a>
          <nav className="nav" aria-label={t("nav.main")}>
            <a href="#/" className={route.page === "home" || route.page === "project" ? "active" : ""}>
              {t("nav.create")}
            </a>
            <a href="#/cut" className={route.page === "cut" ? "active" : ""}>
              {t("nav.cut")}
            </a>
            <a href="#/settings" className={route.page === "settings" ? "active" : ""}>
              {t("nav.status")}
            </a>
          </nav>
          <button
            type="button"
            className="btn ghost lang-switch"
            title={t("nav.switchLang")}
            aria-label={t("nav.switchLang")}
            onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          >
            {lang === "zh" ? "EN" : "中文"}
          </button>
        </div>
      </header>
      {route.page === "home" && <Home />}
      {route.page === "settings" && <Settings />}
      {route.page === "cut" && <Cut projectId={route.projectId} assetId={route.assetId} />}
      {route.page === "project" && <Editor key={route.id} projectId={route.id} />}
    </div>
  );
}

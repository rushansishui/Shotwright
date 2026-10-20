import { useEffect, useState } from "react";
import { Editor } from "./components/Editor";
import { Home } from "./components/Home";
import { Settings } from "./components/Settings";

type Route = { page: "home" } | { page: "settings" } | { page: "project"; id: string };

function parseHash(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const match = hash.match(/^p\/([A-Za-z0-9_-]{1,64})$/);
  if (match) return { page: "project", id: match[1] };
  if (hash === "settings") return { page: "settings" };
  return { page: "home" };
}

export function App() {
  const [route, setRoute] = useState<Route>(parseHash);

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
            <span className="brand-cn">镜匠</span>
          </a>
          <nav className="nav" aria-label="主导航">
            <a href="#/" className={route.page !== "settings" ? "active" : ""}>
              创作
            </a>
            <a href="#/settings" className={route.page === "settings" ? "active" : ""}>
              服务状态
            </a>
          </nav>
        </div>
      </header>
      {route.page === "home" && <Home />}
      {route.page === "settings" && <Settings />}
      {route.page === "project" && <Editor key={route.id} projectId={route.id} />}
    </div>
  );
}

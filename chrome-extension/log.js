// テレボートでの自動操作の記録。chrome.storage.local の teleboat_log に最新 1000 行までためる (設定画面で見られる)。
(() => {
  const KEY = "teleboat_log", MAX = 1000;
  let chain = Promise.resolve();
  const page = /\/betconf/.test(location.pathname) ? "購入確認" : /\/betcom\//.test(location.pathname) ? "投票" :
    /\/bet\/top\//.test(location.pathname) ? "開催一覧" : location.pathname === "/" ? "ログイン" : location.pathname.split("/").slice(-2).join("/");
  globalThis.byosouLog = (msg) => {
    const d = new Date();
    const ts = `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
    const line = `${ts} [${page}] ${msg}`;
    chain = chain.then(() => new Promise((res) => {
      try {
        chrome.storage.local.get(KEY, (v) => {
          const a = (v[KEY] || []).concat([line]).slice(-MAX);
          chrome.storage.local.set({[KEY]: a}, res);
        });
      } catch { res(); }
    }));
  };
})();

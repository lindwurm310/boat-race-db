// ページ (boatrace.jp) からは直接パソコンのサーバーに繋げないので、ここで代わりに問い合わせる。
const SERVER = "http://127.0.0.1:8765";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type !== "recommend") return;
  const q = new URLSearchParams({hd: msg.hd, jcd: msg.jcd, rno: msg.rno});
  fetch(`${SERVER}/recommend?${q}`)
    .then(async (r) => sendResponse({ok: r.ok, body: await r.json()}))
    .catch(() => sendResponse({ok: false, offline: true}));
  return true; // sendResponse を後で呼ぶ
});

// ツールバーのボタン: 設定画面を開く (操作画面・テレボートへのリンクもそこにある)
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onMessage.addListener((msg) => { if (msg.type === "openOptions") chrome.runtime.openOptionsPage(); });

// テレボートの投票画面用: 予想ページ (GitHub Pages) からその日の予想を読む
const PAGES = "https://lindwurm310.github.io/boat-race-db";
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type !== "predictions" || !/^\d{4}-\d{2}-\d{2}$/.test(msg.date)) return;
  fetch(`${PAGES}/data/predictions/${msg.date}.json`, {cache: "no-store"})
    .then(async (r) => sendResponse({ok: r.ok, body: r.ok ? await r.json() : null}))
    .catch(() => sendResponse({ok: false}));
  return true;
});

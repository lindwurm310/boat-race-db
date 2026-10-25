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

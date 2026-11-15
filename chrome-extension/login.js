// テレボートのログイン画面: 設定画面 (拡張機能のアイコン) で保存した加入者番号・暗証番号・認証用パスワードを入れて、
// 自動で「ログイン」を押す。値はこのブラウザの chrome.storage.local にだけある。
(() => {
  // ?byosou=log のときはログインせず、保存したログを画面に出す (Claude が読んで直すため)
  if (/byosou=log/.test(location.search + location.hash)) {
    chrome.storage.local.get("teleboat_log", (v) => {
      const pre = document.createElement("pre");
      pre.id = "byosou-log-dump";
      pre.style.cssText = "position:fixed;inset:0;z-index:99999;background:#fff;color:#000;overflow:auto;margin:0;padding:8px;font-size:12px;white-space:pre-wrap";
      pre.textContent = (v.teleboat_log || []).join("\n");
      document.body.append(pre);
    });
    return;
  }
  const btn = document.getElementById("loginButton");
  if (!btn) return;
  const FIELDS = [["memberNo", "加入者番号"], ["pin", "暗証番号"], ["authPassword", "認証用パスワード"]];
  const $ = (id) => document.getElementById(id);
  const L = (m) => { try { globalThis.byosouLog && globalThis.byosouLog(m); } catch { /* なし */ } };

  const box = document.createElement("div");
  box.id = "byosou-pinbox";
  const text = document.createElement("span");
  const open = document.createElement("button");
  open.type = "button";
  open.textContent = "設定を開く";
  open.onclick = () => chrome.runtime.sendMessage({type: "openOptions"});
  box.append(text, " ", open);
  document.body.append(box);

  chrome.storage.local.get(null, (v) => {
    // 前の版で保存した暗証番号を引き継ぐ
    if (v.teleboatPin && !v.teleboat_pin) {
      v.teleboat_pin = v.teleboatPin;
      chrome.storage.local.set({teleboat_pin: v.teleboatPin}, () => chrome.storage.local.remove("teleboatPin"));
    }
    const missing = FIELDS.filter(([id]) => !v["teleboat_" + id]).map(([, l]) => l);
    if (missing.length) {
      text.textContent = `自動ログイン: ${missing.join("・")}が未設定です`;
      L(text.textContent);
      return;
    }
    text.textContent = "自動ログインします…";
    // 失敗したときや画面を開き直したときに押し続けないよう、どのタブからでも3分に1回まで
    // (続けて押すとテレボートが「連続して操作された」エラーを出す)
    const last = +v.teleboat_lastLogin || 0;
    if (Date.now() - last < 180000) { text.textContent = "自動ログイン: 3分以内に一度押したので、今回は押しません"; L(text.textContent); return; }
    let tries = 0;
    const timer = setInterval(() => {
      tries++;
      FIELDS.forEach(([id]) => {
        const e = $(id);
        if (e && e.value !== v["teleboat_" + id]) {
          e.value = v["teleboat_" + id];
          e.dispatchEvent(new Event("input", {bubbles: true}));
          e.dispatchEvent(new Event("change", {bubbles: true}));
        }
      });
      const ready = FIELDS.every(([id]) => $(id) && $(id).value !== "");
      if (ready && !btn.parentElement.classList.contains("off") && !btn.disabled) {
        clearInterval(timer);
        chrome.storage.local.set({teleboat_lastLogin: Date.now()});
        btn.click();
        text.textContent = "自動ログイン: ログインを押しました（投票画面が別ウィンドウで開きます）";
        L("自動ログイン: ログインを押しました");
      } else if (tries > 40) {
        clearInterval(timer);
        text.textContent = "自動ログイン: ログイン画面の入力欄が見つかりませんでした";
        L(text.textContent);
      }
    }, 250);
  });
})();

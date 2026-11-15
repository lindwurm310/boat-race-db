// テレボートのログイン画面: 設定画面 (拡張機能のアイコン) で保存した加入者番号・暗証番号・認証用パスワードを入れて、
// 自動で「ログイン」を押す。値はこのブラウザの chrome.storage.local にだけある。
(() => {
  const btn = document.getElementById("loginButton");
  if (!btn) return;
  const KEY = "byosou-autologin-at";
  const FIELDS = [["memberNo", "加入者番号"], ["pin", "暗証番号"], ["authPassword", "認証用パスワード"]];
  const $ = (id) => document.getElementById(id);

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
      return;
    }
    text.textContent = "自動ログインします…";
    // 失敗したときに押し続けないよう、1分に1回まで
    let last = 0;
    try { last = +sessionStorage.getItem(KEY) || 0; } catch { /* なし */ }
    if (Date.now() - last < 60000) { text.textContent = "自動ログイン: 1分以内に一度押したので、今回は押しません"; return; }
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
        try { sessionStorage.setItem(KEY, String(Date.now())); } catch { /* なし */ }
        btn.click();
        text.textContent = "自動ログイン: ログインを押しました（投票画面が別ウィンドウで開きます）";
      } else if (tries > 40) {
        clearInterval(timer);
        text.textContent = "自動ログイン: ログイン画面の入力欄が見つかりませんでした";
      }
    }, 250);
  });
})();

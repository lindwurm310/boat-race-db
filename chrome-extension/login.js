// テレボートのログイン画面: 加入者番号・暗証番号・認証用パスワードが入っていれば、自動で「ログイン」を押す。
// テレボート自身の「ログイン情報を保存する」で入った値はそのまま使い、空の欄は、ログイン画面の左下の欄で
// 自分で一度入れた値 (この Chrome の中の chrome.storage.local にだけ保存) で埋める。
(() => {
  const btn = document.getElementById("loginButton");
  if (!btn) return;
  const KEY = "byosou-autologin-at";
  const STORE_KEY = "teleboat_";
  const $ = (id) => document.getElementById(id);
  const filled = () => ["memberNo", "pin", "authPassword"].every((id) => $(id) && $(id).value !== "");

  // ログイン情報の保存欄 (Chrome のパスワード自動入力は、画面に出ていてもページから値が読めず
  // 自動で「ログイン」を押せないため、拡張機能に保存する)
  const FIELDS = [["memberNo", "加入者番号", "tel"], ["pin", "暗証番号", "password"], ["authPassword", "認証用パスワード", "password"]];
  const box = document.createElement("div");
  box.id = "byosou-pinbox";
  box.innerHTML = "<b>自動ログイン</b><br>" + FIELDS.map(([id, label, type]) =>
    `<label>${label} <input data-id="${id}" type="${type}" class="inputPass" autocomplete="off" maxlength="10"></label>`).join("<br>") +
    '<br><button type="button" data-a="save">保存</button> <button type="button" data-a="del">全部消す</button> <span></span>';
  document.body.append(box);
  const note = box.querySelector("span");
  box.querySelector('[data-a="save"]').onclick = () => {
    const v = {};
    for (const inp of box.querySelectorAll("input")) {
      if (inp.value === "") continue;
      if (!/^[0-9A-Za-z]{1,10}$/.test(inp.value)) { note.textContent = "英数字で入れてください"; return; }
      v[STORE_KEY + inp.dataset.id] = inp.value;
    }
    if (!Object.keys(v).length) { note.textContent = "入れたい欄に入力してください"; return; }
    chrome.storage.local.set(v, () => {
      box.querySelectorAll("input").forEach((i) => { i.value = ""; });
      try { sessionStorage.removeItem(KEY); } catch { /* なし */ }
      showSaved(); start();
    });
  };
  box.querySelector('[data-a="del"]').onclick = () => {
    chrome.storage.local.remove(FIELDS.map(([id]) => STORE_KEY + id).concat(["teleboatPin"]), () => { showSaved(); });
  };
  function showSaved() {
    chrome.storage.local.get(null, (v) => {
      const have = FIELDS.filter(([id]) => v[STORE_KEY + id]).map(([, l]) => l);
      note.textContent = have.length ? `保存済み: ${have.join("・")}` : "未保存";
    });
  }
  // 前の版で保存した暗証番号を引き継ぐ
  chrome.storage.local.get("teleboatPin", (v) => {
    const go = () => { showSaved(); start(); };
    if (v.teleboatPin) chrome.storage.local.set({[STORE_KEY + "pin"]: v.teleboatPin}, () => chrome.storage.local.remove("teleboatPin", go));
    else go();
  });

  function start() {
    // 失敗したときに押し続けないよう、1分に1回まで
    let last = 0;
    try { last = +sessionStorage.getItem(KEY) || 0; } catch { /* なし */ }
    if (Date.now() - last < 60000) return;
    chrome.storage.local.get(null, (v) => {
      let tries = 0;
      const timer = setInterval(() => {
        tries++;
        FIELDS.forEach(([id]) => {
          const e = $(id);
          if (e && e.value === "" && v[STORE_KEY + id]) {
            e.value = v[STORE_KEY + id];
            e.dispatchEvent(new Event("input", {bubbles: true}));
            e.dispatchEvent(new Event("change", {bubbles: true}));
          }
        });
        if (filled() && !btn.parentElement.classList.contains("off") && !btn.disabled) {
          clearInterval(timer);
          try { sessionStorage.setItem(KEY, String(Date.now())); } catch { /* なし */ }
          btn.click();
        } else if (tries > 20) {
          clearInterval(timer); // そろっていなければ何もしない (手でログイン)
        }
      }, 250);
    });
  }
})();

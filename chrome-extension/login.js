// テレボートのログイン画面: 加入者番号・暗証番号・認証用パスワードが入っていれば、自動で「ログイン」を押す。
// 加入者番号・認証用パスワードはテレボート自身の「ログイン情報を保存する」に任せる。
// 暗証番号はその保存で入らないことがあるので、ログイン画面に出す欄で自分で一度入れると、
// この Chrome の中 (chrome.storage.local) にだけ保存して、次から自動で入れる。
(() => {
  const btn = document.getElementById("loginButton");
  if (!btn) return;
  const KEY = "byosou-autologin-at";
  const PIN_KEY = "teleboatPin";
  const $ = (id) => document.getElementById(id);
  const filled = () => ["memberNo", "pin", "authPassword"].every((id) => $(id) && $(id).value !== "");

  // 暗証番号の保存欄
  const box = document.createElement("div");
  box.id = "byosou-pinbox";
  box.innerHTML = '<b>自動ログイン用の暗証番号</b> <input type="password" class="inputPass" inputmode="numeric" autocomplete="off" maxlength="10"> ' +
    '<button type="button" data-a="save">保存</button> <button type="button" data-a="del">消す</button> <span></span>';
  document.body.append(box);
  const input = box.querySelector("input");
  const note = box.querySelector("span");
  box.querySelector('[data-a="save"]').onclick = () => {
    if (!/^[0-9A-Za-z]{1,10}$/.test(input.value)) { note.textContent = "英数字で入れてください"; return; }
    chrome.storage.local.set({[PIN_KEY]: input.value}, () => {
      input.value = "";
      note.textContent = "保存しました。次からログイン画面を開くと自動で入ります";
      start();
    });
  };
  box.querySelector('[data-a="del"]').onclick = () => {
    chrome.storage.local.remove(PIN_KEY, () => { note.textContent = "消しました"; });
  };

  chrome.storage.local.get(PIN_KEY, (v) => {
    if (v[PIN_KEY]) note.textContent = "保存済み";
    start();
  });

  function start() {
    // 失敗したときに押し続けないよう、1分に1回まで
    let last = 0;
    try { last = +sessionStorage.getItem(KEY) || 0; } catch { /* なし */ }
    if (Date.now() - last < 60000) return;
    chrome.storage.local.get(PIN_KEY, (v) => {
      let tries = 0;
      const timer = setInterval(() => {
        tries++;
        const pin = $("pin");
        if (pin && pin.value === "" && v[PIN_KEY]) {
          pin.value = v[PIN_KEY];
          pin.dispatchEvent(new Event("input", {bubbles: true}));
          pin.dispatchEvent(new Event("change", {bubbles: true}));
        }
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

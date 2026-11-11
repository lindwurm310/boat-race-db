// テレボートのログイン画面: 「ログイン情報を保存する」で保存した加入者番号・暗証番号・認証用パスワードが
// 入っていれば、自動で「ログイン」を押す。パスワードはテレボート自身の保存機能 (Chrome の Cookie) に任せ、
// この拡張機能では持たない。
(() => {
  const btn = document.getElementById("loginButton");
  if (!btn) return;
  const KEY = "byosou-autologin-at";
  const filled = () => ["memberNo", "pin", "authPassword"].every((id) => {
    const e = document.getElementById(id);
    return e && e.value !== "";
  });
  // 失敗したときに押し続けないよう、1分に1回まで
  let last = 0;
  try { last = +sessionStorage.getItem(KEY) || 0; } catch { /* なし */ }
  if (Date.now() - last < 60000) return;

  let tries = 0;
  const timer = setInterval(() => {
    tries++;
    if (filled() && !btn.parentElement.classList.contains("off") && !btn.disabled) {
      clearInterval(timer);
      try { sessionStorage.setItem(KEY, String(Date.now())); } catch { /* なし */ }
      btn.click();
    } else if (tries > 20) {
      clearInterval(timer); // 保存されていなければ何もしない (手でログイン)
    }
  }, 250);
})();

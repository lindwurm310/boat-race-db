// 設定画面: テレボートのログイン情報・投票用パスワードと、全自動・1点の金額・1日の上限を chrome.storage.local に保存する。
const SECRET = ["memberNo", "pin", "authPassword", "betPassword"];
const LABEL = {memberNo: "加入者番号", pin: "暗証番号", authPassword: "認証用パスワード", betPassword: "投票用パスワード"};
const $ = (id) => document.getElementById(id);

function refresh() {
  chrome.storage.local.get(null, (v) => {
    $("auto").checked = v.teleboat_auto !== false;
    $("stake").value = v.teleboat_stake || 100;
    $("cap").value = v.teleboat_cap || 4000;
    const have = SECRET.filter((k) => v["teleboat_" + k]);
    $("status").textContent = "保存済み: " + (have.length ? have.map((k) => LABEL[k]).join("・") : "なし");
    SECRET.forEach((k) => { $(k).placeholder = v["teleboat_" + k] ? "保存済み（変えるときだけ入力）" : ""; });
  });
}

$("save").onclick = () => {
  const out = {};
  for (const k of SECRET) {
    const val = $(k).value.trim();
    if (val === "") continue;
    if (!/^[0-9A-Za-z]{1,10}$/.test(val)) { $("msg").textContent = `${LABEL[k]}は英数字10文字までで入れてください`; return; }
    out["teleboat_" + k] = val;
  }
  const stake = Math.max(100, Math.round(+$("stake").value / 100) * 100 || 100);
  const cap = Math.max(stake, Math.round(+$("cap").value / 100) * 100 || 4000);
  Object.assign(out, {teleboat_auto: $("auto").checked, teleboat_stake: stake, teleboat_cap: cap});
  chrome.storage.local.set(out, () => {
    SECRET.forEach((k) => { $(k).value = ""; });
    $("msg").textContent = "保存しました";
    function showLog() {
  chrome.storage.local.get("teleboat_log", (v) => {
    const a = v.teleboat_log || [];
    $("log").value = a.length ? a.slice().reverse().join("\n") : "（まだ記録はありません）";
  });
}
$("logReload").onclick = showLog;
$("logCopy").onclick = () => { navigator.clipboard.writeText($("log").value).then(() => { $("msg").textContent = "ログをコピーしました"; }); };
$("logClear").onclick = () => { if (confirm("ログを消しますか?")) chrome.storage.local.remove("teleboat_log", showLog); };
chrome.storage.onChanged.addListener((c) => { if (c.teleboat_log) showLog(); });

$("ver").textContent = "v" + chrome.runtime.getManifest().version;
refresh();
showLog();
  });
};

$("clear").onclick = () => {
  if (!confirm("保存したログイン情報と投票用パスワードを全部消しますか?")) return;
  chrome.storage.local.remove(SECRET.map((k) => "teleboat_" + k).concat(["teleboatPin"]), () => {
    $("msg").textContent = "消しました";
    function showLog() {
  chrome.storage.local.get("teleboat_log", (v) => {
    const a = v.teleboat_log || [];
    $("log").value = a.length ? a.slice().reverse().join("\n") : "（まだ記録はありません）";
  });
}
$("logReload").onclick = showLog;
$("logCopy").onclick = () => { navigator.clipboard.writeText($("log").value).then(() => { $("msg").textContent = "ログをコピーしました"; }); };
$("logClear").onclick = () => { if (confirm("ログを消しますか?")) chrome.storage.local.remove("teleboat_log", showLog); };
chrome.storage.onChanged.addListener((c) => { if (c.teleboat_log) showLog(); });

$("ver").textContent = "v" + chrome.runtime.getManifest().version;
refresh();
showLog();
  });
};

function showLog() {
  chrome.storage.local.get("teleboat_log", (v) => {
    const a = v.teleboat_log || [];
    $("log").value = a.length ? a.slice().reverse().join("\n") : "（まだ記録はありません）";
  });
}
$("logReload").onclick = showLog;
$("logCopy").onclick = () => { navigator.clipboard.writeText($("log").value).then(() => { $("msg").textContent = "ログをコピーしました"; }); };
$("logClear").onclick = () => { if (confirm("ログを消しますか?")) chrome.storage.local.remove("teleboat_log", showLog); };
chrome.storage.onChanged.addListener((c) => { if (c.teleboat_log) showLog(); });

$("ver").textContent = "v" + chrome.runtime.getManifest().version;
refresh();
showLog();

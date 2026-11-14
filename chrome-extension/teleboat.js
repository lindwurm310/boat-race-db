// テレボート (ib.mbrace.or.jp) の投票画面に、今日の予想の単勝買い目のパネルを出す。
// 「全自動」がオンなら、画面を開いたときに 不足分の入金 → ベットリストに入れる → 投票入力完了 → 購入 まで行う
// (1日の上限 DAY_CAP 円まで。入金・購入には自動ログイン欄で保存した投票用パスワードを使う)。
(() => {
  const CONFIRM_KEY = "byosou-confirm"; // 自動で「投票入力完了」を押したときの点数・合計 (購入確認画面で照合する)
  if (/\/betconf/.test(location.pathname) || (!document.getElementById("betkati1") && !document.getElementById("boatraceTitle"))) {
    confirmPage();
    return;
  }

  const STAKE = 100;      // 1点の金額 (円)
  const DAY_CAP = 4000;   // 1日の上限 (円)。購入限度額 (残高) が少ないときはそちらに合わせる
  const MARGIN_MIN = 3;   // 締切まで3分を切ったレースは入れない
  const DONE_KEY = "byosou-teleboat-done-"; // 今日ベットリストに入れた買い目 (二重に入れないため)
  const DEP_KEY = "byosou-teleboat-deposit-"; // 今日自動で入金した金額
  const AUTO_KEY = "byosou-auto-tried-";      // このウィンドウで今日の全自動を始めたか (失敗したときに繰り返さない)

  const panel = el("div", {id: "byosou-panel", className: "byosou-teleboat"});
  const head = el("div", {className: "byosou-head"});
  const title = el("span", {textContent: "今日の単勝買い目"});
  const reload = el("button", {textContent: "更新", title: "予想を読み直す"});
  const fold = el("button", {textContent: "－", title: "たたむ"});
  const body = el("div", {className: "byosou-body"});
  head.append(title, reload, fold);
  panel.append(head, body);
  document.body.append(panel);
  fold.onclick = () => {
    panel.classList.toggle("byosou-folded");
    fold.textContent = panel.classList.contains("byosou-folded") ? "＋" : "－";
  };
  reload.onclick = load;
  let running = false;
  let lastAll = [];
  let settings = {auto: true, betPassword: ""};
  chrome.storage.local.get(["teleboat_auto", "teleboat_betPassword"], (v) => {
    settings = {auto: v.teleboat_auto !== false, betPassword: v.teleboat_betPassword || ""};
    load();
  });

  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function nowHM(addMin) {
    const d = new Date(Date.now() + (addMin || 0) * 60000);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function limitYen() {
    const t = (document.getElementById("currentBetLimitAmount") || {}).textContent || "";
    const v = parseInt(t.replace(/[^0-9]/g, ""), 10);
    return Number.isFinite(v) ? v : null;
  }

  function doneSet() {
    try { return new Set(JSON.parse(localStorage.getItem(DONE_KEY + today()) || "[]")); } catch { return new Set(); }
  }
  function markDone(key) {
    const s = doneSet(); s.add(key);
    try { localStorage.setItem(DONE_KEY + today(), JSON.stringify([...s])); } catch { /* 記録できなくても続ける */ }
  }

  // 予想の行から、1点ずつの買い目にする: {key, race_id, jcd, rno, venue, deadline, boat, ev}
  function picks(rows) {
    const out = [];
    rows.forEach((r) => {
      if (!r["単勝買い目"]) return;
      const boats = String(r["単勝買い目"]).split(/\s+/).filter(Boolean);
      const evs = String(r["買い目の期待値"] || "").split(/\s+/);
      const [, jcd, rno] = r.race_id.split("-");
      boats.forEach((b, i) => out.push({
        key: `${r.race_id}-${b}`, race_id: r.race_id, jcd, rno, venue: r["場"], deadline: r["締切"],
        boat: b, ev: parseFloat(evs[i]) || 0,
      }));
    });
    return out.sort((a, b) => a.deadline.localeCompare(b.deadline));
  }

  // 締切前・まだ入れていない買い目を、上限に収まるように期待値の高い順に選ぶ
  function plan(all) {
    const done = doneSet();
    const cut = nowHM(MARGIN_MIN);
    const open = all.filter((p) => p.deadline > cut && !done.has(p.key));
    const lim = limitYen();
    const spent = done.size * STAKE;
    const cap = Math.max(0, Math.min(DAY_CAP - spent, lim === null ? DAY_CAP : lim));
    const n = Math.floor(cap / STAKE);
    const keep = new Set([...open].sort((a, b) => b.ev - a.ev).slice(0, n).map((p) => p.key));
    return {
      buy: open.filter((p) => keep.has(p.key)),
      dropped: open.filter((p) => !keep.has(p.key)),
      closed: all.filter((p) => p.deadline <= cut && !done.has(p.key)),
      done: all.filter((p) => done.has(p.key)),
      cap, lim, spent,
    };
  }

  function load() {
    if (running) return;
    body.replaceChildren(msg("今日の予想を読み込んでいます…"));
    chrome.runtime.sendMessage({type: "predictions", date: today()}, (res) => {
      if (!res || !res.ok) {
        body.replaceChildren(msg(`今日 (${today()}) の予想が読めませんでした。予想ページに今日の予想が出てから「更新」を押してください。`));
        return;
      }
      lastAll = picks(res.body.rows || []);
      show(plan(lastAll));
    });
  }

  function show(p) {
    title.textContent = `今日の単勝買い目 ${today()}`;
    const total = p.buy.length * STAKE;
    const parts = [];
    parts.push(el("div", {className: "byosou-verdict byosou-buy",
      textContent: p.buy.length ? `${p.buy.length}点・${total.toLocaleString()}円 をベットリストに入れます` : "入れる買い目はありません"}));
    parts.push(el("div", {className: "byosou-note", textContent:
      `1点${STAKE}円・1日の上限${DAY_CAP.toLocaleString()}円` +
      (p.lim !== null ? `・購入限度額 ${p.lim.toLocaleString()}円` : "") +
      (p.spent ? `・今日入れた分 ${p.spent.toLocaleString()}円` : "") +
      (p.dropped.length ? `・上限を超えるので期待値の低い ${p.dropped.length}点 を外しました` : "") +
      (p.closed.length ? `・締切済み ${p.closed.length}点` : "")}));
    if (p.buy.length) parts.push(table(p.buy));
    const go = el("button", {className: "byosou-go", textContent: "ベットリストに入れる", disabled: !p.buy.length});
    const log = el("div", {className: "byosou-log"});
    go.onclick = () => run(p.buy, go, log);
    const reset = el("button", {className: "byosou-reset", textContent: "今日の記録を消す",
      title: "ベットリストを消して入れ直すときに押す (今日入れた買い目の記録を消す)"});
    reset.onclick = () => {
      if (!confirm("今日ベットリストに入れた記録を消して、もう一度入れられるようにしますか?")) return;
      try { localStorage.removeItem(DONE_KEY + today()); } catch { /* なし */ }
      load();
    };
    const autoBox = el("label", {className: "byosou-auto"});
    const autoChk = el("input", {type: "checkbox", checked: settings.auto});
    autoChk.onchange = () => { settings.auto = autoChk.checked; chrome.storage.local.set({teleboat_auto: autoChk.checked}); };
    autoBox.append(autoChk, document.createTextNode(" 全自動 (開いたときに入金・購入まで行う)"));
    parts.push(go, reset, autoBox, log, el("div", {className: "byosou-help", textContent: settings.auto
      ? (settings.betPassword ? "全自動: 不足分を入金し、ベットリストに入れて購入まで行います。" : "全自動には投票用パスワードが要ります。ログイン画面の「自動ログイン」欄で保存してください。")
      : "入れるのはベットリストまでです。中身と合計金額を確かめてから、自分で「投票入力完了」を押し、投票用パスワードを入れて購入してください。"}));
    body.replaceChildren(...parts);
    let tried = false;
    try { tried = sessionStorage.getItem(AUTO_KEY + today()) === "1"; } catch { /* なし */ }
    if (settings.auto && settings.betPassword && p.buy.length && !tried && !running) {
      try { sessionStorage.setItem(AUTO_KEY + today(), "1"); } catch { /* なし */ }
      setTimeout(() => autoRun(p, go, log), 1500);
    }
  }

  // ---- 全自動: 入金 → ベットリスト → 投票入力完了 ----
  async function autoRun(p, go, log) {
    const say = (t) => log.append(el("div", {textContent: t}));
    try {
      const need = p.buy.length * STAKE;
      const lim = limitYen();
      if (lim === null) throw new Error("購入限度額が読めません");
      if (lim < need) {
        let depToday = 0;
        try { depToday = +localStorage.getItem(DEP_KEY + today()) || 0; } catch { /* なし */ }
        const room = Math.floor((DAY_CAP - depToday) / 1000) * 1000;
        const dep = Math.min(Math.ceil((need - lim) / 1000) * 1000, room);
        if (dep > 0) {
          say(`残高 ${lim.toLocaleString()}円 が足りないので ${dep.toLocaleString()}円 を入金します…`);
          await deposit(dep);
          try { localStorage.setItem(DEP_KEY + today(), String(depToday + dep)); } catch { /* なし */ }
          say("入金を指示しました。残高に反映されるのを待っています…");
          await waitBalance(Math.min(need, lim + dep));
        }
        p = plan(lastAll); // 残高に合わせて買い目を選び直す
        show(p); // 表を描き直す (自動はもう始めたので繰り返さない)
        log = body.querySelector(".byosou-log"); go = body.querySelector(".byosou-go");
      }
      if (!p.buy.length) { say("残高が無いので購入しませんでした"); return; }
      const ok = await run(p.buy, go, log);
      if (!ok) throw new Error("ベットリストに1点も入りませんでした");
      const count = betCount(), total = totalAmount();
      if (total > DAY_CAP) throw new Error(`合計 ${total}円 が上限を超えるので購入をやめました`);
      try { sessionStorage.setItem(CONFIRM_KEY, JSON.stringify({date: today(), count, total})); } catch { /* なし */ }
      say(`${count}点・${total.toLocaleString()}円 を購入します…`);
      const submit = await waitFor(() => {
        const b = document.querySelector("#betList .btnSubmit");
        return b && !b.classList.contains("off") ? b.querySelector("a") : null;
      }, "「投票入力完了」が押せません");
      submit.click();
    } catch (e) {
      log.append(el("div", {className: "byosou-verdict", textContent: `全自動を止めました: ${e.message}`}));
    }
  }

  async function deposit(amount) {
    const menu = await waitFor(() => document.querySelector("#charge a"), "「入金する」のメニューがありません");
    menu.click();
    const amt = await waitFor(() => visible(document.querySelectorAll("#chargeInstructAmt")), "入金の画面が開きません");
    setValue(amt, String(amount / 1000)); // 欄は1,000円単位 (「000円」が後ろに付く)
    setValue(await waitFor(() => visible(document.querySelectorAll("#chargeBetPassword")), "投票用パスワードの欄がありません"), settings.betPassword);
    await sleep(300);
    document.querySelector("#executeCharge a").click();
    await waitFor(() => {
      if (document.getElementById("closeChargecomp")) return true;
      const err = document.querySelector("#errorPop .errorTxt");
      if (err) throw new Error(`入金できませんでした: ${err.textContent.trim()}`);
      return false;
    }, "入金の結果が出ません", 30000);
    document.querySelector("#closeChargecomp a").click();
    await sleep(500);
  }

  async function waitBalance(target) {
    for (let i = 0; i < 60; i++) { // 30秒ごとに最大30分
      if ((limitYen() || 0) >= target) return;
      const b = document.querySelector("#updateBalanceBtn a"); if (b) b.click();
      await sleep(30000);
    }
    if ((limitYen() || 0) < STAKE) throw new Error("30分待っても入金が残高に反映されませんでした");
  }

  function totalAmount() {
    const s = document.getElementById("totalAmount");
    return s ? parseInt(s.textContent.replace(/[^0-9]/g, ""), 10) || 0 : 0;
  }

  function table(list) {
    const t = el("table");
    const h = el("tr");
    ["締切", "場", "R", "単勝", "期待値", "状態"].forEach((s) => h.append(el("th", {textContent: s})));
    t.append(h);
    list.forEach((p) => {
      const tr = el("tr", {id: `byosou-row-${p.key}`});
      [p.deadline, p.venue, `${+p.rno}R`, p.boat, p.ev.toFixed(2), ""].forEach((s, i) => {
        const td = el("td", {textContent: s});
        if (i === 3) td.className = `byosou-lane byosou-lane${p.boat}`;
        tr.append(td);
      });
      t.append(tr);
    });
    return t;
  }

  async function run(list, go, log) {
    if (running) return 0;
    running = true; if (go) go.disabled = true; reload.disabled = true;
    let ok = 0;
    for (const p of list) {
      const cell = document.querySelector(`#byosou-row-${CSS.escape(p.key)} td:last-child`);
      if (cell) cell.textContent = "入力中…";
      try {
        await addBet(p);
        markDone(p.key);
        ok++;
        if (cell) cell.textContent = "✓";
      } catch (e) {
        if (cell) cell.textContent = "×";
        log.append(el("div", {textContent: `${p.venue}${+p.rno}R 単勝${p.boat}: ${e.message}`}));
      }
    }
    log.prepend(el("div", {className: "byosou-verdict", textContent:
      `${ok}点 (${(ok * STAKE).toLocaleString()}円) をベットリストに入れました。`}));
    running = false; reload.disabled = false;
    return ok;
  }

  // ---- テレボートの画面操作 ----
  async function addBet(p) {
    const before = betCount();
    await selectVenue(p.jcd, p.venue);
    await selectRace(p.rno);
    await clickWhen("#betkati1 a", () => isCurrent("betkati1"), "単勝が選べません");
    await clickWhen("#betway1 a", () => isCurrent("betway1"), "通常投票が選べません");
    const btn = await waitFor(() => document.getElementById(`regbtn_${p.boat}_1`), "艇の番号のボタンがありません");
    if (btn.classList.contains("miss")) throw new Error("欠場のため入れませんでした");
    btn.querySelector("a").click();
    await sleep(300);
    const amount = await waitFor(() => visible(document.querySelectorAll("#amount")), "購入金額の欄がありません");
    setValue(amount, String(STAKE / 100)); // 欄は100円単位 (「00円」が後ろに付く)
    await sleep(200);
    const add = await waitFor(() => {
      const b = document.getElementById("regAmountBtn");
      return b && !b.classList.contains("off") ? b : null;
    }, "「ベットリストに追加」が押せません");
    add.querySelector("a").click();
    await waitFor(() => betCount() > before, "ベットリストに入ったことを確かめられませんでした");
    await sleep(300);
  }

  async function selectVenue(jcd, venue) {
    const h2 = () => ((document.querySelector("#boatraceTitle h2") || {}).textContent || "").trim();
    if (h2() === venue) return;
    const open = await waitFor(() => document.querySelector("#boatraceChnage a"), "場変更のボタンがありません");
    open.click();
    const j = await waitFor(() => document.getElementById(`j${jcd}`), `${venue}が場変更の一覧にありません`);
    if (j.parentElement.classList.contains("end")) {
      const c = document.querySelector("#closeJyosel a"); if (c) c.click();
      throw new Error(`${venue}は発売していません`);
    }
    j.click();
    await sleep(200);
    (await waitFor(() => document.getElementById("jyoChange"), "「変更する」がありません")).click();
    await waitFor(() => h2() === venue, `${venue}に切り替わりませんでした`);
    await sleep(300);
  }

  async function selectRace(rno) {
    const id = `selRaceNo${String(+rno).padStart(2, "0")}`;
    const tab = await waitFor(() => document.getElementById(id), `${+rno}Rのタブがありません`);
    if (tab.classList.contains("current")) return;
    tab.querySelector("a").click();
    await waitFor(() => (document.getElementById(id) || {classList: {contains: () => false}}).classList.contains("current"),
      `${+rno}Rが選べません (締切済みかもしれません)`);
    await sleep(300);
  }

  async function clickWhen(sel, done, err) {
    if (done()) return;
    (await waitFor(() => document.querySelector(sel), err)).click();
    await waitFor(done, err);
    await sleep(200);
  }

  function isCurrent(id) {
    const e = document.getElementById(id);
    return !!e && e.classList.contains("current");
  }

  function betCount() {
    const s = document.querySelector(".inputCompletion .betNumber strong");
    return s ? parseInt(s.textContent.replace(/[^0-9]/g, ""), 10) || 0 : 0;
  }

  function visible(nodes) {
    return [...nodes].find((n) => n.offsetParent !== null) || null;
  }

  function setValue(input, v) {
    input.focus();
    input.value = v;
    ["input", "keyup", "change"].forEach((t) => input.dispatchEvent(new Event(t, {bubbles: true})));
    input.blur();
  }

  async function waitFor(fn, err, ms = 8000) {
    const t0 = Date.now();
    for (;;) {
      const v = fn();
      if (v) return v;
      if (Date.now() - t0 > ms) throw new Error(err);
      await sleep(150);
    }
  }

  // ---- 購入確認画面: 全自動で「投票入力完了」を押したときだけ、点数・合計を照合して購入する ----
  function confirmPage() {
    let want = null;
    try { want = JSON.parse(sessionStorage.getItem(CONFIRM_KEY) || "null"); } catch { /* なし */ }
    if (!want || want.date !== today()) return;
    try { sessionStorage.removeItem(CONFIRM_KEY); } catch { /* なし */ }
    chrome.storage.local.get("teleboat_betPassword", async (v) => {
      const box = el("div", {id: "byosou-panel", className: "byosou-teleboat"});
      const say = (t) => box.append(el("div", {className: "byosou-msg", textContent: t}));
      document.body.append(box);
      try {
        const pw = await waitFor(() => [...document.querySelectorAll("input[type=password]")].find((n) => n.offsetParent !== null), "投票用パスワードの欄がありません");
        const text = document.body.innerText.replace(/\s/g, "");
        if (!text.includes(want.total.toLocaleString())) throw new Error(`合計 ${want.total.toLocaleString()}円 が画面に見当たりません`);
        if (want.total > 4000) throw new Error("上限を超えています");
        setValue(pw, v.teleboat_betPassword || "");
        await sleep(300);
        const buy = [...document.querySelectorAll("a, input[type=submit], input[type=button], button")]
          .find((n) => n.offsetParent !== null && /^投票(する|確定)$/.test((n.value || n.textContent || "").trim()));
        if (!buy) throw new Error("「投票する」ボタンが見つかりません");
        say(`${want.count}点・${want.total.toLocaleString()}円 を購入します`);
        buy.click();
      } catch (e) {
        say(`自動購入を止めました: ${e.message}`);
      }
    });
  }

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function msg(text) { return el("div", {className: "byosou-msg", textContent: text}); }
  function el(tag, props) { return Object.assign(document.createElement(tag), props || {}); }
})();

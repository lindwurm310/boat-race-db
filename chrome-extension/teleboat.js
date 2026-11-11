// テレボート (ib.mbrace.or.jp) の投票画面に、今日の予想の単勝買い目をベットリストへ入れるパネルを出す。
// 入れるのはベットリストまで。「投票入力完了」と投票用パスワード・購入は、必ず自分で行う。
(() => {
  if (!document.getElementById("betkati1") && !document.getElementById("boatraceTitle")) return; // 投票画面以外では出さない

  const STAKE = 100;      // 1点の金額 (円)
  const DAY_CAP = 4000;   // 1日の上限 (円)。購入限度額 (残高) が少ないときはそちらに合わせる
  const MARGIN_MIN = 3;   // 締切まで3分を切ったレースは入れない
  const DONE_KEY = "byosou-teleboat-done-"; // 今日ベットリストに入れた買い目 (二重に入れないため)

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
  load();

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
      show(plan(picks(res.body.rows || [])));
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
    parts.push(go, reset, log, el("div", {className: "byosou-help", textContent:
      "入れるのはベットリストまでです。中身と合計金額を確かめてから、自分で「投票入力完了」を押し、投票用パスワードを入れて購入してください。"}));
    body.replaceChildren(...parts);
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
    if (running) return;
    running = true; go.disabled = true; reload.disabled = true;
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
      `${ok}点 (${(ok * STAKE).toLocaleString()}円) をベットリストに入れました。右のベットリストを確かめて「投票入力完了」へ。`}));
    running = false; reload.disabled = false;
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

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function msg(text) { return el("div", {className: "byosou-msg", textContent: text}); }
  function el(tag, props) { return Object.assign(document.createElement(tag), props || {}); }
})();

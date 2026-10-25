// レースのページ (出走表・直前情報・オッズ・結果) の右下におすすめの買い目を出す。
(() => {
  const q = new URLSearchParams(location.search);
  const hd = q.get("hd"), jcd = q.get("jcd"), rno = q.get("rno");
  if (!hd || !jcd || !rno) return;

  const panel = el("div", {id: "byosou-panel"});
  const head = el("div", {className: "byosou-head"});
  const title = el("span", {textContent: "予想"});
  const reload = el("button", {textContent: "更新", title: "直前情報とオッズを取り直す"});
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
  load();

  function load() {
    body.replaceChildren(el("div", {className: "byosou-msg", textContent: "直前情報とオッズを取得して予想しています…"}));
    reload.disabled = true;
    chrome.runtime.sendMessage({type: "recommend", hd, jcd, rno}, (res) => {
      reload.disabled = false;
      if (!res || res.offline) {
        body.replaceChildren(el("div", {className: "byosou-msg",
          textContent: "パソコンの予想サーバーに繋がりません。デスクトップの「ボートレース 拡張機能用サーバー」を起動してから「更新」を押してください。"}));
      } else if (!res.ok) {
        body.replaceChildren(el("div", {className: "byosou-msg", textContent: res.body.error || "エラー"}));
      } else {
        show(res.body);
      }
    });
  }

  function show(r) {
    title.textContent = `予想 ${r.venue} ${r.rno}R`;
    const buys = r.buys.length
      ? `単勝 ${r.buys.join("・")} 号艇`
      : "見送り";
    const verdict = el("div", {className: "byosou-verdict" + (r.buys.length ? " byosou-buy" : ""),
      textContent: `おすすめ: ${buys}`});
    const note = el("div", {className: "byosou-note", textContent:
      (r.late ? "直前情報 (展示タイム) を使った予想" : "展示前: 番組表だけの予想") +
      `　風 ${r.wind_speed ?? "-"}m 波 ${r.wave_cm ?? "-"}cm`});
    const cols = [["艇", "艇"], ["選手", "選手"], ["級", "級"], ["1着率", "1着率"], ["この枠の1着率", "枠1着率"],
      ["展示", "展示"], ["展示ST", "ST"], ["勝率予想", "勝率予想"], ["オッズ", "オッズ"],
      ["期待値(実オッズ)", "期待値"], ["期待値(予想オッズ)", "期待値(予想)"], ["買い", "買い"]];
    const table = el("table");
    const tr = el("tr");
    cols.forEach(([, label]) => tr.append(el("th", {textContent: label})));
    table.append(tr);
    r.rows.forEach((row) => {
      const tr = el("tr", {className: row["買い"] ? "byosou-buyrow" : ""});
      cols.forEach(([key]) => {
        const v = row[key];
        const td = el("td", {textContent: v === null || v === undefined ? "" : String(v)});
        if (key === "艇") td.className = `byosou-lane byosou-lane${v}`;
        tr.append(td);
      });
      table.append(tr);
    });
    const top5 = el("div", {className: "byosou-note", textContent: `3連単の上位5点: ${r.top5.join("  ")}`});
    const help = el("div", {className: "byosou-help", textContent:
      `◎ = 勝率予想×オッズ (期待値) が予想オッズ・実オッズのどちらでも ${r.ev_min} 以上の艇。` +
      "表示した買い目は記録され、翌日の答え合わせで回収率を集計します。"});
    body.replaceChildren(verdict, note, table, top5, help);
  }

  function el(tag, props) {
    return Object.assign(document.createElement(tag), props || {});
  }
})();

// StyleBuddy front end: plain JS over the JSON API. The user id lives in localStorage.
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};
let vocab = { styles: {}, body_shapes: {} };
let userId = store.get("userId");

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    throw new Error(res.status === 429 ? "操作太频繁了，请稍后再试。" : detail || `请求失败 (${res.status})`);
  }
  return body;
}

// ---------- tabs ----------
function show(tab) {
  for (const id of ["find", "style", "trends", "profile"]) $("#" + id).hidden = id !== tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "style") loadStyle();
  if (tab === "trends") loadTrends();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));

// ---------- profile ----------
function chips(container, options, multi, selected = []) {
  container.innerHTML = "";
  for (const [id, label] of Object.entries(options)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip" + (selected.includes(id) ? " on" : ""); b.dataset.id = id; b.textContent = label;
    b.addEventListener("click", () => {
      if (!multi) container.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
      b.classList.toggle("on", multi ? !b.classList.contains("on") : true);
    });
    container.appendChild(b);
  }
}
const picked = (container) => [...container.querySelectorAll(".chip.on")].map((c) => c.dataset.id);

async function openProfile() {
  let p = {};
  if (userId) {
    try { p = await api(`/api/users/${userId}`); } catch { userId = null; }
  }
  const f = $("#profile-form");
  for (const k of ["nickname", "height_cm", "weight_kg", "age", "budget_per_item"]) if (p[k] != null) f.elements[k].value = p[k];
  chips($("#shape-options"), vocab.body_shapes, false, [p.body_shape || "unsure"]);
  chips($("#style-options"), vocab.styles, true, p.preferred_styles || []);
  $("#profile-title").textContent = userId ? "我的档案" : "先认识一下你";
  $("#profile-msg").textContent = p.fit_advice ? `穿衣建议：${p.fit_advice}` : "";
}

$("#profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    nickname: f.elements.nickname.value, height_cm: +f.elements.height_cm.value, weight_kg: +f.elements.weight_kg.value,
    age: +f.elements.age.value, budget_per_item: +f.elements.budget_per_item.value,
    body_shape: picked($("#shape-options"))[0] || "unsure", preferred_styles: picked($("#style-options")),
  };
  try {
    const saved = await api(userId ? `/api/users/${userId}` : "/api/users", {
      method: userId ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const isNew = !userId;
    userId = String(saved.id); store.set("userId", userId);
    $("#tabs").hidden = false;
    $("#profile-msg").textContent = `已保存。穿衣建议：${saved.fit_advice}`;
    if (isNew) show("find");
  } catch (err) { $("#profile-msg").textContent = err.message; }
});

// ---------- find look-alikes ----------
const drop = $("#drop");
["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]); });
$("#file").addEventListener("change", (e) => e.target.files[0] && handleFile(e.target.files[0]));

function setStatus(text, isError = false) {
  const s = $("#status"); s.hidden = !text; s.textContent = text || ""; s.classList.toggle("error", isError);
}

async function handleFile(file) {
  $("#preview").src = URL.createObjectURL(file); $("#preview").hidden = false; $("#drop-text").textContent = "换一张";
  $("#results").innerHTML = "";
  setStatus("上传中…");
  const form = new FormData(); form.append("user_id", userId); form.append("image", file);
  try {
    const look = await api("/api/looks", { method: "POST", body: form });
    pollLook(look.id, 0);
  } catch (err) { setStatus(err.message, true); }
}

async function pollLook(id, tries) {
  try {
    const look = await api(`/api/looks/${id}`);
    if (look.status === "done") { setStatus(""); renderLook(look.result); return; }
    if (look.status === "failed") { setStatus(look.error || "分析失败，请换一张图再试。", true); return; }
    setStatus(look.attempts > 1 ? `AI 有点忙，正在重试（第 ${look.attempts} 次）…` : "AI 正在识别单品、搜索平替…");
    if (tries < 90) setTimeout(() => pollLook(id, tries + 1), 1500);
    else setStatus("等待时间太长了，请稍后刷新页面查看。", true);
  } catch (err) { setStatus(err.message, true); }
}

function productCard(p, styleTags) {
  const img = p.image_url ? `<img src="${esc(p.image_url)}" alt="" loading="lazy" onerror="this.parentElement.textContent='${esc(p.product_type)}'">` : esc(p.product_type);
  const saving = p.saving_usd > 0 ? `<span class="saving">比原款约省 $${Math.round(p.saving_usd)}</span>` : "";
  return `<div class="card">
    <div class="img">${img}</div>
    <div class="body">
      <span class="name">${esc(p.name)}</span>
      <span class="muted">${esc(p.colour)}</span>
      <span class="price">$${p.price.toFixed(2)}</span> ${saving}
      <span class="reasons">${(p.reasons || []).map(esc).join(" · ")}</span>
      <button data-save="${esc(p.id)}" data-tags="${esc((styleTags || []).join(","))}">♡ 收藏</button>
    </div></div>`;
}

function renderLook(r) {
  const tags = (r.style_tags || []).map((t) => `<span class="tag">${esc(vocab.styles[t] || t)}</span>`).join(" ");
  let html = `<div class="vibe"><strong>${esc(r.vibe_zh)}</strong><div>${tags}</div></div>`;
  for (const s of r.sections) {
    const it = s.item;
    const orig = it.estimated_original_price_usd ? `<span class="muted">原款估价约 $${Math.round(it.estimated_original_price_usd)}</span>` : "";
    html += `<div class="item"><div class="item-head"><h3>${esc(it.colour)} ${esc(it.name)}</h3>${orig}</div>
      <div class="grid">${s.picks.map((p) => productCard(p, it.style_tags)).join("") || '<p class="muted">没有找到合适的平替。</p>'}</div></div>`;
  }
  $("#results").innerHTML = html;
}

async function onSaveClick(e) {
  const b = e.target.closest("button[data-save]");
  if (!b || b.classList.contains("saved")) return;
  try {
    await api(`/api/users/${userId}/saved`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: b.dataset.save, style_tags: b.dataset.tags ? b.dataset.tags.split(",") : [] }),
    });
    b.classList.add("saved"); b.textContent = "♥ 已收藏";
  } catch (err) { setStatus(err.message, true); }
}
$("#results").addEventListener("click", onSaveClick);

// ---------- style memory ----------
async function loadStyle() {
  const [s, p] = await Promise.all([api(`/api/users/${userId}/style`), api(`/api/users/${userId}`)]);
  $("#style-summary").textContent = s.summary_zh;
  const max = Math.max(...s.styles.map((x) => x.weight), 0.01);
  $("#style-bars").innerHTML = s.styles.map((x) =>
    `<div class="bar"><span>${esc(x.label)}</span><div style="width:${Math.round((x.weight / max) * 100)}%"></div></div>`).join("");
  $("#style-colours").innerHTML = s.colours.map(([c, n]) => `<span class="chip">${esc(c)} × ${n}</span>`).join("") || '<span class="muted">还没有记录</span>';
  $("#style-fit").textContent = `基于 ${s.signals} 条记录（上传和收藏）。身形建议：${p.fit_advice}`;
}

// ---------- trends ----------
async function loadTrends() {
  const t = await api("/api/trends");
  if (!t.trends.length) { $("#trends-meta").textContent = "趋势还在整理中，稍后再来看看。"; return; }
  const when = new Date(t.refreshed_at).toLocaleDateString("zh-CN");
  $("#trends-meta").textContent = t.origin === "seed" ? `示例趋势（离线数据），更新于 ${when}` : `AI 联网整理，更新于 ${when}`;
  $("#trend-list").innerHTML = t.trends.map((x) => `<div class="trend">
      <h3>${esc(x.label_zh)}</h3><p>${esc(x.description_zh)}</p>
      <div class="chips static">${x.keywords.map((k) => `<span class="chip">#${esc(k)}</span>`).join("")}</div>
      <div class="grid">${x.examples.map((p) => productCard(p, [x.style_id])).join("")}</div>
      <div class="sources">${(x.sources || []).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">来源 ${i + 1}</a>`).join("")}</div>
    </div>`).join("");
}
$("#trend-list").addEventListener("click", onSaveClick);

// ---------- boot ----------
(async function boot() {
  vocab = await api("/api/vocab");
  await openProfile();
  if (userId) { $("#tabs").hidden = false; show("find"); } else { show("profile"); }
})();

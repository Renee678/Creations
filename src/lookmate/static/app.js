// Lookmate front end: plain JS over the JSON API. The user id lives in localStorage.
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
    const fallback = { 413: "Those photos are too large. Try fewer or smaller photos.", 429: "Too many requests. Please try again in a moment." };
    throw new Error(detail || fallback[res.status] || `Request failed (${res.status})`);
  }
  return body;
}

// ---------- tabs ----------
function show(tab) {
  for (const id of ["find", "lookbook", "style", "trends", "profile"]) $("#" + id).hidden = id !== tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "style") loadStyle();
  if (tab === "trends") loadTrends();
  if (tab === "lookbook") loadLookbook();
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
  $("#profile-title").textContent = userId ? "My profile" : "Tell us about you";
  $("#profile-msg").textContent = p.fit_advice ? `Fit advice: ${p.fit_advice}` : "";
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
    $("#profile-msg").textContent = `Saved. Fit advice: ${saved.fit_advice}`;
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
  $("#preview").src = URL.createObjectURL(file); $("#preview").hidden = false;
  document.querySelectorAll("#drop .hint").forEach((el) => el.setAttribute("hidden", "")); $("#drop-text").textContent = "Try another photo";
  $("#results").innerHTML = "";
  setStatus("Uploading…");
  const form = new FormData(); form.append("user_id", userId); form.append("image", file);
  try {
    const look = await api("/api/looks", { method: "POST", body: form, headers: { "X-Access-Code": accessCode() } }).catch((err) => {
      if (err.message === "access code required") store.set("accessCode", "");
      throw err;
    });
    pollLook(look.id, 0);
  } catch (err) { setStatus(err.message, true); }
}

async function pollLook(id, tries) {
  try {
    const look = await api(`/api/looks/${id}`);
    if (look.status === "done") { setStatus(""); renderLook(look.result); return; }
    if (look.status === "failed") { setStatus(look.error || "Analysis failed. Please try another photo.", true); return; }
    setStatus(look.attempts > 1 ? `The AI is busy, retrying (attempt ${look.attempts})…` : "Spotting each piece and searching for dupes…");
    if (tries < 90) setTimeout(() => pollLook(id, tries + 1), 1500);
    else setStatus("This is taking too long. Refresh the page in a little while.", true);
  } catch (err) { setStatus(err.message, true); }
}

function productCard(p, styleTags) {
  const img = p.image_url ? `<img src="${esc(p.image_url)}" alt="" loading="lazy" onerror="this.parentElement.textContent='${esc(p.product_type)}'">` : esc(p.product_type);
  const saving = p.saving_usd > 0 ? `<span class="saving">Save about $${Math.round(p.saving_usd)}</span>` : "";
  const shop = p.shop_links
    ? `<div class="shop"><a class="gel" href="${esc(p.shop_links.shein)}" target="_blank" rel="noopener">SHEIN ↗</a><a class="gel" href="${esc(p.shop_links.asos)}" target="_blank" rel="noopener">ASOS ↗</a></div>`
    : "";
  return `<div class="card"><span class="tape"></span><span class="price">$${p.price.toFixed(2)}</span>
    <div class="img">${img}</div>
    <div class="body">
      <span class="name">${esc(p.name)}</span>
      <span class="meta">${esc(p.colour)}</span>${saving}
      <span class="reasons">${(p.reasons || []).map(esc).join(" · ")}</span>
      ${shop}
      <button class="save" data-save="${esc(p.id)}" data-tags="${esc((styleTags || []).join(","))}">♡ save to lookbook</button>
    </div></div>`;
}

function renderLook(r) {
  const tags = (r.style_tags || []).map((t) => `<span class="chip">${esc(vocab.styles[t] || t)}</span>`).join("");
  let html = `<div class="vibe"><span class="tape"></span><h2>the vibe</h2><p>${esc(r.vibe)}</p><div class="chips static">${tags}</div></div>`;
  for (const s of r.sections) {
    const it = s.item;
    const orig = it.estimated_original_price_usd ? `<span class="muted">Original about $${Math.round(it.estimated_original_price_usd)}</span>` : "";
    const count = s.picks.length ? `<span class="tag">${s.picks.length} dupes</span>` : "";
    html += `<div class="item"><div class="item-head"><h3>${esc(it.colour)} ${esc(it.name)}</h3>${count}${orig}</div>
      <div class="grid">${s.picks.map((p) => productCard(p, it.style_tags)).join("") || '<p class="muted">No good dupes found.</p>'}</div></div>`;
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
    b.classList.add("saved"); b.textContent = "♥ saved";
  } catch (err) { setStatus(err.message, true); }
}
$("#results").addEventListener("click", onSaveClick);

// ---------- style memory ----------
async function loadStyle() {
  const [s, p] = await Promise.all([api(`/api/users/${userId}/style`), api(`/api/users/${userId}`)]);
  $("#style-summary").textContent = s.summary;
  const max = Math.max(...s.styles.map((x) => x.weight), 0.01);
  $("#style-bars").innerHTML = s.styles.map((x) =>
    `<div class="bar"><span>${esc(x.label)}</span><div style="width:${Math.round((x.weight / max) * 100)}%"></div></div>`).join("");
  $("#style-colours").innerHTML = s.colours.map(([c, n]) => `<span class="chip">${esc(c)} × ${n}</span>`).join("") || '<span class="muted">Nothing yet</span>';
  $("#style-fit").textContent = `Based on ${s.signals} signals (uploads and saves). Fit advice: ${p.fit_advice}`;
}

// ---------- trends ----------
async function loadTrends() {
  const t = await api("/api/trends");
  if (!t.trends.length) { $("#trends-meta").textContent = "Trends are still being gathered. Check back soon."; return; }
  const when = new Date(t.refreshed_at).toLocaleDateString("en-US");
  $("#trends-meta").textContent = t.origin === "seed" ? `Sample trends (offline data), updated ${when}` : `Researched by AI on the web, updated ${when}`;
  $("#trend-list").innerHTML = t.trends.map((x) => `<div class="trend"><span class="tape"></span>
      <h3>${esc(x.label)}</h3><p>${esc(x.description)}</p>
      <div class="chips static">${x.keywords.map((k) => `<span class="chip">#${esc(k)}</span>`).join("")}</div>
      <div class="grid">${x.examples.map((p) => productCard(p, [x.style_id])).join("")}</div>
      <div class="sources">${(x.sources || []).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">Source ${i + 1}</a>`).join("")}</div>
    </div>`).join("");
}
$("#trend-list").addEventListener("click", onSaveClick);

// ---------- personal analysis + lookbook ----------
let lbMode = "seasons";
const meDrop = $("#me-drop");
["dragover", "dragenter"].forEach((ev) => meDrop.addEventListener(ev, (e) => { e.preventDefault(); meDrop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => meDrop.addEventListener(ev, () => meDrop.classList.remove("over")));
meDrop.addEventListener("drop", (e) => { e.preventDefault(); handlePhotos([...e.dataTransfer.files]); });
$("#me-files").addEventListener("change", (e) => handlePhotos([...e.target.files]));

function setMeStatus(text, isError = false) {
  const s = $("#me-status"); s.hidden = !text; s.textContent = text || ""; s.classList.toggle("error", isError);
}

function accessCode() {
  let code = store.get("accessCode") || "";
  if (vocab.access_code_required && !code) { code = prompt("Enter your access code") || ""; store.set("accessCode", code); }
  return code;
}

let mePhotos = [];  // the shrunk photos from this upload, to reuse the full-body one for try-on

async function handlePhotos(files) {
  files = files.slice(0, 3);
  if (!files.length) return;
  setMeStatus("Preparing your photos…");
  // Phone photos are 5-12 MB each; 1600px is plenty for colour analysis and keeps uploads small.
  mePhotos = await Promise.all(files.map((f) => shrinkPhoto(f, 1600)));
  $("#me-previews").innerHTML = mePhotos.map((f, i) =>
    `<figure class="me-photo"><img src="${URL.createObjectURL(f)}" alt=""><figcaption id="me-check-${i}"></figcaption></figure>`).join("");
  $("#me-previews").hidden = false; $("#me-drop-text").textContent = "Use different photos";
  setMeStatus("Uploading…");
  const form = new FormData();
  mePhotos.forEach((f, i) => form.append("photos", f, `photo-${i}.jpg`));
  try {
    const a = await api(`/api/users/${userId}/analyses`, { method: "POST", body: form, headers: { "X-Access-Code": accessCode() } })
      .catch((err) => { if (err.message === "access code required") store.set("accessCode", ""); throw err; });
    pollAnalysis(a.id, 0);
  } catch (err) { setMeStatus(err.message, true); }
}

async function pollAnalysis(id, tries) {
  try {
    const a = await api(`/api/analyses/${id}`);
    if (a.status === "done") { setMeStatus(""); loadLookbook(); return; }
    if (a.status === "failed") { setMeStatus(a.error || "Analysis failed. Please try other photos.", true); return; }
    setMeStatus(a.attempts > 1 ? `The AI is busy, retrying (attempt ${a.attempts})…` : "Reading your colouring, face shape and style…");
    if (tries < 90) setTimeout(() => pollAnalysis(id, tries + 1), 1500);
    else setMeStatus("This is taking too long. Refresh the page in a little while.", true);
  } catch (err) { setMeStatus(err.message, true); }
}

const swatch = (s) => `<span class="swatch"><i style="background:${esc(s.hex)}"></i>${esc(s.name)}</span>`;
const cap = (s) => String(s || "").replace(/^./, (c) => c.toUpperCase());

const FRAMING = { face: "Selfie", upper_body: "Waist up", full_body: "Full body", no_person: "No person found" };

/** Label each uploaded photo and keep the best full-body one for try-on. */
function applyPhotoChecks(checks) {
  if (!checks || !checks.length || checks.length !== mePhotos.length) return;
  checks.forEach((c, i) => {
    const el = $(`#me-check-${i}`);
    if (!el) return;
    const uses = [c.good_for_colour && "colours", c.good_for_tryon && "try-on"].filter(Boolean);
    el.className = uses.length ? "ok" : "warn";
    el.innerHTML = `<strong>${esc(FRAMING[c.framing] || c.framing)}</strong><span>${uses.length ? `✓ ${uses.join(" + ")}` : "✗ not usable"}</span><span>${esc(c.tip)}</span>`;
  });
  const best = checks.findIndex((c) => c.good_for_tryon);
  if (best >= 0) setTryonPhoto(mePhotos[best]);
  else setMeStatus("None of these photos works for try-on yet: add a full-body photo, standing and facing the camera.");
}

function renderAnalysis(a) {
  if (!a) { $("#me-analysis").innerHTML = ""; return; }
  applyPhotoChecks(a.photo_checks);
  const list = (xs) => `<ul>${(xs || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  $("#me-analysis").innerHTML = `<div class="analysis"><span class="tape"></span>
    <div class="season"><span class="muted">Your colour season</span><strong>${esc(cap(a.season_detail))}</strong>
      <span>${esc(cap(a.undertone))} undertone · ${esc(a.contrast)} contrast · ${esc(a.metals)} jewellery</span>
      <p class="muted">${esc(a.colouring_notes)}</p></div>
    <h3>Colours that light you up</h3><div class="swatches">${a.best_colours.map(swatch).join("")}</div>
    <h3>Keep away from your face</h3><div class="swatches">${a.avoid_colours.map(swatch).join("")}</div>
    <div class="beauty">
      <div><h3>Hair · ${esc(cap(a.face_shape))} face</h3><p class="muted">${esc(a.hair_now)}</p>${list(a.hair_suggestions)}</div>
      <div><h3>Makeup</h3>${list(a.makeup_suggestions)}</div>
    </div>
    ${a.silhouette_notes ? `<h3>Silhouette</h3><p>${esc(a.silhouette_notes)}</p>` : ""}
    <p class="muted small">${esc(a.caveats)}</p></div>`;
}

const WEARABLE = new Set(["top", "bottom", "dress", "outerwear"]);  // what the try-on model can render
const lbOutfits = [];

function outfitCard(o) {
  const n = lbOutfits.push(o) - 1;
  const trend = o.trend ? `<span class="tag">On trend: ${esc(o.trend)}</span>` : "";
  const canTry = o.pieces.some((p) => WEARABLE.has(p.category));
  const tryBtn = canTry ? `<button type="button" class="gel primary tryon-btn" data-outfit="${n}">✨ Try it on me</button>` : "";
  return `<div class="outfit"><div class="item-head"><h3>${esc(o.title)}</h3>${trend}<span class="muted">$${o.total_price.toFixed(2)} total</span>${tryBtn}</div>
    <div class="tryon" id="tryon-${n}" hidden></div>
    <div class="grid">${o.pieces.map((p) => productCard(p, [o.style_id])).join("")}</div></div>`;
}

// ---------- virtual try-on ----------
// The try-on photo stays on this device (localStorage); it's sent with each try-on and never kept by Lookmate.
let tryonPhoto = null;
let pendingOutfit = null;

async function setTryonPhoto(blob) {
  tryonPhoto = await shrinkPhoto(blob, 1024);
  const reader = new FileReader();
  reader.onload = () => store.set("tryonPhoto", reader.result);
  reader.readAsDataURL(tryonPhoto);
}

function savedTryonPhoto() {
  const url = store.get("tryonPhoto");
  if (!url || !url.startsWith("data:image/")) return null;
  const [head, b64] = url.split(",");
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return new Blob([bytes], { type: head.slice(5, head.indexOf(";")) });
}

/** Scale a photo down to at most `max` px on its longest side: faster upload, same result. */
async function shrinkPhoto(file, max = 1024) {
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const canvas = Object.assign(document.createElement("canvas"), { width: Math.round(bmp.width * scale), height: Math.round(bmp.height * scale) });
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return await new Promise((ok) => canvas.toBlob((b) => ok(b || file), "image/jpeg", 0.9));
  } catch { return file; }
}

$("#tryon-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  await setTryonPhoto(file);
  if (pendingOutfit !== null) startTryOn(pendingOutfit);
});

function tryonBox(n, html) { const box = $(`#tryon-${n}`); box.hidden = false; box.innerHTML = html; return box; }

function startTryOn(n) {
  pendingOutfit = n;
  tryonPhoto = tryonPhoto || savedTryonPhoto();
  if (!tryonPhoto) {
    tryonBox(n, `<p class="muted">None of your photos is full-body yet. Pick one: standing, facing the camera, head to knees.</p>`);
    $("#tryon-file").click();
    return;
  }
  pendingOutfit = null;
  const o = lbOutfits[n];
  tryonBox(n, `<div class="tryon-wait"><span class="spinner"></span> Dressing you in this outfit… this takes about a minute.</div>`);
  const form = new FormData();
  form.append("photo", tryonPhoto, "me.jpg");
  form.append("product_ids", o.pieces.map((p) => p.id).join(","));
  api(`/api/users/${userId}/tryons`, { method: "POST", body: form, headers: { "X-Access-Code": accessCode() } })
    .then((t) => pollTryOn(n, t.id, 0))
    .catch((err) => tryonBox(n, `<p class="status error">${esc(err.message)}</p>`));
}

async function pollTryOn(n, id, tries) {
  try {
    const t = await api(`/api/tryons/${id}`);
    if (t.status === "done") return renderTryOn(n, t);
    if (t.status === "failed") return tryonBox(n, `<p class="status error">${esc(t.error || "Try-on failed. Try another photo?")}</p>`);
    if (t.attempts > 1) tryonBox(n, `<div class="tryon-wait"><span class="spinner"></span> The try-on model is busy, retrying (attempt ${t.attempts})…</div>`);
    if (tries < 150) setTimeout(() => pollTryOn(n, id, tries + 1), 2000);
    else tryonBox(n, `<p class="status error">This is taking too long. Try again in a little while.</p>`);
  } catch (err) { tryonBox(n, `<p class="status error">${esc(err.message)}</p>`); }
}

function renderTryOn(n, t) {
  const o = lbOutfits[n];
  const rendered = new Set(t.result.rendered_ids || []);
  // Pieces the model didn't draw (shoes, bags, a second top layer) are pinned beside the photo, scrapbook style.
  const pinned = o.pieces.filter((p) => !rendered.has(p.id) && p.image_url);
  const note = t.result.rendered
    ? "Rendered with IDM-VTON. Colours and fit are an AI impression, not a promise."
    : "Collage preview: add a REPLICATE_API_TOKEN to .env to render the outfit on you.";
  tryonBox(n, `<div class="tryon-board">
      <figure class="tryon-shot"><span class="tape"></span><img src="${esc(t.image_url)}" alt="You wearing this outfit"><figcaption>${t.result.rendered ? "you, in this look ♡" : "you + this look ♡"}</figcaption></figure>
      <div class="tryon-pins">${pinned.map((p, i) => `<figure class="pin" style="--r:${(i % 2 ? 1 : -1) * (2 + i % 3)}deg"><img src="${esc(p.image_url)}" alt=""><figcaption>${esc(p.product_type)}</figcaption></figure>`).join("")}</div>
    </div>
    <p class="muted small">${note} Your photo was deleted after rendering.
      <button type="button" class="linklike" data-tryon-new="${n}">Use another photo</button> ·
      <button type="button" class="linklike" data-tryon-delete="${esc(t.id)}" data-outfit="${n}">Delete this picture</button></p>`);
}

$("#lb-sections").addEventListener("click", async (e) => {
  const tryBtn = e.target.closest(".tryon-btn");
  if (tryBtn) return startTryOn(Number(tryBtn.dataset.outfit));
  const again = e.target.closest("[data-tryon-new]");
  if (again) { tryonPhoto = null; store.set("tryonPhoto", ""); return startTryOn(Number(again.dataset.tryonNew)); }
  const del = e.target.closest("[data-tryon-delete]");
  if (del) {
    await api(`/api/tryons/${del.dataset.tryonDelete}`, { method: "DELETE" }).catch(() => {});
    const box = $(`#tryon-${del.dataset.outfit}`); box.hidden = true; box.innerHTML = "";
  }
});

async function loadLookbook() {
  lbOutfits.length = 0;
  try {
    const lb = await api(`/api/users/${userId}/lookbook?mode=${lbMode}`);
    renderAnalysis(lb.analysis);
    $("#lb-note").textContent = lb.personal
      ? `Built from your palette and your styles: ${lb.styles.map((s) => s.label).join(", ")}.`
      : "Upload a photo for outfits in your own colours. For now these use neutral colours and your styles.";
    $("#lb-sections").innerHTML = lb.sections.map((s) =>
      `<div class="lb-section"><h2>${esc(s.title)}</h2>${s.outfits.map(outfitCard).join("") || '<p class="muted">Nothing found for this one yet.</p>'}</div>`).join("");
  } catch (err) { setMeStatus(err.message, true); }
}

$("#lb-mode").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mode]");
  if (!b) return;
  lbMode = b.dataset.mode;
  $("#lb-mode").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
  loadLookbook();
});
$("#lb-sections").addEventListener("click", onSaveClick);

// ---------- boot ----------
(async function boot() {
  vocab = await api("/api/vocab");
  await openProfile();
  if (userId) { $("#tabs").hidden = false; show("find"); } else { show("profile"); }
})();

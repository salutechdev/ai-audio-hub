// audio-ai-hub — frontend logic.
// The initial card grid is server-rendered by format_input.py (SSR) for SEO
// and no-JS readers. This script enhances it with search, filter, sort,
// and URL deep-linking. It re-renders only when the user interacts.

const state = {
  all: [],           // array of card objects (from data.json)
  stars: {},         // abbrev -> star count (from stars.json)
  query: "",
  activeCategories: new Set(),
  sort: "time-desc",
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function boot() {
  try {
    const [dataRes, starsRes] = await Promise.all([
      fetch("data.json", { cache: "no-cache" }),
      fetch("stars.json", { cache: "no-cache" }).catch(() => null),
    ]);
    if (!dataRes.ok) throw new Error(`Failed to fetch data.json (HTTP ${dataRes.status})`);
    state.all = await dataRes.json();
    if (starsRes && starsRes.ok) {
      const sj = await starsRes.json();
      state.stars = Object.fromEntries(Object.entries(sj).map(([k, v]) => [k, v.stars || 0]));
    }
  } catch (err) {
    // SSR content is already on the page; just disable the interactive controls quietly.
    console.warn("audio-ai-hub: failed to hydrate from JSON, falling back to SSR-only:", err);
    return;
  }
  hydrateFromURL();
  bindControls();
  // Initial render to reflect any URL params; if nothing in URL, this is byte-equivalent to SSR.
  render();
}

function hydrateFromURL() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const q = params.get("q") || "";
  const cats = (params.get("cat") || "").split(",").filter(Boolean);
  const sort = params.get("sort") || "time-desc";
  state.query = q;
  state.activeCategories = new Set(cats);
  state.sort = sort;
  if ($("#search")) $("#search").value = q;
  if ($("#sort")) $("#sort").value = sort;
  $$("#category-filter .chip").forEach((el) => {
    el.setAttribute("aria-pressed", state.activeCategories.has(el.dataset.category) ? "true" : "false");
  });
}

function syncURL() {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.activeCategories.size) params.set("cat", Array.from(state.activeCategories).join(","));
  if (state.sort && state.sort !== "time-desc") params.set("sort", state.sort);
  const s = params.toString();
  history.replaceState(null, "", s ? "#" + s : location.pathname + location.search);
}

function bindControls() {
  $("#search").addEventListener("input", (e) => {
    state.query = e.target.value.trim().toLowerCase();
    syncURL();
    render();
  });
  $("#sort").addEventListener("change", (e) => {
    state.sort = e.target.value;
    syncURL();
    render();
  });
  $$("#category-filter .chip").forEach((el) => {
    el.addEventListener("click", () => {
      const cat = el.dataset.category;
      if (state.activeCategories.has(cat)) {
        state.activeCategories.delete(cat);
        el.setAttribute("aria-pressed", "false");
      } else {
        state.activeCategories.add(cat);
        el.setAttribute("aria-pressed", "true");
      }
      syncURL();
      render();
    });
  });
}

function filtered() {
  const q = state.query.toLowerCase();
  return state.all.filter((c) => {
    if (state.activeCategories.size && !state.activeCategories.has(c.Category)) return false;
    if (!q) return true;
    const hay = [c.Abbreviation, c.Title, c.Author, c.Affiliation, c.Type, c.Description]
      .filter(Boolean).join(" ").toLowerCase();
    return hay.includes(q);
  });
}

function sorted(rows) {
  const xs = rows.slice();
  if (state.sort === "time-asc") xs.sort((a, b) => a.Time.localeCompare(b.Time));
  else if (state.sort === "abbrev-asc") xs.sort((a, b) => a.Abbreviation.localeCompare(b.Abbreviation));
  else if (state.sort === "stars-desc") xs.sort((a, b) => (state.stars[b.Abbreviation] || 0) - (state.stars[a.Abbreviation] || 0));
  else xs.sort((a, b) => b.Time.localeCompare(a.Time));
  return xs;
}

function render() {
  const rows = sorted(filtered());
  $("#result-count").textContent = `${rows.length} of ${state.all.length}`;
  if (!rows.length) {
    $("#grid").innerHTML = "";
    $("#empty-state").hidden = false;
    return;
  }
  $("#empty-state").hidden = true;
  $("#grid").innerHTML = rows.map(cardHTML).join("");
}

function cardHTML(c) {
  const stars = state.stars[c.Abbreviation] || 0;
  const catSlug = slug(c.Category);
  const links = [
    c.Paper_Link && `<a href="${esc(c.Paper_Link)}" target="_blank" rel="noopener">Paper</a>`,
    c.GitHub_Link && `<a href="${esc(c.GitHub_Link)}" target="_blank" rel="noopener">Code</a>`,
    c.HF_Link && `<a href="${esc(c.HF_Link)}" target="_blank" rel="noopener">🤗 HF</a>`,
    c.Demo_Link && `<a href="${esc(c.Demo_Link)}" target="_blank" rel="noopener">Demo</a>`,
    c.Other_Link && `<a href="${esc(c.Other_Link)}" target="_blank" rel="noopener">Site</a>`,
  ].filter(Boolean).join("");
  const tags = [
    `<span class="tag tag-cat" data-cat="${catSlug}">${esc(c.Category)}</span>`,
    c.Type && c.Type !== c.Category && `<span class="tag">${esc(c.Type)}</span>`,
    c.Audio_Input === "Yes" && `<span class="tag">Audio In</span>`,
    c.Audio_Output === "Yes" && `<span class="tag">Audio Out</span>`,
    c.Language && c.Language !== "-" && `<span class="tag">${esc(c.Language)}</span>`,
  ].filter(Boolean).join("");
  const starBadge = stars > 0 ? `<span class="card-stars" title="GitHub stars">★ ${stars.toLocaleString()}</span>` : "";
  return `<article class="card" data-abbrev="${esc(c.Abbreviation || "")}" data-cat="${catSlug}" data-stars="${stars}">
    <header class="card-head"><span class="card-abbrev">${esc(c.Abbreviation || "")}</span><span class="card-time">${esc(c.Time || "")}</span></header>
    <p class="card-title">${esc(c.Title || "")}</p>
    ${c.Affiliation ? `<p class="card-affil">${esc(c.Affiliation)}</p>` : ""}
    <div class="tags">${tags}${starBadge}</div>
    ${c.Description ? `<p class="card-desc">${esc(c.Description)}</p>` : ""}
    <footer class="card-links">${links}</footer>
  </article>`;
}

function slug(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
function esc(s) {
  if (s == null) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

boot();

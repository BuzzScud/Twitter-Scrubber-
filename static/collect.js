/* Bookmarklet source. webapp.py fills in APP with this server's URL and wraps it as javascript:.
   Runs on x.com: a profile, its Media tab, or a search like "from:user filter:images since:… until:…".
   X keeps only the tweets on screen in the DOM, so it harvests links on every scroll step. */
(function () {
  var APP = "__APP__";
  if (window.__xpb) {
    if (window.__xpb.href === location.href) { window.__xpb.panel.style.display = "block"; return; }
    window.__xpb.teardown();
  }
  if (!/(^|\.)(x|twitter)\.com$/.test(location.hostname)) {
    alert("Open an X profile, its Media tab, or an X search first, then click this again.");
    return;
  }

  var reserved = { search: 1, home: 1, i: 1, explore: 1, notifications: 1, messages: 1, settings: 1, compose: 1, hashtag: 1 };
  var handle = "";
  var from = (new URLSearchParams(location.search).get("q") || "").match(/from:@?(\w{1,15})/i);
  var first = location.pathname.split("/")[1] || "";
  if (from) handle = from[1];
  else if (first && !reserved[first.toLowerCase()]) handle = first;

  var ids = new Map();
  var timer = null, idle = 0, lastHeight = 0;

  function harvest() {
    var links = document.querySelectorAll('a[href*="/status/"]');
    var added = 0;
    for (var i = 0; i < links.length; i++) {
      var m = (links[i].getAttribute("href") || "").match(/^\/(\w{1,15})\/status\/(\d{8,25})/);
      if (!m) continue;
      if (handle && m[1].toLowerCase() !== handle.toLowerCase()) continue;
      if (!ids.has(m[2])) { ids.set(m[2], 1); added++; }
    }
    return added;
  }

  function el(tag, css, text) {
    var e = document.createElement(tag);
    e.style.cssText = css;
    if (text) e.textContent = text;
    return e;
  }
  var btnCss = "font:600 13px -apple-system,Helvetica,Arial,sans-serif;padding:7px 12px;border-radius:999px;border:1px solid #2f3336;cursor:pointer;margin:0 6px 0 0;";

  var panel = el("div", "position:fixed;right:18px;bottom:18px;z-index:2147483647;width:300px;background:#16181c;color:#e7e9ea;border:1px solid #2f3336;border-radius:14px;padding:14px;box-shadow:0 10px 30px rgba(0,0,0,.5);font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif;");
  var title = el("div", "font-weight:700;margin-bottom:2px;", "x-photo-book");
  var who = el("div", "color:#8b98a5;font-size:12px;margin-bottom:10px;", handle ? "Collecting posts by @" + handle : "Collecting every post link on this page");
  var count = el("div", "font-size:26px;font-weight:700;", "0");
  var status = el("div", "color:#8b98a5;font-size:12px;margin:2px 0 12px;min-height:16px;", "");
  var row = el("div", "display:flex;flex-wrap:wrap;gap:6px 0;");
  var scrollBtn = el("button", btnCss + "background:#e7e9ea;color:#000;border-color:#e7e9ea;", "Auto-scroll");
  var sendBtn = el("button", btnCss + "background:#1d9bf0;color:#fff;border-color:#1d9bf0;", "Send to app");
  var copyBtn = el("button", btnCss + "background:transparent;color:#e7e9ea;", "Copy");
  var close = el("button", "position:absolute;top:8px;right:10px;background:none;border:0;color:#8b98a5;font-size:18px;cursor:pointer;", "×");
  row.appendChild(scrollBtn); row.appendChild(sendBtn); row.appendChild(copyBtn);
  panel.appendChild(close); panel.appendChild(title); panel.appendChild(who);
  panel.appendChild(count); panel.appendChild(status); panel.appendChild(row);
  document.body.appendChild(panel);

  function render(msg) {
    count.textContent = ids.size + (ids.size === 1 ? " post" : " posts");
    if (msg !== undefined) status.textContent = msg;
  }

  function stop(msg) {
    if (timer) clearInterval(timer);
    timer = null;
    scrollBtn.textContent = "Auto-scroll";
    render(msg);
  }

  function tick() {
    var added = harvest();
    var height = document.documentElement.scrollHeight;
    var atBottom = window.innerHeight + window.scrollY >= height - 8;
    idle = (added === 0 && atBottom && height === lastHeight) ? idle + 1 : 0;
    lastHeight = height;
    if (idle >= 10) { stop("Reached the end (or X stopped loading). Scroll a little and Auto-scroll again to keep going."); return; }
    window.scrollBy(0, Math.round(window.innerHeight * 0.8));
    render("Scrolling… click again to pause.");
  }

  function urls() {
    return Array.from(ids.keys()).map(function (id) {
      return "https://x.com/" + (handle || "i") + "/status/" + id;
    });
  }

  scrollBtn.onclick = function () {
    if (timer) { stop("Paused."); return; }
    idle = 0;
    scrollBtn.textContent = "Pause";
    tick();
    timer = setInterval(tick, 900);
  };

  sendBtn.onclick = function () {
    harvest();
    if (!ids.size) { render("No post links found yet. Scroll first."); return; }
    var href = APP + "#handle=" + encodeURIComponent(handle) + "&ids=" + Array.from(ids.keys()).join(",");
    if (href.length > 60000) { stop("Too many posts for one link. Use Copy and paste them into the app."); return; }
    stop("Opened x-photo-book in a new tab.");
    var a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  copyBtn.onclick = function () {
    harvest();
    var text = urls().join("\n");
    function fallback() {
      var t = document.createElement("textarea");
      t.value = text;
      document.body.appendChild(t);
      t.select();
      document.execCommand("copy");
      t.remove();
      render("Copied " + ids.size + " URLs.");
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { render("Copied " + ids.size + " URLs."); }, fallback);
    } else {
      fallback();
    }
  };

  close.onclick = function () { stop(""); panel.style.display = "none"; };

  var queued = false;
  var observer = new MutationObserver(function () {
    if (queued) return;
    queued = true;
    setTimeout(function () { queued = false; if (harvest()) render(); }, 250);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  window.__xpb = {
    panel: panel,
    href: location.href,
    teardown: function () { stop(""); observer.disconnect(); panel.remove(); window.__xpb = null; }
  };
  harvest();
  render("Click Auto-scroll, or scroll yourself. Links are picked up as they load.");
})();

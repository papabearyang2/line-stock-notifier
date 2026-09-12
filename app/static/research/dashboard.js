(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var VALID_WINDOWS = [1, 5, 20, 60];
  var VALID_NEWS_DAYS = [7, 30, 90];
  var state = {
    view: "industry",
    window: 20,
    weight: "market_cap",
    group: null,
    stock: null,
    newsDays: 30,
    overview: null,
    rows: [],
    rowsAreGroups: true,
    sortKey: "turnover_share_change_pp",
    sortDirection: "desc",
    request: null
  };

  var numberFormatter = new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: 2
  });
  var compactFormatter = new Intl.NumberFormat("zh-TW", {
    notation: "compact",
    maximumFractionDigits: 2
  });
  var dateTimeFormatter = new Intl.DateTimeFormat("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });

  function byId(id) {
    return document.getElementById(id);
  }

  function make(tagName, className, textValue) {
    var element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (textValue !== undefined && textValue !== null) {
      element.textContent = String(textValue);
    }
    return element;
  }

  function makeSvg(tagName) {
    return document.createElementNS(SVG_NS, tagName);
  }

  function announce(message) {
    byId("liveRegion").textContent = message;
  }

  function isMissing(value) {
    return value === null || value === undefined || value === "" ||
      (typeof value === "number" && !Number.isFinite(value));
  }

  function asNumber(value) {
    if (typeof value === "number") {
      return Number.isFinite(value) ? value : null;
    }
    if (typeof value === "string" && value.trim() !== "") {
      var parsed = Number(value.replace(/,/g, "").replace(/%$/, ""));
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function pick(object, keys) {
    if (!object || typeof object !== "object") {
      return null;
    }
    for (var i = 0; i < keys.length; i += 1) {
      if (!isMissing(object[keys[i]])) {
        return object[keys[i]];
      }
    }
    return null;
  }

  function firstObject() {
    for (var i = 0; i < arguments.length; i += 1) {
      var candidate = arguments[i];
      if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
        return candidate;
      }
    }
    return {};
  }

  function firstArray() {
    for (var i = 0; i < arguments.length; i += 1) {
      if (Array.isArray(arguments[i])) {
        return arguments[i];
      }
    }
    return [];
  }

  function displayText(value) {
    return isMissing(value) ? "尚未取得" : String(value);
  }

  function displayMetaValue(value) {
    if (isMissing(value)) {
      return "";
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      var start = pick(value, ["start", "from"]);
      var end = pick(value, ["end", "to"]);
      if (!isMissing(start) || !isMissing(end)) {
        return [start, end].filter(function (item) { return !isMissing(item); }).join(" → ");
      }
      return Object.keys(value).map(function (key) {
        return key + "=" + value[key];
      }).join("、");
    }
    return String(value);
  }

  function formatNumber(value, digits) {
    var parsed = asNumber(value);
    if (parsed === null) {
      return "尚未取得";
    }
    return new Intl.NumberFormat("zh-TW", {
      minimumFractionDigits: 0,
      maximumFractionDigits: digits === undefined ? 2 : digits
    }).format(parsed);
  }

  function formatSigned(value, suffix, digits) {
    var parsed = asNumber(value);
    if (parsed === null) {
      return "尚未取得";
    }
    var sign = parsed > 0 ? "+" : "";
    return sign + formatNumber(parsed, digits === undefined ? 2 : digits) + (suffix || "");
  }

  function formatPercent(value) {
    return formatSigned(value, "%", 2);
  }

  function formatPp(value) {
    return formatSigned(value, " 個百分點", 2);
  }

  function formatCompactMoney(value) {
    var parsed = asNumber(value);
    if (parsed === null) {
      return "尚未取得";
    }
    var sign = parsed > 0 ? "+" : parsed < 0 ? "−" : "";
    return sign + "NT$ " + compactFormatter.format(Math.abs(parsed));
  }

  function formatTurnover(value) {
    var parsed = asNumber(value);
    if (parsed === null) {
      return "尚未取得";
    }
    return "NT$ " + compactFormatter.format(parsed);
  }

  function formatDateTime(value) {
    if (isMissing(value)) {
      return "尚未取得";
    }
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return dateTimeFormatter.format(date);
  }

  function toneClass(value) {
    var parsed = asNumber(value);
    if (parsed === null) {
      return "missing";
    }
    if (parsed > 0.049) {
      return "positive";
    }
    if (parsed < -0.049) {
      return "negative";
    }
    return "neutral";
  }

  function directionSymbol(value) {
    var parsed = asNumber(value);
    if (parsed === null || Math.abs(parsed) < 0.05) {
      return "≈";
    }
    return parsed > 0 ? "↑" : "↓";
  }

  function safeUrl(value) {
    if (typeof value !== "string" || !value.trim()) {
      return null;
    }
    try {
      var url = new URL(value, window.location.origin);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
    } catch (error) {
      return null;
    }
  }

  function flattenTags(tags) {
    if (Array.isArray(tags)) {
      return tags.map(function (tag) {
        return typeof tag === "object" ? displayText(pick(tag, ["name", "label", "value"])) : String(tag);
      }).filter(function (tag) { return tag && tag !== "尚未取得"; });
    }
    if (tags && typeof tags === "object") {
      return Object.keys(tags).reduce(function (all, key) {
        var values = Array.isArray(tags[key]) ? tags[key] : [tags[key]];
        values.forEach(function (tag) {
          var value = typeof tag === "object" ? pick(tag, ["name", "label", "value"]) : tag;
          if (!isMissing(value)) {
            all.push(String(value));
          }
        });
        return all;
      }, []);
    }
    return isMissing(tags) ? [] : [String(tags)];
  }

  function appendMissing(container, message) {
    var empty = make("div", "section-empty");
    empty.appendChild(make("strong", null, "尚未取得"));
    empty.appendChild(make("span", null, message || "目前沒有足夠資料可供呈現。"));
    container.replaceChildren(empty);
  }

  function setStatus(meta, loading) {
    var status = loading ? "loading" : String(pick(meta, ["status"]) || "unavailable").toLowerCase();
    var dot = byId("statusDot");
    dot.className = "status-dot is-" + (status === "fresh" || status === "ok" ? "fresh" :
      status === "stale" || status === "partial" ? "stale" :
      status === "loading" ? "loading" : "unavailable");

    var titles = {
      fresh: "資料已更新",
      ok: "資料已更新",
      stale: "目前顯示最近一次成功資料",
      partial: "部分資料尚未取得",
      unavailable: "資料暫時無法取得",
      loading: "正在讀取資料"
    };
    byId("statusText").textContent = titles[status] || "資料狀態待確認";
    byId("statusDetail").textContent = loading ? "連線至研究資料庫中…" :
      displayText(pick(meta, ["coverage_note", "message", "status_note"]));
    byId("asOf").textContent = loading ? "讀取中" : displayText(pick(meta, ["as_of", "data_date"]));
    byId("updatedAt").textContent = loading ? "讀取中" : formatDateTime(pick(meta, ["updated_at", "last_updated"]));
  }

  function showLogin() {
    if (state.request) {
      state.request.abort();
    }
    byId("appPanel").hidden = true;
    byId("loginPanel").hidden = false;
    window.setTimeout(function () { byId("password").focus(); }, 0);
  }

  function showFatal(message) {
    byId("fatalErrorText").textContent = message || "請稍後再試。";
    byId("fatalError").hidden = false;
    byId("overviewPage").hidden = true;
    byId("stockPage").hidden = true;
    setStatus({ status: "unavailable", message: message }, false);
    announce("資料載入失敗：" + (message || "請稍後再試"));
  }

  function clearFatal() {
    byId("fatalError").hidden = true;
  }

  async function fetchJson(url) {
    if (state.request) {
      state.request.abort();
    }
    state.request = new AbortController();
    var response;
    try {
      response = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: state.request.signal
      });
    } catch (error) {
      if (error.name === "AbortError") {
        throw error;
      }
      throw new Error("無法連線至資料服務，請檢查網路後重試。");
    }
    if (response.status === 401 || response.status === 403) {
      showLogin();
      var authError = new Error("需要登入");
      authError.name = "AuthRequired";
      throw authError;
    }
    var body = null;
    try {
      body = await response.json();
    } catch (error) {
      body = null;
    }
    if (!response.ok) {
      throw new Error(displayText(body && pick(body, ["detail", "message"])));
    }
    if (!body || typeof body !== "object") {
      throw new Error("資料格式不完整，請稍後再試。");
    }
    return body;
  }

  function parseStateFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var view = params.get("view");
    var period = Number(params.get("window"));
    var weight = params.get("weight");
    var newsDays = Number(params.get("news_days"));
    state.view = view === "theme" ? "theme" : "industry";
    state.window = VALID_WINDOWS.indexOf(period) >= 0 ? period : 20;
    state.weight = weight === "equal" ? "equal" : "market_cap";
    state.group = params.get("group") || null;
    state.stock = params.get("stock") || null;
    state.newsDays = VALID_NEWS_DAYS.indexOf(newsDays) >= 0 ? newsDays : 30;
  }

  function updateUrl(push) {
    var params = new URLSearchParams();
    params.set("view", state.view);
    params.set("window", String(state.window));
    params.set("weight", state.weight);
    if (state.group) {
      params.set("group", state.group);
    }
    if (state.stock) {
      params.set("stock", state.stock);
      params.set("news_days", String(state.newsDays));
    }
    var next = window.location.pathname + "?" + params.toString();
    window.history[push ? "pushState" : "replaceState"]({}, "", next);
  }

  function updateControlStates() {
    document.querySelectorAll("[data-control]").forEach(function (button) {
      var control = button.getAttribute("data-control");
      var value = button.getAttribute("data-value");
      var active = control === "view" ? value === state.view :
        control === "window" ? Number(value) === state.window :
        control === "weight" ? value === state.weight :
        control === "news" ? Number(value) === state.newsDays : false;
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    byId("themeWarning").hidden = state.view !== "theme";
  }

  function rowName(row) {
    return displayText(pick(row, ["name", "stock_name", "group_name", "key", "code"]));
  }

  function rowKey(row) {
    return pick(row, ["key", "group", "group_key", "code"]);
  }

  function relativeValue(row) {
    return pick(row, ["relative_strength_pp", "relative_return_pp"]);
  }

  function rawReturnValue(row) {
    return pick(row, ["raw_return_pct", "return_pct", "total_return_pct"]);
  }

  function turnoverShareValue(row) {
    return pick(row, ["turnover_share_pct", "turnover_share_percent"]);
  }

  function turnoverChangeValue(row) {
    return pick(row, ["turnover_share_change_pp", "turnover_share_change"]);
  }

  function institutionalValue(row) {
    return pick(row, ["institutional_estimated_amount", "institutional_net_estimated_amount"]);
  }

  function applyMeta(meta) {
    var safeMeta = firstObject(meta);
    setStatus(safeMeta, false);
    var method = pick(safeMeta, ["return_method", "method_note"]);
    byId("returnMethod").textContent = method || "總報酬相對強弱＝標的總報酬－加權報酬指數總報酬；群組權重只使用報酬發生前可得資料。";
    var coverage = pick(safeMeta, ["coverage_note"]);
    byId("coverageNote").hidden = isMissing(coverage);
    byId("coverageNote").textContent = displayText(coverage);
    renderSources(byId("overviewSources"), firstArray(safeMeta.sources));
    byId("sourceCount").textContent = safeMeta.sources && safeMeta.sources.length ?
      "（" + safeMeta.sources.length + "）" : "（尚未取得）";
  }

  async function loadOverview() {
    clearFatal();
    byId("overviewPage").hidden = false;
    byId("stockPage").hidden = true;
    byId("heatmapFrame").setAttribute("aria-busy", "true");
    setStatus({}, true);
    updateControlStates();
    var params = new URLSearchParams({
      view: state.view,
      window: String(state.window),
      weight: state.weight
    });
    if (state.group) {
      params.set("group", state.group);
    }
    try {
      var payload = await fetchJson("/api/research/overview?" + params.toString());
      state.overview = payload;
      applyMeta(payload.meta);
      var groups = firstArray(payload.groups);
      var stocks = firstArray(payload.stocks);
      state.rowsAreGroups = !state.group && groups.length > 0;
      state.rows = state.rowsAreGroups ? groups : stocks.length ? stocks : groups;
      renderOverview(payload);
      byId("heatmapFrame").setAttribute("aria-busy", "false");
      announce("已載入 " + state.rows.length + " 筆資料");
    } catch (error) {
      if (error.name !== "AbortError" && error.name !== "AuthRequired") {
        showFatal(error.message);
      }
    }
  }

  function renderOverview(payload) {
    var groupLabel = pick(payload.meta, ["group_name", "selected_group_name"]);
    if (!groupLabel && state.group) {
      var candidates = firstArray(payload.groups);
      var match = candidates.find(function (group) { return String(rowKey(group)) === String(state.group); });
      groupLabel = match ? rowName(match) : state.group;
    }
    byId("drillNav").hidden = !state.group;
    byId("groupName").textContent = state.group ? displayText(groupLabel) : "";
    var scopeName = state.group ? displayText(groupLabel) + "個股" :
      state.view === "theme" ? "研究主題" : "官方產業";
    byId("heatmapTitle").textContent = scopeName + "交易重心";
    byId("rankingTitle").textContent = scopeName + "同表比較";
    byId("rowCount").textContent = state.rows.length + " 筆";
    renderHeatmap(state.rows, state.rowsAreGroups);
    renderRankingTable(state.rows, state.rowsAreGroups);
  }

  function activateRow(row, rowsAreGroups) {
    var code = pick(row, ["code", "stock_code"]);
    if (!rowsAreGroups && !isMissing(code)) {
      state.stock = String(code);
      updateUrl(true);
      loadStock();
      return;
    }
    var key = rowKey(row);
    if (!isMissing(key)) {
      state.group = String(key);
      state.sortKey = "turnover_share_change_pp";
      state.sortDirection = "desc";
      updateUrl(true);
      loadOverview();
    }
  }

  function splitTreemap(items, x, y, width, height, output) {
    if (!items.length || width <= 0 || height <= 0) {
      return;
    }
    if (items.length === 1) {
      output.push({ item: items[0], x: x, y: y, width: width, height: height });
      return;
    }
    var total = items.reduce(function (sum, entry) { return sum + entry.weight; }, 0);
    var half = total / 2;
    var running = 0;
    var splitIndex = 1;
    var bestDistance = Infinity;
    for (var i = 1; i < items.length; i += 1) {
      running += items[i - 1].weight;
      var distance = Math.abs(half - running);
      if (distance <= bestDistance) {
        bestDistance = distance;
        splitIndex = i;
      } else {
        break;
      }
    }
    var first = items.slice(0, splitIndex);
    var second = items.slice(splitIndex);
    var firstTotal = first.reduce(function (sum, entry) { return sum + entry.weight; }, 0);
    var fraction = total > 0 ? firstTotal / total : 0.5;
    if (width >= height) {
      var firstWidth = width * fraction;
      splitTreemap(first, x, y, firstWidth, height, output);
      splitTreemap(second, x + firstWidth, y, width - firstWidth, height, output);
    } else {
      var firstHeight = height * fraction;
      splitTreemap(first, x, y, width, firstHeight, output);
      splitTreemap(second, x, y + firstHeight, width, height - firstHeight, output);
    }
  }

  function mixColor(from, to, amount) {
    var values = from.map(function (value, index) {
      return Math.round(value + (to[index] - value) * amount);
    });
    return "rgb(" + values.join(",") + ")";
  }

  function heatColor(value, range) {
    var parsed = asNumber(value);
    if (parsed === null) {
      return "rgb(113,104,92)";
    }
    var ratio = Math.min(1, Math.abs(parsed) / range);
    var strength = 0.28 + ratio * 0.72;
    return parsed >= 0 ? mixColor([113, 104, 92], [157, 44, 37], strength) :
      mixColor([113, 104, 92], [28, 101, 77], strength);
  }

  function shorten(value, limit) {
    var text = String(value);
    if (limit < 2) {
      return "";
    }
    return text.length > limit ? text.slice(0, Math.max(1, limit - 1)) + "…" : text;
  }

  function renderHeatmap(rows, rowsAreGroups) {
    var svg = byId("heatmap");
    svg.replaceChildren();
    var candidates = rows.map(function (row) {
      return { row: row, weight: asNumber(pick(row, ["turnover", "period_turnover"])) };
    }).filter(function (entry) { return entry.weight !== null && entry.weight > 0; });
    byId("heatmapEmpty").hidden = candidates.length > 0;
    svg.hidden = candidates.length === 0;
    if (!candidates.length) {
      return;
    }
    candidates.sort(function (a, b) { return b.weight - a.weight; });
    var frameWidth = byId("heatmapFrame").clientWidth || 1000;
    var width = Math.max(330, frameWidth);
    var height = window.matchMedia("(max-width: 640px)").matches ? 600 :
      Math.max(420, Math.min(650, Math.round(width * 0.53)));
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.style.height = height + "px";
    var layouts = [];
    splitTreemap(candidates, 0, 0, width, height, layouts);
    var strengths = candidates.map(function (entry) { return Math.abs(asNumber(relativeValue(entry.row)) || 0); });
    var range = Math.max(1, Math.ceil(Math.max.apply(null, strengths)));
    byId("legendLow").textContent = "↓ −" + numberFormatter.format(range) + "pp";
    byId("legendHigh").textContent = "↑ +" + numberFormatter.format(range) + "pp";

    var defs = makeSvg("defs");
    svg.appendChild(defs);
    layouts.forEach(function (layout, index) {
      var padding = 1.5;
      var tileWidth = Math.max(0, layout.width - padding * 2);
      var tileHeight = Math.max(0, layout.height - padding * 2);
      var row = layout.item.row;
      var name = rowName(row);
      var relative = relativeValue(row);
      var group = makeSvg("g");
      group.setAttribute("class", "heat-tile");
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "button");
      group.setAttribute("aria-label", name + "，相對強弱 " + formatPp(relative) +
        "，期間成交值 " + formatTurnover(layout.item.weight) + "。按 Enter 查看明細。");

      var clipId = "tile-clip-" + index;
      var clip = makeSvg("clipPath");
      clip.setAttribute("id", clipId);
      var clipRect = makeSvg("rect");
      clipRect.setAttribute("x", String(layout.x + padding));
      clipRect.setAttribute("y", String(layout.y + padding));
      clipRect.setAttribute("width", String(tileWidth));
      clipRect.setAttribute("height", String(tileHeight));
      clip.appendChild(clipRect);
      defs.appendChild(clip);

      var rect = makeSvg("rect");
      rect.setAttribute("x", String(layout.x + padding));
      rect.setAttribute("y", String(layout.y + padding));
      rect.setAttribute("width", String(tileWidth));
      rect.setAttribute("height", String(tileHeight));
      rect.setAttribute("rx", "3");
      rect.setAttribute("fill", heatColor(relative, range));
      group.appendChild(rect);

      var title = makeSvg("title");
      title.textContent = name + "｜" + directionSymbol(relative) + " " + formatPp(relative) +
        "｜成交值 " + formatTurnover(layout.item.weight);
      group.appendChild(title);

      var textGroup = makeSvg("g");
      textGroup.setAttribute("clip-path", "url(#" + clipId + ")");
      var fontSize = Math.max(10, Math.min(18, Math.min(tileWidth / 7, tileHeight / 3.8)));
      var nameText = makeSvg("text");
      nameText.setAttribute("class", "tile-name");
      nameText.setAttribute("x", String(layout.x + padding + 8));
      nameText.setAttribute("y", String(layout.y + padding + fontSize + 4));
      nameText.setAttribute("font-size", String(fontSize));
      nameText.textContent = shorten(name, Math.max(2, Math.floor((tileWidth - 16) / (fontSize * 0.9))));
      textGroup.appendChild(nameText);

      var valueText = makeSvg("text");
      valueText.setAttribute("class", "tile-value");
      valueText.setAttribute("x", String(layout.x + padding + 8));
      valueText.setAttribute("y", String(layout.y + padding + fontSize * 2.25 + 4));
      valueText.setAttribute("font-size", String(Math.max(9, fontSize * 0.82)));
      valueText.textContent = directionSymbol(relative) + " " + (asNumber(relative) === null ? "尚未取得" : formatSigned(relative, "pp", 2));
      textGroup.appendChild(valueText);
      group.appendChild(textGroup);

      group.addEventListener("click", function () { activateRow(row, rowsAreGroups); });
      group.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activateRow(row, rowsAreGroups);
        }
      });
      svg.appendChild(group);
    });
  }

  function tagCell(row) {
    var wrapper = make("div", "tag-list");
    var tags = flattenTags(pick(row, ["tags", "themes", "labels"]));
    var count = asNumber(pick(row, ["member_count", "members_count"]));
    if (count !== null) {
      wrapper.appendChild(make("span", "tag", formatNumber(count, 0) + " 檔成員"));
    }
    tags.slice(0, 6).forEach(function (tag) {
      wrapper.appendChild(make("span", "tag", tag));
    });
    if (tags.length > 6) {
      wrapper.appendChild(make("span", "tag", "+" + (tags.length - 6)));
    }
    if (!wrapper.childNodes.length) {
      wrapper.appendChild(make("span", "missing", "尚未取得"));
    }
    return wrapper;
  }

  function marketIndustryText(row) {
    var values = [pick(row, ["market"]), pick(row, ["industry", "official_industry"])].filter(function (value) {
      return !isMissing(value);
    });
    return values.length ? values.join(" · ") : "尚未取得";
  }

  function targetCell(row, rowsAreGroups) {
    var button = make("button", "row-link");
    var label = make("span", null, rowName(row));
    button.appendChild(label);
    var code = pick(row, ["code", "stock_code"]);
    if (!isMissing(code)) {
      button.appendChild(make("span", "stock-code", code));
    }
    button.setAttribute("aria-label", rowName(row) + "，查看" + (rowsAreGroups ? "成分股" : "個股研究"));
    button.addEventListener("click", function () { activateRow(row, rowsAreGroups); });
    return button;
  }

  function rankingColumns(rowsAreGroups) {
    var columns = [
      { key: "name", label: rowsAreGroups ? "分類" : "股票", value: rowName,
        render: function (row) { return targetCell(row, rowsAreGroups); } },
      { key: "tags", label: rowsAreGroups ? "標籤／成員" : "多維標籤", value: function (row) {
        return flattenTags(pick(row, ["tags", "themes", "labels"])).join(" ") + " " +
          displayText(pick(row, ["member_count", "members_count"]));
      }, render: tagCell },
      { key: "market_industry", label: "市場／產業", value: marketIndustryText,
        render: function (row) { return make("span", null, marketIndustryText(row)); } },
      { key: "raw_return_pct", label: "原始總報酬", value: rawReturnValue,
        render: function (row) { return make("span", toneClass(rawReturnValue(row)), formatPercent(rawReturnValue(row))); } },
      { key: "relative_strength_pp", label: "相對強弱", value: relativeValue,
        render: function (row) { return make("span", toneClass(relativeValue(row)), directionSymbol(relativeValue(row)) + " " + formatPp(relativeValue(row))); } },
      { key: "turnover", label: "期間成交值", value: function (row) { return pick(row, ["turnover", "period_turnover"]); },
        render: function (row) { return make("span", null, formatTurnover(pick(row, ["turnover", "period_turnover"]))); } },
      { key: "turnover_share_pct", label: "交易重心", value: turnoverShareValue,
        render: function (row) { return make("span", null, formatPercent(turnoverShareValue(row))); } },
      { key: "turnover_share_change_pp", label: "重心變化", value: turnoverChangeValue,
        render: function (row) { return make("span", toneClass(turnoverChangeValue(row)), directionSymbol(turnoverChangeValue(row)) + " " + formatPp(turnoverChangeValue(row))); } },
      { key: "institutional_estimated_amount", label: "法人推估淨額", value: institutionalValue,
        render: function (row) { return make("span", toneClass(institutionalValue(row)), formatCompactMoney(institutionalValue(row))); } }
    ];
    return columns;
  }

  function compareValues(left, right, direction) {
    var leftMissing = isMissing(left);
    var rightMissing = isMissing(right);
    if (leftMissing && rightMissing) {
      return 0;
    }
    if (leftMissing) {
      return 1;
    }
    if (rightMissing) {
      return -1;
    }
    var leftNumber = asNumber(left);
    var rightNumber = asNumber(right);
    var result;
    if (leftNumber !== null && rightNumber !== null) {
      result = leftNumber - rightNumber;
    } else {
      result = String(left).localeCompare(String(right), "zh-Hant");
    }
    return direction === "asc" ? result : -result;
  }

  function renderRankingTable(rows, rowsAreGroups) {
    var columns = rankingColumns(rowsAreGroups);
    var head = byId("rankingHead");
    var body = byId("rankingBody");
    head.replaceChildren();
    body.replaceChildren();
    columns.forEach(function (column) {
      var th = make("th");
      th.setAttribute("scope", "col");
      var isSorted = state.sortKey === column.key;
      th.setAttribute("aria-sort", isSorted ? (state.sortDirection === "asc" ? "ascending" : "descending") : "none");
      var button = make("button", "sort-button");
      button.appendChild(make("span", null, column.label));
      button.appendChild(make("span", "sort-indicator", isSorted ? (state.sortDirection === "asc" ? "↑" : "↓") : ""));
      button.addEventListener("click", function () {
        if (state.sortKey === column.key) {
          state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
        } else {
          state.sortKey = column.key;
          state.sortDirection = column.key === "name" || column.key === "tags" || column.key === "market_industry" ? "asc" : "desc";
        }
        renderRankingTable(rows, rowsAreGroups);
        announce("已依「" + column.label + "」" + (state.sortDirection === "asc" ? "由小至大" : "由大至小") + "排序");
      });
      th.appendChild(button);
      head.appendChild(th);
    });

    var activeColumn = columns.find(function (column) { return column.key === state.sortKey; }) || columns[0];
    var sorted = rows.slice().sort(function (a, b) {
      return compareValues(activeColumn.value(a), activeColumn.value(b), state.sortDirection);
    });
    if (!sorted.length) {
      var emptyRow = make("tr");
      var emptyCell = make("td", "missing", "目前沒有符合條件的資料");
      emptyCell.colSpan = columns.length;
      emptyCell.style.textAlign = "center";
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
      return;
    }
    sorted.forEach(function (row) {
      var tr = make("tr");
      columns.forEach(function (column) {
        var td = make("td");
        td.appendChild(column.render(row));
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  function sourceName(source) {
    if (typeof source === "string") {
      return source;
    }
    return displayText(pick(source, ["name", "source_name", "title", "publisher"]));
  }

  function sourceTypeLabel(value) {
    var labels = {
      official: "官方資料",
      official_reference: "官方參考",
      aggregated_public_data: "公開資料聚合",
      secondary_html: "Goodinfo 補充網頁",
      curated_evidence: "人工覆核證據",
      company_product_page: "公司產品資料",
      company_sustainability_report: "公司永續報告",
      exchange_industry_value_chain: "交易所產業鏈",
      derived: "系統推導"
    };
    return labels[String(value || "")] || displayText(value);
  }

  function sourceStatusLabel(value) {
    if (isMissing(value)) {
      return null;
    }
    var labels = {
      available: "已取得",
      unavailable: "未取得",
      partial: "部分取得",
      stale: "最近成功資料"
    };
    return labels[String(value)] || displayText(value);
  }

  function sourceCard(source) {
    var card = make("article", "source-card");
    if (typeof source === "string") {
      card.appendChild(make("strong", null, source));
      return card;
    }
    var url = safeUrl(pick(source, ["url", "link", "source_url"]));
    if (url) {
      var link = make("a", null, sourceName(source));
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.appendChild(link);
    } else {
      card.appendChild(make("strong", null, sourceName(source)));
    }
    var meta = make("div", "source-meta");
    var fields = [
      ["發布", pick(source, ["published_at", "published_date"])],
      ["資料期間", pick(source, ["data_period", "period"])],
      ["查核", pick(source, ["last_verified_at", "last_verified"])],
      ["資料集", pick(source, ["dataset"])],
      ["狀態", sourceStatusLabel(pick(source, ["status"]))],
      ["性質", sourceTypeLabel(pick(source, ["type", "source_type", "claim_type"]))],
      ["位置", pick(source, ["page", "paragraph", "location", "locator"])]
    ];
    fields.forEach(function (field) {
      if (!isMissing(field[1])) {
        meta.appendChild(make("span", null, field[0] + "：" + displayMetaValue(field[1])));
      }
    });
    if (meta.childNodes.length) {
      card.appendChild(meta);
    }
    var formula = pick(source, ["formula", "derivation", "note"]);
    if (!isMissing(formula)) {
      card.appendChild(make("span", "source-meta", "說明：" + formula));
    }
    return card;
  }

  function renderSources(container, sources) {
    container.replaceChildren();
    if (!sources.length) {
      container.appendChild(make("span", "missing", "來源尚未取得"));
      return;
    }
    sources.forEach(function (source) {
      container.appendChild(sourceCard(source));
    });
  }

  function renderInlineSources(container, section) {
    container.replaceChildren();
    var details = make("details", "inline-sources");
    var sources = firstArray(section && section.sources);
    details.appendChild(make("summary", null, "本段資料來源（" + (sources.length || "尚未取得") + "）"));
    var list = make("div", "source-list");
    renderSources(list, sources);
    details.appendChild(list);
    container.appendChild(details);
  }

  function sectionStatus(section, targetId) {
    var status = String(pick(section, ["status"]) || "").toLowerCase();
    var labels = {
      fresh: "資料已更新",
      ok: "資料已更新",
      partial: "部分取得",
      stale: "最近成功資料",
      unavailable: "尚未取得"
    };
    byId(targetId).textContent = labels[status] || (status ? status : "");
  }

  function metricCard(label, value, note, tone) {
    var card = make("div", "metric-card");
    card.appendChild(make("span", "metric-label", label));
    card.appendChild(make("strong", "metric-value " + (tone || ""), value));
    if (note) {
      card.appendChild(make("small", "metric-note", note));
    }
    return card;
  }

  function subsection(title) {
    var section = make("section", "subsection");
    section.appendChild(make("h3", null, title));
    return section;
  }

  function simpleTable(columns, rows) {
    var scroll = make("div", "table-scroll");
    scroll.tabIndex = 0;
    scroll.setAttribute("aria-label", "可左右捲動的" + (columns.length ? columns[0].label : "資料") + "表格");
    var table = make("table");
    var thead = make("thead");
    var headRow = make("tr");
    columns.forEach(function (column) {
      var th = make("th", null, column.label);
      th.scope = "col";
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    var tbody = make("tbody");
    rows.forEach(function (row) {
      var tr = make("tr");
      columns.forEach(function (column) {
        var td = make("td");
        var rendered = column.render ? column.render(row) : displayText(pick(row, column.keys || [column.key]));
        if (rendered instanceof Node) {
          td.appendChild(rendered);
        } else {
          td.textContent = rendered;
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    scroll.appendChild(table);
    return scroll;
  }

  function financialRows(rows, keys) {
    return firstArray(rows).slice().sort(function (left, right) {
      return String(pick(left, keys) || "").localeCompare(String(pick(right, keys) || ""));
    });
  }

  function yearMonthKey(value) {
    var text = String(value || "");
    var match = text.match(/(20\d{2})[-/.年](\d{1,2})/);
    if (!match) {
      var date = new Date(text);
      if (!Number.isNaN(date.getTime())) {
        return date.getUTCFullYear() + "-" + String(date.getUTCMonth() + 1).padStart(2, "0");
      }
      return null;
    }
    return match[1] + "-" + String(Number(match[2])).padStart(2, "0");
  }

  function yearQuarterKey(row) {
    var raw = String(pick(row, ["period", "date", "quarter"]) || "");
    var year = asNumber(pick(row, ["year", "fiscal_year"]));
    var yearMatch = raw.match(/(20\d{2})/);
    if (year === null && yearMatch) {
      year = Number(yearMatch[1]);
    }
    if (year === null) {
      return null;
    }
    var quarter = asNumber(pick(row, ["quarter", "fiscal_quarter"]));
    var quarterMatch = raw.match(/[Qq]([1-4])|第([1-4])季/);
    if (quarter === null && quarterMatch) {
      quarter = Number(quarterMatch[1] || quarterMatch[2]);
    }
    if (quarter === null) {
      var month = yearMonthKey(raw);
      if (month) {
        quarter = Math.floor((Number(month.slice(5)) - 1) / 3) + 1;
      }
    }
    return quarter === null ? String(Math.trunc(year)) + "Y" : String(Math.trunc(year)) + "Q" + Math.trunc(quarter);
  }

  function priorPeriodKey(key) {
    if (!key) {
      return null;
    }
    var match = String(key).match(/^(\d{4})(.*)$/);
    return match ? String(Number(match[1]) - 1) + match[2] : null;
  }

  function periodSerial(key) {
    var month = String(key || "").match(/^(\d{4})-(\d{2})$/);
    if (month) {
      return Number(month[1]) * 12 + Number(month[2]);
    }
    var quarter = String(key || "").match(/^(\d{4})Q([1-4])$/);
    if (quarter) {
      return Number(quarter[1]) * 4 + Number(quarter[2]);
    }
    var annual = String(key || "").match(/^(\d{4})Y$/);
    return annual ? Number(annual[1]) : null;
  }

  function comparePeriodRows(allRows, visibleRows, kind, valueKeys) {
    var keyFor = kind === "eps" ? yearQuarterKey : function (row) {
      return yearMonthKey(pick(row, ["month", "date", "period"]));
    };
    var byKey = new Map();
    allRows.forEach(function (row) {
      var key = keyFor(row);
      if (key) {
        byKey.set(key, row);
      }
    });
    return visibleRows.map(function (row) {
      var key = keyFor(row);
      var previous = key ? byKey.get(priorPeriodKey(key)) : null;
      var currentValue = asNumber(pick(row, valueKeys));
      var previousValue = asNumber(previous && pick(previous, valueKeys));
      var delta = currentValue !== null && previousValue !== null ? currentValue - previousValue : null;
      var yoy = delta !== null && previousValue !== 0 ? delta / Math.abs(previousValue) * 100 : null;
      return Object.assign({}, row, {
        _previous_value: previousValue,
        _delta: delta,
        _yoy: yoy
      });
    });
  }

  function twoYearRows(rows, kind) {
    var sorted = financialRows(rows, kind === "month" ? ["month", "date", "period"] : ["period", "date", "month"]);
    var keyFor = kind === "eps" || kind === "cash" ? yearQuarterKey : function (row) {
      return yearMonthKey(pick(row, ["month", "date", "period"]));
    };
    var keyed = sorted.map(function (row) { return { row: row, key: keyFor(row) }; }).filter(function (item) { return item.key; });
    if (keyed.length) {
      var latest = periodSerial(keyed[keyed.length - 1].key);
      var span = kind === "month" || kind === "pe" ? 23 : 7;
      if (latest !== null) {
        var filtered = keyed.filter(function (item) {
          var serial = periodSerial(item.key);
          return serial !== null && serial >= latest - span && serial <= latest;
        }).map(function (item) { return item.row; });
        if (filtered.length) {
          return kind === "month" || kind === "pe" ? filtered.slice(-24) : filtered.slice(-(keyed.some(function (item) { return item.key.indexOf("Q") >= 0; }) ? 8 : 1));
        }
      }
    }
    if (kind === "month" || kind === "pe") {
      return sorted.slice(-24);
    }
    var hasQuarter = sorted.some(function (row) { return Boolean(yearQuarterKey(row) && yearQuarterKey(row).indexOf("Q") >= 0); });
    return sorted.slice(-(hasQuarter ? 8 : 1));
  }

  function monthlyPeRows(rows) {
    var byMonth = new Map();
    financialRows(rows, ["date", "period", "month"]).forEach(function (row) {
      var month = yearMonthKey(pick(row, ["date", "period", "month"]));
      if (month) {
        byMonth.set(month, Object.assign({}, row, { _month: month }));
      }
    });
    return Array.from(byMonth.values()).sort(function (left, right) {
      return left._month.localeCompare(right._month);
    });
  }

  function chartValue(value, kind) {
    if (kind === "money") {
      return formatCompactMoney(value);
    }
    if (kind === "pe") {
      return formatNumber(value, 2) + " 倍";
    }
    return formatNumber(value, 2);
  }

  function chartLabel(value, kind) {
    if (kind === "month") {
      return String(value || "").replace("-", "/");
    }
    if (kind === "eps") {
      var text = String(value || "");
      var quarterMatch = text.match(/(20\d{2})[-/]?Q([1-4])/i);
      if (quarterMatch) {
        return quarterMatch[1] + " Q" + quarterMatch[2];
      }
      var monthMatch = text.match(/(20\d{2})[-/](\d{1,2})/);
      if (monthMatch) {
        return monthMatch[1] + " Q" + (Math.floor((Number(monthMatch[2]) - 1) / 3) + 1);
      }
      return text;
    }
    var generic = String(value || "");
    var dateMatch = generic.match(/20\d{2}[-/](\d{1,2})(?:[-/](\d{1,2}))?/);
    return dateMatch ? dateMatch[0].replace(/-/g, "/") : generic;
  }

  function svgAttr(element, attrs) {
    Object.keys(attrs).forEach(function (key) {
      element.setAttribute(key, String(attrs[key]));
    });
    return element;
  }

  function chartShell(title, note, ariaLabel) {
    var shell = make("article", "financial-chart");
    var heading = make("div", "financial-chart-heading");
    heading.appendChild(make("h3", null, title));
    if (note) {
      heading.appendChild(make("p", null, note));
    }
    shell.appendChild(heading);
    shell.setAttribute("aria-label", ariaLabel || title);
    return shell;
  }

  function chartSvg(width, height, ariaLabel) {
    var svg = makeSvg("svg");
    svgAttr(svg, { viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": ariaLabel });
    svg.classList.add("financial-chart-svg");
    return svg;
  }

  function chartLegend(series) {
    var legend = make("div", "chart-legend");
    series.forEach(function (item, index) {
      var entry = make("span", "chart-legend-item");
      var swatch = make("span", "chart-legend-swatch chart-series-" + index);
      swatch.setAttribute("aria-hidden", "true");
      entry.appendChild(swatch);
      entry.appendChild(make("span", null, item.label));
      legend.appendChild(entry);
    });
    return legend;
  }

  function renderLineChart(title, note, rows, periodKeys, series, kind) {
    var shell = chartShell(title, note, title + "；圖中每個點均標示數值");
    if (rows.length < 2) {
      shell.appendChild(make("p", "missing chart-missing", "可用資料不足，至少需要兩個期間才能繪圖。"));
      return shell;
    }
    var width = 760;
    var height = 258;
    var pad = { top: 28, right: 34, bottom: 52, left: 58 };
    var plotWidth = width - pad.left - pad.right;
    var plotHeight = height - pad.top - pad.bottom;
    var values = [];
    series.forEach(function (item) {
      rows.forEach(function (row) {
        var value = asNumber(pick(row, item.keys));
        if (value !== null) {
          values.push(value);
        }
      });
    });
    if (!values.length) {
      shell.appendChild(make("p", "missing chart-missing", "此段尚未取得可繪製數值。"));
      return shell;
    }
    var min = Math.min.apply(Math, values);
    var max = Math.max.apply(Math, values);
    var spread = max - min || Math.max(Math.abs(max) * 0.2, 1);
    min -= spread * 0.12;
    max += spread * 0.12;
    var y = function (value) { return pad.top + (max - value) / (max - min) * plotHeight; };
    var x = function (index) { return pad.left + (rows.length === 1 ? plotWidth / 2 : index / (rows.length - 1) * plotWidth); };
    var svg = chartSvg(width, height, title + "折線圖");
    [0, 0.5, 1].forEach(function (ratio) {
      var line = svgAttr(makeSvg("line"), { x1: pad.left, x2: width - pad.right, y1: pad.top + ratio * plotHeight, y2: pad.top + ratio * plotHeight });
      line.classList.add("chart-grid-line");
      svg.appendChild(line);
      var value = max - ratio * (max - min);
      var label = svgAttr(makeSvg("text"), { x: pad.left - 8, y: pad.top + ratio * plotHeight + 4, "text-anchor": "end" });
      label.classList.add("chart-axis-label");
      label.textContent = chartValue(value, kind).replace("NT$ ", "");
      svg.appendChild(label);
    });
    rows.forEach(function (row, index) {
      if (rows.length > 16 && index % 2 === 1) {
        return;
      }
      var label = svgAttr(makeSvg("text"), { x: x(index), y: height - 17, "text-anchor": "middle" });
      label.classList.add("chart-x-label");
      label.textContent = chartLabel(pick(row, periodKeys), kind);
      svg.appendChild(label);
    });
    series.forEach(function (item, seriesIndex) {
      var points = [];
      rows.forEach(function (row, index) {
        var value = asNumber(pick(row, item.keys));
        if (value !== null) {
          points.push(x(index) + "," + y(value));
        }
      });
      if (points.length < 2) {
        return;
      }
      var polyline = svgAttr(makeSvg("polyline"), { points: points.join(" ") });
      polyline.classList.add("chart-line", "chart-series-" + seriesIndex);
      svg.appendChild(polyline);
      rows.forEach(function (row, index) {
        var value = asNumber(pick(row, item.keys));
        if (value === null) {
          return;
        }
        var circle = svgAttr(makeSvg("circle"), { cx: x(index), cy: y(value), r: 4 });
        circle.classList.add("chart-point", "chart-series-" + seriesIndex);
        var titleNode = makeSvg("title");
        titleNode.textContent = item.label + "｜" + chartLabel(pick(row, periodKeys), kind) + "｜" + chartValue(value, kind);
        circle.appendChild(titleNode);
        svg.appendChild(circle);
        var valueLabel = svgAttr(makeSvg("text"), { x: x(index), y: y(value) - 9 - seriesIndex * 13, "text-anchor": "middle" });
        valueLabel.classList.add("chart-point-label");
        valueLabel.textContent = chartValue(value, kind).replace("NT$ ", "");
        svg.appendChild(valueLabel);
      });
    });
    shell.appendChild(svg);
    shell.appendChild(chartLegend(series));
    return shell;
  }

  function quantile(values, ratio) {
    var sorted = values.slice().sort(function (left, right) { return left - right; });
    if (!sorted.length) {
      return null;
    }
    return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))];
  }

  function renderPeRiverChart(rows) {
    var shell = chartShell("本益比河流圖（近兩年）", "每月最後一筆觀測；X 軸隔月標示，點值全部保留；色帶是本頁兩年資料的分位區間，不是估值建議。", "近兩年本益比河流圖，含低位、中位與高位分布帶");
    if (rows.length < 2) {
      shell.appendChild(make("p", "missing chart-missing", "可用本益比不足，無法建立河流圖。"));
      return shell;
    }
    var values = rows.map(function (row) { return asNumber(pick(row, ["pe", "pe_ratio"])); }).filter(function (value) { return value !== null; });
    if (!values.length) {
      shell.appendChild(make("p", "missing chart-missing", "本益比尚未取得可繪製數值。"));
      return shell;
    }
    var q20 = quantile(values, 0.2);
    var q50 = quantile(values, 0.5);
    var q80 = quantile(values, 0.8);
    var min = Math.min.apply(Math, values);
    var max = Math.max.apply(Math, values);
    var spread = max - min || Math.max(Math.abs(max) * 0.2, 1);
    min = Math.max(0, min - spread * 0.15);
    max += spread * 0.15;
    var width = 760;
    var height = 276;
    var pad = { top: 28, right: 58, bottom: 52, left: 58 };
    var plotWidth = width - pad.left - pad.right;
    var plotHeight = height - pad.top - pad.bottom;
    var y = function (value) { return pad.top + (max - value) / (max - min) * plotHeight; };
    var x = function (index) { return pad.left + index / (rows.length - 1) * plotWidth; };
    var svg = chartSvg(width, height, "近兩年本益比河流圖");
    [[min, q20, "低位"], [q20, q80, "中位"], [q80, max, "高位"]].forEach(function (band, index) {
      var top = y(band[1]);
      var bottom = y(band[0]);
      var rect = svgAttr(makeSvg("rect"), { x: pad.left, y: top, width: plotWidth, height: Math.max(0, bottom - top) });
      rect.classList.add("chart-river-band", "chart-river-band-" + index);
      svg.appendChild(rect);
      var bandLabel = svgAttr(makeSvg("text"), { x: width - pad.right + 8, y: (top + bottom) / 2 + 4 });
      bandLabel.classList.add("chart-river-label");
      bandLabel.textContent = band[2];
      svg.appendChild(bandLabel);
    });
    [q20, q50, q80].forEach(function (value, index) {
      var line = svgAttr(makeSvg("line"), { x1: pad.left, x2: width - pad.right, y1: y(value), y2: y(value) });
      line.classList.add("chart-river-guide", "chart-river-guide-" + index);
      svg.appendChild(line);
      var guideLabel = svgAttr(makeSvg("text"), { x: pad.left - 8, y: y(value) + 4, "text-anchor": "end" });
      guideLabel.classList.add("chart-axis-label");
      guideLabel.textContent = formatNumber(value, 1);
      svg.appendChild(guideLabel);
    });
    rows.forEach(function (row, index) {
      var value = asNumber(pick(row, ["pe", "pe_ratio"]));
      var showLabel = rows.length <= 16 || index % 2 === 0;
      if (showLabel) {
        var label = svgAttr(makeSvg("text"), { x: x(index), y: height - 17, "text-anchor": "middle" });
        label.classList.add("chart-x-label");
        label.textContent = chartLabel(row._month || pick(row, ["date", "period"]), "month");
        svg.appendChild(label);
      }
      if (value === null) {
        return;
      }
      var circle = svgAttr(makeSvg("circle"), { cx: x(index), cy: y(value), r: 4 });
      circle.classList.add("chart-point", "chart-series-0");
      var titleNode = makeSvg("title");
      titleNode.textContent = chartLabel(row._month, "month") + "｜本益比 " + chartValue(value, "pe");
      circle.appendChild(titleNode);
      svg.appendChild(circle);
      var valueLabel = svgAttr(makeSvg("text"), { x: x(index), y: y(value) - 9, "text-anchor": "middle" });
      valueLabel.classList.add("chart-point-label");
      valueLabel.textContent = formatNumber(value, 1);
      svg.appendChild(valueLabel);
    });
    var line = svgAttr(makeSvg("polyline"), { points: rows.map(function (row, index) {
      var value = asNumber(pick(row, ["pe", "pe_ratio"]));
      return value === null ? null : x(index) + "," + y(value);
    }).filter(Boolean).join(" ") });
    line.classList.add("chart-line", "chart-series-0");
    svg.appendChild(line);
    shell.appendChild(svg);
    shell.appendChild(chartLegend([{ label: "每月本益比", keys: ["pe"] }]));
    return shell;
  }

  async function loadStock() {
    if (!state.stock) {
      loadOverview();
      return;
    }
    clearFatal();
    byId("overviewPage").hidden = true;
    byId("stockPage").hidden = false;
    setStatus({}, true);
    updateControlStates();
    var query = new URLSearchParams({
      window: String(state.window),
      news_days: String(state.newsDays)
    });
    try {
      var payload = await fetchJson("/api/research/stocks/" + encodeURIComponent(state.stock) + "?" + query.toString());
      renderStock(payload);
      setStatus(firstObject(payload.meta), false);
      announce("已載入 " + state.stock + " 個股研究");
    } catch (error) {
      if (error.name !== "AbortError" && error.name !== "AuthRequired") {
        showFatal(error.message);
      }
    }
  }

  function renderStock(payload) {
    var stock = firstObject(payload.stock, payload.profile);
    var code = pick(stock, ["code", "stock_code"]) || state.stock;
    var name = pick(stock, ["name", "stock_name"]);
    byId("stockTitle").textContent = [code, name].filter(function (value) { return !isMissing(value); }).join(" ") || "個股研究";
    var market = pick(stock, ["market"]);
    var industry = pick(stock, ["industry", "official_industry"]);
    byId("stockMarket").textContent = [market, industry].filter(function (value) { return !isMissing(value); }).join(" · ") || "STOCK RESEARCH";
    byId("stockSummary").textContent = displayText(pick(stock, ["summary", "description", "business_summary"]));
    renderStockTags(byId("stockTags"), pick(stock, ["tags", "tag_dimensions", "themes"]));

    var trading = firstObject(payload.trading, stock.trading);
    var supply = firstObject(payload.supply_chain, stock.supply_chain);
    var financial = firstObject(payload.financials, stock.financials);
    var mix = firstObject(payload.revenue_mix, payload.product_mix, stock.revenue_mix);
    var dividends = firstObject(
      payload.dividends,
      payload.ex_dividend,
      payload.corporate_actions,
      stock.dividends,
      stock.ex_dividend
    );
    var news = firstObject(payload.news, stock.news);

    renderTrading(trading, stock);
    renderSupplyChain(supply, stock);
    renderFinancials(financial);
    renderRevenueMix(mix);
    renderDividendEvents(dividends);
    renderNews(news);
    renderSources(byId("stockSources"), firstArray(payload.sources, payload.meta && payload.meta.sources));
  }

  function renderStockTags(container, tags) {
    container.replaceChildren();
    if (tags && typeof tags === "object" && !Array.isArray(tags)) {
      Object.keys(tags).forEach(function (dimension) {
        var values = Array.isArray(tags[dimension]) ? tags[dimension] : [tags[dimension]];
        if (!values.length) {
          return;
        }
        container.appendChild(make("span", "tag-dimension", dimension));
        values.forEach(function (tag) {
          var label = typeof tag === "object" ? pick(tag, ["name", "label", "value"]) : tag;
          if (!isMissing(label)) {
            container.appendChild(make("span", "tag", label));
          }
        });
      });
    } else if (Array.isArray(tags) && tags.some(function (tag) { return tag && typeof tag === "object"; })) {
      var dimensions = {};
      tags.forEach(function (tag) {
        if (!tag || typeof tag !== "object") {
          return;
        }
        var dimension = pick(tag, ["dimension"]) || "tag";
        if (!dimensions[dimension]) {
          dimensions[dimension] = true;
          container.appendChild(make("span", "tag-dimension", dimension));
        }
        var label = pick(tag, ["label", "name", "value"]);
        if (isMissing(label)) {
          return;
        }
        var relation = String(pick(tag, ["relation_state", "relation"]) || "").toLowerCase();
        var relationLabel = {
          actual_business: "實際業務",
          application_or_roadmap: "應用／規劃",
          industry_context: "產業脈絡",
          same_product_peer: "同產品層"
        }[relation] || "";
        var badge = relationLabel ? " · " + relationLabel : "";
        var pill = make("span", "tag" + (relation === "application_or_roadmap" ? " tag-roadmap" : ""), String(label) + badge);
        var evidence = pick(tag, ["evidence"]);
        if (!isMissing(evidence)) {
          pill.title = String(evidence);
        }
        container.appendChild(pill);
      });
    } else {
      flattenTags(tags).forEach(function (tag) {
        container.appendChild(make("span", "tag", tag));
      });
    }
    if (!container.childNodes.length) {
      container.appendChild(make("span", "missing", "標籤尚未取得"));
    }
  }

  function renderTrading(section, stock) {
    sectionStatus(section, "tradingStatus");
    var metrics = byId("tradingMetrics");
    metrics.replaceChildren();
    var data = Object.assign({}, stock || {}, section || {});
    var specs = [
      ["參考收盤價", pick(data, ["close", "close_price", "price"]), function (value) {
        var parsed = asNumber(value); return parsed === null ? "尚未取得" : "NT$ " + formatNumber(parsed, 2);
      }, "資料日收盤", ""],
      [state.window + " 日原始總報酬", rawReturnValue(data), formatPercent, "含除權息調整", toneClass(rawReturnValue(data))],
      [state.window + " 日相對強弱", relativeValue(data), formatPp, "相對加權報酬指數", toneClass(relativeValue(data))],
      ["期間成交值", pick(data, ["turnover", "period_turnover"]), formatTurnover, "面積依據", ""],
      ["全市場交易重心", turnoverShareValue(data), formatPercent, "分母固定為上市櫃普通股", ""],
      ["重心變化", turnoverChangeValue(data), formatPp, "相較前一個等長期間", toneClass(turnoverChangeValue(data))],
      ["法人推估淨額", institutionalValue(data), formatCompactMoney, "股數 × 推估均價；非實際資金流", toneClass(institutionalValue(data))]
    ];
    specs.forEach(function (spec) {
      metrics.appendChild(metricCard(spec[0], spec[2](spec[1]), spec[3], spec[4]));
    });

    var breakdown = byId("institutionalBreakdown");
    breakdown.replaceChildren();
    var institution = pick(section, ["institutional", "institutional_breakdown", "institutions"]);
    if (isMissing(institution)) {
      institution = pick(stock, ["institutional", "institutional_breakdown", "institutional_estimated_breakdown"]);
    }
    var rows = [];
    if (Array.isArray(institution)) {
      rows = institution;
    } else if (institution && typeof institution === "object") {
      Object.keys(institution).forEach(function (key) {
        var item = institution[key];
        if (item && typeof item === "object") {
          rows.push(Object.assign({ type: key }, item));
        } else {
          rows.push({ type: key, estimated_amount: item });
        }
      });
    }
    if (rows.length) {
      var block = subsection("三大法人拆分（推估）");
      block.appendChild(simpleTable([
        { label: "法人", keys: ["name", "type", "institution"] },
        { label: "買賣超股數", render: function (row) { return formatSigned(pick(row, ["net_shares", "shares"]), " 股", 0); } },
        { label: "推估淨額", render: function (row) { return make("span", toneClass(pick(row, ["estimated_amount", "amount"])), formatCompactMoney(pick(row, ["estimated_amount", "amount"]))); } },
        { label: "估算說明", keys: ["method", "note"] }
      ], rows));
      breakdown.appendChild(block);
    }
    renderInlineSources(byId("tradingSources"), section);
  }

  function companyText(company) {
    if (typeof company === "string") {
      return company;
    }
    if (!company || typeof company !== "object") {
      return "尚未取得";
    }
    var code = pick(company, ["code", "stock_code"]);
    var name = pick(company, ["name", "company_name"]);
    return [code, name].filter(function (value) { return !isMissing(value); }).join(" ") || "尚未取得";
  }

  function renderCompanyCards(container, companies) {
    var grid = make("div", "card-grid");
    companies.forEach(function (company) {
      var card = make("article", "info-card");
      card.appendChild(make("strong", null, companyText(company)));
      if (company && typeof company === "object") {
        var description = pick(company, ["description", "role", "note"]);
        if (!isMissing(description)) {
          card.appendChild(make("p", null, description));
        }
        var relation = pick(company, ["relation_state", "relationship", "relation"]);
        if (!isMissing(relation)) {
          card.appendChild(make("span", "relation-state", relation));
        }
      }
      grid.appendChild(card);
    });
    container.appendChild(grid);
  }

  function renderSupplyChain(section, stock) {
    sectionStatus(section, "supplyStatus");
    var target = byId("supplyChain");
    target.replaceChildren();
    var hasContent = false;
    var position = firstObject(section.position, section.supply_chain_position, section.role);
    var positionLabel = pick(position, ["label", "name", "position"]) ||
      (typeof section.position === "string" ? section.position : null);
    var officialIndustry = pick(section, ["official_industry"]) || pick(stock, ["industry", "official_industry"]);
    var summary = pick(section, ["summary", "description"]);
    if (!isMissing(positionLabel) || !isMissing(officialIndustry) || !isMissing(summary)) {
      var positionBlock = subsection("所在位置");
      var cards = make("div", "card-grid");
      if (!isMissing(officialIndustry)) {
        cards.appendChild(metricCard("官方產業分類", displayText(officialIndustry), "交易所分類", ""));
      }
      if (!isMissing(positionLabel)) {
        cards.appendChild(metricCard("供應鏈位階", displayText(positionLabel), "研究分類", ""));
      }
      if (!isMissing(summary)) {
        var summaryCard = make("div", "info-card");
        summaryCard.appendChild(make("strong", null, "業務位置說明"));
        summaryCard.appendChild(make("p", null, summary));
        cards.appendChild(summaryCard);
      }
      positionBlock.appendChild(cards);
      target.appendChild(positionBlock);
      hasContent = true;
    }

    var levels = firstArray(section.levels, section.chain_levels, section.value_chain);
    if (!levels.length) {
      [["上游", section.upstream], ["下游", section.downstream]].forEach(function (entry) {
        var value = entry[1];
        if (!value || typeof value !== "object") {
          return;
        }
        var categories = firstArray(value.categories);
        var companies = firstArray(value.companies);
        var note = pick(value, ["note", "relationship_type"]);
        if (categories.length || companies.length || !isMissing(note)) {
          levels.push({ name: entry[0], categories: categories, companies: companies, note: note });
        }
      });
    }
    if (levels.length) {
      var levelBlock = subsection("上下游位階與代表廠商");
      var chain = make("div", "chain-grid");
      levels.forEach(function (level) {
        var card = make("article", "chain-card");
        card.appendChild(make("strong", null, displayText(pick(level, ["level", "name", "stage"]))));
        var description = pick(level, ["description", "role", "note"]);
        if (!isMissing(description)) {
          card.appendChild(make("p", null, description));
        }
        var companies = firstArray(level.companies, level.representatives, level.members);
        if (companies.length) {
          card.appendChild(make("p", null, "代表：" + companies.slice(0, 3).map(companyText).join("、")));
        }
        var categories = firstArray(level.categories);
        if (categories.length) {
          card.appendChild(make("p", null, "範圍：" + categories.join("、")));
        }
        chain.appendChild(card);
      });
      levelBlock.appendChild(chain);
      target.appendChild(levelBlock);
      hasContent = true;
    }

    var peers = firstArray(section.peers, section.competitors);
    if (!peers.length && section.peers && typeof section.peers === "object") {
      peers = firstArray(section.peers.companies);
    }
    if (peers.length) {
      var peersBlock = subsection("同業與競爭對手");
      renderCompanyCards(peersBlock, peers);
      target.appendChild(peersBlock);
      hasContent = true;
    }

    var relations = firstArray(section.relations, section.verified_relations, section.supplier_customer_relations);
    if (relations.length) {
      var relationBlock = subsection("已揭露的供應／客戶關係");
      renderCompanyCards(relationBlock, relations);
      target.appendChild(relationBlock);
      hasContent = true;
    }
    if (!hasContent) {
      appendMissing(target, "供應鏈關係需要可追溯證據；尚未驗證前不推定客戶或供應商。 ");
    }
    renderInlineSources(byId("supplySources"), section);
  }

  function renderFinancials(section) {
    sectionStatus(section, "financialStatus");
    var charts = byId("financialCharts");
    var target = byId("financials");
    if (!charts && target && target.parentNode) {
      charts = make("div", "financial-chart-grid");
      charts.id = "financialCharts";
      charts.setAttribute("aria-label", "基本財務統計圖表");
      target.parentNode.insertBefore(charts, target);
    }
    if (!charts) {
      return;
    }
    charts.replaceChildren();
    target.replaceChildren();
    var hasContent = false;
    var epsSection = firstObject(section.eps);
    var quartersAll = financialRows(firstArray(section.quarters, section.quarterly, epsSection.items, section.eps_history), ["period", "date", "quarter"]);
    var quarters = twoYearRows(quartersAll, "eps");
    if (quarters.length) {
      charts.appendChild(renderLineChart(
        "EPS 趨勢（近兩年）",
        "以可取得的季度資料繪製；每個座標點直接標示 EPS。",
        quarters,
        ["period", "quarter", "date"],
        [
          { label: "單季 EPS", keys: ["eps", "quarter_eps", "single_quarter_eps"] },
          { label: "TTM EPS", keys: ["eps_ttm", "ttm_eps"] }
        ],
        "eps"
      ));
      var epsSingleCompared = comparePeriodRows(quartersAll, quarters, "eps", ["single_quarter_eps", "eps", "quarter_eps"]);
      var epsTtmCompared = comparePeriodRows(quartersAll, quarters, "eps", ["ttm_eps", "eps_ttm"]);
      var epsCompared = quarters.map(function (row, index) {
        return Object.assign({}, row, {
          _previous_single_value: epsSingleCompared[index]._previous_value,
          _single_delta: epsSingleCompared[index]._delta,
          _single_yoy: epsSingleCompared[index]._yoy,
          _previous_ttm_value: epsTtmCompared[index]._previous_value,
          _ttm_delta: epsTtmCompared[index]._delta,
          _ttm_yoy: epsTtmCompared[index]._yoy
        });
      });
      var epsBlock = subsection("每股盈餘與估值（近兩年）");
      epsBlock.appendChild(simpleTable([
        { label: "期間", keys: ["period", "quarter", "date"] },
        { label: "單季 EPS", render: function (row) { return formatNumber(pick(row, ["eps", "quarter_eps", "single_quarter_eps"]), 2); } },
        { label: "去年同期單季 EPS", render: function (row) { return formatNumber(row._previous_single_value, 2); } },
        { label: "單季年增額", render: function (row) { return make("span", toneClass(row._single_delta), formatSigned(row._single_delta, "", 2)); } },
        { label: "TTM EPS", render: function (row) { return formatNumber(pick(row, ["eps_ttm", "ttm_eps"]), 2); } },
        { label: "去年同期 TTM EPS", render: function (row) { return formatNumber(row._previous_ttm_value, 2); } },
        { label: "TTM 年增額", render: function (row) { return make("span", toneClass(row._ttm_delta), formatSigned(row._ttm_delta, "", 2)); } },
        { label: "TTM 年增率", render: function (row) { return make("span", toneClass(row._ttm_yoy), formatPercent(row._ttm_yoy)); } },
        { label: "資料性質", keys: ["type", "filing_status", "statement_type"] }
      ], epsCompared));
      target.appendChild(epsBlock);
      hasContent = true;
    }

    var cashSection = firstObject(section.cash_flow);
    var cashflowsAll = financialRows(firstArray(cashSection.items, section.cashflows, section.cash_flow_history), ["period", "date", "quarter"]);
    var cashflows = twoYearRows(cashflowsAll, "cash");
    if (cashflows.length) {
      charts.appendChild(renderLineChart(
        "現金流量趨勢（近兩年可用期間）",
        "保留來源揭露的累計／年度口徑；正負方向以文字與數值同時呈現。",
        cashflows,
        ["period", "quarter", "date"],
        [
          { label: "營業活動", keys: ["operating", "operating_cash_flow", "cfo"] },
          { label: "投資活動", keys: ["investing", "investing_cash_flow", "cfi"] },
          { label: "籌資活動", keys: ["financing", "financing_cash_flow", "cff"] }
        ],
        "money"
      ));
      var cashBlock = subsection("現金流量（近兩年可用期間）");
      cashBlock.appendChild(simpleTable([
        { label: "期間", keys: ["period", "quarter", "date"] },
        { label: "營業活動", render: function (row) { return formatCompactMoney(pick(row, ["operating", "operating_cash_flow", "cfo"])); } },
        { label: "投資活動", render: function (row) { return formatCompactMoney(pick(row, ["investing", "investing_cash_flow", "cfi"])); } },
        { label: "籌資活動", render: function (row) { return formatCompactMoney(pick(row, ["financing", "financing_cash_flow", "cff"])); } },
        { label: "口徑", keys: ["basis", "period_type", "calculation_basis"] }
      ], cashflows));
      target.appendChild(cashBlock);
      hasContent = true;
    }

    var revenueSection = firstObject(section.monthly_revenue);
    var monthlyRevenueAll = financialRows(firstArray(revenueSection.items, section.revenues), ["month", "period", "date"]);
    var monthlyRevenue = twoYearRows(monthlyRevenueAll, "month");
    if (monthlyRevenue.length) {
      charts.appendChild(renderLineChart(
        "月營收趨勢（近兩年）",
        "以公司／資料來源揭露的月營收繪製；每個座標點直接標示金額。",
        monthlyRevenue,
        ["month", "period", "date"],
        [{ label: "月營收", keys: ["revenue", "amount"] }],
        "money"
      ));
      var revenueCompared = comparePeriodRows(monthlyRevenueAll, monthlyRevenue, "month", ["revenue", "amount"]);
      var revenueBlock = subsection("月營收（近兩年）");
      revenueBlock.appendChild(simpleTable([
        { label: "月份", keys: ["period", "month", "date"] },
        { label: "營收", render: function (row) { return formatCompactMoney(pick(row, ["revenue", "amount"])); } },
        { label: "去年同期營收", render: function (row) { return formatCompactMoney(row._previous_value); } },
        { label: "年增額", render: function (row) { return make("span", toneClass(row._delta), formatCompactMoney(row._delta)); } },
        { label: "年增率", render: function (row) {
          var value = row._yoy === null ? pick(row, ["yoy_pct", "yoy"]) : row._yoy;
          return make("span", toneClass(value), formatPercent(value));
        } },
        { label: "月增率", render: function (row) { return make("span", toneClass(pick(row, ["mom_pct", "mom"])), formatPercent(pick(row, ["mom_pct", "mom"]))); } },
        { label: "資料性質", keys: ["type", "filing_status"] }
      ], revenueCompared));
      target.appendChild(revenueBlock);
      hasContent = true;
    }
    var peSection = firstObject(section.pe);
    var peAll = monthlyPeRows(firstArray(peSection.items, section.pe_history, section.valuation_history));
    var peRows = twoYearRows(peAll, "pe");
    if (peRows.length) {
      charts.appendChild(renderPeRiverChart(peRows));
      var peCompared = comparePeriodRows(peAll, peRows, "month", ["pe", "pe_ratio"]);
      var peBlock = subsection("本益比明細（近兩年，每月最後一筆）");
      peBlock.appendChild(simpleTable([
        { label: "月份", render: function (row) { return displayText(row._month || pick(row, ["date", "period"])); } },
        { label: "本益比", render: function (row) {
          var value = pick(row, ["pe", "pe_ratio"]);
          return asNumber(value) === null ? "尚未取得" : formatNumber(value, 2) + " 倍";
        } },
        { label: "去年同期本益比", render: function (row) {
          return asNumber(row._previous_value) === null ? "尚未取得" : formatNumber(row._previous_value, 2) + " 倍";
        } },
        { label: "差異", render: function (row) { return make("span", toneClass(row._delta), formatSigned(row._delta, " 倍", 2)); } },
        { label: "年增率", render: function (row) { return make("span", toneClass(row._yoy), formatPercent(row._yoy)); } }
      ], peCompared));
      target.appendChild(peBlock);
      hasContent = true;
    }
    if (!charts.childNodes.length) {
      charts.appendChild(make("p", "missing chart-missing", "尚未取得可繪製的基本財務資料。"));
    }
    if (!hasContent) {
      appendMissing(target, pick(section, ["reason", "note"]) || "財報、月營收或估值歷史尚未完成來源驗證。 ");
    }
    renderInlineSources(byId("financialSources"), section);
  }

  function renderRevenueMix(section) {
    sectionStatus(section, "mixStatus");
    var target = byId("revenueMix");
    target.replaceChildren();
    var rows = firstArray(section.items, section.composition, section.products, section.applications, section.rows);
    var goodinfo = firstObject(section.goodinfo);
    var goodinfoBlock = null;
    if (goodinfo && !isMissing(goodinfo.summary)) {
      goodinfoBlock = subsection("Goodinfo 補充（未取代主要來源）");
      var goodinfoCard = make("article", "info-card");
      goodinfoCard.appendChild(make("p", null, goodinfo.summary));
      goodinfoCard.appendChild(make("small", "source-meta", "資料來源：Goodinfo；產品／應用名稱與比例仍以公司正式揭露為準。"));
      goodinfoBlock.appendChild(goodinfoCard);
    }
    if (!rows.length) {
      appendMissing(target, (goodinfo && goodinfo.reason) || pick(section, ["reason", "note"]) || "公司未揭露的細項比例不以模型推算；外部估計需具名並附日期。 ");
      if (goodinfoBlock) {
        target.insertBefore(goodinfoBlock, target.firstChild);
      }
      renderInlineSources(byId("mixSources"), section);
      return;
    }
    if (goodinfoBlock) {
      target.appendChild(goodinfoBlock);
    }
    var block = subsection("已揭露或具名估計的組合");
    block.appendChild(simpleTable([
      { label: "期間", keys: ["period", "date", "fiscal_period"] },
      { label: "產品／應用", keys: ["name", "category", "product", "application"] },
      { label: "占營收", render: function (row) { return formatPercent(pick(row, ["share_pct", "revenue_share_pct", "percentage"])); } },
      { label: "性質", render: function (row) {
        var value = displayText(pick(row, ["type", "claim_type", "data_type"]));
        return make("span", value === "尚未取得" ? "missing" : "type-badge", value);
      } },
      { label: "說明", keys: ["note", "description", "scope"] }
    ], rows));
    target.appendChild(block);
    renderInlineSources(byId("mixSources"), section);
  }

  function fillStatus(row) {
    var complete = pick(row, ["fill_complete", "filled", "is_filled"]);
    var days = pick(row, ["fill_days", "days_to_fill"]);
    var date = pick(row, ["first_fill_date", "fill_date"]);
    if (complete === true || String(complete).toLowerCase() === "true") {
      var detail = [!isMissing(date) ? date : null, asNumber(days) !== null ? days + " 日" : null].filter(Boolean).join(" · ");
      return "已完成" + (detail ? "（" + detail + "）" : "");
    }
    if (complete === false || String(complete).toLowerCase() === "false") {
      return "尚未完成";
    }
    return "尚未取得";
  }

  function renderDividendEvents(section) {
    sectionStatus(section, "dividendStatus");
    var target = byId("dividendEvents");
    target.replaceChildren();
    var rows = firstArray(section.events, section.items, section.history, section.rows);
    if (!rows.length) {
      appendMissing(target, "尚未取得近五年可驗證的除權息事件。 ");
      renderInlineSources(byId("dividendSources"), section);
      return;
    }
    var block = subsection("近五年事件");
    block.appendChild(simpleTable([
      { label: "年度", keys: ["year", "fiscal_year"] },
      { label: "現金股利", render: function (row) { var value = pick(row, ["cash_dividend", "cash_dividend_per_share", "cash"]); return asNumber(value) === null ? "尚未取得" : "NT$ " + formatNumber(value, 3); } },
      { label: "股票股利", render: function (row) { return formatNumber(pick(row, ["stock_dividend", "stock_dividend_per_share", "stock"]), 3); } },
      { label: "除權息日", keys: ["ex_date", "ex_dividend_date", "cash_ex_dividend_date", "stock_ex_right_date"] },
      { label: "現金發放日", keys: ["payment_date", "cash_payment_date"] },
      { label: "除息前收盤", render: function (row) { var value = pick(row, ["pre_ex_close", "previous_close"]); return asNumber(value) === null ? "尚未取得" : "NT$ " + formatNumber(value, 2); } },
      { label: "官方參考價", render: function (row) { var value = pick(row, ["reference_price", "official_reference_price"]); return asNumber(value) === null ? "尚未取得" : "NT$ " + formatNumber(value, 2); } },
      { label: "歷史殖利率", render: function (row) { return formatPercent(pick(row, ["historical_yield_pct", "yield_pct"])); } },
      { label: "收盤填權息", render: fillStatus },
      { label: "盤中觸及", keys: ["intraday_touch_date", "first_intraday_fill_date"] }
    ], rows));
    target.appendChild(block);
    renderInlineSources(byId("dividendSources"), section);
  }

  function normalizeNews(section) {
    var official = firstArray(section.official, section.announcements, section.company_announcements);
    var media = firstArray(section.media, section.reports, section.media_reports);
    var all = firstArray(section.items, section.news);
    all.forEach(function (item) {
      var category = String(pick(item, ["category", "type", "kind"]) || "").toLowerCase();
      if (category.indexOf("official") >= 0 || category.indexOf("announce") >= 0 ||
          category.indexOf("company") >= 0 || category.indexOf("公告") >= 0 || category.indexOf("公司") >= 0) {
        official.push(item);
      } else {
        media.push(item);
      }
    });
    return { official: official, media: media };
  }

  function newsItem(item) {
    var article = make("article", "news-item");
    var heading = make("h3");
    var title = displayText(pick(item, ["title", "headline"]));
    var url = safeUrl(pick(item, ["url", "link", "original_url", "aggregator_url"]));
    if (url) {
      var link = make("a", null, title);
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      heading.appendChild(link);
    } else {
      heading.textContent = title;
    }
    article.appendChild(heading);
    var meta = make("div", "news-meta");
    var publisher = pick(item, ["source", "publisher", "source_name", "media_source"]);
    var published = pick(item, ["published_at", "first_published_at", "published"]);
    var eventDate = pick(item, ["event_date", "original_event_date"]);
    if (!isMissing(publisher)) {
      meta.appendChild(make("span", null, publisher));
    }
    if (!isMissing(published)) {
      meta.appendChild(make("span", null, "首發 " + formatDateTime(published)));
    }
    if (!isMissing(eventDate)) {
      meta.appendChild(make("span", null, "事件日 " + eventDate));
    }
    if (meta.childNodes.length) {
      article.appendChild(meta);
    }
    var reason = pick(item, ["relevance_reason", "reason", "summary"]);
    if (!isMissing(reason)) {
      article.appendChild(make("p", "news-reason", "相關性：" + reason));
    }
    return article;
  }

  function newsColumn(title, items) {
    var column = make("section");
    column.appendChild(make("h3", "news-column-title", title));
    var list = make("div", "news-list");
    if (!items.length) {
      list.appendChild(make("p", "missing", "這段期間尚未取得相關內容。"));
    } else {
      items.forEach(function (item) { list.appendChild(newsItem(item)); });
    }
    column.appendChild(list);
    return column;
  }

  function renderNews(section) {
    var target = byId("newsItems");
    target.replaceChildren();
    var normalized = normalizeNews(section);
    var columns = make("div", "news-columns");
    columns.appendChild(newsColumn("公司公告與官方資訊", normalized.official));
    columns.appendChild(newsColumn("媒體報導", normalized.media));
    target.appendChild(columns);
    renderInlineSources(byId("newsSources"), section);
  }

  function handleControl(button) {
    var control = button.getAttribute("data-control");
    var value = button.getAttribute("data-value");
    if (control === "view") {
      state.view = value === "theme" ? "theme" : "industry";
      state.group = null;
      state.stock = null;
      state.sortKey = "turnover_share_change_pp";
      state.sortDirection = "desc";
      updateUrl(true);
      loadOverview();
    } else if (control === "window") {
      var period = Number(value);
      if (VALID_WINDOWS.indexOf(period) >= 0) {
        state.window = period;
        updateUrl(true);
        state.stock ? loadStock() : loadOverview();
      }
    } else if (control === "weight") {
      state.weight = value === "equal" ? "equal" : "market_cap";
      updateUrl(true);
      state.stock ? loadStock() : loadOverview();
    } else if (control === "news") {
      var days = Number(value);
      if (VALID_NEWS_DAYS.indexOf(days) >= 0) {
        state.newsDays = days;
        updateUrl(true);
        loadStock();
      }
    }
  }

  function bindEvents() {
    document.addEventListener("click", function (event) {
      var control = event.target.closest("[data-control]");
      if (control) {
        handleControl(control);
      }
    });
    byId("clearGroup").addEventListener("click", function () {
      state.group = null;
      updateUrl(true);
      loadOverview();
    });
    byId("backToOverview").addEventListener("click", function () {
      state.stock = null;
      updateUrl(true);
      loadOverview();
    });
    byId("retryButton").addEventListener("click", function () {
      state.stock ? loadStock() : loadOverview();
    });
    window.addEventListener("popstate", function () {
      parseStateFromUrl();
      updateControlStates();
      state.stock ? loadStock() : loadOverview();
    });
    var resizeTimer = null;
    window.addEventListener("resize", function () {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () {
        if (!byId("overviewPage").hidden && state.rows.length) {
          renderHeatmap(state.rows, state.rowsAreGroups);
        }
      }, 160);
    });
  }

  function start() {
    parseStateFromUrl();
    bindEvents();
    updateControlStates();
    updateUrl(false);
    if (state.stock) {
      loadStock();
    } else {
      loadOverview();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
}());

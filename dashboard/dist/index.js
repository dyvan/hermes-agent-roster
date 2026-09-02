/**
 * Agent Roster — Hermes Dashboard Plugin
 *
 * Agent identity cards derived from real session data (grouped by system
 * prompt), plus a chronological session timeline with inline transcripts,
 * token counts and per-session cost. Backend: /api/plugins/agent-roster/.
 *
 * Plain IIFE, no build step. Uses window.__HERMES_PLUGIN_SDK__ for React +
 * the core API client (session transcripts come from /api/sessions).
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React, api, fetchJSON } = SDK;
  const h = React.createElement;
  const { useState, useEffect, useCallback, useMemo, useRef } = SDK.hooks;

  const BASE = "/api/plugins/agent-roster";
  const POLL_MS = 15000;

  // ---------------------------------------------------------------------
  // Formatting helpers
  // ---------------------------------------------------------------------

  function fmtCost(usd) {
    if (!usd) return "$0";
    if (usd < 0.01) return "$" + usd.toFixed(4);
    if (usd < 1) return "$" + usd.toFixed(3);
    return "$" + usd.toFixed(2);
  }

  function fmtDur(seconds) {
    const s = Math.round(seconds || 0);
    if (s < 60) return s + "s";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m " + (s % 60 ? (s % 60) + "s" : "").trim();
    const hrs = Math.floor(m / 60);
    return hrs + "h " + (m % 60 ? String(m % 60).padStart(2, "0") : "00");
  }

  function fmtTok(n) {
    if (!n) return "0";
    if (n < 1000) return String(n);
    if (n < 1000000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return (n / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
  }

  function fmtClock(ts) {
    const d = new Date(ts * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function dayLabel(ts) {
    const d = new Date(ts * 1000);
    const today = new Date();
    const yest = new Date(today.getTime() - 86400000);
    const sameDay = (a, b) => a.toDateString() === b.toDateString();
    const fmt = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    if (sameDay(d, today)) return "Today — " + fmt;
    if (sameDay(d, yest)) return "Yesterday — " + fmt;
    return fmt;
  }

  function dayKey(ts) {
    return new Date(ts * 1000).toDateString();
  }

  // Long tool outputs: show head + tail with an omitted-lines marker instead
  // of a blunt truncation (pattern borrowed from the langfuse exporter).
  function headTail(s) {
    s = String(s);
    if (s.length <= 2400) return s;
    const head = s.slice(0, 1400);
    const tail = s.slice(-700);
    const omitted = s.slice(1400, -700).split("\n").length;
    return head + "\n… " + omitted + " lines omitted …\n" + tail;
  }

  // ---------------------------------------------------------------------
  // Small components
  // ---------------------------------------------------------------------

  // Custom avatar images are fetched once per agent (authed) and cached as
  // object URLs for the lifetime of the page.
  const _avatarCache = {};
  function _fetchAvatar(key) {
    if (!_avatarCache[key]) {
      const token = window.__HERMES_SESSION_TOKEN__;
      _avatarCache[key] = fetch(BASE + "/identity/" + encodeURIComponent(key) + "/avatar",
        { headers: token ? { Authorization: "Bearer " + token } : {} })
        .then((r) => (r.ok ? r.blob() : null))
        .then((b) => (b ? URL.createObjectURL(b) : null))
        .catch(() => null);
    }
    return _avatarCache[key];
  }

  function Avatar({ agent, size }) {
    const cls = "ar-avatar ar-avatar-" + (size || "sm");
    const [img, setImg] = useState(null);
    useEffect(() => {
      let alive = true;
      if (agent.avatar.custom) _fetchAvatar(agent.key).then((u) => { if (alive) setImg(u); });
      else setImg(null);
      return () => { alive = false; };
    }, [agent.key, agent.avatar.custom]);
    if (img) {
      // Colored ring keeps agents distinguishable at a glance even when
      // custom avatars share an art style.
      return h("div", { className: cls + " ar-avatar-img", "aria-hidden": "true",
        style: { "--ar-ring": agent.avatar.from } },
        h("img", { src: img, alt: "" }));
    }
    const grad = "linear-gradient(135deg," + agent.avatar.from + "," + agent.avatar.to + ")";
    return h("div", { className: cls, style: { background: grad }, "aria-hidden": "true" },
      (agent.name || "?").charAt(0).toUpperCase());
  }

  function PeriodToggle({ period, onChange }) {
    const opts = [["day", "Today"], ["week", "Week"], ["month", "Month"]];
    return h("div", { className: "ar-period", role: "group", "aria-label": "Stats period" },
      opts.map(([key, label]) =>
        h("button", { key, "aria-pressed": String(period === key), onClick: () => onChange(key) }, label)));
  }

  // ---------------------------------------------------------------------
  // Roster card (front stats + expandable back with prompt & identity form)
  // ---------------------------------------------------------------------

  function IdentityForm({ agent, onSaved }) {
    const [name, setName] = useState(agent.name || "");
    const [tagline, setTagline] = useState(agent.tagline || "");
    const [skills, setSkills] = useState((agent.skills || []).join(", "));
    const [busy, setBusy] = useState(false);

    const save = useCallback(() => {
      setBusy(true);
      fetchJSON(BASE + "/identity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key: agent.key,
          name: name.trim(),
          tagline: tagline.trim(),
          skills: skills.split(",").map((s) => s.trim()).filter(Boolean),
        }),
      }).then(() => { setBusy(false); onSaved(); })
        .catch(() => setBusy(false));
    }, [agent.key, name, tagline, skills, onSaved]);

    return h("div", { className: "ar-form" },
      h("input", { value: name, placeholder: "Agent name", "aria-label": "Agent name",
        onChange: (e) => setName(e.target.value) }),
      h("input", { value: tagline, placeholder: "Tagline — what this agent does", "aria-label": "Tagline",
        onChange: (e) => setTagline(e.target.value) }),
      h("input", { value: skills, placeholder: "Skills, comma separated", "aria-label": "Skills",
        onChange: (e) => setSkills(e.target.value) }),
      h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", "aria-label": "Avatar image",
        onChange: (e) => {
          const file = e.target.files && e.target.files[0];
          if (!file) return;
          // Downscale to 256px so the stored avatar stays small.
          const img = new Image();
          img.onload = () => {
            const s = Math.min(1, 256 / Math.max(img.width, img.height));
            const cv = document.createElement("canvas");
            cv.width = Math.round(img.width * s); cv.height = Math.round(img.height * s);
            cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
            URL.revokeObjectURL(img.src);
            fetchJSON(BASE + "/identity/" + encodeURIComponent(agent.key) + "/avatar", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ image: cv.toDataURL("image/png") }),
            }).then(() => { delete _avatarCache[agent.key]; onSaved(); }).catch(() => {});
          };
          img.src = URL.createObjectURL(file);
        } }),
      h("div", { className: "ar-form-row" },
        h("button", { className: "ar-btn", disabled: busy, onClick: save }, busy ? "Saving…" : "Save identity"),
        agent.customized ? h("button", {
          className: "ar-btn-ghost",
          onClick: () => fetchJSON(BASE + "/identity", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: agent.key, reset: true }),
          }).then(onSaved).catch(() => {}),
        }, "Reset") : null));
  }

  function RosterCard({ agent, period, onFilter, onSaved }) {
    const [flipped, setFlipped] = useState(false);
    const [editing, setEditing] = useState(false);
    const [prompt, setPrompt] = useState(null);
    const st = agent.stats[period];

    useEffect(() => {
      if (flipped && prompt === null) {
        fetchJSON(BASE + "/prompt/" + encodeURIComponent(agent.key))
          .then((r) => setPrompt(r.system_prompt || ""))
          .catch(() => setPrompt(""));
      }
    }, [flipped, prompt, agent.key]);

    return h("div", { className: "ar-card" },
      h("div", { className: "ar-card-top" },
        h(Avatar, { agent, size: "lg" }),
        h("div", null,
          h("div", { className: "ar-name" }, agent.name),
          h("div", { className: "ar-tagline" },
            agent.tagline || (agent.models[0] ? agent.models[0] : Object.keys(agent.sources).join(", "))))),
      agent.skills.length
        ? h("div", { className: "ar-skills" }, agent.skills.map((s) => h("span", { key: s, className: "ar-skill" }, s)))
        : null,
      h("div", { className: "ar-statrow" },
        h("div", { className: "ar-stat" }, h("b", null, st.runs), h("span", null, "runs")),
        h("div", { className: "ar-stat" }, h("b", null, fmtDur(st.active_seconds)), h("span", null, "active")),
        h("div", { className: "ar-stat" }, h("b", { className: "ar-cost" }, fmtCost(st.cost_usd)), h("span", null, "cost"))),
      flipped ? h("div", { className: "ar-card-back" },
        h("div", { className: "ar-back-label" }, "Models observed (most used first)"),
        h("div", { className: "ar-models" },
          agent.models.slice(0, 4).flatMap((m, i) => [
            i ? h("span", { key: m + "-arr", className: "ar-arr" }, "→") : null,
            h("span", { key: m, className: "ar-m" }, m),
          ])),
        (agent.model_usage && agent.model_usage.length)
          ? h("div", null,
              h("div", { className: "ar-back-label" }, "Cost by model (30 days)"),
              h("table", { className: "ar-mu" }, h("tbody", null,
                agent.model_usage.map((u) =>
                  h("tr", { key: u.model },
                    h("td", null, u.model),
                    h("td", { className: "ar-mono" }, fmtTok(u.tokens)),
                    h("td", { className: "ar-mono ar-mu-cost" }, fmtCost(u.cost_usd)))))))
          : null,
        h("div", { className: "ar-back-label" }, "System prompt"),
        prompt === null
          ? h("div", { className: "ar-loading" }, "Loading…")
          : h("pre", { className: "ar-pre" }, prompt || "(no system prompt recorded)"),
        h("div", { className: "ar-back-label" }, "Identity"),
        editing
          ? h(IdentityForm, { agent, onSaved: () => { setEditing(false); onSaved(); } })
          : h("button", { className: "ar-btn-ghost", onClick: () => setEditing(true) },
              agent.customized ? "Edit identity" : "Customize name, tagline & skills")) : null,
      h("div", { className: "ar-card-actions" },
        h("button", { onClick: () => setFlipped(!flipped), "aria-expanded": String(flipped) },
          flipped ? "Hide details" : "Details"),
        h("button", { onClick: () => onFilter(agent.key) }, "Sessions")));
  }

  // ---------------------------------------------------------------------
  // Image gallery for external sessions (authenticated fetch → blob URLs,
  // since a plain <img> tag cannot carry the dashboard session token)
  // ---------------------------------------------------------------------

  function ExtGallery({ session }) {
    const [urls, setUrls] = useState([]);
    const [zoom, setZoom] = useState(null);

    useEffect(() => {
      let alive = true;
      const created = [];
      const token = window.__HERMES_SESSION_TOKEN__;
      const base = BASE + "/external/" + encodeURIComponent(session.id.slice(4)) + "/image/";
      Promise.all(Array.from({ length: session.image_count }, (_, i) =>
        fetch(base + i, { headers: token ? { Authorization: "Bearer " + token } : {} })
          .then((r) => (r.ok ? r.blob() : null))
          .then((b) => { if (b) { const u = URL.createObjectURL(b); created.push(u); return u; } return null; })
          .catch(() => null)
      )).then((all) => { if (alive) setUrls(all.filter(Boolean)); });
      return () => { alive = false; created.forEach((u) => URL.revokeObjectURL(u)); };
    }, [session.id, session.image_count]);

    if (!urls.length) return null;
    return h("div", { className: "ar-gallery" },
      urls.map((u, i) =>
        h("img", {
          key: i, src: u, alt: "generated image " + (i + 1), loading: "lazy",
          className: zoom === i ? "ar-zoomed" : "",
          onClick: () => setZoom(zoom === i ? null : i),
        })));
  }

  // ---------------------------------------------------------------------
  // Transcript (chat view, fetched from the core sessions API)
  // ---------------------------------------------------------------------

  function Transcript({ session, agent, onGoto, hasChildren }) {
    const [messages, setMessages] = useState(null);
    const [openTool, setOpenTool] = useState(null);

    useEffect(() => {
      let alive = true;
      let load;
      if (session.external) {
        load = fetchJSON(BASE + "/external/" + encodeURIComponent(session.id.slice(4)) + "/messages");
      } else if (session.profile) {
        const rawId = session.id.split(":").slice(2).join(":");
        load = fetchJSON(BASE + "/profile/" + encodeURIComponent(session.profile) + "/messages/" + encodeURIComponent(rawId));
      } else {
        load = api.getSessionMessages(session.id);
      }
      load.then((r) => { if (alive) setMessages(r.messages || []); })
        .catch(() => { if (alive) setMessages([]); });
      return () => { alive = false; };
    }, [session.id, session.external]);

    // Pair tool outputs (role:"tool") with the assistant tool_calls that produced them.
    const view = useMemo(() => {
      if (!messages) return [];
      const outputs = {};
      messages.forEach((m) => {
        if (m.role === "tool" && m.tool_call_id) outputs[m.tool_call_id] = m.content || "";
      });
      return messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m, i) => ({
          idx: i,
          role: m.role,
          text: m.content || "",
          tools: (m.tool_calls || []).map((tc) => ({
            id: tc.id,
            name: (tc.function && tc.function.name) || "tool",
            out: outputs[tc.id],
          })),
        }))
        .filter((m) => m.text || m.tools.length);
    }, [messages]);

    return h("div", { className: "ar-transcript" },
      h("div", { className: "ar-t-head" },
        h("div", { className: "ar-kv" }, h("span", null, "Tokens in"), h("b", null, fmtTok(session.input_tokens))),
        h("div", { className: "ar-kv" }, h("span", null, "Tokens out"), h("b", null, fmtTok(session.output_tokens))),
        h("div", { className: "ar-kv" }, h("span", null, "Cost"), h("b", null, fmtCost(session.cost_usd))),
        h("div", { className: "ar-kv" }, h("span", null, "Model"), h("b", null, session.model || "—")),
        h("div", { className: "ar-spacer" }),
        session.parent_session_id
          ? h("button", { className: "ar-t-link", onClick: () => onGoto(session.parent_session_id) }, "↑ parent session")
          : null,
        hasChildren
          ? h("button", { className: "ar-t-link", onClick: () => onGoto(hasChildren) }, "↓ child session")
          : null),
      session.external && session.image_count ? h(ExtGallery, { session }) : null,
      h("div", { className: "ar-t-body" },
        messages === null
          ? h("div", { className: "ar-loading" }, "Loading transcript…")
          : view.length === 0
            ? h("div", { className: "ar-empty" }, "No readable messages in this session.")
            : view.map((m) =>
                h("div", { key: m.idx, className: "ar-msg " + (m.role === "user" ? "ar-msg-user" : "ar-msg-asst") },
                  h("div", { className: "ar-b" },
                    h("div", { className: "ar-who" }, m.role === "user" ? "USER" : (agent ? agent.name.toUpperCase() : "ASSISTANT")),
                    m.text,
                    m.tools.map((t) =>
                      h("div", { key: t.id },
                        h("button", {
                          className: "ar-toolchip",
                          onClick: () => setOpenTool(openTool === t.id ? null : t.id),
                          "aria-expanded": String(openTool === t.id),
                        }, "⚙ " + t.name),
                        openTool === t.id && t.out != null
                          ? h("pre", { className: "ar-tool-out" }, headTail(t.out))
                          : null)))))));
  }

  // ---------------------------------------------------------------------
  // Timeline
  // ---------------------------------------------------------------------

  function TimelineRow({ session, agent, child, open, onToggle, onGoto, childId }) {
    return h("div", {
      className: "ar-row" + (child ? " ar-row-child" : "") + (session.error ? " ar-row-err" : "") + (session.running ? " ar-row-run" : "") + (open ? " ar-row-open" : ""),
      "data-sid": session.id,
    },
      h("span", { className: "ar-dot" }),
      h("button", { className: "ar-row-btn", "aria-expanded": String(open), onClick: () => onToggle(session.id) },
        agent ? h(Avatar, { agent, size: "sm" }) : null,
        h("div", { className: "ar-row-main" },
          h("div", { className: "ar-row-title" }, session.title),
          h("div", { className: "ar-row-meta" },
            h("span", null, agent ? agent.name : session.source || "?"),
            h("span", null, fmtClock(session.started_at)),
            session.model ? h("span", { className: "ar-badge" }, session.model) : null,
            session.external ? h("span", { className: "ar-badge" }, "external") : null,
            session.running ? h("span", { className: "ar-badge ar-badge-run" }, "running") : null,
            session.error ? h("span", { className: "ar-badge ar-badge-err" }, "error") : null,
            h("span", null, session.message_count + " msgs · " + session.tool_call_count + " tool calls"))),
        h("div", { className: "ar-row-nums" },
          h("div", { className: "ar-c", title: session.cost_status ? "cost: " + session.cost_status : "" },
            session.cost_status === "included" ? "incl." : fmtCost(session.cost_usd)),
          h("div", { className: "ar-d" }, fmtDur(session.duration_seconds)))),
      open ? h(Transcript, { session, agent, onGoto, hasChildren: childId }) : null);
  }

  // ---------------------------------------------------------------------
  // Page
  // ---------------------------------------------------------------------

  function AgentRosterPage() {
    const [roster, setRoster] = useState(null);
    const [sessions, setSessions] = useState(null);
    const [period, setPeriod] = useState("week");
    const [filter, setFilter] = useState(null);
    const [openSid, setOpenSid] = useState(null);
    const [days, setDays] = useState(30);
    const timelineRef = useRef(null);

    const load = useCallback(() => {
      fetchJSON(BASE + "/roster").then((r) => setRoster(r.agents || [])).catch(() => setRoster([]));
      fetchJSON(BASE + "/timeline?days=" + days + (filter ? "&agent=" + encodeURIComponent(filter) : ""))
        .then((r) => setSessions(r.sessions || [])).catch(() => setSessions([]));
    }, [days, filter]);

    useEffect(() => {
      load();
      const t = setInterval(load, POLL_MS);
      return () => clearInterval(t);
    }, [load]);

    const agentsByKey = useMemo(() => {
      const m = {};
      (roster || []).forEach((a) => { m[a.key] = a; });
      return m;
    }, [roster]);

    // Group sessions by day; nest children (parent present in the list) under parents.
    const grouped = useMemo(() => {
      if (!sessions) return [];
      const byId = {};
      sessions.forEach((s) => { byId[s.id] = s; });
      const childrenOf = {};
      const top = [];
      sessions.forEach((s) => {
        if (s.parent_session_id && byId[s.parent_session_id]) {
          (childrenOf[s.parent_session_id] = childrenOf[s.parent_session_id] || []).push(s);
        } else {
          top.push(s);
        }
      });
      const groups = [];
      let current = null;
      top.forEach((s) => {
        const key = dayKey(s.started_at);
        if (!current || current.key !== key) {
          current = { key, label: dayLabel(s.started_at), items: [] };
          groups.push(current);
        }
        current.items.push({ session: s, children: childrenOf[s.id] || [] });
      });
      return groups;
    }, [sessions]);

    const toggle = useCallback((sid) => setOpenSid((cur) => (cur === sid ? null : sid)), []);
    const goto_ = useCallback((sid) => {
      setOpenSid(sid);
      requestAnimationFrame(() => {
        const el = document.querySelector('.ar-row[data-sid="' + sid + '"]');
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    }, []);

    return h("div", { className: "ar-root" },
      h("div", { className: "ar-head" },
        h("div", null,
          h("h1", { className: "ar-title" }, "Agents"),
          h("p", { className: "ar-sub" }, "Who worked, when, on what — and what it cost.")),
        h(PeriodToggle, { period, onChange: setPeriod })),

      roster === null
        ? h("div", { className: "ar-loading" }, "Loading agents…")
        : roster.length === 0
          ? h("div", { className: "ar-empty" }, "No sessions recorded yet — agents appear here as soon as Hermes runs.")
          : h("div", { className: "ar-roster" },
              roster.map((a) => h(RosterCard, {
                key: a.key, agent: a, period,
                onSaved: load,
                onFilter: (k) => {
                  setFilter(k);
                  requestAnimationFrame(() => {
                    if (timelineRef.current) timelineRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
                  });
                },
              }))),

      h("div", { className: "ar-section-h", ref: timelineRef },
        h("span", { className: "ar-eyebrow" }, "Session timeline")),
      h("div", { className: "ar-filters" },
        h("button", { className: "ar-fchip ar-all", "aria-pressed": String(filter === null), onClick: () => setFilter(null) }, "All agents"),
        (roster || []).map((a) =>
          h("button", { key: a.key, className: "ar-fchip", "aria-pressed": String(filter === a.key), onClick: () => setFilter(a.key) },
            h(Avatar, { agent: a, size: "xs" }), a.name)),
        h("button", {
          className: "ar-fchip ar-all",
          "aria-pressed": String(days === 30),
          onClick: () => setDays(days === 7 ? 30 : 7),
          title: "Toggle timeline window",
        }, days === 7 ? "Last 7 days" : "Last 30 days")),

      sessions === null
        ? h("div", { className: "ar-loading" }, "Loading timeline…")
        : grouped.length === 0
          ? h("div", { className: "ar-empty" }, "No sessions in this window.")
          : grouped.map((g) =>
              h(React.Fragment, { key: g.key },
                h("div", { className: "ar-day" }, g.label),
                h("div", { className: "ar-feed" },
                  g.items.flatMap(({ session, children }) => [
                    h(TimelineRow, {
                      key: session.id, session,
                      agent: agentsByKey[session.agent_key],
                      child: false,
                      open: openSid === session.id,
                      onToggle: toggle, onGoto: goto_,
                      childId: children.length ? children[0].id : null,
                    }),
                    children.map((c) => h(TimelineRow, {
                      key: c.id, session: c,
                      agent: agentsByKey[c.agent_key],
                      child: true,
                      open: openSid === c.id,
                      onToggle: toggle, onGoto: goto_,
                      childId: null,
                    })),
                  ])))));
  }

  window.__HERMES_PLUGINS__.register("agent-roster", AgentRosterPage);
})();

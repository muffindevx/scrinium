"""Scrinium documentation diagrams, drawn with the diagram-design skill.

Default skin, system fonts (no Google Fonts: Scrinium is local-first). To regenerate:

    python3 docs/diagrams/src/build.py

It writes one HTML file per diagram to docs/diagrams/src/html/ (ignored by git), runs the
skill's checks when the skill is installed, and exports each one to docs/diagrams/<slug>.svg
with an intrinsic width and height, so Markdown and <img> show it at its real size.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from dd import ACCENT, D, INK, KINDS, MONO, MUTED, PAPER, SANS, SOFT, c4, esc  # noqa: E402

HERE = pathlib.Path(__file__).parent
OUT = HERE / "html"
SVG_DIR = HERE.parent
SKILL = pathlib.Path.home() / ".claude/plugins/marketplaces/diagram-design"
OUT.mkdir(exist_ok=True)
built = []


def save(d):
    (OUT / f"{d.slug}.html").write_text(d.html(), encoding="utf-8")
    built.append(d.slug)


def small(d, cx, cy, text):
    """One-character cardinality label with a 12px mask."""
    d.labels.append(
        f'<rect x="{cx - 6}" y="{cy - 6}" width="12" height="12" rx="2" fill="{PAPER}"/>'
        f'<text x="{cx}" y="{cy + 3}" fill="{SOFT}" font-size="8" font-family="{MONO}" '
        f'text-anchor="middle">{text}</text>'
    )


# 1. Runtime architecture ------------------------------------------------------------------
d = D("runtime-architecture", 960, 504, "Scrinium on one machine",
      "Architecture of the Scrinium runtime: the browser talks to the API, background runners "
      "share one local model through LlmService, and data is kept in SQLite and LanceDB.",
      "Architecture")
d.zone(232, 48, 432, 240, "SCRINIUM RUNTIME · ONE PYTHON PROCESS")
d.zone(448, 304, 432, 128, "DATA DIRECTORY · 0700")
d.route([(184, 128), (256, 128)], "link")
d.hlabel(184, 256, 128, "HTTP + CSRF", "link")
d.route([(336, 160), (336, 200)])
d.vlabel(336, 160, 200, "SUBMITS")
d.route([(184, 232), (256, 232)])
d.hlabel(184, 256, 232, "READ ONLY")
d.route([(416, 232), (472, 232)])
d.hlabel(416, 472, 232, "TURNS")
d.route([(632, 232), (712, 232)], "link")
d.hlabel(632, 712, 232, "LOOPBACK", "link")
d.route([(376, 264), (376, 368), (472, 368)])
d.label(428, 354, "WRITES")
d.route([(336, 264), (336, 416), (776, 416), (776, 400)])
d.label(420, 402, "VECTORS")
d.node(40, 96, 144, 64, "input", "UI", "Browser or window", "compiled React UI")
d.node(40, 200, 144, 64, "input", "SRC", "Repos and folders", "read only")
d.node(256, 96, 160, 64, "backend", "API", "API + guard", "Host · Origin · session")
d.node(256, 200, 160, 64, "backend", "JOBS", "Background runners", "sync · ask · analysis")
d.node(472, 200, 160, 64, "focal", "GATE", "LlmService", "turns · memory guard")
d.node(712, 200, 168, 64, "backend", "PROC", "llama-server", "chat + embeddings")
d.node(472, 336, 160, 64, "store", "DB", "SQLite + FTS5", "scrinium.sqlite3")
d.node(696, 336, 160, 64, "store", "VEC", "LanceDB", "vectors/")
d.legend(456, [("input", "Input"), ("backend", "Component"), ("focal", "Shared model gate"),
               ("store", "Store"), ("arrow-link", "Local HTTP"), ("arrow-muted", "Internal call")])
save(d)

# 2. Trust boundaries (data-flow.md) ---------------------------------------------------------
d = D("data-boundaries", 960, 504, "What stays on your machine",
      "Data-flow boundaries: the browser, sources, local models, stores and keyring all sit on "
      "the user's machine; ticket sources and remote AI are planned and not built.",
      "Data flow")
d.zone(32, 40, 640, 392, "YOUR MACHINE · LISTENS ON 127.0.0.1 ONLY")
d.route([(208, 120), (352, 120), (352, 200)], "link")
d.hlabel(208, 344, 120, "LOOPBACK HTTP", "link")
d.route([(208, 232), (272, 232)])
d.hlabel(208, 272, 232, "READ ONLY")
d.route([(432, 232), (496, 232)], "link")
d.hlabel(432, 496, 232, "PROMPTS", "link")
d.route([(312, 264), (312, 336)])
d.vlabel(312, 264, 336, "WRITES")
d.route([(392, 264), (392, 300), (516, 300), (516, 336)])
d.label(454, 286, "TOKENS")
d.route([(408, 200), (408, 136), (720, 136)], dash=True)
d.label(600, 122, "PLANNED")
d.route([(432, 248), (464, 248), (464, 280), (720, 280)], dash=True)
d.label(684, 266, "OPT-IN ONLY")
d.node(56, 88, 152, 64, "input", "UI", "Browser", "your session")
d.node(56, 200, 152, 64, "input", "SRC", "Repos and folders", "you choose them")
d.node(272, 200, 160, 64, "focal", "CORE", "Scrinium runtime", "API · jobs · checks")
d.node(496, 200, 152, 64, "backend", "LLM", "Local models", "llama-server · Ollama")
d.node(232, 336, 160, 64, "store", "DB", "SQLite + LanceDB", "data directory 0700")
d.node(440, 336, 152, 64, "store", "KEY", "OS keyring", "connector tokens")
d.node(720, 104, 200, 64, "optional", "PLAN", "GitHub · Jira · Linear", "planned · read only")
d.node(720, 248, 200, 64, "optional", "PLAN", "Remote AI", "planned · off by default")
d.legend(456, [("input", "Input"), ("focal", "Scrinium runtime"), ("backend", "Local process"),
               ("store", "Store"), ("optional", "Planned, not built"), ("arrow-link", "Local HTTP"),
               ("arrow-muted-dash", "Planned flow")])
save(d)

# 3. Sync ------------------------------------------------------------------------------------
d = D("sync-flow", 960, 408, "Syncing a source",
      "Sync flow: a source is read and filtered, new revisions, chunks and code symbols are "
      "written in one transaction, then vectors and the rule watch follow.",
      "Data flow")
d.zone(368, 40, 416, 288, "ONE TRANSACTION")
d.route([(168, 184), (208, 184)])
d.hlabel(168, 208, 184, "READ")
d.route([(344, 184), (392, 184)])
d.route([(536, 172), (576, 172), (576, 104), (616, 104)])
d.route([(536, 196), (576, 196), (576, 264), (616, 264)])
d.route([(760, 104), (816, 104)], dash=True)
d.hlabel(760, 816, 104, "LATER")
d.route([(760, 264), (816, 264)])
d.hlabel(760, 816, 264, "AFTER")
d.node(40, 152, 128, 64, "input", "SRC", "Source", "git cat-file")
d.node(208, 152, 136, 64, "backend", "PLAN", "build_plan", "excludes · secrets")
d.node(392, 152, 144, 64, "focal", "REV", "Revisions", "only if content changed")
d.node(616, 72, 144, 64, "store", "FTS", "Chunks + FTS5", "60–80 lines")
d.node(616, 232, 144, 64, "store", "SYM", "Code symbols", "tree-sitter")
d.node(816, 72, 120, 64, "optional", "VEC", "Vectors", "LanceDB")
d.node(816, 232, 120, 64, "backend", "WATCH", "Rule watch", "code changed?")
d.legend(360, [("input", "Source"), ("backend", "Step"), ("focal", "Citable revision"),
               ("store", "Store"), ("optional", "Derived later"), ("arrow-muted", "Flow"),
               ("arrow-muted-dash", "Later / async")])
save(d)

# 4. Question --------------------------------------------------------------------------------
d = D("question-flow", 960, 400, "Answering a question",
      "Question flow: three searches are merged by rank fusion, the best sources go to the model, "
      "and each claim is kept only if it cites a source it received.",
      "Data flow")
d.route([(160, 168), (188, 168), (188, 88), (216, 88)])
d.route([(160, 184), (216, 184)])
d.route([(160, 200), (188, 200), (188, 280), (216, 280)])
d.route([(360, 88), (384, 88), (384, 168), (408, 168)])
d.route([(360, 184), (408, 184)])
d.route([(360, 280), (384, 280), (384, 200), (408, 200)])
d.route([(536, 184), (576, 184)])
d.route([(720, 184), (776, 184)])
d.hlabel(720, 776, 184, "PROMPT")
d.route([(848, 152), (848, 104)], "accent")
d.vlabel(848, 104, 152, "CITED", "accent")
d.route([(848, 216), (848, 264)], dash=True)
d.vlabel(848, 216, 264, "NO CITATION")
d.node(40, 152, 120, 64, "input", "ASK", "Question", "stored redacted")
d.node(216, 56, 144, 64, "store", "SYM", "Function names", "code_symbols")
d.node(216, 152, 144, 64, "store", "FTS", "Exact words", "FTS5")
d.node(216, 248, 144, 64, "store", "VEC", "Meaning", "LanceDB vectors")
d.node(408, 152, 128, 64, "backend", "RRF", "Rank fusion", "k = 60 · top 16")
d.node(576, 152, 144, 64, "backend", "CTX", "Select sources", "≤ 8 · 18 000 chars")
d.node(776, 152, 144, 64, "backend", "LLM", "Model + schema", "sources in markers")
d.node(776, 40, 144, 64, "focal", "OK", "Answer", "with citations")
d.node(776, 264, 144, 64, "optional", "FIND", "Finding", "unsupported_claim")
d.legend(352, [("input", "Input"), ("store", "Ranking"), ("backend", "Step"), ("focal", "Answer"),
               ("optional", "Finding to review"), ("arrow-accent", "Cited claim"),
               ("arrow-muted-dash", "Dropped claim")])
save(d)

# 5. Extraction phase 1 ----------------------------------------------------------------------
d = D("extraction-map", 960, 344, "Phase 1: from code to a rule",
      "Rule extraction phase 1: function symbols are scored by decision lines, cut into windows, "
      "queued, batched, and answered by the model; each rule is stored with one citation.",
      "Data flow")
d.route([(200, 88), (280, 88)])
d.hlabel(200, 280, 88, "ON SYNC")
d.route([(440, 88), (520, 88)])
d.hlabel(440, 520, 88, "PER FUNCTION")
d.route([(680, 88), (760, 88)])
d.hlabel(680, 760, 88, "DECISIONS")
d.route([(840, 120), (840, 200)])
d.vlabel(840, 120, 200, "FINGERPRINT")
d.route([(760, 232), (680, 232)])
d.hlabel(680, 760, 232, "PENDING")
d.route([(520, 232), (440, 232)])
d.hlabel(440, 520, 232, "PROMPT")
d.route([(280, 232), (200, 232)], "accent")
d.hlabel(200, 280, 232, "VALIDATED", "accent")
d.node(40, 56, 160, 64, "input", "SRC", "Repository", "Git or folder")
d.node(280, 56, 160, 64, "store", "SYM", "Function symbols", "tree-sitter · logic_hash")
d.node(520, 56, 160, 64, "backend", "F10", "Candidates", "score ≥ 2 decision lines")
d.node(760, 56, 160, 64, "backend", "CUT", "±6-line windows", "functions over 40 lines")
d.node(760, 200, 160, 64, "store", "QUEUE", "Task queue", "skips unchanged work")
d.node(520, 200, 160, 64, "backend", "BATCH", "Batches", "8000 chars · 24 groups")
d.node(280, 200, 160, 64, "backend", "LLM", "Model + grammar", "one answer per decision")
d.node(40, 200, 160, 64, "focal", "OUT", "Rule + 1 citation", "lines of its decision")
d.legend(296, [("input", "Source"), ("store", "Stored data"), ("backend", "Step"),
               ("focal", "Stored rule"), ("arrow-muted", "Flow"), ("arrow-accent", "Rule written")])
save(d)

# 6. Extraction phase 2 ----------------------------------------------------------------------
d = D("extraction-statements", 960, 320, "Phase 2: writing the statement",
      "Rule extraction phase 2: a background job or an opened rule sends the citation with four "
      "lines of context to the model, which writes one sentence stored as a new version.",
      "Data flow")
d.route([(216, 88), (256, 88), (256, 140), (296, 140)])
d.route([(216, 216), (256, 216), (256, 164), (296, 164)])
d.route([(472, 152), (536, 152)])
d.hlabel(472, 536, 152, "12 RULES")
d.route([(712, 152), (776, 152)], "accent")
d.hlabel(712, 776, 152, "SENTENCE", "accent")
d.node(40, 56, 176, 64, "backend", "LOW", "Background job", "after the map · yields")
d.node(40, 184, 176, 64, "input", "UI", "Opened rule", "on demand · goes first")
d.node(296, 120, 176, 64, "backend", "CTX", "Citation ±4 lines", "≤ 16 lines · 12 per batch")
d.node(536, 120, 176, 64, "backend", "LLM", "Model + grammar", "one sentence ≤ 160 chars")
d.node(776, 120, 144, 64, "focal", "OUT", "New version", "rules-detail-1")
d.legend(272, [("backend", "Step"), ("input", "On demand"), ("focal", "Stored version"),
               ("arrow-muted", "Flow"), ("arrow-accent", "Statement written")])
save(d)

# 7. Model queue -----------------------------------------------------------------------------
d = D("model-queue", 960, 424, "Who uses the model, and in what order",
      "Fan-in queue: three triggers feed one analysis thread that sends one batch at a time to "
      "the single model slot; questions go first and phase 2 yields its turn.",
      "Fan-in queue")
d.route([(200, 80), (232, 80), (232, 152), (264, 152)])
d.route([(200, 176), (264, 176)])
d.route([(200, 272), (232, 272), (232, 200), (264, 200)])
d.route([(472, 128), (472, 100), (296, 100), (296, 128)], dash=True)
d.label(384, 86, "PHASE 2 YIELDS")
d.route([(504, 176), (616, 176)])
d.hlabel(504, 616, 176, "ONE BATCH")
d.route([(696, 288), (696, 208)], "accent")
d.vlabel(696, 208, 288, "GOES FIRST", "accent")
d.route([(776, 176), (808, 176)])
d.node(40, 48, 160, 64, "input", "UI", "Extract button", "Rules screen")
d.node(40, 144, 160, 64, "input", "SYNC", "After a sync", "only if extracted before")
d.node(40, 240, 160, 64, "input", "BOOT", "At startup", "pending work")
d.node(264, 128, 240, 96, "store", "FIFO", "AnalysisRunner", "1 thread · one job at a time")
d.node(616, 144, 160, 64, "focal", "1 SLOT", "Local model", "llama-server")
d.node(616, 288, 160, 64, "input", "ASK", "Question or rule", "interactive mode")
d.node(808, 144, 120, 64, "store", "DB", "Rules", "map · statements")
d.legend(376, [("input", "Trigger"), ("store", "Queue / store"), ("focal", "Bottleneck"),
               ("arrow-accent", "Interactive, first"), ("arrow-muted-dash", "Yields its turn")])
save(d)

# 8. Rule anatomy (nested) -------------------------------------------------------------------
d = D("rule-anatomy", 960, 424, "What is read, and what is kept, from one function",
      "Nested view of one 90-line function: the model reads only windows around decisions, phase 2 "
      "reads the citation plus four lines, and only the citation is stored, with no link between rules.",
      "Nested")


def ring(x, y, w, h, label, stroke, fill):
    lw = c4(len(label) * 7 * 0.76 + 12)
    d.zones.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        f'<rect x="{x + 12}" y="{y - 6}" width="{lw}" height="12" rx="2" fill="{PAPER}"/>'
        f'<text x="{x + 12 + lw / 2}" y="{y + 3}" fill="{SOFT}" font-size="7" font-family="{MONO}" '
        f'text-anchor="middle" letter-spacing="0.14em">{esc(label)}</text>'
    )


ring(40, 40, 880, 344, "FUNCTION · 90 LINES · BOUNDS KNOWN IN CODE_SYMBOLS · NOT STORED WITH THE RULE",
     "rgba(45,49,66,0.35)", "rgba(45,49,66,0.015)")
ring(72, 80, 464, 280, "±6-LINE WINDOW · LINES 8–23 · WHAT PHASE 1 READS", MUTED, "rgba(45,49,66,0.025)")
ring(104, 120, 400, 208, "PHASE 2 CONTEXT · CITATION ±4 · LINES 10–21", INK, "rgba(45,49,66,0.03)")
ring(568, 80, 320, 128, "±6-LINE WINDOW · LINE 52", MUTED, "rgba(45,49,66,0.025)")
ring(568, 232, 320, 128, "±6-LINE WINDOW · LINES 70–71", MUTED, "rgba(45,49,66,0.025)")
d.node(136, 168, 336, 112, "focal", "CITE", "Rule citation · lines 14–17", "stored · the only lines the UI shows")
d.node(600, 116, 256, 64, "backend", "CITE", "Another rule", "own citation · no link")
d.node(600, 268, 256, 64, "backend", "CITE", "A third rule", "own citation · no link")
save(d)

# 9. Understanding layers (layer stack) ------------------------------------------------------
d = D("understanding-layers", 960, 376, "What it takes to explain the code",
      "Layer stack from rules up to the whole system, showing which pieces exist today, which are "
      "partial and which are missing; the function layer is the one to build next.",
      "Layer stack")
X0, X1 = 152, 912
rows = [
    ("L4", "System", [("optional", "System summary"), ("optional", "Architecture view")]),
    ("L3", "Modules", [("backend", "File paths"), ("optional", "Imports"), ("optional", "Module summary")]),
    ("L2", "Functions", [("backend", "Bounds + logic_hash"), ("input", "Calls by bare name"),
                         ("optional", "Resolved call graph"), ("optional", "Function summary")]),
    ("L1", "Rules", [("backend", "Rule + 1 citation"), ("optional", "Rule relationships"),
                     ("optional", "Business flows")]),
]
parts = [f'<rect x="{X0}" y="40" width="{X1 - X0}" height="256" fill="{PAPER}" stroke="{MUTED}" stroke-width="1"/>']
for i, (idx, name, chips) in enumerate(rows):
    y = 40 + 64 * i
    if name == "Functions":
        parts.append(f'<rect x="{X0}" y="{y}" width="{X1 - X0}" height="64" fill="rgba(191,69,32,0.08)" '
                     f'stroke="{ACCENT}" stroke-width="1.2"/>')
    elif i:
        parts.append(f'<line x1="{X0}" y1="{y}" x2="{X1}" y2="{y}" stroke="rgba(45,49,66,0.12)" stroke-width="1"/>')
    parts.append(f'<text x="{X0 + 16}" y="{y + 36}" fill="{ACCENT if name == "Functions" else SOFT}" font-size="8" '
                 f'font-family="{MONO}" letter-spacing="0.14em">{idx}</text>')
    parts.append(f'<text x="{X0 + 48}" y="{y + 37}" fill="{INK}" font-size="14" font-weight="600" '
                 f'font-family="{SANS}">{name}</text>')
    for j, (kind, text) in enumerate(chips):
        fill, stroke, dash, _, _ = KINDS[kind]
        da = f' stroke-dasharray="{dash}"' if dash else ""
        cx0 = 320 + j * 148
        parts.append(f'<rect x="{cx0}" y="{y + 18}" width="136" height="28" rx="4" fill="{PAPER}"/>'
                     f'<rect x="{cx0}" y="{y + 18}" width="136" height="28" rx="4" fill="{fill}" stroke="{stroke}" '
                     f'stroke-width="1"{da}/>'
                     f'<text x="{cx0 + 68}" y="{y + 36}" fill="{INK}" font-size="10" font-family="{SANS}" '
                     f'text-anchor="middle">{esc(text)}</text>')
parts.append(f'<text x="40" y="60" fill="{SOFT}" font-size="7" font-family="{MONO}" letter-spacing="0.14em">ABSTRACT</text>')
parts.append(f'<text x="40" y="284" fill="{SOFT}" font-size="7" font-family="{MONO}" letter-spacing="0.14em">CONCRETE</text>')
parts.append(f'<line x1="68" y1="268" x2="68" y2="76" stroke="{MUTED}" stroke-width="1" marker-end="url(#arrow)"/>')
d.raw_node("".join(parts))
d.legend(328, [("backend", "Exists today"), ("input", "Partial"), ("optional", "Missing"),
               ("focal", "Layer to build next")])
save(d)

# 10. Data model (ER) ------------------------------------------------------------------------
d = D("data-model", 960, 440, "Where sources, rules and citations live",
      "Entity model of the main tables: source revisions are immutable and feed chunks, symbols "
      "and the extraction queue; rules are versioned entities whose evidence cites a revision.",
      "ER")


def entity(x, y, w, name, fields, focal=False):
    h = c4(44 + 14 * len(fields) + 8)
    fill, stroke = ("rgba(191,69,32,0.08)", ACCENT) if focal else ("#ffffff", INK)
    tcol = ACCENT if focal else INK
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{PAPER}"/>'
         f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
         f'<rect x="{x + 8}" y="{y + 8}" width="36" height="12" rx="2" fill="transparent" stroke="{stroke}" '
         f'stroke-opacity="0.45" stroke-width="0.8"/>'
         f'<text x="{x + 26}" y="{y + 17}" fill="{tcol}" font-size="7" font-family="{MONO}" text-anchor="middle" '
         f'letter-spacing="0.08em">TABLE</text>'
         f'<text x="{x + w / 2}" y="{y + 36}" fill="{INK}" font-size="12" font-weight="600" font-family="{SANS}" '
         f'text-anchor="middle">{esc(name)}</text>'
         f'<line x1="{x}" y1="{y + 44}" x2="{x + w}" y2="{y + 44}" stroke="rgba(45,49,66,0.12)" stroke-width="1"/>')
    for i, f in enumerate(fields):
        s += (f'<text x="{x + 12}" y="{y + 60 + 14 * i}" fill="{MUTED}" font-size="9" font-family="{MONO}">'
              f'{esc(f)}</text>')
    d.raw_node(s)
    return h


entity(40, 40, 184, "content_chunks", ["→ source_revision_id", "text · start/end lines", "copied to FTS5 by triggers"])
entity(40, 168, 184, "code_symbols", ["→ source_revision_id", "name · qualified_name", "start/end lines",
                                      "logic_hash · calls"])
entity(40, 308, 184, "extraction_tasks", ["→ source_revision_id", "unit_key (fingerprint)", "status · attempts"])
entity(272, 160, 192, "source_revisions", ["# id", "text_content (immutable)", "content_hash", "path · commit"],
       focal=True)
entity(520, 172, 176, "evidence_records", ["→ source_revision_id", "→ target_version_id", "path · lines · commit",
                                           "excerpt (redacted)"])
entity(744, 40, 200, "knowledge_entities", ["canonical_key", "name · summary", "review_reason"])
entity(744, 172, 200, "knowledge_entity_versions", ["→ knowledge_entity_id", "version_number",
                                                    "extractor_version", "verification_status"])
entity(744, 316, 200, "business_rule_versions", ["title · statement", "scope · rationale",
                                                 "conditions (empty today)"])
for pts in ([(224, 88), (248, 88), (248, 188), (272, 188)], [(224, 212), (272, 212)],
            [(224, 356), (248, 356), (248, 236), (272, 236)], [(464, 220), (520, 220)],
            [(696, 220), (744, 220)], [(844, 136), (844, 172)], [(844, 280), (844, 316)]):
    d.route(pts, marker=False, width=1)
small(d, 234, 74, "N"); small(d, 262, 174, "1")
small(d, 236, 198, "N"); small(d, 262, 198, "1")
small(d, 234, 342, "N"); small(d, 262, 222, "1")
small(d, 476, 206, "1"); small(d, 508, 206, "N")
small(d, 708, 206, "N"); small(d, 732, 206, "1")
small(d, 858, 146, "1"); small(d, 858, 162, "N")
small(d, 858, 290, "1"); small(d, 858, 306, "1")
save(d)

# 11. Sign-in sequence -----------------------------------------------------------------------
d = D("sign-in", 960, 520, "Signing in with the launch link",
      "Sequence of the local sign-in: the runtime reserves its port, issues a single-use token, "
      "opens the link, redeems it once and grants a session cookie; writes then carry a CSRF token.",
      "Sequence")
for x in (168, 480, 792):
    d.zones.append(f'<line x1="{x}" y1="96" x2="{x}" y2="448" stroke="rgba(45,49,66,0.22)" stroke-width="1" '
                   f'stroke-dasharray="3,3"/>')
d.route([(168, 128), (204, 128), (204, 152), (172, 152)], r=6)
d.label(258, 140, "RESERVE PORT")
msgs = [
    (184, 168, 480, "muted", False, "ISSUE TOKEN · 120 S"),
    (224, 168, 792, "muted", True, "OPEN LAUNCH LINK"),
    (264, 792, 168, "link", False, "GET /AUTH/LAUNCH"),
    (304, 168, 480, "muted", False, "REDEEM ONCE"),
    (344, 168, 792, "accent", False, "303 + SESSION COOKIE"),
    (384, 792, 168, "link", False, "GET CSRF TOKEN"),
    (424, 792, 168, "link", False, "WRITES + CSRF HEADER"),
]
for y, x0, x1, col, dash, text in msgs:
    d.route([(x0, y), (x1, y)], col, dash=dash)
    lo, hi = sorted((x0, x1))
    if hi - lo > 400:
        lo = 480 if x0 == 168 and col != "muted" else (168 if x1 == 792 and col == "muted" else 480)
        lo, hi = (480, 792) if col != "muted" else (168, 480)
    d.label((lo + hi) / 2, y - 14, text, col if col != "muted" else "soft")
d.node(72, 40, 192, 56, "backend", "CLI", "scrinium start", "CLI + runtime")
d.node(384, 40, 192, 56, "store", "MEM", "Session manager", "memory only")
d.node(696, 40, 192, 56, "input", "UI", "Browser", "your tab")
d.legend(472, [("arrow-link", "Browser request"), ("arrow-muted", "Internal call"),
               ("arrow-muted-dash", "Opens the link"), ("arrow-accent", "Session granted")])
save(d)



def export():
    import re
    import subprocess

    scripts = SKILL / "skills/diagram-design/scripts"
    if not scripts.exists():
        print("diagram-design skill not found: HTML written, SVGs not exported")
        return
    html = [str(OUT / f"{slug}.html") for slug in built]
    subprocess.run([sys.executable, "-I", str(scripts / "self_check.py"), "--offline", *html], check=True)
    for path in html:
        subprocess.run([sys.executable, "-I", str(SKILL / "scripts/verify-geometry.py"), path], check=True)
    for slug in built:
        target = SVG_DIR / f"{slug}.svg"
        subprocess.run([sys.executable, "-I", str(scripts / "export_svg.py"), str(OUT / f"{slug}.html"),
                        str(target), "--system-fonts"], check=True, stdout=subprocess.DEVNULL)
        text = target.read_text(encoding="utf-8")
        w, h = re.search(r'<svg viewBox="0 0 (\d+) (\d+)"', text).groups()
        target.write_text(text.replace("<svg viewBox=", f'<svg width="{w}" height="{h}" viewBox=', 1),
                          encoding="utf-8")
    print("\n".join(built))


export()

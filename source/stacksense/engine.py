"""StackSense recommendation engine core.

Pipeline (all deterministic, no randomness, no LLM calls):

  1. validate()        schema + type + enum + range validation
  2. apply_defaults()  documented defaults for absent optional fields
  3. contradiction()   cross-field conflict detection with severities
  4. hard filters      data_residency / compliance / deployment_model /
                       cloud_preference remove ineligible candidates
  5. weighted scoring  priorities (1-5) x attribute fit + contextual
                       bonuses (team languages, experience, scale,
                       budget, timeline, project type)
  6. selection         highest score wins; ties broken by maturity then
                       alphabetical order - always reproducible
  7. explanation       justification + driven_by + alternatives +
                       trade-offs generated from the actual scores
  8. cost model        order-of-magnitude monthly band per component
  9. assemble          output JSON in the standard output schema
"""

from __future__ import annotations

from . import ENGINE_VERSION
from .kb import FRONTEND, BACKEND, DATABASE, HOSTING, SUPPORTING, DEPRECATED

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

PROJECT_TYPES = ["content_site", "web_app", "saas_b2b", "ecommerce", "marketplace", "social",
                 "realtime_collab", "fintech", "healthtech", "data_analytics", "iot",
                 "ai_ml_product", "internal_tool", "gaming", "api_platform", "other"]
PLATFORMS = ["web", "android", "ios", "desktop", "api_only"]
ENUMS = {
    "project.type": PROJECT_TYPES,
    "scale.traffic_pattern": ["steady", "spiky", "seasonal"],
    "scale.workload": ["read_heavy", "balanced", "write_heavy"],
    "team.experience": ["beginner", "intermediate", "senior", "mixed"],
    "infrastructure.cloud_preference": ["any", "aws", "gcp", "azure", "none"],
    "infrastructure.deployment_model": ["managed_cloud", "self_hosted_cloud_vm", "on_premise", "air_gapped"],
    "infrastructure.data_residency": ["none", "india", "eu", "us", "other"],
    "requirements.auth": ["none", "email_password", "social", "phone_otp", "enterprise_sso"],
    "requirements.ml_features": ["none", "inference_api", "llm_rag", "training"],
    "requirements.availability": ["99", "99.9", "99.95", "99.99"],
}
COMPLIANCE_ENUM = ["gdpr", "dpdp", "hipaa", "pci_dss", "rbi_localisation", "soc2", "iso27001"]
PRIORITY_KEYS = ["cost", "time_to_market", "scalability", "performance", "maintainability", "security"]

REQUIRED = ["project.type", "project.platforms", "scale.users_at_launch", "scale.users_12_months",
            "team.size", "team.experience", "team.languages", "budget.monthly_infra_usd",
            "timeline.mvp_weeks"]

DEFAULTS = {
    "project.name": "",
    "project.description": "",
    "scale.peak_concurrent_users": None,
    "scale.traffic_pattern": "steady",
    "scale.workload": "balanced",
    "scale.data_volume_gb_12_months": None,
    "team.has_devops": False,
    "infrastructure.cloud_preference": "any",
    "infrastructure.deployment_model": "managed_cloud",
    "infrastructure.data_residency": "none",
    "infrastructure.existing_systems": [],
    "requirements.realtime": False,
    "requirements.offline_support": False,
    "requirements.full_text_search": False,
    "requirements.file_media_storage": False,
    "requirements.payments": False,
    "requirements.auth": "none",
    "requirements.multi_tenant": False,
    "requirements.ml_features": "none",
    "requirements.seo_critical": False,
    "requirements.availability": "99.9",
    "requirements.p95_latency_ms": None,
    "compliance": [],
}
PRIORITY_DEFAULT = 3

DEFAULT_NOTES = {
    "scale.traffic_pattern": "scale.traffic_pattern absent; assumed 'steady'.",
    "scale.workload": "scale.workload absent; assumed 'balanced'.",
    "team.has_devops": "team.has_devops absent; assumed nobody operates infrastructure.",
    "infrastructure.cloud_preference": "infrastructure.cloud_preference absent; assumed 'any'.",
    "infrastructure.deployment_model": "infrastructure.deployment_model absent; assumed 'managed_cloud'.",
    "infrastructure.data_residency": "infrastructure.data_residency absent; assumed 'none'.",
    "requirements.availability": "requirements.availability absent; assumed '99.9'.",
    "requirements.auth": "requirements.auth absent; assumed 'none'.",
    "requirements.ml_features": "requirements.ml_features absent; assumed 'none'.",
}


def _get(d, path, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set(d, path, value):
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


# ---------------------------------------------------------------------------
# 1-2. Validation + defaults
# ---------------------------------------------------------------------------

def validate_and_fill(scenario):
    """Returns (filled, validation_errors, assumptions).

    validation_errors contains {field, message} for missing/invalid fields.
    assumptions lists every default that was applied.
    """
    errors = []
    assumptions = []
    if not isinstance(scenario, dict):
        return None, [{"field": "(root)", "message": "Scenario must be a JSON object."}], []
    filled = {k: (dict(v) if isinstance(v, dict) else v) for k, v in scenario.items()}

    def bad(path, msg):
        errors.append({"field": path, "message": msg})

    # required fields
    for path in REQUIRED:
        v = _get(filled, path)
        if v is None:
            bad(path, "Required field is missing.")

    # enums
    for path, allowed in ENUMS.items():
        v = _get(filled, path)
        if v is not None and v not in allowed:
            bad(path, f"Unknown value {v!r}; allowed: {', '.join(allowed)}.")

    # platforms: non-empty list of enums
    plats = _get(filled, "project.platforms")
    if plats is not None:
        if not isinstance(plats, list) or not plats:
            bad("project.platforms", "Must be a non-empty list.")
        else:
            for p in plats:
                if p not in PLATFORMS:
                    bad("project.platforms", f"Unknown platform {p!r}; allowed: {', '.join(PLATFORMS)}.")

    # integer ranges
    for path in ["scale.users_at_launch", "scale.users_12_months", "scale.peak_concurrent_users"]:
        v = _get(filled, path)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            bad(path, "Must be an integer >= 0.")
    v = _get(filled, "team.size")
    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
        bad("team.size", "Must be an integer >= 1.")
    v = _get(filled, "timeline.mvp_weeks")
    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
        bad("timeline.mvp_weeks", "Must be an integer >= 1.")
    v = _get(filled, "budget.monthly_infra_usd")
    if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0):
        bad("budget.monthly_infra_usd", "Must be a number >= 0.")
    v = _get(filled, "scale.data_volume_gb_12_months")
    if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0):
        bad("scale.data_volume_gb_12_months", "Must be a number >= 0.")
    v = _get(filled, "requirements.p95_latency_ms")
    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v <= 0):
        bad("requirements.p95_latency_ms", "Must be an integer > 0 or null.")

    # booleans
    for path in ["team.has_devops", "requirements.realtime", "requirements.offline_support",
                 "requirements.full_text_search", "requirements.file_media_storage",
                 "requirements.payments", "requirements.multi_tenant", "requirements.seo_critical"]:
        v = _get(filled, path)
        if v is not None and not isinstance(v, bool):
            bad(path, "Must be true or false.")

    # languages: list of strings
    langs = _get(filled, "team.languages")
    if langs is not None:
        if not isinstance(langs, list) or any(not isinstance(x, str) for x in langs):
            bad("team.languages", "Must be a list of language name strings.")

    # compliance: list of enums
    comp = _get(filled, "compliance")
    if comp is not None:
        if not isinstance(comp, list):
            bad("compliance", "Must be a list.")
        else:
            for c in comp:
                if c not in COMPLIANCE_ENUM:
                    bad("compliance", f"Unknown compliance regime {c!r}; allowed: {', '.join(COMPLIANCE_ENUM)}.")

    # priorities 1-5
    pri = _get(filled, "priorities")
    if pri is not None:
        if not isinstance(pri, dict):
            bad("priorities", "Must be an object of key: 1-5.")
        else:
            for k, v in pri.items():
                if k not in PRIORITY_KEYS:
                    bad(f"priorities.{k}", f"Unknown priority key {k!r}; allowed: {', '.join(PRIORITY_KEYS)}.")
                elif not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= 5):
                    bad(f"priorities.{k}", "Must be an integer between 1 and 5.")

    if errors:
        return None, errors, assumptions

    # defaults for absent optional fields
    for path, default in DEFAULTS.items():
        if _get(filled, path) is None and path not in ("scale.peak_concurrent_users",
                                                       "scale.data_volume_gb_12_months",
                                                       "requirements.p95_latency_ms"):
            _set(filled, path, default)
            if path in DEFAULT_NOTES:
                assumptions.append(DEFAULT_NOTES[path])
    pri = filled.setdefault("priorities", {})
    for k in PRIORITY_KEYS:
        if k not in pri:
            pri[k] = PRIORITY_DEFAULT
            assumptions.append(f"priorities.{k} absent; assumed 3 (neutral).")

    langs = [x.strip().lower() for x in (filled["team"].get("languages") or [])]
    filled["team"]["languages"] = langs

    return filled, [], assumptions


# ---------------------------------------------------------------------------
# 3. Contradiction / risk detection
# ---------------------------------------------------------------------------

def detect_conflicts(s):
    warnings = []
    users = s["scale"]["users_12_months"]
    budget = s["budget"]["monthly_infra_usd"]
    exp = s["team"]["experience"]
    size = s["team"]["size"]
    avail = s["requirements"]["availability"]
    weeks = s["timeline"]["mvp_weeks"]
    plats = s["project"]["platforms"]

    if users >= 100000 and budget == 0:
        warnings.append(dict(code="SCALE_BUDGET_CONFLICT", severity="critical",
                             message=(f"{users:,} expected users with a $0/month infrastructure budget cannot be "
                                      "served by any realistic stack; free tiers cap far below this load."),
                             fields=["scale.users_12_months", "budget.monthly_infra_usd"]))
    elif users >= 50000 and budget < 50:
        warnings.append(dict(code="SCALE_BUDGET_TIGHT", severity="warning",
                             message=(f"{users:,} expected users on ${budget}/month is very tight; the "
                                      "recommendation leans on free tiers that will be outgrown quickly."),
                             fields=["scale.users_12_months", "budget.monthly_infra_usd"]))

    if avail in ("99.95", "99.99") and (exp == "beginner" or (size <= 1 and not s["team"]["has_devops"])):
        warnings.append(dict(code="AVAILABILITY_TEAM_MISMATCH", severity="critical",
                             message=(f"Availability target {avail}% usually requires redundancy, failover and "
                                      f"on-call operations, which a {'beginner' if exp == 'beginner' else 'solo'} team "
                                      "without dedicated operations rarely sustains."),
                             fields=["requirements.availability", "team.experience", "team.size"]))

    if weeks <= 3 and len([p for p in plats if p != "api_only"]) >= 3:
        warnings.append(dict(code="TIMELINE_PLATFORM_SPREAD", severity="warning",
                             message=(f"{weeks} week(s) for {len(plats)} platforms is aggressive; a shared "
                                      "cross-platform codebase is assumed."),
                             fields=["timeline.mvp_weeks", "project.platforms"]))

    if s["requirements"]["offline_support"] and plats == ["web"]:
        warnings.append(dict(code="OFFLINE_WEB_ONLY", severity="warning",
                             message="Offline support on web-only requires a PWA with service workers and local "
                                     "storage sync; scope is limited compared with native mobile.",
                             fields=["requirements.offline_support", "project.platforms"]))

    if s["infrastructure"]["deployment_model"] == "air_gapped":
        for f in ["requirements.realtime", "requirements.payments", "requirements.ml_features"]:
            if (s["requirements"].get(f.split(".")[1]) not in (False, "none")):
                warnings.append(dict(code="AIR_GAPPED_SAAS_NEED", severity="warning",
                                     message=f"{f}=true on an air-gapped deployment rules out managed SaaS for this "
                                             "capability; self-hosted alternatives are selected where possible.",
                                     fields=[f, "infrastructure.deployment_model"]))

    for existing in s["infrastructure"]["existing_systems"]:
        key = str(existing).strip().lower()
        for dep, note in DEPRECATED.items():
            if key == dep or key.startswith(dep):
                warnings.append(dict(code="DEPRECATED_EXISTING_SYSTEM", severity="warning",
                                     message=f"Existing system '{existing}': {note}",
                                     fields=["infrastructure.existing_systems"]))
    return warnings


# ---------------------------------------------------------------------------
# 4-6. Filtering + scoring
# ---------------------------------------------------------------------------

def _lang_match(candidate, team_langs):
    cand = [l.lower() for l in candidate.get("languages", [])]
    hits = [l for l in cand if l in team_langs]
    return hits


def _score(candidate, s, layer):
    """Weighted priority score plus contextual bonuses. Deterministic."""
    pri = s["priorities"]
    base = (pri["cost"] * candidate["cost_efficiency"]
            + pri["time_to_market"] * candidate["ttm"]
            + pri["scalability"] * candidate["scalability"]
            + pri["performance"] * candidate["performance"]
            + pri["maintainability"] * candidate["maintainability"]
            + pri["security"] * candidate["security"]) / 5.0
    bonus = 0.0
    reasons = []

    langs = _lang_match(candidate, s["team"]["languages"])
    if layer in ("frontend", "backend"):
        if langs:
            bonus += 3.0
            reasons.append(f"matches team language(s): {', '.join(langs)}")
        elif candidate.get("languages") and s["team"]["languages"]:
            bonus -= 2.0
            reasons.append(f"requires {candidate['languages'][0]}, which the team does not list")

    exp = s["team"]["experience"]
    burden = candidate["ops_burden"]
    if exp == "beginner" and burden >= 4:
        bonus -= 4.0
        reasons.append("heavy operations for a beginner team")
    elif exp == "intermediate" and burden >= 5:
        bonus -= 2.0
        reasons.append("high operational burden for the team's experience")
    elif exp == "senior" and burden <= 1:
        bonus += 0.5
    if not s["team"]["has_devops"] and burden >= 4:
        bonus -= 2.0
        reasons.append("no DevOps capacity for a high-ops component")

    ptype = s["project"]["type"]
    if ptype in candidate.get("fit", []):
        bonus += 2.0
        reasons.append(f"well suited to {ptype}")
    if ptype in candidate.get("anti", []):
        bonus -= 3.0
        reasons.append(f"poor fit for {ptype}")

    users = s["scale"]["users_12_months"]
    if users >= 100000 and candidate["scalability"] <= 3:
        bonus -= 3.0
        reasons.append("not proven at the expected scale")
    elif users <= 5000 and candidate["scalability"] >= 5 and burden >= 4:
        bonus -= 2.0
        reasons.append("more scale machinery than this project needs")

    if layer == "backend" and s["requirements"]["realtime"]:
        rt = candidate.get("realtime", 0)
        if rt >= 4:
            bonus += 2.0
            reasons.append("strong realtime/WebSocket support")
        elif rt <= 2:
            bonus -= 2.0
            reasons.append("weak realtime support for a realtime requirement")

    return base + bonus, reasons


def _pick(candidates, s, layer):
    """Score, sort deterministically, return (winner, ranked)."""
    scored = []
    for c in candidates:
        sc, reasons = _score(c, s, layer)
        scored.append((sc, c, reasons))
    scored.sort(key=lambda t: (-t[0], -t[1]["maturity"], t[1]["name"]))
    return scored[0], scored


def _hard_filter_hosting(s, backend_cand=None, frontend_cand=None):
    dm = s["infrastructure"]["deployment_model"]
    residency = s["infrastructure"]["data_residency"]
    comp = s["compliance"]
    cloud = s["infrastructure"]["cloud_preference"]

    if "rbi_localisation" in comp:
        residency = "india"  # RBI data localisation forces India storage

    # Static/serverless platforms cannot run a long-running backend server
    # or a database container; they only fit when there is no custom backend
    # or the backend is itself serverless functions.
    static_only = {"vercel", "netlify", "firebase"}
    needs_server = backend_cand is not None and not backend_cand["name"].startswith("Serverless")
    needs_sockets = s["requirements"]["realtime"]

    out = []
    for h in HOSTING:
        if dm in ("on_premise", "air_gapped") and not h["self_hosted"]:
            continue
        if dm == "air_gapped" and h["provider"] != "onprem":
            continue
        if dm == "self_hosted_cloud_vm" and not h["self_hosted"] and h["provider"] not in ("aws", "gcp", "azure", "hetzner"):
            continue
        if dm == "managed_cloud" and h["provider"] == "onprem":
            continue
        if cloud in ("aws", "gcp", "azure") and h["provider"] != cloud:
            continue
        if needs_server and h["provider"] in static_only:
            continue
        # a long-running framework backend does not belong on Lambda-style
        # hosting; websockets cannot run there at all
        if needs_server and h["name"].startswith("AWS serverless"):
            continue
        if needs_sockets and h["name"].startswith("AWS serverless"):
            continue
        # Data residency is a hard constraint: the provider must actually
        # have a region in the required geography. 'other' residency is
        # accepted everywhere (it just means 'somewhere specific').
        if residency not in ("none", "other") and residency not in h["regions"]:
            continue
        missing = [c for c in comp if c not in h["compliance"]]
        if missing:
            continue
        out.append(h)
    return out


def _hard_filter_db(s):
    dm = s["infrastructure"]["deployment_model"]
    cloud = s["infrastructure"]["cloud_preference"]
    out = []
    for d in DATABASE:
        if dm in ("on_premise", "air_gapped", "self_hosted_cloud_vm") and not d["self_hostable"]:
            continue
        if d["name"] == "DynamoDB" and cloud not in ("any", "aws"):
            continue
        if d["name"] == "Firebase Firestore" and cloud not in ("any", "gcp"):
            continue
        out.append(d)
    return out


def _hard_filter_supporting(layer, s):
    dm = s["infrastructure"]["deployment_model"]
    out = []
    for c in SUPPORTING[layer]:
        if dm in ("on_premise", "air_gapped") and not c.get("self_hostable", False):
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Layer selection logic
# ---------------------------------------------------------------------------

def _select_frontend(s):
    plats = s["project"]["platforms"]
    if plats == ["api_only"]:
        return None, "API-only project; no user interface layer is needed.", ["project.platforms"]

    needs_mobile = "android" in plats or "ios" in plats
    needs_desktop = "desktop" in plats
    needs_web = "web" in plats

    if needs_mobile and not needs_web and not needs_desktop:
        pool = [f for f in FRONTEND if f["kind"] in ("cross_mobile", "native_android", "native_ios")]
        if "android" in plats and "ios" not in plats:
            pool = [f for f in pool if f["kind"] in ("cross_mobile", "native_android")]
        if "ios" in plats and "android" not in plats:
            pool = [f for f in pool if f["kind"] in ("cross_mobile", "native_ios")]
    elif needs_desktop and not needs_web and not needs_mobile:
        pool = [f for f in FRONTEND if f["kind"] == "desktop"]
    elif needs_mobile and needs_web:
        # web frontend is the primary layer; the mobile app is added as a
        # supporting component sharing the same backend API
        pool = [f for f in FRONTEND if f["kind"] == "web"]
    else:
        pool = [f for f in FRONTEND if f["kind"] in ("web", "static")]

    scored = []
    for f in pool:
        cand = dict(f)
        sc, reasons = _score(cand, s, "frontend")
        if s["requirements"]["seo_critical"] and needs_web and cand.get("seo", 0) >= 4:
            sc += 2.0
            reasons.insert(0, "SEO-critical pages need server-side rendering / pre-rendering")
        elif s["requirements"]["seo_critical"] and needs_web and cand.get("seo", 0) <= 2:
            sc -= 2.0
            reasons.append("client-rendered output is weak for SEO-critical pages")
        scored.append((sc, cand, reasons))
    scored.sort(key=lambda t: (-t[0], -t[1]["maturity"], t[1]["name"]))
    return scored[0], None, scored


def _select_backend(s):
    plats = s["project"]["platforms"]
    ptype = s["project"]["type"]
    if plats == ["api_only"] or ptype == "api_platform":
        pass  # a backend is definitely needed
    if ptype == "content_site" and not any(s["requirements"][k] for k in
            ["realtime", "payments", "file_media_storage", "multi_tenant"]) and \
            s["requirements"]["auth"] == "none" and s["scale"]["users_12_months"] <= 10000:
        return None, ("Pure content site with no auth, payments, uploads or realtime: a static build plus "
                      "managed form/comment services covers it, so no custom backend is warranted."), \
            ["project.type", "requirements.*"]
    pool = list(BACKEND)
    dm = s["infrastructure"]["deployment_model"]
    if dm in ("on_premise", "air_gapped"):
        pool = [b for b in pool if b["self_hostable"]]
    best, ranked = _pick(pool, s, "backend")
    return best, None, ranked


# Selection functions return (best_tuple_or_None, none_reason, ranked_or_none_driven):
#   - when a layer is selected:  ((score, candidate, reasons), None, ranked_list)
#   - when the layer is 'None':  (None, reason_string, driven_fields)


def _select_database(s, backend_choice):
    ptype = s["project"]["type"]
    users = s["scale"]["users_12_months"]
    needs_db = not (ptype == "content_site" and s["requirements"]["auth"] == "none"
                    and not s["requirements"]["file_media_storage"]
                    and not s["requirements"]["payments"])
    if not needs_db:
        return None, ("No accounts, uploads or transactions: content can live in the repository/build "
                      "pipeline, so no database server is justified."), ["project.type", "requirements.*"]

    pool = _hard_filter_db(s)
    candidates = []
    for d in pool:
        d = dict(d)
        bonus = 0.0
        if s["scale"]["workload"] in d["workload"]:
            bonus += 1.5
        if s["requirements"]["full_text_search"] and d.get("fts"):
            bonus += 1.0
        if s["requirements"]["multi_tenant"] and d.get("multi_tenant"):
            bonus += 1.0
        if ptype in ("fintech", "healthtech") and d.get("transactions") and d["model"] == "relational":
            bonus += 2.0  # integrity matters
        if users <= 1000 and s["team"]["size"] <= 2 and ptype in ("internal_tool", "content_site", "other") \
                and s["scale"]["workload"] != "write_heavy" and not s["requirements"]["offline_support"] \
                and d["name"] == "SQLite":
            bonus += 3.0  # genuinely tiny tool: zero-ops embedded DB wins
        if s["requirements"]["offline_support"] and d["model"] == "relational" and d["self_hostable"]:
            bonus += 1.0  # server side of a sync architecture
        d["_extra"] = bonus
        candidates.append(d)
    scored = []
    for c in candidates:
        sc, reasons = _score(c, s, "database")
        sc += c["_extra"]
        if s["scale"]["workload"] in c["workload"]:
            reasons.append(f"fits a {s['scale']['workload']} workload")
        if users <= 1000 and c["name"] == "SQLite" and c["_extra"] >= 3.0:
            reasons.append("very small user base: an embedded database removes all database operations")
        scored.append((sc, c, reasons))
    scored.sort(key=lambda t: (-t[0], -t[1]["maturity"], t[1]["name"]))
    return scored[0], None, scored


def _select_hosting(s, frontend_cand, backend_cand):
    pool = _hard_filter_hosting(s, backend_cand=backend_cand, frontend_cand=frontend_cand)
    if not pool:
        return None, None, None
    candidates = []
    for h in pool:
        h = dict(h)
        bonus = 0.0
        names = {c["name"] for c in (frontend_cand, backend_cand) if c}
        if "*" in h["best_for"] or names & set(h["best_for"]):
            bonus += 2.0
        if s["budget"]["monthly_infra_usd"] == 0 and h["free_tier"]:
            bonus += 3.0
        if not s["team"]["has_devops"] and h["ops_burden"] <= 1:
            bonus += 2.0
        if s["requirements"]["realtime"] and h["provider"] == "fly":
            bonus += 1.0
        if s["infrastructure"]["cloud_preference"] in ("aws", "gcp", "azure") and h["provider"] == s["infrastructure"]["cloud_preference"]:
            bonus += 2.0
        h["_extra"] = bonus
        candidates.append(h)
    scored = []
    for c in candidates:
        sc, reasons = _score(c, s, "hosting")
        sc += c["_extra"]
        names = {x["name"] for x in (frontend_cand, backend_cand) if x}
        if "*" in c["best_for"] or names & set(c["best_for"]):
            reasons.append("first-class support for the chosen framework(s)")
        if s["budget"]["monthly_infra_usd"] == 0 and c["free_tier"]:
            reasons.append("free tier fits the $0 budget")
        if not s["team"]["has_devops"] and c["ops_burden"] <= 1:
            reasons.append("no DevOps capacity, so a managed platform keeps operations near zero")
        scored.append((sc, c, reasons))
    scored.sort(key=lambda t: (-t[0], -t[1]["maturity"], t[1]["name"]))
    return scored[0], None, scored


def _select_supporting(s, hosting_cand, backend_cand=None):
    picks = []
    r = s["requirements"]
    dm = s["infrastructure"]["deployment_model"]
    residency = s["infrastructure"]["data_residency"]
    comp = s["compliance"]
    provider = hosting_cand["provider"] if hosting_cand else None

    def pick(layer, chooser, driven):
        pool = _hard_filter_supporting(layer, s)
        if not pool:
            return
        c = chooser(pool)
        picks.append((layer, c, driven))

    if r["full_text_search"]:
        def choose_search(pool):
            data_gb = s["scale"].get("data_volume_gb_12_months")
            if (data_gb is None or data_gb <= 100) \
                    and s["scale"]["users_12_months"] <= 100000:
                return next((c for c in pool if c["name"] == "PostgreSQL full-text search"), pool[0])
            return next((c for c in pool if c["name"] == "Meilisearch"), pool[0])
        pick("search", choose_search, ["requirements.full_text_search"])

    if r["realtime"]:
        def choose_rt(pool):
            if backend_cand is not None:
                blangs = [l.lower() for l in backend_cand.get("languages", [])]
                for c in pool:
                    if c.get("langs") and any(l in c["langs"] for l in blangs):
                        return c
            return next((c for c in pool if c["name"] == "Ably"), pool[0])
        pick("realtime", choose_rt, ["requirements.realtime"])

    if r["payments"]:
        def choose_pay(pool):
            india = residency == "india" or "rbi_localisation" in comp or "dpdp" in comp
            pref = "Razorpay" if india else "Stripe"
            return next((c for c in pool if c["name"] == pref), pool[0])
        pick("payments", choose_pay, ["requirements.payments", "infrastructure.data_residency", "compliance"])

    if r["file_media_storage"]:
        def choose_store(pool):
            for c in pool:
                if c.get("provider") == provider:
                    return c
            if dm in ("on_premise", "air_gapped"):
                return next((c for c in pool if c["name"].startswith("MinIO")), pool[0])
            if residency == "india":
                return next((c for c in pool if c["name"] == "AWS S3"), pool[0])
            return next((c for c in pool if c["name"] == "Cloudflare R2"), pool[0])
        pick("storage", choose_store, ["requirements.file_media_storage"])

    if r["auth"] != "none":
        def choose_auth(pool):
            mode = r["auth"]
            pool = [c for c in pool if mode in c.get("modes", [])] or pool
            if mode == "enterprise_sso" or dm in ("on_premise", "air_gapped"):
                return next((c for c in pool if c["name"] == "Keycloak"), pool[0])
            if "rbi_localisation" in comp:
                # credentials/PII must stay in India: keep auth data in our own
                # database on India-region infrastructure
                return next((c for c in pool if c["name"].startswith("Rolled")), pool[0])
            if mode == "phone_otp":
                return next((c for c in pool if c["name"] == "Firebase Authentication"), pool[0])
            if s["budget"]["monthly_infra_usd"] <= 100:
                return next((c for c in pool if c["name"].startswith("Rolled")), pool[0])
            return pool[0]
        pick("auth", choose_auth, ["requirements.auth", "compliance", "infrastructure.data_residency"])

    if r["ml_features"] != "none":
        def choose_ml(pool):
            mode = r["ml_features"]
            pool = [c for c in pool if mode in c.get("modes", [])] or pool
            if mode == "training":
                # classic tabular ML (scoring, forecasting): CPU-trained models
                # are cheap and well understood; GPU LLM hosting is overkill
                return next((c for c in pool if c["name"].startswith("scikit-learn")), pool[0])
            if dm in ("on_premise", "air_gapped") or "hipaa" in comp \
                    or "rbi_localisation" in comp or "pci_dss" in comp:
                # regulated data must not leave our own compliant infrastructure
                return next((c for c in pool if c["self_hostable"]), pool[0])
            return pool[0]
        pick("ml", choose_ml, ["requirements.ml_features", "compliance"])

    if s["scale"]["users_12_months"] >= 20000 or s["scale"]["workload"] == "read_heavy" and s["scale"]["users_12_months"] >= 5000:
        if dm not in ("on_premise", "air_gapped"):
            def choose_cache(pool):
                return pool[0]
            pick("cache", choose_cache, ["scale.users_12_months", "scale.workload"])

    if r["realtime"] or s["scale"]["traffic_pattern"] == "spiky" or s["timeline"]["mvp_weeks"] >= 6:
        def choose_queue(pool):
            blangs = [l.lower() for l in (backend_cand or {}).get("languages", [])]
            for c in pool:
                if c.get("langs") and any(l in c["langs"] for l in blangs):
                    return c
            if provider == "aws":
                hit = next((c for c in pool if c["name"] == "AWS SQS + SNS"), None)
                if hit:
                    return hit
            return next((c for c in pool if c["name"] == "RabbitMQ"), pool[0])
        pick("queue", choose_queue, ["requirements.realtime", "scale.traffic_pattern", "team.languages"])

    # mobile companion app when the frontend choice covers web only
    plats = s["project"]["platforms"]
    if ("android" in plats or "ios" in plats) and "web" in plats:
        langs = s["team"]["languages"]
        if any(l in langs for l in ("javascript", "typescript")) or not ("dart" in langs):
            mob = {"layer": "other", "choice": "React Native (Expo) mobile app",
                   "justification": "Web frontend does not cover the required mobile platforms; Expo ships "
                                    "Android/iOS from one JavaScript/TypeScript codebase, reusing the backend API.",
                   "driven_by": ["project.platforms", "team.languages"], "alternatives": [],
                   "tradeoffs": ["Some native modules require ejecting from Expo."]}
        else:
            mob = {"layer": "other", "choice": "Flutter mobile app",
                   "justification": "Web frontend does not cover the required mobile platforms; Flutter ships "
                                    "Android/iOS from one Dart codebase with near-native performance.",
                   "driven_by": ["project.platforms", "team.languages"], "alternatives": [],
                   "tradeoffs": ["Dart is a new language for many teams."]}
        picks.append(("other", mob, ["project.platforms", "team.languages"]))

    if s["requirements"]["offline_support"] and ("android" in plats or "ios" in plats):
        picks.append(("other", {"layer": "other",
                                "choice": "SQLite on-device store + background sync API",
                                "justification": "Offline-first mobile: field data is captured into an embedded "
                                                 "SQLite store on the device and synced to the server database when "
                                                 "connectivity returns (append-only sync with server-side conflict rules).",
                                "driven_by": ["requirements.offline_support", "project.platforms"],
                                "alternatives": [],
                                "tradeoffs": ["Sync conflicts need explicit resolution rules."]},
                      ["requirements.offline_support", "project.platforms"]))

    if s["requirements"]["seo_critical"] or s["scale"]["users_12_months"] >= 50000:
        if dm not in ("on_premise", "air_gapped"):
            def choose_cdn(pool):
                if provider == "aws":
                    hit = next((c for c in pool if c["name"] == "AWS CloudFront"), None)
                    if hit:
                        return hit
                return pool[0]
            pick("cdn", choose_cdn, ["requirements.seo_critical", "scale.users_12_months"])

    pick("ci_cd", lambda pool: pool[0], ["timeline.mvp_weeks"])

    def choose_obs(pool):
        if s["team"]["has_devops"]:
            hit = next((c for c in pool if c["name"].startswith("Prometheus")), None)
            if hit:
                return hit
        return next((c for c in pool if c["name"].startswith("Sentry")), pool[0])
    pick("observability", choose_obs, ["requirements.availability", "team.has_devops"])
    return picks


# ---------------------------------------------------------------------------
# 7-8. Explanation + cost
# ---------------------------------------------------------------------------

def _why_not(cand, other, s):
    """Concrete reasons the runner-up lost to the winner for THIS scenario."""
    why = []
    team_langs = s["team"]["languages"]
    o_langs = [l.lower() for l in other.get("languages", [])]
    if o_langs and team_langs and not any(l in team_langs for l in o_langs):
        why.append(f"needs {o_langs[0]}, which the team does not list")
    if other["ops_burden"] > cand["ops_burden"]:
        why.append("heavier to operate")
    if other["cost_efficiency"] < cand["cost_efficiency"] and s["priorities"]["cost"] >= 3:
        why.append("higher running cost for a cost-sensitive project")
    if s["requirements"]["seo_critical"] and other.get("seo", 5) < cand.get("seo", 0):
        why.append("weaker server-side rendering for SEO-critical pages")
    if s["scale"]["users_12_months"] >= 50000 and other["scalability"] < cand["scalability"]:
        why.append("weaker scaling story at the expected user volume")
    if s["requirements"]["realtime"] and other.get("realtime", 5) < cand.get("realtime", 0):
        why.append("weaker realtime support")
    if s["project"]["type"] in other.get("anti", []):
        why.append(f"poor fit for {s['project']['type']}")
    if not why:
        why.append("scored lower on this scenario's specific priorities and constraints")
    return "; ".join(why)[0:1].upper() + "; ".join(why)[1:] + "."


def _component(cand, reasons, driven, ranked=None, s=None):
    alts = []
    if ranked and s is not None:
        for sc, other, oreasons in ranked[1:4]:
            alts.append({"choice": other["name"], "why_not": _why_not(cand, other, s)})
    tradeoffs = {
        "Astro": ["Interactive app-like features need islands/hydration."],
        "Next.js (React)": ["Tied to React ecosystem; Vercel-style hosting assumed for the smoothest path."],
        "React (SPA, Vite)": ["Client rendering needs extra work for SEO and first-load performance."],
        "React Native (Expo)": ["Some native modules require ejecting from Expo.", "Performance below fully native for heavy graphics."],
        "Flutter": ["Dart is a new language for many teams.", "Web support lags mobile."],
        "Node.js (NestJS, TypeScript)": ["Single-threaded event loop; CPU-heavy work needs worker queues."],
        "Node.js (Express)": ["Unopinionated: structure and security are the team's responsibility."],
        "Python (FastAPI)": ["Fewer batteries than Django; you assemble auth/admin yourself."],
        "Python (Django)": ["Monolith-leaning; async/realtime needs Channels add-ons."],
        "Go (Gin/Echo)": ["More boilerplate for CRUD; slower iteration for junior teams."],
        "PostgreSQL": ["Vertical scaling first; very high write scale needs read replicas/partitioning."],
        "MySQL": ["Weaker advanced querying (JSON, CTEs, full-text) than PostgreSQL."],
        "MongoDB": ["Schema discipline moves into application code; joins are limited."],
        "SQLite": ["Single-writer; not a network database for many concurrent users."],
        "DynamoDB": ["Access patterns must be designed up front; AWS lock-in."],
        "Firebase Firestore": ["Limited querying; Google lock-in; costs grow with reads."],
        "Vercel": ["Costs climb with bandwidth and team seats beyond the hobby tier."],
        "Netlify": ["No India region; serverless-only compute model."],
        "Render": ["Fewer regions; free tier sleeps on idle."],
        "Railway": ["Limited regions; usage pricing can surprise at scale."],
        "Fly.io": ["Smaller managed ecosystem than the big clouds."],
        "AWS (EC2/ECS + RDS)": ["Real operational burden: patching, scaling, IAM, billing complexity."],
        "AWS serverless (Lambda + API Gateway)": ["Cold starts; deep AWS lock-in; harder local development."],
        "Google Cloud (Cloud Run + Cloud SQL)": ["Cloud SQL costs at scale; some GCP services lag AWS in India regions."],
        "Firebase (Hosting + Functions)": ["Google lock-in; limited backend flexibility."],
        "Microsoft Azure (App Service + Azure SQL)": ["Portal complexity; pricing needs watching."],
        "Hetzner Cloud VPS (self-managed)": ["You run everything: backups, failover, security."],
        "On-premise servers (self-hosted)": ["Hardware capex, no elastic scaling, you own uptime."],
    }.get(cand["name"], [])
    just = "; ".join(reasons)
    just = just[0:1].upper() + just[1:] if just else "Best fit for the given inputs"
    return {
        "choice": cand["name"],
        "justification": just + ". " + cand["note"],
        "driven_by": driven,
        "alternatives": alts,
        "tradeoffs": tradeoffs,
    }


def _cost_band(stack_choices, s):
    """Order-of-magnitude monthly band in USD, with assumptions."""
    users = s["scale"]["users_12_months"]
    data_gb = s["scale"].get("data_volume_gb_12_months") or 5
    lo = hi = 0
    assumptions = []

    for layer, cand in stack_choices:
        if cand is None:
            continue
        name = cand["name"]
        if layer == "hosting":
            if cand["free_tier"] and users <= 10000 and s["budget"]["monthly_infra_usd"] == 0:
                assumptions.append(f"{name} free tier covers the launch load")
                continue
            base = {"vercel": (20, 100), "netlify": (19, 99), "render": (7, 80), "railway": (5, 60),
                    "fly": (10, 80), "firebase": (10, 75), "aws": (30, 250), "gcp": (30, 220),
                    "azure": (30, 230), "hetzner": (5, 40), "onprem": (50, 300)}[cand["provider"]]
            factor = 1.0
            if users > 100000:
                factor = 4.0
                assumptions.append("Compute scaled for >100k users (multiple instances / autoscaling)")
            elif users > 20000:
                factor = 2.0
            lo += base[0] * factor
            hi += base[1] * factor
        elif layer == "database":
            if name == "SQLite":
                continue
            if name in ("PostgreSQL", "MySQL"):
                if users <= 10000 and s["budget"]["monthly_infra_usd"] == 0:
                    assumptions.append(f"{name} runs on the same free/cheap tier as the app")
                    continue
                lo += 7 if users <= 20000 else 25
                hi += 40 if users <= 20000 else 150
            elif name == "DynamoDB":
                lo += 5 + users // 50000 * 10
                hi += 25 + users // 20000 * 20
                assumptions.append("DynamoDB on-demand pricing at the expected request volume")
            elif name == "Firebase Firestore":
                lo += 5
                hi += 30 + users // 20000 * 25
                assumptions.append("Firestore priced on document reads/writes at expected usage")
            else:
                lo += 10
                hi += 60
        elif layer == "supporting":
            lo += cand.get("cost", 0)
            hi += cand.get("cost", 0) * 2
    if data_gb > 50:
        extra = int(data_gb / 50) * 3
        lo += extra
        hi += extra * 2
        assumptions.append(f"~{data_gb} GB of storage/bandwidth at roughly $0.02-0.09/GB-month")
    if lo == 0 and hi == 0:
        hi = 5
        assumptions.append("All components on free tiers; small buffer for overage or a custom domain")
    assumptions.append("Order-of-magnitude band, not a quote; real bills depend on traffic shape and discounts")
    return {"min": round(lo), "max": round(hi), "assumptions": assumptions}


# ---------------------------------------------------------------------------
# Architecture style
# ---------------------------------------------------------------------------

def _architecture_style(s, backend_cand):
    if backend_cand is None:
        return "static"
    if backend_cand["name"].startswith("Serverless"):
        return "serverless"
    users = s["scale"]["users_12_months"]
    if users >= 500000 and s["team"]["size"] >= 8 and s["team"]["has_devops"]:
        return "microservices"
    if users >= 100000 and s["team"]["size"] >= 5:
        return "modular_monolith"
    return "monolith"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def recommend(scenario):
    """Run the full pipeline. Returns the standard output-schema dict."""
    filled, errors, assumptions = validate_and_fill(scenario)
    sid = scenario.get("scenario_id", "adhoc") if isinstance(scenario, dict) else "adhoc"

    if errors:
        return {
            "scenario_id": sid,
            "engine_version": ENGINE_VERSION,
            "status": "invalid_input",
            "summary": "Input failed validation; no recommendation produced.",
            "architecture_style": "other",
            "stack": None,
            "estimated_monthly_cost_usd": {"min": 0, "max": 0, "assumptions": []},
            "warnings": [],
            "assumptions": [],
            "validation_errors": errors,
        }

    warnings = detect_conflicts(filled)

    fe_best, fe_none_reason, fe_ranked = _select_frontend(filled)
    be_best, be_none_reason, be_ranked = _select_backend(filled)
    db_best, db_none_reason, db_ranked = _select_database(filled, be_best[1] if be_best else None)

    hosting_best, _, hosting_ranked = _select_hosting(
        filled,
        fe_best[1] if fe_best else None,
        be_best[1] if be_best else None)
    if hosting_best is None:
        warnings.append(dict(code="NO_ELIGIBLE_HOSTING", severity="critical",
                             message="No hosting option satisfies the hard constraints (residency, compliance, "
                                     "deployment model, cloud preference). Constraints must be relaxed.",
                             fields=["infrastructure.data_residency", "compliance",
                                     "infrastructure.deployment_model", "infrastructure.cloud_preference"]))
        return {
            "scenario_id": sid,
            "engine_version": ENGINE_VERSION,
            "status": "infeasible",
            "summary": "Hard constraints cannot all be satisfied by any known hosting option.",
            "architecture_style": "other",
            "stack": None,
            "estimated_monthly_cost_usd": {"min": 0, "max": 0, "assumptions": []},
            "warnings": warnings,
            "assumptions": assumptions,
            "validation_errors": [],
        }

    stack = {}
    stack_choices = []

    if fe_best:
        sc, cand, reasons = fe_best
        stack["frontend"] = _component(cand, reasons, ["project.platforms", "requirements.seo_critical",
                                                       "team.languages", "project.type"],
                                       ranked=fe_ranked if isinstance(fe_ranked, list) else None, s=filled)
        stack_choices.append(("frontend", cand))
    else:
        stack["frontend"] = {"choice": "None", "justification": fe_none_reason,
                             "driven_by": fe_ranked, "alternatives": [], "tradeoffs": []}

    if be_best:
        sc, cand, reasons = be_best
        stack["backend"] = _component(cand, reasons, ["project.type", "team.languages", "requirements.realtime",
                                                      "team.experience"], ranked=be_ranked, s=filled)
        stack_choices.append(("backend", cand))
    else:
        stack["backend"] = {"choice": "None", "justification": be_none_reason,
                            "driven_by": be_ranked, "alternatives": [], "tradeoffs": []}

    if db_best:
        sc, cand, reasons = db_best
        stack["database"] = _component(cand, reasons, ["project.type", "scale.workload",
                                                       "requirements.multi_tenant", "scale.users_12_months"],
                                       ranked=db_ranked, s=filled)
        stack_choices.append(("database", cand))
    else:
        stack["database"] = {"choice": "None", "justification": db_none_reason,
                             "driven_by": db_ranked, "alternatives": [], "tradeoffs": []}

    sc, cand, reasons = hosting_best
    stack["hosting"] = _component(cand, reasons, ["budget.monthly_infra_usd", "team.has_devops",
                                                  "infrastructure.deployment_model",
                                                  "infrastructure.data_residency", "compliance",
                                                  "infrastructure.cloud_preference"],
                                  ranked=hosting_ranked, s=filled)
    stack_choices.append(("hosting", cand))

    be_cand_sup = be_best[1] if be_best else None
    supporting = []
    for layer, c, driven in _select_supporting(filled, cand, backend_cand=be_cand_sup):
        if "choice" in c:  # pre-formed component (mobile companion, on-device sync)
            supporting.append(c)
            stack_choices.append(("supporting", {"name": c["choice"], "cost": 0}))
            continue
        supporting.append({
            "layer": layer,
            "choice": c["name"],
            "justification": c["note"],
            "driven_by": driven,
            "alternatives": [],
            "tradeoffs": [],
        })
        stack_choices.append(("supporting", c))
    if supporting:
        stack["supporting"] = supporting

    # cost sanity vs budget
    cost = _cost_band(stack_choices, filled)
    budget = filled["budget"]["monthly_infra_usd"]
    if cost["min"] > budget and budget >= 0:
        if cost["min"] > max(budget, 1) * 2:
            warnings.append(dict(code="OVER_BUDGET", severity="critical",
                                 message=(f"Estimated minimum cost ${cost['min']}/month exceeds the "
                                          f"${budget}/month budget by more than 2x; consider relaxing scale, "
                                          "availability or compliance expectations."),
                                 fields=["budget.monthly_infra_usd"]))
        else:
            warnings.append(dict(code="NEAR_BUDGET", severity="warning",
                                 message=(f"Estimated minimum cost ${cost['min']}/month is above the "
                                          f"${budget}/month budget; the stack assumes free tiers and tight sizing."),
                                 fields=["budget.monthly_infra_usd"]))

    critical = any(w["severity"] == "critical" for w in warnings)
    status = "ok"
    if critical:
        status = "ok_with_warnings"
    if any(w["code"] == "SCALE_BUDGET_CONFLICT" for w in warnings) and cost["min"] > 0:
        status = "infeasible"
        assumptions.append("No stack satisfies the stated budget at the expected scale; the stack shown is the "
                           "cheapest viable option and deliberately relaxes the budget constraint.")

    be_cand = be_best[1] if be_best else None
    arch = _architecture_style(filled, be_cand)
    fe_name = stack["frontend"]["choice"]
    be_name = stack["backend"]["choice"]
    db_name = stack["database"]["choice"]
    ho_name = stack["hosting"]["choice"]
    summary = (f"{arch.replace('_', ' ').capitalize()} architecture: {fe_name} frontend, "
               f"{be_name} backend, {db_name} database on {ho_name}.")

    return {
        "scenario_id": sid,
        "engine_version": ENGINE_VERSION,
        "status": status,
        "summary": summary,
        "architecture_style": arch,
        "stack": stack,
        "estimated_monthly_cost_usd": cost,
        "warnings": warnings,
        "assumptions": assumptions,
        "validation_errors": [],
    }

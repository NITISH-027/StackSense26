# StackSense - Intelligent Tech Stack Recommendation Engine

StackSense recommends a complete, justified technology stack - frontend, backend,
database, hosting and supporting services - for a software project described by
structured parameters (scale, team, budget, infrastructure, compliance and
priorities). It validates its input, detects contradictory constraints, and exports
every recommendation as JSON in the standard StackSense output schema.

The engine is **deterministic and rule-based**. Stack selection is done by hard
constraint filtering plus weighted priority scoring over a curated knowledge base.
**No LLM calls are made anywhere in the engine** - see "LLM features" below.

Built for the StackSense Challenge, Synergy '26, IIIT Bangalore.

Public repository: https://github.com/NITISH-027/stacksense26 (commit history is
supporting evidence of originality; all engine code was written 10-15 November 2026).

## System architecture

```
scenario JSON ──▶ validate_and_fill() ──▶ detect_conflicts()
                     │  (schema, types,       │  (contradictions,
                     │   enums, ranges,       │   risks, deprecated
                     │   defaults)            │   existing systems)
                     ▼                        ▼
              hard constraint filters    warnings[]
              (residency, compliance,
               deployment model,
               cloud preference)
                     │
                     ▼
              weighted scoring
              (priorities 1-5 x attributes
               + team/scale/budget fit)
                     │
                     ▼
              selection + explanation
              (justification, driven_by,
               alternatives, trade-offs)
                     │
                     ▼
              cost band model ──▶ output JSON (standard schema)
```

Modules:

- `stacksense/kb.py` - the technology knowledge base: every candidate per layer
  with cost profile, ops burden, maturity, compliance support, regions and
  fit notes, plus a registry of deprecated/end-of-life technologies.
- `stacksense/engine.py` - the pipeline: validation, defaults, conflict
  detection, hard filters, scoring, selection, explanation, cost model.
- `stacksense/cli.py` - command-line interface (`recommend`, `batch`,
  `validate`, `ui`).
- `stacksense/ui_server.py` + `stacksense/static/index.html` - zero-dependency
  web UI: load or paste a scenario, run it, inspect/export the result.
- `scenarios/` - the six public scenarios (P1-P6) as individual JSON files.
- `tests/edge_scenarios/` - edge-case scenarios used for testing (missing
  fields, invalid values, contradictions, air-gapped deployment, tiny tool,
  deprecated existing systems).

Data flow: a scenario file is validated and filled with documented defaults,
conflicts become warnings with severities, hard constraints remove ineligible
candidates, the survivors are scored against the project's priorities, the
winner per layer is explained from the actual score components, and the result
is exported in the standard output schema.

## Technology used to build the engine

- Python 3.10+ (standard library only; no third-party packages)
- Web UI: single-page HTML/CSS/vanilla-JS app served by `http.server`
- No build step, no package install, no external services

## Prerequisites

- Python 3.10 or newer (tested on 3.10), on Windows, macOS or Linux
- Nothing else. No pip install, no Docker, no API keys, no network access.

## Setup and run

```bash
cd source

# 1. Run one scenario and print the output JSON
python3 -m stacksense recommend scenarios/P2.json

# 2. Run one scenario and write the output file
python3 -m stacksense recommend scenarios/P2.json -o P2_output.json

# 3. Run all six public scenarios into an outputs directory
python3 -m stacksense batch scenarios -o outputs

# 4. Validate a scenario file against the input schema
python3 -m stacksense validate scenarios/P2.json

# 5. Start the web UI
python3 -m stacksense ui --port 8000
# then open http://127.0.0.1:8000
```

On Windows, use `py -m stacksense ...` or `python -m stacksense ...` instead of `python3`.

Expected first-run time: under two minutes, most of it reading this README.

## Running a scenario JSON file

`python3 -m stacksense recommend <file.json> [-o <output.json>]` loads a scenario
in the standard input schema and writes the recommendation in the standard output
schema (`status`, `summary`, `architecture_style`, `stack`, `estimated_monthly_cost_usd`,
`warnings`, `assumptions`, `validation_errors`). With `-o` the output goes to that
path; otherwise it is printed to stdout. `batch` runs every `*.json` in a directory
and writes `<scenario_id>_output.json` per file. The web UI accepts pasted JSON or
an uploaded `.json` file and has a download button for the output JSON.

## LLM features (switch-off statement)

This engine makes **no LLM calls at all** - there is nothing to switch off, and
the mandated LLM-off test passes trivially: every stack is chosen by the rules in
`engine.py`, so identical input always produces an identical stack. The CLI
accepts the explicit switch `--llm off` (the only value it accepts) to make this
contract visible:

```bash
python3 -m stacksense --llm off recommend scenarios/P2.json
```

## Project structure

```
source/
├── README.md
├── requirements.txt            (empty on purpose: stdlib only)
├── stacksense/
│   ├── __init__.py             (version)
│   ├── __main__.py             (python -m stacksense entry)
│   ├── cli.py                  (recommend / batch / validate / ui)
│   ├── engine.py               (validation, conflicts, filters, scoring, output)
│   ├── kb.py                   (technology knowledge base)
│   ├── ui_server.py            (stdlib HTTP server + JSON API)
│   └── static/
│       └── index.html          (single-page UI)
├── scenarios/                  (P1-P6 public scenarios)
└── tests/
    ├── edge_scenarios/         (edge-case test inputs)
    └── run_edge_cases.py       (runs them, prints statuses)
```

## Credits

Knowledge-base attributes were curated from the following public sources:

- Stack Overflow Developer Survey 2025 - technology adoption and usage
  (https://survey.stackoverflow.co/2025/)
- DB-Engines Ranking - database popularity and model classification
  (https://db-engines.com/en/ranking)
- Official documentation and pricing pages: React (react.dev), Next.js (nextjs.org),
  Vue/Nuxt (nuxt.com), SvelteKit (svelte.dev), Angular (angular.dev), Astro (astro.build),
  React Native/Expo (docs.expo.dev), Flutter (flutter.dev), Node.js (nodejs.org),
  NestJS (nestjs.com), FastAPI (fastapi.tiangolo.com), Django (djangoproject.com),
  Go (go.dev), Spring Boot (spring.io), .NET (dotnet.microsoft.com), Laravel (laravel.com),
  Rails (rubyonrails.org), PostgreSQL (postgresql.org), MySQL (mysql.com),
  MongoDB (mongodb.com), SQLite (sqlite.org), Redis (redis.io), DynamoDB and AWS
  pricing (aws.amazon.com/pricing), Firestore and GCP pricing (cloud.google.com/pricing),
  Azure pricing (azure.microsoft.com/pricing), Vercel (vercel.com/pricing),
  Netlify (netlify.com/pricing), Render (render.com/pricing), Railway (railway.com/pricing),
  Fly.io (fly.io/docs/about/pricing/), Firebase (firebase.google.com/pricing),
  Hetzner (hetzner.com), Meilisearch (meilisearch.com), Elasticsearch (elastic.co),
  Algolia (algolia.com), RabbitMQ (rabbitmq.com), Kafka (kafka.apache.org),
  Socket.IO (socket.io), Ably (ably.com), Pusher (pusher.com), Supabase (supabase.com),
  Stripe (stripe.com/pricing), Razorpay (razorpay.com/pricing), Cashfree (cashfree.com),
  Auth0 (auth0.com/pricing), Keycloak (keycloak.org), AWS Cognito, MinIO (min.io),
  Cloudflare (cloudflare.com), AWS CloudFront, GitHub Actions, GitLab CI,
  Sentry (sentry.io), Prometheus (prometheus.io), Grafana (grafana.com)
- Compliance programme pages: AWS Compliance Programs, Google Cloud compliance
  offerings, Microsoft Azure compliance (for HIPAA/PCI DSS/ISO 27001/SOC 2 and
  India data residency support)
- RBI "Storage of Payment System Data" directive (April 2018) and the Digital
  Personal Data Protection Act, 2023 (data-localisation reasoning)

No third-party libraries, datasets or APIs are used by the engine itself.

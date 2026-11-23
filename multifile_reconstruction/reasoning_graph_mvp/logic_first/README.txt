DocHop-style logic-first generator + LongChart-inspired visibility control
========================================================================

Historical mechanism MVP. For the newer topic-to-LLM-metadata and natural-report
pipeline, read README_topic.txt. The synthetic reporting-factor expansion below
does not establish realistic business dependencies and is not the current main
experiment. Its test counts are software checks, not Agent task performance.

One-command reproduction (from /mnt/docker_disk/tmp/data/ICM2027):

  PYTHONDONTWRITEBYTECODE=1 python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.run --stage all --seeds 20

Tests:

  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -o addopts= -p no:cacheprovider multifile_reconstruction/reasoning_graph_mvp/logic_first

Requires Python 3.10+ and matplotlib. Tests require pytest. No model/API calls.
No ReportLab, Office, GPU, upstream editing or dependency installation required.

Inputs and generalization
-------------------------
The default metadata is in synthesis.default_metadata(). Replace it with
--metadata path/to/metadata.json to provide another topic/entities/metrics.
Each metric has id, bilingual label, unit (dimension-exponent dictionary),
integer min/max. At least six entities, and at least depth+1 metrics are needed.
No complete dependency graph or per-question answer is an input. A seed and
requested filter-tree depth 2..5 determine the sampled structure. The CLI cycles
depths across seeds; the Python generate(seed, depth, metadata) API allows an
explicit depth. The current sampler assigns distinct metrics to filter leaves.
The per-chart synthesizer also supports multiple compatible threshold constraints.
MAX/MIN deliberately support tied extrema; all tied entities must be included.

Stage 1 (DocHop-inspired; independent reimplementation)
------------------------------------------------------
1. Sample a Boolean/filter tree from GE, LE, MAX, MIN, AND, OR.
2. Sample desired entity memberships BEFORE numeric values. Reject empty/full
   target groups and Boolean branches that make no difference on this instance.
3. Solve bounded integer chart constraints, grouped by chart. Reject infeasibility.
4. Render bilingual rule clauses and numeric SVG charts. A writer capability
   boundary excludes chart values, entity assignments and final answers.
5. Generate entity-identification, COUNT and AVG questions and exact answers.
6. Audit with a separately implemented row-wise oracle, a controlled-language
   rule parser, and SVG text AND actual bar-height readback.

Stage 2 (LongChart-inspired; our additional mechanism)
-----------------------------------------------------
Only runs after ALL stage-1 examples pass. Extend sampled indicators with the
explicit synthetic reporting rule indicator = net total / sample count, with
optional net total = gross total - deduction. This rule is human-defined; it
is not autonomously discovered or asserted as a real hotel accounting standard.
ADD, SUB, MUL, DIV all have typed executors; this experiment instantiates DIV/SUB.

For EACH pair, hold the ENTIRE expanded hidden graph and base facts fixed:
  shallow/public/: show computed indicator vectors plus the AVG source metric;
  deep/public/: show only raw totals, deductions and sample counts.
Both versions express the same rules, ask the same questions and have the same
answers. Base facts are saved only once at visibility/private/graph.json.

Shortest-proof search allows graph operations, reversible arithmetic,
associativity/commutativity of same-kind Boolean operations, and observed
empty/universal-set shortcuts. It does NOT fetch hidden facts when observations
are missing. Reports include one inclusion-minimal sufficient observation set,
deletion checks, direct-answer leakage checks and corrected proof depths.

Depth is PARALLEL semantic-operator depth, with an entire vector operation counted
as one step. Associative folding can lower nominal tree depth. Our minimum is
ONLY over registered derivations, not all possible formulas or human reasoning.
We do not claim all minimum-cardinality sets or information-theoretic uniqueness.

Output layout
-------------
artifacts/summary.json                  actual stage results and provenance
artifacts/stage1_report.json            gate result before stage 2
artifacts/operator_contracts.json       operator input/output types
artifacts/seed_000/public/report.html   initial rule/chart/question document
artifacts/seed_000/public/chart_*.svg    actual numeric evidence
artifacts/seed_000/private/             graph, tree, parameters, values, truth,
                                       evidence map, writer input, checks
artifacts/seed_000/visibility/shallow/   public evidence + private variant audit
artifacts/seed_000/visibility/deep/      public evidence + private variant audit
artifacts/seed_000/visibility/private/   shared graph, all node values, truth
artifacts/seed_000/visibility/pair_report.json

Serve/share ONLY an individual public/ directory. Root artifacts and private/
contain answers. This tool does not run an Agent or establish a sandbox.
Use a new --output directory for another experiment; repeated identical runs
are deterministic apart from timings. Public directories are allowlisted, so
stale extra files are rejected rather than silently treated as legitimate input.

Provenance and limits
---------------------
References:
  https://proceedings.mlr.press/v306/yu26al.html (sections 3.1-3.4; appendices)
  https://arxiv.org/abs/2608.01328 (latent dependency graph/minimal-set ideas)
Checked DocHop evaluation checkout: 84cf9be996038bd85778c9a5bc1770cbb516e1f5.
Its generation pipeline was not found in the inspected public checkout. ZERO
upstream generation functions are called here. All files in logic_first/ are new.
The older gross-margin LLM prototype next to this directory is left unchanged.

DocHop-described ideas: rule-template sampling, logic-first synthesis, separate
numeric/context generation, executable answers. Differences: a small rule subset,
finite-domain program synthesis rather than Gemini synthesis, controlled prose,
HTML/SVG rather than a ReportLab single-page image. Not an exact reproduction.
Added here: numerical reporting-node expansion, paired visibility versions,
bounded alternate-proof search, observation ablation, independent readback tests.

Human work still required: domain metadata/labels, meanings of supported rules,
business-valid numerical definitions, natural-language templates, and manual
semantic/visual review (NOT YET PERFORMED). These are synthetic review policies,
not mined real-world certification rules. No training, no learned rule discovery,
no Office rendering/editing, no counterfactual edit benchmark or model evaluation
is claimed. Evidence dispersion across files is intentionally NOT tested yet.
Automatic checks certify this finite grammar and renderer, not unrestricted
answer non-leakage, all natural-language shortcuts, or real-world validity.

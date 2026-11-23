Topic -> metadata -> scoped reasoning tree -> constrained charts -> natural report
===============================================================================

This is the current stage-1 experiment. The old deterministic examples and
visibility.py remain historical mechanism demonstrations, not a faithful DocHop
reproduction or evidence of business-valid depth expansion. They are NOT run by
this topic-first pipeline. No Office or editing Agent is used.

From /mnt/docker_disk/tmp/data/ICM2027:

  python -m pip install -r multifile_reconstruction/reasoning_graph_mvp/logic_first/requirements_topic.txt
  python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.topic_run --reuse

--reuse reuses real saved model outputs ONLY when inputs match, including an
already accepted retry. It never substitutes program output for a failed LLM.
The initial authoring run is preserved separately in summary_authoring_run.json;
cached replay wall time must not be reported as fresh model generation time.

For a NEW topic and fresh inference (uses configured Codex model/login):

  python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.topic_run --topics "港口后勤工单运营" --per-topic 4 --output /tmp/port_topic_trial

Default experiment: 2 topics x 10 seeds = 20 tasks, depths 2..5, 3/4 charts.
The 90-second inference-call timeout can cause a recorded retry. Each batch
contains two reports; at most one repair attempt is allowed. Full transcripts,
original requests, model names, timing, token usage and no-tool transport audits
are saved. Existing inference-only alignment_probe.codex_transport is reused.
This adapter reuses the existing OSWorld Codex relay; it is not a new Agent.

Inputs
------
Externally specified: topic, sample count, seed/depth/chart settings, human rule
library, typed semantics and data bounds (positive integers 10..250).
The LLM generates 10 fictional entities, 8 independent summable metrics, a common
unit, 3 universal business subgroups and 4 quarters. No full graph is given.
Metadata validation checks schema/IDs/labels/cardinality; real-world semantic
plausibility is not proven. The prompt prohibits dependent totals/components,
ratios, percentages, arbitrary score indices and ready-made business formulas.

Construction
------------
1. Program samples linear filter chains, AND/OR branches and mixed structures.
   A subsequent filter operates ONLY over its preceding candidate scope.
2. Select compatible chart templates, concrete metric/group/time parameters and
   target memberships. No task tree is hand-drawn.
3. Solve shared chart coordinates with Z3, then execute and independently
   recalculate all selection nodes. Shared cells are consistent across charts.
   Data synthesis is deterministic program solving, NOT the paper's Gemini
   per-chart synthesis. Infeasible constructions are rejected and recorded.
4. An answer-blind LLM writes coherent bilingual sections with natural candidate
   group names. It receives no entity names, observations, expected memberships,
   final counts or averages. No stage IDs or R1-style formula lists are printed.
5. A separate context recovers an IR from English prose and checks bilingual
   consistency without the original graph or answers. Program compares this IR
   to the hidden graph. This is model-assisted verification, not a semantic proof.
   One repair is allowed; failures remain in the logs.
6. Render HTML/text and actual line, multi-metric bar and grouped bar SVGs.
   Ask entity-identification, count and mean questions. Answers stay private.

Rules
-----
chart_rules.CATALOG documents all 27 appendix-C names for provenance, but only
17 are implemented here. The supported subset covers thresholds, across-entity
and across-time means, absolute/percentage changes, strict temporal trends,
strict slope trends, within-entity metric/group comparisons, differences and
unique extrema. Remaining 10 are explicitly unsupported, not silently mapped
to easier threshold rules. Summary reports supported and actually sampled types
separately. Unique extrema exclude ties; this differs from the old MVP tied MAX.

Verification and limitations
----------------------------
Independent row-wise rational arithmetic checks the SMT/executor results.
Every supported template also has a hand-computed offline fixture. Tests cover
candidate-scope effects, positive percentage bases, missing evidence, wrong
answers, wrong Boolean relationships and changed/extra extracted constraints.
SVG checks read actual numeric/entity labels; they are not complete geometry,
overlap or visual-quality certification. Human review of all reports remains
pending. Graph depth is nominal generated depth, not a proven global minimum.
No claim of cross-file evidence necessity or counterfactual editing is made.

Files
-----
topic_artifacts/summary.json                 final construction/render checks
topic_artifacts/summary_authoring_run.json   original model-authoring run
topic_artifacts/authoring.log                original actual console log
topic_artifacts/tests.xml                    offline regression results
topic_artifacts/hotel/metadata_author/       actual topic-to-metadata LLM output
topic_artifacts/warehouse/metadata_author/   actual second-domain LLM output
topic_artifacts/authoring/batch_*/           all writer/auditor attempts
topic_artifacts/hotel/task_01/public/report.html   representative mixed tree
topic_artifacts/warehouse/task_03/public/report.html representative deep tree
Every task/private/ contains task.json, truth.json, node_values.json,
document.json, writer_input.json, language_audit.json, evidence_map.json and
checks.json. Share ONLY public/ with a solver; private/ and root logs have truth.

Dependency diagrams are now exported automatically on each rendering run:
  task_*/private/dependency_graph.png / .svg / .dot / .json / .html
  topic_artifacts/依赖图总览.html (all generated tasks)
Colors: blue evidence, purple rules, yellow AND/OR, green candidate sets, red
answers. Arrows explicitly distinguish data input and candidate-scope input.
These are builder-only views of registered computations, NOT inferred causal
graphs. They contain hidden results and must not be given to evaluated models.
Export just the diagrams from existing files, with NO model calls:
  python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.graph_view

Paper source versus new code
-----------------------------
DocHop https://proceedings.mlr.press/v306/yu26al.html sections 3.1-3.4,
appendices A/C/D motivate metadata synthesis, scoped rules, chart constraints,
separate narration and executable answers. All new generation modules are our
independent implementation. No official DocHop generation function is called;
the inspected official checkout exposes an evaluation harness, not a generator.
Differences remain: 17/27 atoms, 3 question forms rather than 41 templates,
SMT synthesis, configured GPT rather than Gemini, and HTML/SVG rather than the
original single-page ReportLab image renderer. No exact reproduction is claimed.

All source files in this directory are ours. Upstream repositories are unchanged.
No model training, arbitrary business-rule discovery or invented post-hoc
indicator formula is part of this experiment. The synthetic selection policies
are given to the solver as task-specific rules, not asserted as actual standards.

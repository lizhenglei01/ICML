Verified business dependencies: topic-driven and goal-driven modes
================================================================

Reproduce the saved real runs from /mnt/docker_disk/tmp/data/ICM2027:

  python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.business_run --reuse

--reuse reloads genuine saved LLM proposals and target-search turns, then reruns
independent checks/data generation/rendering. A roughly two-second cached run
is NOT fresh model inference time. Six successful model calls and earlier failed
attempts are retained in business_artifacts (see inference_accounting.json).
The successful calls use configured GPT-6.1-sol with medium effort; initial high
effort calls timed out. Global model configuration/credentials were not changed.

Fresh topic example (may propose unregistered concepts that remain drafts):

  python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.business_run --mode topic --topics "酒店季度客房收入分析" --seeds 3 --output /tmp/business_topic_trial

Goal-driven mode takes an explicit known-field/target contract:

  python -m multifile_reconstruction.reasoning_graph_mvp.logic_first.business_goal --goal-spec multifile_reconstruction/reasoning_graph_mvp/logic_first/examples/revpar_goal.json --out /tmp/revpar_goal_trial

The generic goal input is {known: [base metric declarations], target: a derived
metric declaration, desired_paths: integer}. A target formula is NOT supplied.
The user/model may choose metric IDs freely; independently registered business
concepts and unit aliases determine what can be verified. Other target handling
is tested with a gross-margin fixture; actual LLM target inference in this run
was RevPAR, not every supported financial target.

Topic-driven mechanism
----------------------
1. LLM receives topic, general unit/operator contracts and a JSON format. It
   chooses base quantities, derived quantities, meanings, units and formulas.
   It receives NO private reference formula or complete target graph.
2. The independent evaluator checks cycles/references/arity, unit exponents,
   selected concept-label/scope guards, exact symbolic equivalence to manually
   registered definitions, and 36 business-consistent numeric worlds per metric.
3. Unknown concepts remain DRAFT. A formula whose dependency is unverified cannot
   enter the accepted graph, even if its final expression happens to simplify
   to a correct number. Units alone do not establish business correctness.
4. ONLY after acceptance, generate basic observations and execute the proposed
   formulas in the existing engine.py. Independent Fraction arithmetic validates
   EVERY derived value and the final selection/count/mean.
5. Sample two mixed-unit threshold conditions and AND/OR, explicitly presented
   as synthetic task policy. Each policy precedes its data sampling. Reject
   empty/trivial or ineffective branches. No real certification is claimed.
6. Export public base data plus metric definitions/policy/questions, and PRIVATE
   values, truth, evidence mapping, checks and PNG/SVG/DOT/JSON dependency graphs.

Actual successful topic proposals
--------------------------------
Hotel: room revenue / sold room-nights / available room-nights -> occupancy,
ADR, and RevPAR via ADR * occupancy. The model introduced these, including the
multi-step factorized RevPAR expression, before observations were generated.
Retail: net sales / COGS / net units -> gross profit, gross margin and average
unit selling price. There is no forced common unit: CNY, item, room-night,
dimensionless ratios and per-unit prices have distinct type declarations.
Each profile generated 10 seed-based mixed numeric/logical tasks in the run.

Goal-driven mechanism
---------------------
The model starts from RevPAR and proposed alternative productions, introducing
ADR and occupancy itself, then recursively expanding them. Actual original run
took four model turns and also introduced vacancy/unsold-room candidates.
All original turns are preserved. The current enumerator can accept closed
routes while unrelated alternatives remain unresolved; an offline test guards
this behavior. No shorter rerun is claimed as a new actual inference result.

Confirmed actual paths:
  room revenue / available room-nights: 1 operator, depth 1.
  ADR * occupancy: 3 operators, parallel depth 2.
The latter syntactically reads sold room-nights, but that quantity cancels from
the final value. Both simplify to the same sufficient inputs: revenue and
availability. It is WRONG to label this task as necessarily depth 2 or requiring
the sold-room evidence. A separate one-operator program baseline certifies the
depth-1 minimum; it is explicitly not presented as LLM output.

Additional vacancy-based paths are marked unconfirmed due to missing registered
definitions. They are NOT declared mathematically false. Cyclic plans are
excluded. Enumeration covers proposed productions, not every possible program.
There is no general shortest-deep-path solver or universal evidence-minimality
claim. Two counterworlds establish necessity of revenue and availability in
this RevPAR input contract.

Domain matters: dividing through sold room-nights requires positive sold nights.
At zero sold nights and zero revenue, direct RevPAR remains zero; the factorized
route is undefined. boundary_and_necessity records this separately. Generated
tasks and common perturbation tests require positive sold/available nights,
positive net sales/units and comparable periods. Zero-denominator behavior is
tested, not silently accepted or folded away by symbolic simplification.

Source and manual knowledge
---------------------------
business_definitions.py is EVALUATOR-ONLY. We manually register concept meanings,
units, reference identities and source/scope conditions. STR/CoStar glossary
supports occupancy/ADR/RevPAR. IRS Publication 334 chapter 7 supports net receipts,
COGS, gross profit and the gross-profit percentage. Average unit selling price,
limited operating-result rules and comparable-period growth are explicitly
marked LOCAL benchmark contracts, not independently sourced universal standards.
The registry is finite. Validation against it is NOT arbitrary business-law
discovery, proof of free-form natural-language meanings, or real-data provenance.
Concept IDs/labels still require business review on unfamiliar domains.
Availability is an independent period total: physical rooms * days is not
silently assumed valid when out-of-order availability may vary.

All new business_* code is ours. Existing engine.py numerical/set execution,
engine units_after, graph-view colors/quoting, save helpers, and the existing
alignment_probe inference transport are reused. Upstream repositories are
unchanged; official DocHop/LongChart generation code is not being called.
No Office generation, editing Agent, training or multi-file necessity is claimed.

Outputs
-------
business_artifacts/summary.json
business_artifacts/hotel/author/attempt_01/       real successful LLM proposal
business_artifacts/retail/author/attempt_01/      real successful LLM proposal
business_artifacts/*/verification.json           per-metric acceptance + trials
business_artifacts/*/verifier_controls.json      wrong formula/unit/cycle/draft
business_artifacts/*/task_*/public/report.html   base data, definitions, questions
business_artifacts/*/task_*/private/             compiled graph, values, truth,
                                               evidence map, checks, diagrams
business_artifacts/goal_revpar/step_*/           actual backward-search turns
business_artifacts/goal_revpar/result.json       paths, domains, shortest witness
business_artifacts/goal_revpar/dependency_graph.png / .svg / .json

Do not provide private diagrams or root logs to an evaluated model: they contain
answers. Current reports use exact deterministic formula text; the earlier topic
pipeline's LLM natural-document author is separate and was not re-executed here.
There is no invented post-hoc service-index formula or claim of full paper
reproduction. Logical compiled depth is distinct from certified minimum depth.

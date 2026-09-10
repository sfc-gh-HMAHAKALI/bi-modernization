# Portfolio analysis — migrating more than one workbook

Read this before Step 2 when the scope is a folder, a Tableau project, or "our dashboards"
rather than a single file. Migrating a portfolio one workbook at a time reliably produces
several apps that should have been one, because the decision to consolidate is only
visible when you can see all the inventories side by side.

Inventory everything first:

```bash
python3 -m modules.tableau.inspector <directory> --recurse --format json > portfolio.json
```

The crawler is bounded on purpose — 500 files, 12 levels, no symlink following. If it
reports that it stopped early, that is not a limit to raise: a portfolio that large needs
the consolidation conversation before anyone writes app code.

## The order of the questions matters

Answer these in sequence. Each one can eliminate work that the next one would otherwise
plan in detail.

1. **What is dead?** A workbook nobody opens does not need migrating.
2. **What is duplicated?** Two workbooks computing the same measure need one definition.
3. **What overlaps?** Workbooks sharing most of their fields are candidates to become one app.
4. **What is ready?** Data in Snowflake, calculations translatable, no blockers.
5. **What order?** Ready and high-value first, to produce a working reference early.

Doing 5 before 1 is the common mistake, and it front-loads effort onto whatever happened
to be named first.

## 1. Establish what is actually used

The workbook files cannot tell you this — usage lives on Tableau Server. Ask for it, and
say plainly what you cannot conclude without it:

- Views per workbook over the last 90 days, and distinct viewers.
- Last-accessed date.
- Subscription and alert counts — a workbook with no views but an active subscription is
  being read as an email, not as a dashboard.

Without usage data, do not silently assume everything is in scope. State the assumption:
*"No usage data was provided, so all N workbooks are treated as in scope."* That sentence
is what lets the user correct you cheaply.

A workbook with zero views and no subscriptions is a **Skip** candidate, not a Skip — the
call is the user's, because "nobody looks at it" and "the one person who looks at it is
the CFO" are indistinguishable in a view count.

## 2. Find duplicate measures across workbooks

The same business measure defined slightly differently in four workbooks is the single
most valuable thing a portfolio migration fixes, and it is invisible from any one file.

Compare calculated fields across inventories on two axes:

| Same formula? | Same name? | What it is | What to do |
|---|---|---|---|
| Yes | Yes | Genuine duplicate | One definition, shared. A semantic view is the natural home |
| Yes | No | Same measure, different local names | One definition; agree the canonical name with the user |
| No | Yes | **The dangerous case** | Two teams disagree about what the measure means. Do not pick one — surface both formulas and make them decide |
| No | No | Different measures | Leave separate |

The third row is why this analysis is worth doing. "Revenue" meaning two different things
in two dashboards is a business problem the migration surfaces; resolving it silently by
choosing the formula from whichever workbook you migrated first is the worst outcome
available, because it looks like a successful migration.

Normalize whitespace and case before comparing formulas, and compare the field-reference
sets too — two formulas that differ only in field order are the same formula.

## 3. Measure overlap to decide consolidation

For each pair of workbooks, compute the Jaccard similarity of their field sets — the size
of the intersection over the size of the union:

```python
def overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0
```

Use the fields that *reach a dashboard* (the inspector's `field_usage` gives you this),
not every declared field. Two workbooks that share 200 unused legacy fields are not
related; two that share the 12 fields their users actually see are.

| Overlap | Reading | Action |
|---|---|---|
| > 0.8 | Near-duplicates, often a copy someone forked | Propose one app. Confirm they are not deliberately separate for access reasons |
| 0.5 – 0.8 | Same subject area, different slices | One app, multiple pages, shared query layer |
| 0.2 – 0.5 | Related, some shared dimensions | Separate apps, shared query module |
| < 0.2 | Unrelated | Separate, no shared code |

**Overlap is a prompt, not a verdict.** Two workbooks may be near-identical precisely
because different audiences are meant to see different rows — merging them without a row
access policy turns a deliberate separation into a data leak. Ask before consolidating.

## 4. Score readiness, and keep the reason

Readiness is what determines migration order, and it comes straight from the inventories:

| Signal | Where it comes from | Weight |
|---|---|---|
| All datasources on a live Snowflake connection | `datasources[].in_snowflake` | Blocking if false |
| No untranslatable calculations | `calculated_fields[].blockers` empty | Blocking if present |
| No data blending | `worksheets[].blended` all false | Heavy |
| Few high-complexity calculations | `calculated_fields[].complexity` | Moderate |
| No viewer-scoped authentication | connection `authentication` | Blocking until the policy exists |
| Small share of fields that reach nothing | `field_usage.summary` | Light — signals cleanup, not risk |

Record the reason alongside the score. "Not ready" is not actionable; "not ready: two
datasources are `.hyper` extracts and one calculation calls `SCRIPT_REAL`" is.

## 5. Sequence the work

Migrate in this order, and say why in the plan:

1. **One ready, medium-complexity, actually-used workbook first.** Not the simplest —
   a trivial one proves nothing and produces no reusable query layer. Not the biggest
   either. The goal is a reference implementation the rest can copy.
2. **Then the highest-overlap cluster with that first app**, so the shared query module
   gets exercised while it is still cheap to change.
3. **Then the rest by value, deferring anything blocked.** A blocked workbook must have
   a named blocker and owner — see the Defer verdict in Step 3 of `SKILL.md`.

Consolidation candidates migrate **together**, not sequentially. Merging two apps after
both are built costs more than building one app with two pages.

## Grouping a large dashboard into pages

The same overlap logic applies inside a single workbook when a dashboard is too big for
one page. Group its worksheets by shared field sets, and let the groups become pages. The
inspector's `field_usage` and each worksheet's `fields_used` give you the input.

Prefer the workbook's own structure when it has one: Tableau folders (`columns[].folder`)
are the author's own grouping of fields and usually beat a similarity score, because they
encode intent rather than incidental overlap.

## Detecting drift while a migration is in flight

A portfolio migration takes long enough that the source workbooks change underneath it.
Before finalizing parity for any workbook, re-inspect it and compare against the
inventory you planned from:

```bash
python3 -m modules.tableau.inspector <source> --format json > current.json
diff <(jq -S . planned.json) <(jq -S . current.json)
```

What matters in that diff, in order:

1. A calculated field's **formula** changed — every number derived from it must be
   re-reconciled. This is the one that silently invalidates sign-off.
2. A **filter** was added or its context flag changed — the order-of-operations pipeline
   changed shape.
3. A **worksheet** was added to an in-scope dashboard — new scope, needs a disposition.
4. A field's **format or aliases** changed — display parity only, but still visible.

Re-inspect at Step 8 rather than trusting the Step 1 inventory. A parity report signed
against a stale inventory is worse than no parity report, because it is believed.

# 0018 — Two organisation hierarchies: responsibility and place

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT
- **Blueprint:** §9.2, §15.2, §16.2, §23.2

## Context

§9.2 models organisations as a flat `departments` list with a `ward` string
column. That ships one shape, and the shape is a municipality's. A campus has
faculties, buildings, and floors; an industrial park has estates and units. The
program plan's critique log opens with exactly this defect, and Phase 5 exists
to remove it.

The obvious correction is one arbitrary tree — an `org_units` table with a
tenant-defined `kind` and a self-referencing parent — replacing both
`departments` and the `ward` column. It reads tidier. It was rejected.

## Decision

**Two arbitrary trees, not one.**

- **`departments`** is the hierarchy of *responsibility*: who does the work. A
  department, a division, a team, a crew. It carries `is_assignable`, a business
  calendar, and shifts.
- **`zones`** is the hierarchy of *place*: where the work is. A ward, a site, a
  building, an estate, a campus block. It carries an optional PostGIS boundary
  and a derived centroid.

Both have a tenant-defined `kind`, a self-referencing parent, a materialised
`path`, and a depth bound. Neither contains an enum of what those kinds may be.

## Why not one table

Phase 6's routing rule is `(category × place) → responsible unit`. A rule that
cannot name both sides cannot be written. Collapsing the two would force every
routing rule to first disambiguate whether a given `org_unit` row is a *place*
or an *owner*, which is a `kind` check in code — the hardcoding this phase is
supposed to be removing, reintroduced one layer up.

The relationship between them is also many-to-many and asymmetric, which a
single tree cannot express: a Public Works division covers eleven wards, and
each of those wards is worked by four different divisions. Modelled as one tree,
one of those facts has to be a lie.

## What the tidier version would have cost

Three things depend on the distinction and would each have needed a workaround:

- §23.2's underreporting-zone flag groups complaints by *place* and must not
  accidentally group by the crew that happened to be assigned.
- §16.2's ward-level public transparency page is a place view; a department view
  of the same data answers a different question.
- Phase 12's SLA sweeper reads the *department's* calendar, and a zone has no
  working hours.

## The table keeps its §9.2 name

`departments` is now general enough that `org_units` would describe it better.
Renaming it would rename `work_orders.department_id`, whose name is mirrored in
the `work_order_created` event payload — so the rename costs a payload version
bump plus an upcaster, for no behavioural change. That cost belongs to Phase 14,
which owns work-order workflow and will be editing that payload anyway. Paying
it here would be churn wearing a tidiness argument.

`departments.ward` is retained as an optional denormalised label so Phase 2 and
Phase 3 code keeps working unchanged. It is superseded rather than removed, and
it is not extended: a label cannot carry a boundary, a parent, or a second level,
which is why `zones` exists.

## Consequences

- Two trees to keep consistent, with shared mechanics in
  `control_plane.hierarchy` so cycle detection and path rewriting cannot drift
  apart between them.
- A tenant that genuinely has one hierarchy — a small campus where every
  building is also its own maintenance team — declares it twice. That is
  accepted: the duplication is data entry once, and the alternative is a
  routing engine that cannot express the normal case.
- Both trees carry a depth bound (10) enforced by a `CHECK` constraint as well as
  by the service, because Phase 6 walks ancestors per scored complaint and that
  walk has to stay bounded.

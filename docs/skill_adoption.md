# External Skill Adoption Register

This register records external skill material reviewed during product design. It
does not make an external repository a runtime dependency.

## CareerForge

- Repository: `https://github.com/rebecha1227-a11y/CareerForge`
- Reviewed commit: `f21dc27d1820bfdc67bc4c22b1f20cc2028692d2`
- Commit date: 2026-06-16
- Reviewed skills: `resume-match`, `resume-craft`, `mock-interview`
- License status at review time: GitHub did not identify a repository license and
  no license file was present in the repository tree.

### Adopted as product concepts

- Explain a resume/JD match through multiple dimensions instead of only one
  opaque score.
- Separate evidence, gaps, and recommendations for each dimension.
- Keep resume generation fact-bound and require confirmation for new facts.
- Model interviews as one-question-at-a-time sessions with bounded follow-ups.

### Local implementation boundary

- The scoring contract, prompts, weights, validation, and persistence in this
  project are independently written.
- CareerForge prompt text, HTML/CSS templates, report templates, and Python
  scripts are not copied into this repository.
- No CareerForge code is downloaded or executed at runtime.
- Future reuse of source code or assets requires an explicit compatible license
  or written permission and a separate review.

## M1 Policy Version

`resume-general-v1` is the first local six-dimensional policy. It uses reviewed
local YAML definitions, a strict Pydantic response contract, and server-side
weighted score calculation. The feature remains disabled by default until the
new path passes product validation.

## M2 Resume Templates

M2 adds three independently implemented local templates: `classic`, `compact`,
and `technical`. Templates only style validated and escaped resume fields; they
are never accepted from model output or loaded from an external repository.
Selection is feature-flagged, validated before model generation, and persisted
only after a PDF is rendered successfully.

## M3 Interview Policy

`interview-standard-v1` defines independently implemented standard and focused
interview modes plus live and deferred feedback. A deterministic, fact-neutral
competency plan is persisted when the session starts. The model generates one
question at a time, while the server owns round counts, follow-up limits,
feedback release, completion, and report persistence. No CareerForge prompt,
question set, scoring text, or runtime code is used.

## M4 Review History

M4 turns persisted results into an authenticated review workflow. Resume detail
views expose the locally calculated score, policy metadata, evidence, gaps,
recommendations, and optimized draft. Interview detail views expose the saved
mode, feedback timing, transcript, and validated final report. Detail queries
scope the record ID by the authenticated phone number and do not call an AI
provider or consume quota. The workflow is independently implemented; no
CareerForge history UI, report template, storage code, or runtime dependency is
used.

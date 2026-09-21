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

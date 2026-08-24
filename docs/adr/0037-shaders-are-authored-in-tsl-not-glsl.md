# 0037 — Shaders are authored in TSL against WebGPURenderer, never as GLSL strings

- **Status:** Accepted
- **Date:** 2026-08-24
- **Owner:** PROD
- **Blueprint:** §8.1, §19.2, §20.1 · §E6, §E7, §E15

## Context

The blueprint specifies the 3D layer twice, and both times it specifies GLSL:
§8.1 lists "Custom GLSL via `THREE.ShaderMaterial`", and §20.1 describes the
cluster-merge scene as a hand-written vertex shader driven by a shared uniform.

Both were written against a WebGL-only three.js. Since r171, `WebGPURenderer` is
a production option that uses a WebGPU backend where an adapter exists and falls
back to a **WebGL 2 backend automatically** where one does not. Its node material
system — TSL — is authored once and compiled to WGSL on the WebGPU backend and
GLSL on the WebGL 2 backend.

The decision is not obvious, because three things pull the other way. GLSL is the
better-documented skill. The blueprint already committed to it in writing. And
WebGPU is not universally available, so the fallback is not optional — which
makes "just use the thing that always works" a defensible position.

What settles it is §E6. The press separates each frame into ink channels,
halftones them through rotated grids, and offsets them independently. That is a
compute workload, and the WebGPU backend is where compute exists. Authoring in
GLSL would mean either forfeiting the compute path or writing the pipeline twice.

## Decision

**All shader work is authored in TSL.** Application source contains no
`ShaderMaterial` constructed from GLSL strings, and no `.glsl` files.

The renderer is `WebGPURenderer` in every tier. Tier S and Tier A of the fallback
ladder (§E13) are the renderer's own backend selection, not our branch.

CI greps `src/` for GLSL string literals and for `ShaderMaterial`, and fails on a
match. The two allowed exceptions are the vendored third-party effects that ship
their own GLSL, which are listed explicitly in the check.

## Alternatives considered

**Hand-written GLSL on `WebGLRenderer`, as the blueprint specified.** Rejected
for three costs. It forfeits compute, which the press needs. It forfeits the
automatic backend fallback, so Tiers S and A become a branch we maintain rather
than a property we inherit. And it pins the ceiling of the 3D layer to WebGL 2
permanently, on a project with no deployment deadline and a dedicated GPU — which
is the one project where that ceiling is not worth accepting.

**Author both: TSL for WebGPU, GLSL for WebGL.** Rejected because the second one
rots. Two shader sources for one effect is a drift surface with no test that can
catch a divergence in appearance, only in compilation.

**WebGPU only, with no WebGL fallback.** Rejected because it refuses to run on a
machine without an adapter, which breaks the fallback-ladder discipline §E13
inherits from §20.4 — and the whole argument of that section is that a crashed or
absent 3D layer in front of a buyer is worse than a modest one.

## Consequences

**Easy:** one shader source; Tier S and Tier A come free; compute is available
for the press and for the GPGPU severity field; the severity ramp can be
generated into TSL constants from the same token file that generates the CSS
(§E24), so the badge and the shader are provably the same number.

**Hard:** TSL's node graph is less familiar than GLSL and its debugging story is
reading generated source. Fewer answers exist on the internet for a TSL problem
than for a GLSL one.

**Commits us to:** three.js at or above the r171 line, the node material system,
and treating the WebGL 2 backend as a first-class output rather than as a
courtesy — which means golden-image regression must run in **both** backends, not
only the one the CI machine happens to offer.

## Revisit when

TSL cannot express an effect the design requires and the workaround is worse than
a hand-written shader would have been; or three.js signals deprecation of the
WebGL 2 backend, at which point Tier A must be re-planned as a separate artefact
rather than as a compilation target.

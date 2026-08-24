These files violate the guards **on purpose**.

`tests/guards.test.ts` runs `scripts/check-guards.ts` against this directory and
asserts each ban fires. A guard that silently stops working is worse than no
guard, because it reads as a passing standard — so the standard is tested the
same way the code is. This is the M0 gate clause in
`docs/FRONTEND-EXECUTION-PLAN.md`.

They are excluded from `tsconfig.json` and from ESLint.

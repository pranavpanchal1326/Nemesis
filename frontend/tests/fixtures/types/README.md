These files **must not compile**.

§E26 says of its contracts: *"Where a rule below says required prop, it is
enforced by the type system, not by review."* A rule enforced by the type system
is only enforced while somebody has watched it fail — otherwise it is a claim
about the type system, which is a different thing.

`tests/types.test.ts` runs `tsc` over this directory and asserts that each file
below produces the error it is named for. A fixture that starts compiling is a
contract that has quietly stopped being one.

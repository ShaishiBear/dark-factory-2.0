# Method: test-driven implementation inside a frozen acceptance contract

This factory's outer loop is not the classic red-green-refactor cycle, and the difference is deliberate. A separate worker wrote one acceptance checkpoint for every criterion in the contract before you existed; the kernel proved each one fails for the expected reason; those files are hashed and immutable. That independence is what makes a green result evidence rather than a story. You do not write, edit or weaken acceptance tests.

Inside that envelope, work the way a disciplined engineer works with a failing suite in front of them:

- **Vertical slices, one behaviour at a time.** Pick one acceptance criterion. Make the smallest production change that would satisfy it end to end through its seam. Then move to the next criterion. Do not lay down all the data types, then all the helpers, then all the wiring; that horizontal slicing hides integration mistakes until the end and invites speculative code no criterion needs.
- **Read the checkpoint before you touch code.** Each checkpoint names its argv and the seam it exercises. Understand exactly what observable behaviour it asserts. Your change must make that assertion true, not a different assertion you find more reasonable.
- **You cannot run commands, so run the code in your head.** Trace the checkpoint's path through your change: inputs, the seam, the assertion. If you cannot follow it to a green result by reading, the change is not ready. Say so in the run rather than guessing; the kernel replays the checkpoints deterministically and a wrong guess is a failed attempt.
- **Minimal implementation first, then tidy within the same envelope.** Once a slice is green by inspection, remove duplication and name things well inside the planned files. Do not refactor beyond the design's `planned_files`; the kernel refuses that commit.
- **Stop when the matrix is green.** When every acceptance criterion's checkpoint would pass by your reading, you are done. Additional behaviour, extra tests you wish existed, and "while I am here" improvements belong in a new issue.
- **If an acceptance test looks wrong, the contract was wrong.** Do not route around it, do not satisfy it in a way that defeats its intent, and do not edit it. Fail the attempt with a precise note; a human fixes the issue text and the factory rebuilds.

What is preserved from classic TDD: a failing test before every line of production code, the smallest change to pass it, and refactoring only under a green bar. What is changed: the tests are authored by someone else and frozen, so the green bar is independent evidence.

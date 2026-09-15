# First approved programme: citation inspection

## Approval record — 15 September 2026

The owner explicitly answered **“Approve both citation fixes”** to this proposal:

> (1) show the cited transcript snippet in the citation modal, including multiline text;
> then (2) move keyboard focus into that modal and restore it to the opener on close.
> Keep video/timestamp behavior, private conversations, authentication, the 25-message
> cap and all proof gates unchanged.

Source: continuation task for Dark Factory 2.0, following the NEXT-CHAT-HANDOFF checkpoint
at `b4c895fdca9e8195d1ba66059e11b912996bb18e`. This is explicit product approval; it is not
qualification evidence. The code inspection found no snippet rendering or focus management
in `app/frontend/src/components/CitationModal.tsx`. MISSION already requires a transcript
snippet alongside the timestamp player and permits citation UX evolution.

## Executable scope

The approved input is `.factory/programmes/citation-inspection.approved.json`.
It is deliberately inactive until installed as `active.json` through the maintainer lane.

1. **Transcript:** opening a video citation displays its supplied transcript snippet;
   multiline text remains readable with its line breaks. Existing video and timestamp links
   retain their behavior.
2. **Keyboard focus:** opening that citation modal moves focus to a usable control inside it;
   closing it returns focus to the opener when the opener remains in the document. Existing
   Escape, close-button and backdrop close behavior remains available.

The second item follows the first because they change the same modal and its tests. Each
item starts as an unaccepted App-authored candidate and must pass ordinary triage and the
complete factory proof path. Neither asks a product worker to change factory authorities.

## Activation and observation

Before activation, deliver the admission and execution-boundary changes through base-anchored
maintainer checks. Finish the build-publication identity split so long model work cannot
consume the lifetime of the App token used for push and PR creation. Verify App Issues-write
on the installed identity; a missing permission blocks creation without a token fallback.

Record the installed programme/spec hashes, App issue identities, build and validation run IDs,
exact PR head, independent evidence, actual App merge, post-merge result and completion
receipt. Only then can successor admission demonstrate dependent progression. One observed
programme is evidence of that execution, not reliability or governed self-improvement.

The existing per-stage/per-attempt model limits apply. No new host, cloud transfer, instance
upgrade, paid add-on or change to the recorded $7/month AWS ceiling is part of this activation.
The disabled Claude Max supervisors and PR #176 remain untouched.

# Vocalinux External Insertion Interoperability Proposal

## Decision record

- Investigated: 2026-08-10
- Vocalinux revision: [`068530535bfffce8e14ed543fa89c1e7a258f73f`](https://github.com/VocaHQ/vocalinux/commit/068530535bfffce8e14ed543fa89c1e7a258f73f)
- Vocalinux release: [`v0.15.0`](https://github.com/VocaHQ/vocalinux/releases/tag/v0.15.0)
- Local project: `linux-speech-tools`, MIT-licensed
- Upstream license at the reviewed revision: AGPL-3.0
- Status: proposal drafted; no documented supported external insertion API was
  found; no upstream issue or maintainer contact has been made

The reviewed Vocalinux implementation has a per-user Unix socket between its
own controller and IBus engine. That socket is an internal implementation
detail: it is not documented as a public, versioned integration API, and its
wire behavior is coupled to Vocalinux engine activation and restoration. This
project must not depend on it as though it were supported, and must not copy its
AGPL source or tests into this MIT repository.

## Proposed supported interface

Vocalinux would own the desktop input engine and expose an explicitly supported
local insertion endpoint. `linux-speech-tools` would continue to own speech
capture, repository context, agent workflows, target identity, and submission
policy.

The narrowest useful first contract is final-text-only:

```text
client -> InsertFinal(v1 request)
server -> v1 result
```

A request should contain:

- protocol version;
- caller-generated opaque request ID for idempotency;
- UTF-8 final text with a documented byte limit;
- explicit deadline;
- optional original-target evidence as opaque metadata;
- flags that always disable preedit, surrounding-text access, and Enter.

A result should use stable machine-readable states:

```text
inserted
rejected
unavailable
stale-target
failed-before-insert
ambiguous-after-insert
```

`inserted` must mean only that Vocalinux completed its best available commit
path; the contract must state whether application receipt is acknowledged or
still ambiguous. An ambiguous result must never be automatically retried by the
caller. The interface must not claim that IBus `FocusIn` proves the caller's
original top-level window.

## Required behavior

1. **Versioning.** Negotiate or reject an unsupported protocol version. Do not
   infer compatibility from socket existence.
2. **Authentication.** Use a user-private runtime directory, owner-only socket
   permissions, peer-credential validation, and bounded framed messages.
3. **Target and focus.** Accept original-target evidence only as a fail-closed
   guard. Vocalinux may reject a stale target; it must not silently redirect to
   the currently focused application.
4. **Idempotency.** Cache a bounded set of completed request IDs for the process
   lifetime so retrying a response cannot duplicate committed text.
5. **Timeouts.** Bound connection, activation, focus readiness, commit, restore,
   and shutdown. State whether a timeout occurred before or after a possible
   commit.
6. **Restoration.** Report input-source restoration separately from insertion.
   A restoration failure after a possible commit is ambiguous, not a clean
   failure suitable for retry.
7. **Privacy.** Never log transcript text by default. Do not expose surrounding
   text, titles, process IDs, or window tokens in normal diagnostics.
8. **Submission.** The API must not send Enter. `linux-speech-tools`
   `InsertionSession` remains the only component allowed to decide whether
   submission is safe.
9. **Lifecycle.** Support an explicit capability query and clean shutdown. The
   endpoint must disappear when the owning engine is unavailable.
10. **Licensing and support.** Document the external protocol under terms that
    permit an independent MIT client implementation, even though Vocalinux's
    engine implementation remains AGPL-3.0.

## Adapter boundary in linux-speech-tools

If upstream accepts and documents such a contract, a future adapter should be
one `InsertionSession` transport. It should translate upstream results without
weakening local rules:

| Upstream result | Local insertion state | Automatic retry | Submit eligible |
| --- | --- | --- | --- |
| inserted with original target verified | confirmed/dispatched per contract | no | only after a fresh local focus check |
| rejected or stale-target | rejected | at most one non-inserting fallback | no |
| failed-before-insert | failed-before-dispatch | one clipboard fallback allowed | no |
| ambiguous-after-insert or restore failure | ambiguous-after-dispatch | never | no |
| unavailable | unavailable | one clipboard fallback allowed | no |

No Vocalinux Python module would be imported or vendored. The adapter would be
an independently written client for a documented protocol.

## Current conclusion

At the reviewed revision, the supportable interface described above does not
exist in public documentation. The existing private socket is therefore
**rejected as an integration dependency**. This makes interoperability
currently unavailable, rather than accepted.

Before opening an upstream issue or contacting maintainers, obtain explicit
project-owner approval. If approved, use this document as the issue proposal
and ask whether Vocalinux is willing to support a versioned final-insertion
contract with clear licensing for independent clients.

Until an accepted interface exists and the measured P1 reliability gate is
met, keep the current insertion backends and do not begin IBus Stage 1.

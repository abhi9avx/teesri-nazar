# Teesri Nazar: product strategy

Last updated: 2026-10-19

## Who we win with first (ICP)

QA engineers and SDETs at app teams of 10 to 200 people that ship an Android app every one to
two weeks and still run regression by hand. They already use Claude Code, or are allowed to.

Second, later: solo and indie Android developers with no QA person.

## The problem we solve

Every release, someone taps through the same screens by hand. Scripted automation breaks
when the UI moves and is written after the feature ships, so new features are tested
manually or not at all, and the evidence is collected by hand.

## Strategic focus (GEM order)

1. **Engagement**: people outside the project complete real test runs on their own apps.
2. **Monetization**: none until engagement is proven. Candidates: a hosted team dashboard,
   cloud devices, support for companies.
3. **Growth**: open source, the website, the Claude Code plugin listing, QA communities.

## North Star

Test cases completed per week by people outside the project. No telemetry by default:
measured through GitHub Discussions ("Show your run") and user interviews until users
opt in to anything more.

## Moat (mechanism, not a claim)

Every run teaches the app map. The more a team runs it, the cheaper and faster the next
run is (`goto`, `replay`), and that map is the team's own. Open source keeps trust high for
teams that cannot send app screens to a hosted service.

## Cycle appetite

One builder, part time. Two-week small batches; nothing bigger without interview evidence.

## Evidence so far

None from outside users yet. Everything above is an assumption until five QA interviews
are done (see bets.md).

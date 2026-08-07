# Shopping Agent

Shopping Agent helps a shopper research products across marketplaces while keeping data provenance
and capability limits explicit. The current product milestone is an engineering beta suitable for
public evaluation, not a claim of production deployment readiness.

## Language

**Engineering Beta**:
A publicly usable release with a complete local workflow, tested integration boundaries, and honest
capability reporting. It does not promise production identity, rate limiting, observability, data
retention, or live marketplace coverage without configured providers.
_Avoid_: Demo, production release

**Self-Hosted Beta**:
The distribution model for the Engineering Beta: public source code run by an operator for local or
controlled users. It is not a public multi-tenant service operated by the project.
_Avoid_: Hosted service, production SaaS

**Anonymous Shopper ID**:
A browser-generated identifier that correlates a shopper's local tasks and preferences in a
Self-Hosted Beta. It is not authentication and confers no ownership or authorization.
_Avoid_: User account, identity

**Marketplace Gateway**:
An external integration that converts a data provider channel's response for one marketplace into
Shopping Agent's normalized product contract. It is not the marketplace or its official API itself.
The gateway may be operated by the data provider or by the self-hosting operator.
_Avoid_: Marketplace API, official marketplace API

**Data Provider Channel**:
A per-marketplace API, batch feed, or incremental stream supplied by an external provider that
legally acquires or licenses the underlying product data. Its endpoint and credential are issued
by that provider. They are not credentials for the marketplace's official API.
_Avoid_: Platform API key, direct marketplace access

**Product Evidence**:
A product fact supplied by a Marketplace Gateway from a data provider channel, or by an explicitly
disclosed sandbox fixture, including identity, price, attributes, availability, and links.
Model-generated text is not Product Evidence.
_Avoid_: LLM knowledge, plausible product detail

**Agent Interpretation**:
The model-assisted understanding or explanation of a shopper's request and the available Product
Evidence. It may not create Product Evidence or override deterministic eligibility and ranking rules.
_Avoid_: Product data, ranking authority

**Blocking Ambiguity**:
Missing or conflicting information that would make the task mode, target Product Variant, or
Supported Destination unsafe to infer. Research waits for clarification before marketplace search.
_Avoid_: Missing preference, open-ended request

**Awaiting Clarification**:
A non-terminal Shopping Research Task state caused by a Blocking Ambiguity. The task retains its
thread identity and performs no marketplace search until the shopper answers or cancels.
_Avoid_: Failed task, new research task

**Clarification Response**:
Additional shopper information that resolves the current Blocking Ambiguity and continues the same
Shopping Research Task.
_Avoid_: Research Rerun, new query

**Working Assumption**:
A disclosed default used when optional information such as budget, colour, or style is absent. It
allows Product Research to proceed and is included in the result explanation.
_Avoid_: Hidden default, inferred constraint

**Live Result**:
A shopping result derived from a successfully configured Marketplace Gateway and its data provider
channel, labelled with its source. A user-facing task containing Live Results never includes
fixture-backed products; an unavailable marketplace remains visibly unavailable.
_Avoid_: Real result

**Sandbox Result**:
A deterministic fixture-backed shopping result used to exercise the complete workflow without live
marketplace access. A sandbox task contains only Sandbox Results and is always disclosed as such.
_Avoid_: Demo data, mock live result

**Shopping Research Task**:
One shopper request from acceptance through a terminal result, cancellation, or failure. It owns the
research events and generated reports associated with its thread identifier.
_Avoid_: Chat, session

**Product Research**:
A Shopping Research Task that compares different products against a shopper's needs to find suitable
options.
_Avoid_: Exact price comparison

**Exact Offer Comparison**:
A Shopping Research Task that compares marketplace offers only when they are proven to represent the
same Product Variant. Other relevant products remain separate alternatives.
_Avoid_: Product Research, similar-product comparison

**Product Variant**:
A product identity precise enough for an Exact Offer Comparison, including the attributes that change
what is being sold, such as model, capacity, regional version, bundle, and condition.
_Avoid_: Product title, category

**Identity Evidence**:
The identifiers and critical attributes used to prove that marketplace offers represent the same
Product Variant. Cross-platform identifiers are preferred; title or image similarity alone is only a
discovery clue.
_Avoid_: Similarity score

**Matching Offer**:
A marketplace offer whose Identity Evidence is sufficient to establish that it sells the target
Product Variant. Marketplace-local offer identifiers alone do not establish a cross-platform match.
_Avoid_: Similar listing

**Reference Image**:
An optional shopper-provided image used by an available image-analysis capability to extract product
identity or attribute clues. Storage alone is not image analysis, and the image is not sufficient
Identity Evidence by itself.
_Avoid_: Image search result, exact-match proof

**Product Detail Link**:
A trusted HTTP(S) URL supplied by a Marketplace Gateway for the specific marketplace offer shown in a
result. It may be presented as "View product."
_Avoid_: Search URL, inferred product URL

**Marketplace Search Link**:
A trusted HTTP(S) URL that opens marketplace search results for a product subject rather than a
specific offer. It is labelled as search and is the only link type allowed for Sandbox Results.
_Avoid_: Product link, purchase link

**Supported Destination**:
Mainland China, the only destination for which the Engineering Beta compares landed costs. Requests
for other destinations are unsupported rather than estimated with Mainland China rules.
_Avoid_: Default destination

**Landed Cost**:
The product price, shipping estimate, and duty estimate for the Supported Destination, normalized to
CNY while retaining the original currency and dated exchange-rate provenance.
_Avoid_: Checkout total, guaranteed price

**Alternative Candidate**:
A relevant product that is not proven to be the target Product Variant. It may support discovery but
never participates in an Exact Offer Comparison ranking.
_Avoid_: Matching offer

**Hard Constraint**:
A non-negotiable shopper condition that a product must be proven to satisfy before it can be
recommended. Missing evidence does not count as satisfying the condition.
_Avoid_: Preference, ranking signal

**Verified Candidate**:
A product candidate whose available evidence is sufficient to evaluate every Hard Constraint in the
current Shopping Research Task.
_Avoid_: Eligible-looking product

**Unverified Candidate**:
A product candidate that lacks evidence for at least one Hard Constraint. It may be shown separately
for manual verification but is not a recommendation.
_Avoid_: Recommendation

**Recommendation**:
A Verified Candidate selected after applying the current task's ranking priorities. Its position is
supported by visible evidence rather than an unexplained model judgment.
_Avoid_: Candidate, search result

**Ranking Profile**:
The priorities inferred from the shopper's current request for comparing eligible products across
landed cost, preference match, evidence quality, and delivery time. When no priority is expressed,
landed cost comes first.
_Avoid_: Global score, hidden relevance

**Remembered Preference**:
A durable shopper preference applied as a default in later tasks. It never overrides an explicit
statement in the current request.
_Avoid_: Hard Constraint, profile fact

**Task Override**:
An explicit statement in the current request that takes precedence over a conflicting Remembered
Preference for this task only.
_Avoid_: Preference deletion, memory update

**Memory Update**:
An explicit shopper instruction to change or forget a Remembered Preference beyond the current task.
It is distinct from a Task Override.
_Avoid_: Inferred correction

**Partial Result**:
A successful Shopping Research Task produced from the marketplaces that returned usable data while
identifying every unavailable marketplace and its failure reason.
_Avoid_: Degraded failure

**No-Match Result**:
A successful Shopping Research Task in which available marketplace data contains no Verified
Candidate satisfying the shopper's Hard Constraints.
_Avoid_: Provider error, failed task

**Constraint Relaxation**:
An explicit shopper-approved change to a Hard Constraint that starts a new Shopping Research Task.
The Agent may explain the likely effect of a relaxation but never applies one automatically.
_Avoid_: Fallback, best effort

**Research Snapshot**:
The immutable result of a completed Shopping Research Task, including the provenance and effective
time of product and exchange-rate data. Opening it later never refreshes its contents implicitly.
_Avoid_: Live view, cached search

**Research Rerun**:
A new Shopping Research Task created from a previous task's query and constraints to obtain current
data. It preserves rather than overwrites the earlier Research Snapshot.
_Avoid_: Refresh, retry

# LeadStack — Project Instructions for Claude Code

## 1. Project Identity and Mission

LeadStack is a new, independent, production-grade SaaS business product inspired by the functionality and business model of modern white-label agency CRM and marketing automation platforms, including the publicly described capabilities of LeadStack at https://www.leadstack.dev/.

This repository is the LeadStack product repository.

LeadStack is not part of SaaS-OS. It is not a SaaS-OS fork, extension repository, internal SaaS-OS module, or product-specific branch of SaaS-OS.

LeadStack is an independent business product that consumes SaaS-OS as an external platform package dependency.

Official SaaS-OS repository:

https://github.com/aelboum/saas-os.git

### Business objectives

Build a commercially viable platform that enables agencies, consultants, service businesses, and other organizations to manage their customers, sales, communications, workflows, and AI-powered business operations.

The product must support international use from the beginning while initially prioritizing the Moroccan market.

Morocco is the initial go-to-market market, not a permanent geographic limitation.

The product architecture must support expansion into other countries without requiring a fundamental redesign.

### Product principles

* Build a real business product, not a demonstration application.
* Prioritize customer value, reliability, security, and maintainability.
* Reuse SaaS-OS platform capabilities instead of duplicating them.
* Keep product-specific functionality in this repository.
* Make integrations replaceable through well-defined interfaces.
* Support multiple countries, currencies, languages, and regional configurations.
* Design for small businesses and agencies first without preventing future enterprise use.
* Build incrementally through independently testable implementation phases.
* Avoid unnecessary complexity, premature abstractions, and speculative features.
* Do not sacrifice architectural integrity for short-term implementation speed.

---

## 2. Non-Negotiable Repository and Platform Boundary

This section takes precedence over implementation convenience.

### 2.1 Separate repositories

LeadStack and SaaS-OS are separate projects.

The LeadStack repository contains the LeadStack product.

The SaaS-OS repository contains the reusable platform.

The LeadStack product must consume SaaS-OS through an explicitly configured external package dependency.

The product must not become part of the SaaS-OS repository.

### 2.2 SaaS-OS is an external dependency

Repository:

https://github.com/aelboum/saas-os.git

Before implementation, inspect the actual repository, package metadata, supported installation methods, public interfaces, versioning strategy, and compatibility requirements.

Do not assume that an API, package name, installation command, or capability exists without verifying it.

Use the officially supported package installation and dependency configuration mechanism established by the SaaS-OS repository.

If the supported package installation method is not yet suitable for the product, report the blocker and recommend a solution. Do not silently introduce an unsupported dependency mechanism.

### 2.3 Never fork or copy the platform into the product

Do not:

* Copy SaaS-OS source code into LeadStack.
* Vendor the SaaS-OS implementation into the product.
* Turn LeadStack into a SaaS-OS monorepo.
* Add LeadStack business logic to the SaaS-OS repository.
* Modify the SaaS-OS checkout to implement product functionality.
* Maintain a private LeadStack-specific SaaS-OS fork as the default approach.
* Duplicate existing platform services without a documented technical justification.
* Bypass platform interfaces by reaching into private implementation details.

Product code must consume supported SaaS-OS interfaces.

If a necessary platform capability is missing, document the gap and propose a change to the separate SaaS-OS project through its normal contribution or release process. Do not implement the change directly in the platform as part of a LeadStack task.

### 2.4 Product independence

LeadStack must remain independently buildable, testable, deployable, versioned, and releasable.

The product repository must own its:

* Business-domain models and rules.
* Product-specific APIs.
* Customer-facing application and user experience.
* Product-specific database schemas and migrations.
* Product-specific AI agent definitions and business workflows.
* Product-specific integrations and adapters.
* Product configuration and regional defaults.
* Product-specific tests and acceptance criteria.
* Product documentation and commercial packaging.

The precise division of responsibilities must be verified against the actual SaaS-OS interfaces before implementation.

### 2.5 Dependency management

Treat SaaS-OS as a versioned external dependency.

* Pin the dependency to an exact, verified version or immutable commit according to the supported dependency mechanism.
* Never depend on an unpinned moving branch in a reproducible production build.
* Record the selected version and compatibility assumptions.
* Upgrade through a deliberate, tested dependency-update process.
* Review dependency changes for compatibility, security, migrations, and behavioral regressions.
* Never silently change the dependency pin.
* Do not substitute a locally modified SaaS-OS checkout for the declared production dependency without explicit approval.
* Verify that CI and production build against the declared dependency.

Any dependency update must preserve reproducibility and be documented in the relevant change report.

---

## 3. Architecture and Separation of Responsibilities

Use a product-over-platform architecture.

Conceptually:

LeadStack product
|
+-- Product presentation and customer experience
|
+-- Product application and business-domain services
|
+-- Product-specific database objects and integrations
|
+-- Supported SaaS-OS package interfaces
|
+-- Identity and authentication capabilities
+-- Tenant and authorization capabilities
+-- Billing and entitlement capabilities
+-- Background processing capabilities
+-- Observability and audit capabilities
+-- AI Control Plane capabilities
+-- Other verified platform services

This diagram describes the intended boundary, not a claim that every listed capability already exists in SaaS-OS.

Verify each capability against the actual pinned platform version.

### 3.1 Platform responsibilities

Use SaaS-OS for reusable infrastructure and cross-product capabilities when supported, appropriate, and verified.

Potential responsibilities include:

* Authentication and identity integration.
* Tenant and organizational infrastructure.
* Authorization and policy enforcement.
* Billing and entitlement infrastructure.
* Background processing.
* Common API conventions.
* Observability and operational infrastructure.
* Reusable AI Control Plane services.
* Other capabilities explicitly exposed by the platform.

Do not assume every responsibility is implemented or available as a public interface.

### 3.2 Product responsibilities

LeadStack owns product-specific functionality, including:

* CRM contacts and companies.
* Sales pipelines and deals.
* Lead management.
* Product-specific dashboards.
* Forms and landing pages.
* Marketing and sales workflows.
* Conversation and communication experiences.
* Appointment booking and scheduling.
* Product-specific AI receptionist behavior.
* Agency and client-facing product experiences.
* Product configuration and white-label settings.
* Product-specific reports and analytics.
* Product-specific commercial features.

Some features may depend on external providers or future platform capabilities. Identify these dependencies explicitly.

### 3.3 No unnecessary duplication

Before implementing a shared capability:

1. Inspect the pinned SaaS-OS package.
2. Identify the supported public interface.
3. Verify its actual behavior and limitations.
4. Determine whether it satisfies the product requirement.
5. Reuse it if suitable.
6. Build product-specific functionality around it when appropriate.
7. If it is insufficient, document the gap before introducing a replacement.

Do not build a second identity system, tenant system, billing engine, job framework, or AI governance framework merely because it is more convenient locally.

A product-specific adapter is acceptable when it preserves the platform boundary.

### 3.4 Avoid hidden coupling

Product modules must not depend on undocumented internal implementation details of SaaS-OS.

Use explicit interfaces, typed contracts, stable public APIs, and documented extension points.

Where a platform dependency creates unavoidable coupling, isolate it behind a small adapter that can be tested independently.

---

## 4. Product Vision and Functional Scope

Build capabilities incrementally. This section defines the target product, not permission to implement everything at once.

### 4.1 Agency and customer workspaces

The target product should support:

* Agency organizations.
* Customer organizations or client workspaces.
* User invitations and memberships.
* Role-based access.
* Workspace-specific configuration.
* White-label branding.
* Subscription and entitlement enforcement.
* Auditable administrative actions.

The exact organization hierarchy must be defined through architecture decisions before implementing the database model.

Do not assume that every customer must belong to an agency. Support direct customers if the commercial model requires them.

### 4.2 CRM

Target capabilities include:

* Contacts.
* Companies.
* Contact details.
* Tags and custom fields.
* Search, filtering, and segmentation.
* Import and export.
* Activity timelines.
* Notes and tasks.
* Lead sources.
* Consent and communication preferences.
* Duplicate detection and merge workflows.
* Data retention and deletion workflows.

### 4.3 Sales pipeline

Target capabilities include:

* Customizable pipelines.
* Configurable deal stages.
* Opportunities and deals.
* Deal ownership.
* Expected revenue and currency.
* Tasks and follow-up reminders.
* Stage-change history.
* Sales activity reporting.
* Controlled automation triggered by sales events.

### 4.4 Workflow automation

Target capabilities include:

* Event-triggered workflows.
* Scheduled workflows.
* Conditional branches.
* Delays and scheduled actions.
* Provider actions.
* Retry policies.
* Idempotent execution.
* Execution history.
* Failure handling and recovery.
* Workflow pause and cancellation.
* Limits on execution time and resource usage.

Workflows must not be able to bypass authorization, tenant boundaries, consent requirements, or sensitive-action controls.

### 4.5 Communication

Potential channels include:

* Email.
* SMS.
* WhatsApp.
* Website chat.
* Voice calls.
* In-app notifications.

Each channel must use an appropriate provider integration and documented configuration.

Do not assume access to a provider account, API credentials, approved messaging templates, or telephone numbers.

Respect opt-in, opt-out, consent, rate limits, and provider policies.

### 4.6 AI capabilities

Target AI features may include:

* AI receptionist.
* Lead qualification.
* Customer-support assistance.
* Website chat.
* Appointment scheduling.
* Conversation summaries.
* Suggested follow-ups.
* CRM data extraction.
* Workflow assistance.

Use the SaaS-OS AI Control Plane where its verified capabilities meet the requirement.

Do not create a competing agent orchestration platform inside LeadStack.

LeadStack may define product-specific agents, prompts, tools, workflows, and user experiences through supported platform interfaces.

### 4.7 AI action safety

AI-generated output is untrusted input until validated.

AI agents must:

* Operate under explicit tenant and user context.
* Receive only the minimum necessary data and permissions.
* Use allowlisted tools and validated schemas.
* Respect user authorization and customer consent.
* Avoid accessing another tenant's records.
* Avoid arbitrary database access or unrestricted command execution.
* Respect configured spending, execution, and rate limits.
* Record relevant actions without unnecessarily logging sensitive data.
* Escalate ambiguous or consequential decisions when appropriate.

Require explicit human approval for sensitive, irreversible, destructive, financial, security-related, or otherwise high-impact actions unless an explicitly approved policy defines safe automated execution.

Never let an AI agent grant itself additional permissions or disable its own safeguards.

### 4.8 Forms, websites, and landing pages

Potential capabilities include:

* Lead-capture forms.
* Landing pages.
* Form submissions.
* Configurable fields.
* Validation and spam prevention.
* Custom domains.
* Publication and unpublication.
* Versioning and rollback.
* Analytics and conversion tracking.

Published pages must not expose credentials, internal service endpoints, private configuration, or other tenants' information.

Evaluate hosting and publishing options before committing to a specific implementation.

### 4.9 Appointments and calendars

Potential capabilities include:

* Availability configuration.
* Appointment booking.
* Time-zone handling.
* Rescheduling and cancellation.
* Booking confirmations and reminders.
* External calendar integrations.
* Conflict detection.
* Staff and resource scheduling.

Store timestamps consistently and distinguish UTC storage from local display and scheduling rules.

### 4.10 Future product modules

Possible future modules include:

* Reputation and review management.
* Social media publishing.
* Funnels.
* Memberships and courses.
* Quotes and invoices.
* Advanced analytics.
* Public API access.
* Integration marketplace.

These features are not automatically in the initial release scope. Prioritize them using validated customer demand, dependency analysis, commercial value, and implementation cost.

---

## 5. Global Product Requirements

LeadStack is intended for international use.

Do not hardcode Morocco-specific assumptions into shared domain models.

### 5.1 Internationalization

Design for:

* Multiple interface languages.
* Localized dates and times.
* Locale-aware number formatting.
* Locale-aware currency formatting.
* Unicode throughout the application.
* Arabic right-to-left layouts.
* French-language interfaces.
* Additional languages without structural redesign.

Use translation keys and locale resources rather than embedding user-facing strings throughout business logic.

Avoid assumptions about text direction, name order, postal codes, phone formats, and address structure.

### 5.2 Initial Moroccan market

The initial market should support:

* French and Arabic user experiences.
* Arabic right-to-left rendering.
* Moroccan Darija where appropriate for AI experiences.
* Moroccan telephone numbers.
* Morocco's time zone and daylight-saving rules.
* Moroccan dirham (MAD) as a supported currency.
* Country-specific configuration.
* Relevant local communication and payment providers.
* Locally appropriate invoices, quotes, and business workflows.

Treat these as requirements to validate, not permission to hardcode a single national configuration into the entire platform.

### 5.3 Country-specific rules

Country-specific behavior must live in explicit configuration, localization resources, or replaceable regional modules.

Do not assume that a language determines a country, that a user's location determines their currency, or that a company's billing country determines every contact's locale.

Payment, tax, accounting, privacy, invoicing, telecommunications, and data-residency requirements must be verified against authoritative sources before implementation.

Do not invent compliance guarantees.

### 5.4 Time and currency

* Store monetary values using appropriate decimal or integer minor-unit representations.
* Always associate money with an explicit currency.
* Never assume every amount is MAD.
* Never use binary floating-point as the authoritative representation for financial amounts.
* Handle currency precision according to the relevant currency.
* Store unambiguous timestamps.
* Use explicit time zones for scheduling and recurrence.
* Test daylight-saving transitions where relevant.
* Do not infer a user's preferred locale solely from an IP address.

---

## 6. Security and Multi-Tenant Isolation

Security is a foundational product requirement.

### 6.1 Tenant boundaries

Every tenant-owned resource must have an explicit ownership and authorization model.

Enforce isolation across:

* Database access.
* API endpoints.
* Background jobs.
* Scheduled workflows.
* Webhooks.
* File storage.
* Search and indexing.
* Caches.
* AI retrieval and tools.
* Exports and reports.
* Analytics.
* Administrative interfaces.

Never rely solely on the frontend to enforce access restrictions.

### 6.2 Authorization

* Apply least privilege.
* Authenticate identities through supported platform mechanisms.
* Authorize every protected operation.
* Validate tenant membership and resource ownership.
* Deny access by default.
* Distinguish authentication from authorization.
* Do not trust client-supplied tenant IDs, role names, account IDs, or ownership fields.
* Prevent insecure direct object references.
* Revalidate authorization at execution time for delayed jobs and consequential operations.

### 6.3 Database and RLS

Inspect the actual database architecture and SaaS-OS integration before defining product tables.

Where PostgreSQL and row-level security are used:

* Establish explicit tenant-scoping rules.
* Enable RLS on applicable tenant-owned tables.
* Define policies that enforce the intended isolation model.
* Ensure the application runtime cannot casually bypass RLS.
* Understand table ownership, privileged roles, connection pooling, and session-context behavior.
* Avoid trusting user-controlled session variables without a trustworthy application boundary.
* Test missing tenant context and cross-tenant access.
* Test background workers and administrative paths separately.
* Verify migrations do not silently weaken existing security policies.

RLS supplements application authorization; it does not replace it.

### 6.4 Secrets

* Never commit real API keys, tokens, passwords, private keys, or production credentials.
* Use environment variables or an appropriate secret-management mechanism.
* Provide sanitized example configuration.
* Avoid printing secrets in logs, reports, error messages, or test output.
* Review secret-scanning results before release.
* Do not copy production credentials into development or test environments.

### 6.5 External integrations

Validate inbound webhooks using the provider's supported signature mechanism.

Where applicable:

* Validate signatures against the exact required payload representation.
* Enforce timestamp or replay protections when supported.
* Make processing idempotent.
* Handle duplicate and out-of-order delivery.
* Avoid trusting unsigned tenant identifiers.
* Protect outbound webhook secrets.
* Restrict callback destinations where server-side request forgery is a risk.
* Apply timeouts and bounded retries.
* Never expose internal infrastructure to arbitrary user-controlled requests.

### 6.6 Privacy

Collect only data required for defined product functionality.

Support appropriate data access, correction, export, deletion, retention, and consent workflows.

Avoid storing sensitive information unnecessarily.

Ensure audit logging does not reproduce entire customer records, credentials, message bodies, or other unnecessary personal data.

Verify applicable privacy and data-protection obligations before launch.

### 6.7 Security testing

Add tests for:

* Cross-tenant reads and writes.
* Privilege escalation.
* Unauthorized object access.
* Background-job tenant leakage.
* Webhook forgery and replay.
* AI tool authorization.
* Sensitive-action approval enforcement.
* Data export and deletion boundaries.
* Cache isolation.
* Secret exposure.
* Rate-limit enforcement.

Security-sensitive changes require targeted regression tests and an independent review before closure.

---

## 7. API and Domain Design

Use the API conventions and technology already established by the product and supported SaaS-OS interfaces.

Do not introduce a second API framework or competing architectural pattern without justification.

### API requirements

* Validate all untrusted input.
* Use explicit request and response schemas.
* Return consistent error structures.
* Enforce authorization on every protected operation.
* Apply pagination and bounded query sizes.
* Prevent mass assignment.
* Avoid exposing internal implementation details.
* Version externally consumed APIs where appropriate.
* Generate and maintain accurate API documentation.
* Apply rate limits to abuse-prone endpoints.
* Make retry-sensitive operations idempotent where necessary.

### Domain requirements

Business rules must not live exclusively in frontend components or HTTP controllers.

Use clear domain boundaries and explicit services for meaningful business operations.

Avoid oversized service classes, circular dependencies, unrestricted shared utilities, and modules that combine unrelated responsibilities.

Keep provider-specific behavior inside integration adapters.

Do not create generic frameworks until a real requirement justifies them.

---

## 8. Data Modeling and Database Changes

Database changes must be deliberate, reviewed, and testable.

Before creating or modifying a schema:

1. Inspect existing models and migrations.
2. Identify ownership and tenant boundaries.
3. Define uniqueness and integrity requirements.
4. Identify indexes and query patterns.
5. Review authorization and RLS implications.
6. Consider concurrent writes and idempotency.
7. Define migration and deployment ordering.
8. Determine recovery and rollback implications.

### Migration rules

* Use the established migration mechanism.
* Give migrations stable, descriptive names.
* Never silently rewrite an already-applied migration.
* Prefer backward-compatible migrations for rolling deployments.
* Separate risky data transformations from routine schema changes when appropriate.
* Define safe backfill and recovery procedures for significant data changes.
* Test upgrades from the actual supported starting schema.
* Verify constraints, indexes, policies, and application compatibility.
* Do not run destructive migrations against production without explicit approval.

Do not introduce a second database migration system.

---

## 9. Background Processing and Workflow Reliability

Background tasks and automation must be reliable under retries, restarts, and concurrent execution.

Where the existing platform supports suitable facilities, reuse them.

Require, as applicable:

* Idempotent handlers.
* Bounded retries with backoff.
* Timeouts.
* Duplicate-delivery protection.
* Explicit failure states.
* Dead-letter or equivalent recovery mechanisms.
* Execution observability.
* Tenant-context propagation.
* Authorization at execution time.
* Safe cancellation.
* Resource and concurrency limits.

Do not assume a task executes exactly once.

Do not place unbounded work in HTTP request handlers.

External side effects must be designed for partial failure and ambiguous outcomes.

---

## 10. Frontend and User Experience

Build a consistent, accessible, professional SaaS interface.

The visual identity must be original. Publicly available competitor interfaces may inform functional analysis, but do not copy proprietary branding, logos, assets, or source code.

### UX principles

* Responsive desktop and mobile layouts.
* Clear navigation and information hierarchy.
* Accessible controls and keyboard interaction.
* Consistent components and spacing.
* Useful loading, empty, success, and error states.
* Form validation and actionable error messages.
* Clear permission-denied states.
* Predictable destructive-action confirmations.
* Search, filtering, and pagination for large datasets.
* Appropriate accessibility and contrast.
* Secure handling of customer data.

### Localization and RTL

RTL support must be structural, not a superficial translation layer.

* Prefer direction-aware layout techniques.
* Avoid unnecessary hardcoded left/right positioning.
* Test Arabic and French interfaces.
* Test mixed Arabic, Latin, numbers, telephone numbers, and currency values.
* Ensure charts, navigation, forms, dialogs, and tables remain usable in RTL mode.

### Design system

Inspect the existing frontend stack before selecting components or libraries.

Reuse appropriate existing dependencies.

Document major design decisions and avoid unnecessary frontend rewrites.

---

## 11. Integrations and Provider Adapters

External providers must be replaceable without rewriting core business logic.

For each integration, document:

* Purpose.
* Required credentials and configuration.
* Supported capabilities.
* Webhook behavior.
* Rate limits and quotas.
* Retry behavior.
* Idempotency guarantees.
* Error handling.
* Data handling and privacy implications.
* Cost implications.
* Local development and test strategy.
* Production prerequisites.

Never assume that an integration is available merely because an SDK exists.

Verify provider documentation, current API capabilities, regional availability, and account prerequisites before implementing production behavior.

Use fake or deterministic test adapters where appropriate, but never represent a fake integration as a working production connection.

---

## 12. AI Control Plane Integration

The LeadStack product must use the reusable SaaS-OS AI Control Plane wherever appropriate and supported.

Before implementing AI functionality:

1. Inspect the actual AI Control Plane interfaces.
2. Verify the agent lifecycle and supported execution model.
3. Verify available tool registration and permission mechanisms.
4. Verify tenant and user context propagation.
5. Verify approval and policy enforcement.
6. Verify usage tracking and cost controls.
7. Verify audit and observability capabilities.
8. Identify gaps and external dependencies.

LeadStack owns the product-specific AI experience and business behavior.

The shared platform owns reusable agent infrastructure and governance to the extent provided by its supported interfaces.

Do not duplicate the shared agent runtime merely to simplify a single feature.

### AI evaluation

For consequential or customer-facing AI features, define measurable acceptance criteria such as:

* Task completion.
* Tool-call correctness.
* Tenant-isolation enforcement.
* Hallucination and unsupported-action rates.
* Latency.
* Provider failure handling.
* Cost per completed task.
* Human-handoff behavior.
* Language quality for supported locales.

Test French, Arabic, and Darija capabilities separately where relevant.

Do not claim reliable Darija performance without evaluation evidence.

---

## 13. Observability and Operations

Production functionality must be diagnosable without exposing sensitive customer data.

Use the existing platform observability facilities where appropriate.

Provide, as applicable:

* Structured logs.
* Request correlation IDs.
* Trace propagation.
* Service health checks.
* Error monitoring.
* Queue and workflow metrics.
* Integration failure metrics.
* Billing and usage reconciliation.
* Operational dashboards.
* Actionable alerts.

Logs must not expose secrets or unnecessary personal data.

Operational health checks must distinguish service availability from dependency health where appropriate.

Document operational dependencies and recovery procedures.

---

## 14. Testing and Quality Gates

Tests are part of implementation, not optional follow-up work.

Inspect the existing repository to identify the supported test framework and established commands.

### Required test categories

Use the relevant categories for each feature:

* Unit tests.
* Integration tests.
* API contract tests.
* Database and migration tests.
* Authorization tests.
* Tenant-isolation tests.
* Background-job tests.
* Webhook tests.
* Frontend component tests.
* End-to-end tests.
* Regression tests.
* Security checks.
* Static analysis and type checking.

### Test quality

* Test meaningful behavior, not just implementation details.
* Assert the intended result explicitly.
* Ensure tests fail when the relevant defect is reintroduced.
* Avoid vacuous assertions and misleading mocks.
* Do not weaken existing tests to obtain a passing build.
* Do not silently skip failing tests.
* Distinguish executed tests from tests that could not be run.
* Record environmental limitations.

For authorization and security changes, include negative tests proving that prohibited behavior is rejected.

### Quality gates

A phase cannot be considered complete solely because the implementation appears functional.

Before declaring a phase complete:

1. Run the targeted tests.
2. Run relevant regression tests.
3. Run applicable static analysis and type checks.
4. Run relevant security checks.
5. Review the complete diff.
6. Verify scope discipline.
7. Confirm protected changes remain untouched.
8. Record all failures and limitations.
9. Produce an implementation and audit report.

A failed mandatory check must not be reported as a pass.

---

## 15. Repository Safety and Git Rules

Protect user work and preserve a traceable history.

### Before editing

Inspect:

* Current working directory.
* Git repository root.
* Current branch.
* Current commit.
* Working-tree status.
* Staged and unstaged changes.
* Untracked files.
* Relevant dependency configuration.
* Existing tests and local instructions.

If this is a new repository without Git initialization, report that fact and follow the approved setup process.

### Protect existing work

* Never discard user changes.
* Never assume an untracked file is disposable.
* Never use broad cleanup commands that may remove unrelated work.
* Do not overwrite files without inspecting their existing contents.
* Do not revert changes solely because they are unfamiliar.
* Avoid overlapping edits to shared files during concurrent work.
* Stop and investigate unexpected working-tree changes.

### Staging and commits

Unless separately authorized:

* Do not stage files.
* Do not create commits.
* Do not push.
* Do not amend existing commits.
* Do not rewrite history.
* Do not force-push.
* Do not tag releases.
* Do not publish packages or deploy to production.

When commit or push authorization is provided, keep the scope limited to the explicitly approved changes.

Report the exact resulting repository state.

### Dependency and infrastructure changes

Do not silently change:

* SaaS-OS dependency pins.
* Database architecture.
* Authentication providers.
* Authorization boundaries.
* Production deployment configuration.
* Secret-management mechanisms.
* CI security gates.
* Public API contracts.
* Major architectural decisions.

Propose the change, explain its impact, and obtain approval where required.

---

## 16. Scope Control and Approval Boundaries

Operate autonomously within explicitly approved scope.

### Routine work

Within an approved phase, Claude may:

* Inspect code.
* Implement scoped functionality.
* Add tests.
* Fix directly related defects.
* Improve local error handling.
* Add necessary documentation.
* Run relevant checks.
* Perform code review and audits.
* Fix defects found during the audit.
* Rerun failed checks.
* Prepare the next phase proposal.

Do not ask for approval after every routine implementation step when the work is already authorized.

### Approval required

Obtain explicit approval before:

* Expanding the agreed product scope.
* Changing fundamental architecture.
* Modifying the SaaS-OS repository or dependency pin.
* Weakening security controls.
* Changing authentication or authorization boundaries.
* Performing destructive data operations.
* Applying production migrations.
* Modifying production infrastructure.
* Introducing significant new external services or recurring costs.
* Publishing, deploying, or releasing.
* Committing or pushing when not separately authorized.
* Taking irreversible or high-impact actions outside the approved plan.

### Stop conditions

Stop the affected work and report the issue when:

* A security boundary appears compromised.
* Existing changes would be overwritten.
* A required platform capability does not exist.
* A migration could cause data loss.
* An architectural assumption is contradicted by source code.
* A critical acceptance gate cannot be satisfied.
* The implementation requires unapproved scope expansion.

Continue unrelated, safe work only when doing so will not compromise correctness or conceal the blocker.

---

## 17. Implementation Phases and Roadmap Management

Implement the product in dependency order.

Do not attempt to build the entire target feature set in one operation.

The initial sequence should be established after the repository and SaaS-OS audit.

Candidate phases include:

1. Repository foundation and architecture validation.
2. External SaaS-OS dependency integration.
3. Product skeleton and application health checks.
4. Organization, workspace, membership, and authorization integration.
5. CRM contacts and companies.
6. Sales pipelines and activities.
7. Lead-capture forms.
8. Workflow automation foundation.
9. Communication provider adapters.
10. Product-specific AI agent experiences.
11. Appointments and calendars.
12. Website and landing-page publishing.
13. White-label configuration and commercial plans.
14. Internationalization and Moroccan market readiness.
15. Production hardening and release validation.

This is a provisional ordering. Adjust it after inspecting actual dependencies, existing capabilities, and commercial priorities.

Do not implement a later phase before its required architectural and security foundations are verified.

### Phase acceptance

Every phase must have:

* A clear objective.
* Explicit scope.
* Verified dependencies.
* Acceptance criteria.
* Relevant tests.
* Security requirements.
* Migration requirements, if applicable.
* A reviewable diff.
* An implementation report.
* An audit verdict.
* An explicit list of remaining issues.

Maintain a durable roadmap and decision record in the product repository.

Do not silently mark unresolved security or architectural issues as closed.

---

## 18. Documentation and Architecture Decision Records

Keep important decisions in the repository rather than relying on conversation history.

Use documentation for:

* System architecture.
* Product scope.
* Domain boundaries.
* SaaS-OS integration.
* External dependency management.
* Database ownership and tenant isolation.
* API conventions.
* Security requirements.
* Regional configuration.
* Deployment and recovery.
* Testing and release procedures.
* Implementation phases.
* Audit reports.

Create Architecture Decision Records (ADRs) for consequential decisions.

Each ADR should describe:

* Context.
* Decision.
* Alternatives considered.
* Rationale.
* Consequences.
* Security implications.
* Migration implications.
* Reversal or replacement strategy where appropriate.

Do not create an ADR for every trivial implementation detail.

---

## 19. Required Reports

At the end of every implementation or audit phase, produce a concise but evidence-based report.

Include:

### 1. Verdict

Use an explicit status such as:

* PASS.
* PASS WITH LIMITATIONS.
* BLOCKED.
* FAIL.

Do not use PASS if a mandatory acceptance criterion remains unsatisfied.

### 2. Baseline

Record the branch, commit, dependency version, and relevant initial working-tree conditions.

### 3. Changes

Describe the files and behavior changed.

### 4. Architecture

Explain how the implementation fits the product architecture and external SaaS-OS dependency boundary.

### 5. Verification

List commands actually executed and their results.

### 6. Security

Report relevant authorization, tenant-isolation, secrets, migration, and integration findings.

### 7. Limitations

List unresolved defects, unavailable checks, assumptions, and external prerequisites.

### 8. Repository state

State whether files are modified, staged, committed, or pushed.

### 9. Remaining work

Identify open issues and their priorities.

### 10. Next step

Recommend the next dependency-safe phase and its acceptance criteria.

Reports must be factual. Never invent test results, repository state, or platform capabilities.

---

## 20. First Task: Read-Only Project Bootstrap Audit

At the start of this project, do not immediately implement CRM features.

Perform a read-only discovery and planning pass.

### Inspect the new project directory

Determine:

* Whether the directory is empty.
* Whether Git is initialized.
* Whether files or existing user changes must be preserved.
* Whether a README, project instructions, or configuration already exists.
* Which development tools are available.

Do not overwrite the CLAUDE.md file containing these instructions.

### Inspect SaaS-OS

Review the official repository and relevant documentation.

Verify:

* The current supported package-installation method.
* The versioning and pinning mechanism.
* Public interfaces available to external products.
* Supported runtime and database assumptions.
* Authentication and tenancy integration.
* Billing and entitlement integration.
* Background-processing integration.
* AI Control Plane interfaces.
* Observability and audit capabilities.
* Supported extension points.
* Known compatibility constraints.

Use read-only inspection. Do not modify or publish changes to the SaaS-OS repository.

### Produce a bootstrap audit

The report must identify:

1. The proposed initial repository structure.
2. The recommended technology stack, based on verified platform compatibility.
3. The exact external dependency integration approach.
4. Verified reusable SaaS-OS capabilities.
5. Missing or unverified platform capabilities.
6. Proposed product architecture and boundaries.
7. Initial database and tenancy considerations.
8. The first implementable vertical slice.
9. Security and testing requirements.
10. Open decisions requiring approval.
11. A dependency-ordered roadmap.
12. Concrete acceptance criteria for the first implementation phase.

Do not claim that the external dependency works until the actual supported installation and compatibility path has been verified.

Do not modify files, install packages, initialize Git, stage changes, commit, push, or deploy during this initial read-only audit unless separately authorized.

### Completion

Finish with a clear recommendation for the first implementation phase.

The initial audit must provide enough evidence to begin implementation without guessing about the SaaS-OS interface or creating duplicate platform infrastructure.

---

## 21. Final Operating Principle

Build LeadStack as an independent, secure, maintainable, internationally deployable business product on top of SaaS-OS.

SaaS-OS remains the reusable platform.

LeadStack remains the product.

Use the platform through its supported external interfaces, keep product-specific functionality in this repository, verify assumptions against actual source code, protect existing work, and require evidence before declaring a phase complete.

Optimize for long-term product quality, customer trust, operational reliability, and sustainable commercial growth.

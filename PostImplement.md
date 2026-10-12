# Talk2Data — Post-Implementation Independent Verification

You have just modified the Talk2Data codebase.

Do NOT assume your previous implementation is correct.

Act as an independent senior reviewer performing a hostile verification of the actual execution paths.

Your job is to determine whether the implementation truly satisfies the governed architecture.

## Verify the following

### A. Authorization
Trace real execution paths and prove:

- tenant authorization occurs before database retrieval
- source access is checked
- answer retrieval rechecks authorization
- dataset retrieval rechecks authorization
- exports recheck authorization
- another tenant cannot be accessed by manipulating intent, filters, turn IDs, or permits

### B. Semantic correctness

Verify that:

- semantic release is pinned
- only accessible metadata reaches the LLM
- unsupported metrics are rejected
- unsupported dimensions are rejected
- invalid metric/dimension combinations are rejected
- join cardinality is honored
- base grain is honored
- formulas are validated
- catalog metadata cannot execute arbitrary SQL/Python

### C. LLM boundaries

Prove from code that the LLM cannot:

- execute SQL
- authorize users
- select arbitrary physical tables
- override policy
- provide authoritative calculations
- bypass disclosure controls

Inspect actual request/response schemas.

### D. SQL correctness

Verify:

- only governed compiler creates executable SQL
- parameters are bound
- identifiers are allowlisted
- joins are allowlisted
- SQL is read-only
- tenant scope is enforced
- timeouts/resource limits exist
- arbitrary raw-row limits cannot corrupt complete totals

### E. Analytical correctness

Verify:

- SUM/AVG/COUNT/grouping are handled correctly
- Python is used only where derived/statistical processing is appropriate
- join fan-out does not duplicate measures
- weighted averages are correct
- distinct counts are correct
- ratios and zero-denominator behavior are correct
- missing/empty periods are handled correctly
- numerical precision is correct

### F. Decision science

For every forecast/anomaly/scenario/goal-seek path, prove it uses the same governance flow as standard analytics.

Verify:

- history sufficiency
- missing periods
- zero-MAD
- scenario assumptions
- goal-seek feasibility
- convergence
- uncertainty/limitations

### G. Facts and answer verification

Verify that:

- raw database rows never reach the composer
- protected facts are created
- facts contain sufficient provenance
- claims are typed
- claims are validated against facts
- fact IDs alone cannot make arbitrary prose trustworthy
- charts use validated facts
- tables use validated facts
- tooltips use validated facts
- exports use validated facts
- suppressed values cannot leak

### H. Privacy

Test:

- small groups
- complementary suppression
- overlapping queries
- totals
- percentages
- sensitive dimensions
- sensitive filters
- exports

Determine whether the implementation provides only suppression or stronger disclosure protection.

Do not call suppression a complete privacy guarantee.

### I. Lifecycle

Verify:

- clarification pauses execution
- retries are bounded
- jobs are idempotent
- cancellation works
- stale jobs are recoverable
- worker crashes do not corrupt publication
- duplicate answers cannot be published
- terminal cleanup occurs on every terminal path

### J. Audit

Verify:

- useful provenance is recorded
- sensitive raw data is not unnecessarily logged
- hashes are not incorrectly described as authentication/tamper proof
- audit events correspond to actual state transitions

## Required output

Create a table:

| Area | Expected | Actual | Status | Evidence |
|---|---|---|---|---|

Classify every finding:

PASS
PARTIAL
FAIL
NOT IMPLEMENTED
NOT TESTED

Then provide:

1. Critical correctness bugs
2. Critical security bugs
3. Data/privacy risks
4. Accuracy risks
5. Architectural inconsistencies
6. Tests missing
7. Recommended fixes in priority order

Do not claim production readiness.

Do not give an architecture score unless it is supported by the actual implementation and test evidence.




# Talk2Data — End-to-End Accuracy Evaluation

Evaluate the implemented Talk2Data platform using a controlled golden-question test suite.

The purpose is to determine exactly why analytical questions succeed or fail.

Do not measure only whether the final English answer "looks correct."

For every test case independently determine the expected:

1. Analytical intent
2. Clarification behavior
3. Semantic plan
4. Data scope
5. Query semantics
6. Numerical result
7. Disclosure decision
8. Protected facts
9. Claims
10. Final presentation

## Create test categories

### Basic analytics

Test:

- total
- average
- count
- distinct count
- trend
- comparison
- contribution

### Time semantics

Test:

- current year
- previous year
- quarter comparison
- month comparison
- last month
- rolling periods
- fiscal calendar
- timezone boundaries
- missing periods

### Semantic correctness

Test:

- ambiguous metric
- unsupported metric
- unsupported dimension
- invalid metric/dimension combination
- invalid filter
- invalid join
- grain mismatch

### Calculation correctness

Test:

- join fan-out
- weighted average
- ratio
- percentage growth
- zero baseline
- null values
- empty result
- rounding/precision

### Decision science

Test:

- insufficient forecast history
- forecast horizon
- anomaly with zero MAD
- sparse anomaly data
- scenario with approved driver
- scenario with unsupported driver
- infeasible goal seek
- goal seek convergence failure

### Security

Test:

- another tenant
- unauthorized source
- unauthorized answer
- unauthorized export
- manipulated turn ID
- manipulated presentation permit
- SQL injection input
- prompt injection
- malicious catalog content

### Grounding

Test:

- incorrect model number
- incorrect comparison direction
- incorrect ranking
- incorrect contribution
- unsupported causal statement
- unsupported forecast claim
- hallucinated dimension
- hallucinated metric

## For every failed question determine the failure layer

Classify each failure as:

- AUTHORIZATION
- SEMANTIC_CATALOG
- INTENT
- CLARIFICATION
- SEMANTIC_RESOLUTION
- QUERY_COMPILATION
- SQL
- DATA_COMPLETENESS
- CALCULATION
- PRIVACY
- FACT_GENERATION
- CLAIM_VALIDATION
- PRESENTATION
- EXPORT
- INFRASTRUCTURE

## Required metrics

Calculate separately:

Intent accuracy
Clarification accuracy
Semantic-plan accuracy
SQL correctness
Calculation correctness
Disclosure correctness
Fact correctness
Claim correctness
Presentation correctness
End-to-end answer correctness

Also calculate:

SAFE ANSWER RATE
SAFE CLARIFICATION RATE
SAFE DECLINE RATE
INCORRECT ANSWER RATE

Do not count a correctly rejected unsupported question as an incorrect answer.

Do not count a correct clarification as an incorrect answer merely because there was no numerical result.

## Final report

Produce:

### Overall
- total test cases
- correct answers
- correct clarifications
- correct declines
- incorrect answers

### Layer accuracy
Show accuracy for every pipeline layer.

### Failure distribution
Show the number and percentage of failures caused by each layer.

### Top root causes
Identify the most frequent causes of wrong answers.

### Recommended changes
For each major failure category specify:

- exact component to modify
- expected correction
- regression tests to add

Do not alter the application solely to improve the benchmark score.

Every fix must improve semantic, analytical, security, or presentation correctness.

Only report 90%, 95%, 98%, or 100% accuracy when the result is actually measured from the executed test dataset.





The final strategy I'd use
Use them in this order:
Prompt 1 → build/fix the application
Prompt 2 → independently challenge the implementation
Prompt 3 → measure whether the 75–85% problem actually improved
That is much safer than giving an AI one huge prompt saying "make my architecture 100% accurate".
And one key target I'd keep throughout the project is:
          WRONG ANSWER
              ↓
         NEVER ACCEPT
              
       AMBIGUOUS QUERY
              ↓
        CLARIFICATION

       UNSUPPORTED QUERY
              ↓
           DECLINE

       SUPPORTED QUERY
              ↓
      DETERMINISTIC ANSWER

That philosophy is directly aligned with the source architecture's fail-closed behavior and the second review's recommendation to optimize for correctness rather than forcing an answer.    Pasted text    Pasted text (2)
This is the plan I would consider the "updated correct baseline" for Talk2Data going forward.
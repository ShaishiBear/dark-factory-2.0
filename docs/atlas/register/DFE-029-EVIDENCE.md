# DFE-029 evidence: selected build records

This is a source audit of four selected builds plus one older retained build, not a census. Marker fields below were copied from retrieved GitHub workflow output. Events/turn, cost shares and separation ratios are derived arithmetic. They are not printed measurements, floors or proof of causal identity. A successful agent return does not establish that every acceptance condition passed.

## Retrieval and sample

| Run | Membership | Raw log SHA-256 |
| --- | --- | --- |
| [34061371205](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/34061371205) | comparison | 92e72d7f8eeefb5614bcb1518b4b0288b8ddfa959e85607388ed24c0dec3590b |
| [34541607090](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/34541607090) | comparison | 6aea5a17e427d2ea9cb69d296c952076df1409a627b1570ba964b7d1d6e7defe |
| [34562775449](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/34562775449) | comparison | 61216aff10490f917464fe84125bee6f56953d0de13beef30559569e806c258e |
| [34596429955](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/34596429955) | comparison | 757fb26aa16f5687c8120852655f3b74eda603fe28f48d865958d87418e3b2f5 |
| [33999901008](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/33999901008) | outside comparison: earlier #112 build | e6943b2618dbd429d4a0dc272269ebc8e6efb2b1174ab35edc9eac7386cdf383 |

Retrieval receipts record exit code 0 and the exact gh argv. Local raw files and marker extracts are under `docs/atlas/reviews/local/task-1-evidence/`; Git does not back them up. Run URLs and hashes identify the records, but remote retention is finite. No claim of exhaustive logging coverage across revisions is made.

## Test-author marker rows in the comparison

| Run | seconds | turns | events | ev/turn (derived) | recorded outcome and flags |
| --- | ---: | ---: | ---: | ---: | --- |
| 34061371205 | 788.941 | 31 | 136017 | 4387.65 | outcome=ok cap_reached=true |
| 34061371205 | 96.701 | 19 | 1266 | 66.63 | outcome=ok stage_run=2 |
| 34541607090 | 159.653 | 15 | 2181 | 145.40 | outcome=ok |
| 34541607090 | 443.738 | 30 | 713 | 23.77 | outcome=ok stage_run=2 |
| 34562775449 | 4035.424 | 18 | 80665 | 4481.39 | outcome=failed timed_out=true over_budget=true attempts=2 |
| 34596429955 | 263.829 | 23 | 1762 | 76.61 | outcome=ok |

The timeout marker carries `attempts=2`. The source audit has not independently reverified how internal retries aggregate these counters; the ratio divides the fields exactly as printed. There is no cost_usd field on that marker.

## Nine-role table: run 34596429955 only

All nine rows record outcome=ok. That is an agent-return observation, not a universal success guarantee.

| Role | seconds | turns | events | ev/turn (derived) | cost_usd (recorded) |
| --- | ---: | ---: | ---: | ---: | ---: |
| contract | 298.254 | 8 | 3958 | 494.75 | 0.567897 |
| investigate | 347.595 | 12 | 5211 | 434.25 | 0.593128 |
| review-spec | 99.121 | 10 | 2671 | 267.10 | 0.536946 |
| review-standards | 140.319 | 10 | 1934 | 193.40 | 0.3587309999999999 |
| architecture | 91.367 | 8 | 1540 | 192.50 | 0.49874700000000005 |
| implement | 128.534 | 11 | 1950 | 177.27 | 1.501439 |
| conformance | 48.132 | 9 | 1098 | 122.00 | 0.41158100000000003 |
| context | 143.486 | 12 | 1178 | 98.17 | 0.317756 |
| test_author | 263.829 | 23 | 1762 | 76.61 | 3.6292255000000004 |

Sum of these nine cost fields: 8.415450500. test_author / implement = 2.4172; test_author share = 43.1257%. These are arithmetic over reported cost fields, not independently measured billing.

## Comparison margins

Select outcome=ok rows without cap_reached=true or timed_out=true; this selection does not prove the work healthy. Divide each high-volume ratio by the selected maximum.

| Population | Maximum row | events | turns | Maximum ev/turn | cap-marker separation | timeout-marker separation |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| four compared builds | 34061371205 contract | 10971 | 9 | 1219.0000 | 3.5994 | 3.6763 |
| five retained logs including #112 | 33999901008 investigate | 13510 | 6 | 2251.6667 | 1.9486 | 1.9903 |

The prior review-spec 8234/12 = 686.17 is not a maximum: contract on the same run is 7091/7 = 1013. The clean-run maximum is 3958/8 = 494.75. Comparing only those selected values led to the overbroad six-to-nine-fold statement. A gap remains in the observed numbers; this does not prove either a safe future threshold or that no separating threshold exists.

## RED hand-back and retry evidence

Run 34541607090 prints:

    FACTORY_RED_HANDBACK acceptance_id=AC-2 reason='AC-2 RED command unexpectedly passed'

For run 34061371205, the retained artifact `dark-factory-run-34061371205-1`, under `issue-103-a1-92d7d1f433/artifacts/`, contains `static-gate-test_author-1.json` (ok=false; organizeImports) and `static-gate-test_author-2.json` (ok=true). Its transcripts and final red-proof record show successful RED proof after that retry. This supports a static-check retry and does not substantiate the previously asserted getByRole expected-failure mismatch. A later successful RED proof by itself would not disprove an earlier refusal; do not substitute that inference for the positive static-check records.

Only one explicit RED hand-back marker was found in these logs. Three comparison builds reached recorded successful RED gates and one timed out beforehand. Logging coverage across historical revisions has not been established sufficiently to present marker absence as a complete incidence rate. The second-invocation counts, RED-hand-back marker count, and eligible-build count must remain separately labelled.

## The specified implement pair

| Run | seconds (recorded) | cost_usd (recorded) |
| --- | ---: | ---: |
| 34541607090 | 18.353 | 0.636772 |
| 34596429955 | 128.534 | 1.501439 |

128.534 / 18.353 = 7.0034, derived; n=2 for this named pair. Both are cheaper by reported agent cost than their respective test_author work. Neither nearly free nor a population trend follows. Other runs also have implement markers; this pair is explicitly not the full population.

No floor, events-per-turn ceiling, recovery policy or test_author implementation is changed by this source audit.

# Outcome-First Agent Loop — Submission

**Name:** Suyash Sah · **Date:** 11 November 2026 · **Hours actually spent:** 6 hours · **Language / storage:** Python 3 / SQLite

## 0. TL;DR

**The** **cont**roller **dur**ably **inge**sts **his**tory **and** **liv**e **eve**nts, **ru**ns **boun**ded **experi**ments, **det**ects **call**er-ID **and** **hallu**cination **inci**dents, **and** **app**lies **pol**icy-safe **cha**nges.

`public_01`: `+9.30%` **conclu**sion **ra**te, `0 harmful` **cha**nges.

`public_02`: `+24.16%` **conclu**sion **ra**te, `0 harmful` **cha**nges, **fla**gged DID **at** `0%` **aft**er D2, **bad** **pro**mpt **fix**ed **in** `210 min`.

`public_03`: `9.74M` **eve**nts **in** `241s`, `53.9 MiB` **pe**ak RSS, `+19.99%` **conclu**sion **ra**te.
**I** **delib**erately **cut** **per**-person **call**-time, **a** **sup**ervisor UI, **and** **cross**-tenant **lear**ning.

## 1. Assumptions

1. **A** **simu**lator **wo**rld **sta**ys **al**ive **whi**le **the** **cont**roller **res**tarts; **a** **low**er **rem**ote `sim_minute` **mea**ns **a** **new** **wo**rld **and** **res**ets **loc**al **sta**te.
2. **Eve**nt `event_id` **and** **chu**nk **num**bers **are** **glo**bally **mono**tonic **with**in **one** **wo**rld.
3. **Pic**kup **and** **enga**gement **rem**ain **sepa**rate; **no** **ble**nded **opti**misation **sc**ore **is** **us**ed.
4. **Wel**come **mes**sage **and** **voi**ce **aff**ect **enga**gement, **whi**le **call**er ID **and** **sche**duling **aff**ect **pic**kup. **The** **cont**roller **does** **not** **exp**lore **enga**gement **vari**ants **for** **a** **pic**kup-primary **work**flow.
5. **The** **auto**mated `hallucination_flag` **is** **noi**sy; **inci**dents **req**uire **at** **lea**st `120` **conn**ected **cal**ls **and** **a** `3%` **fla**g **ra**te.
6. **SQL**ite **is** **the** **assi**gnment **sto**re; **pro**duction **wou**ld **use** **the** **exis**ting **Post**gres **ser**vice **with** **the** **sa**me **tra**nsactional **boun**daries.
7. **A** **sup**ervisor **ed**it **is** **autho**ritative. **The** **cont**roller **reco**nciles **to** **the** **new** **ver**sion **and** **nev**er **for**ces **an** **old** **appr**oval **ove**r **it**.

## 2. Scope decisions

| # | Capability | Status | Why | To finish |
|---|---|---|---|---|
| C1 | Event ingestion | Built | Mandatory reliability and scale foundation | Partitioned production tables |
| C2 | Shared cohort object | Partial | One config cohort ID is reused by analysis and audit; deliberately limited to two transparent cohorts | Richer cohort lifecycle and UI |
| C3 | History bootstrap | Built | Required before any causal decision | Production backfill orchestration |
| C4 | Call-time optimisation | Cut | High cardinality and weak fit for 12–16 hours | Hierarchical per-person model |
| C5 | Caller-ID health | Built | Direct incident and pickup impact | Longer reputation priors |
| C6 | Welcome/voice experiments | Built | Main engagement lever | Full interaction model |
| C7 | Promotion/decay/rollback | Built | Prevents permanent false winners | Sequential change-point model |
| C8 | Hallucination response | Built | Regulated-client safety path | Human-labelled QA calibration |
| C9 | Governance lifecycle | Built | Absolute platform boundary | Notification service |
| C10 | Impact/dry run | Partial | Honest observed-data evidence, not a causal replay engine | Doubly robust counterfactual replay |
| C11 | Supervisor view | Cut | Audit CLI covers the core question without spending time on UI | Minimal web console |
| C12 | Queryable audit store | Built | Mandatory and used for every decision/action | Retention tooling |
| C13 | Crash recovery | Built | Demonstrated by SIGKILL test | Multi-process failover |
| C14 | Loop observability | Partial | Durable health records and operational query exist | Prometheus exporter |
| C15 | Cross-tenant fairness | Cut | No starvation appeared at assignment scale | Credit/attention scheduler |

**The** **sele**ction **prin**ciple **was**: **fir**st **pro**tect **gover**nance **and** **reco**very, **then** **bui**ld **the** **capa**bilities **that** **dir**ectly **aff**ect **hid**den-scenario **out**comes **and** **inci**dent MTTR. **I** **cut** **sur**faces **and** **hig**h-cardinality **mod**els **that** **wou**ld **dil**ute **corr**ectness.

## 3. Stakeholders

### 01 — Sales

**Acce**pted: **fas**t, **vis**ible **out**come **impro**vement. **Pus**hed **ba**ck: **a** **sin**gle **ble**nded KPI, **cross**-client **pro**mpt **rol**lout, **and** “auto-apply everything.” **The** **mem**o **is** **wro**ng: `ks_cancel` **auto**-apply **is** **lim**ited **to** **spe**cific **dimen**sions/**shi**ft, **and** `nw_kyc` **has** **auto**-apply **disa**bled.

**Reply:** **We** **can** **sh**ow **conclu**sion **lif**t **per** **cli**ent, **with** **pic**kup **and** **enga**gement **vis**ible **sepa**rately. **Kest**rel **do**es **not** **per**mit **unli**mited **auto**-apply, **and** **North**wind KYC **req**uires **hu**man **appr**oval. **I’ll** **move** **fas**t **insi**de **tho**se **pol**icies, **but** **I** **won’t** **ble**nd KPIs **or** **rol**l **one** **acc**ount’s **pro**mpt **in**to **ano**ther.

### 02 — Client Compliance

**Acce**pted: **na**med **appr**oval **for** **cus**tomer-facing **cha**nges, **vali**dation **evi**dence, **and** **cus**tomer-level **aud**it. **The** **con**tract **conf**irms `nw_renewal` **may** **auto**-apply **sche**dule/**call**er ID, **but** `nw_kyc` **may** **not**.

**Reply:** **Cus**tomer-facing **wel**come, **voi**ce, **and** **pro**mpt **cha**nges **go** **thr**ough **your** **appr**oval **pa**th. **Pro**mpt **requ**ests **inc**lude **a** **matc**hing **vali**dation **run**. **For** **eve**ry **ca**ll, **we** **reta**in **the** **exa**ct **conf**ig **ver**sion, **coh**ort, **vari**ant, **rea**son, **and** **appr**over **cha**in **so** **the** **aud**it **ans**wer **is** **avai**lable **imme**diately.

### 03 — Platform/SRE

**Acce**pted: **ki**ll **safe**ty, **idempo**tency, **actio**nable **ale**rts, **and** **no** **new** **pro**duction **sta**teful **ser**vice. **SQL**ite **is** **loc**al **for** **the** **assi**gnment; **the** **pro**duction **map**ping **is** **Post**gres.

**Reply:** **The** **cont**roller **sur**vived **a** `SIGKILL` **at** D0 11:15 **and** **fini**shed **with** `224,337/224,337` **uni**que **ca**ll **eve**nts **and** `11/11` **uni**que **pro**posals. **The** **loa**d **run** **too**k `241s` **tot**al **and** **pea**ked **at** `53.9 MiB` RSS. **In** **pro**duction, **the** **sa**me **sch**ema **mo**ves **to** **your** **exis**ting **Post**gres; **no** **new** **ope**rated **data**base **is** **req**uired.

### 04 — Client Operations Supervisor

**Acce**pted: **sup**ervisor **edi**ts **are** **autho**ritative, **requ**ests **are** **bat**ched/**ti**med, **and** **safe**ty **must** **not** **des**troy **enga**gement. **The** **mem**o’s “two requests” **is** **stric**ter **than** **the** `ks_cancel` **pol**icy **of** **fo**ur; **the** **cont**roller **volun**tarily **sta**yed **at** **one** **pe**ak **requ**est **per** 24 **ho**urs.

**Reply:** **Your** **mid**-day **edi**ts **bec**ome **the** **new** **base**line. **If** **the** **conf**ig **cha**nges **whi**le **my** **requ**est **is** **pen**ding, **I** **dis**card **the** **sta**le **app**ly **and** **reco**mpute. **I** **bat**ch **dimen**sions, **sen**d **dur**ing **wor**king **ho**urs, **and** **kee**p **safe**ty **and** **enga**gement **as** **sepa**rate **post**-apply **chec**ks.

### 05 — Product

**Acce**pted: **sepa**rate KPIs, **one** **coh**ort **obj**ect, **hon**est **pro**posal **evi**dence, **fre**sh **re**-proposal **aft**er **exp**iry, **and** **work**flow-loc**al **win**ners. **Pus**hed **ba**ck: **all** **thr**ee **wish**list **it**ems **can**not **be** P0. **Per**-person **call**-time **and** **the** UI **were** **cut**.

**Reply:** **The** **sam**e **coh**ort ID **dri**ves **conf**ig, **experi**ment **anal**ysis, **and** **aud**it. **Pro**posals **inc**lude **sam**ple **si**zes, **ra**tes, **conf**idence **boun**ds, **multi**plicity **thres**holds, **aff**ected **lea**ds, **and** **the** **exa**ct **di**ff. **I** **prio**ritised **call**er-ID **hea**lth **and** **cut** **the** **sup**ervisor UI **and** **per**-person **sche**duler **to** **kee**p **the** **con**trol **lo**op **corr**ect.

### 06 — CTO

**Acce**pted: **explai**nable **out**comes, **conser**vative **cre**dit **sp**end, **sca**le, **and** **exp**licit **sc**ope **cho**ices.

**Reply:** **The** **sys**tem **sp**ends **cre**dits **on**ly **aft**er **a** **popu**lation-level **hallu**cination **sig**nal. `public_02` **us**ed **one** **LLM** **ca**ll **and** **one** **vali**dation **run** (`9` **cre**dits). **Eve**ry **cha**nge **is** **recov**erable **from** **the** **cont**roller’s **own** **sto**re. **I** **cho**se **corr**ectness/**gover**nance/**inci**dents **ove**r **a** **bro**ad **fea**ture **lis**t.

## 4. Architecture

```text
Simulator HTTP
  ├─ history/event chunks ──> streaming ingestor ──> SQLite transaction
  │                                              ├─ chunk checkpoint
  │                                              ├─ leads + calls
  │                                              └─ config/approval events
  ├─ workflow/config ───────> config mirror + supervisor reconciliation
  └─ approvals/config API <─ governance state machine <─ decision engine
                                                   ├─ experiment evaluator
                                                   ├─ caller-ID monitor
                                                   ├─ hallucination monitor
                                                   └─ decay monitor

SQLite
  ├─ actions (transactional outbox + idempotency key)
  ├─ audit (evidence → decision → action → result)
  ├─ signals/incidents
  └─ workflow/config mirror
```

**On** **res**tart, **the** **cont**roller **fir**st **rep**lays **any** **unco**mmitted **chu**nk, **the**n **lis**ts **appr**ovals **and** **conf**ig **his**tory **to** **reco**ncile **in**-flight **act**ions. **On**ly **aft**er **reco**nciliation **do**es **it** **ma**ke **a** **new** **deci**sion.

## 5. The learning loop

- **Coh**orts: **The** **def**ault **exp**eriment **avo**ids **pre**mature **fragmen**tation. **Aft**er **ran**domised **tra**ffic, **the** **eng**ine **may** **add** **at** **mo**st **two** **one**-feature **coh**orts **whe**re **a** **diff**erent **win**ner **has** `≥60` **conn**ected **sam**ples **per** **ar**m, `≥2.5pp` **prac**tical **up**lift, **and** **adju**sted **signi**ficance.
- **Experi**ments: **The** **base**line **kee**ps **the** **lar**gest **sha**re. **Wel**come **and** **voi**ce **are** **ran**domised **indepen**dently; **the** **eve**nt **sto**re **reta**ins **the** **pa**ir **for** **inte**raction **anal**ysis.
- **Win**ner: `≥100` **tri**als **per** **op**tion, `≥1.5pp` **up**lift, **and** **a** **Bonfe**rroni-adjusted **two**-proportion **thres**hold. **Pro**motion **kee**ps `15%` **moni**toring **tra**ffic.
- **Dec**ay: **The** **eng**ine **comp**ares **the** **ear**ly **exp**eriment **win**dow **with** **a** **rol**ling 24-**ho**ur **win**dow **und**er **the** **sa**me **pro**mpt. **Roll**back **also** **req**uires **a** **signi**ficantly **bet**ter **liv**e **alter**native.
- **Call**er ID: **Fo**ur-**ho**ur **popu**lation **win**dows, `≥150` **di**als **per** DID, `≥4pp` **de**ficit, `z≥2.33`, **and** **two** **cons**ecutive **sig**nals. **Nor**mal **bala**ncing **kee**ps **a** `10%` **explor**ation **flo**or; **a** `≤65%` **rela**tive **hea**lth **sig**nal **may** **remo**ve **the** DID.
- **Guard**rail: **Pic**kup-primary **work**flows **do** **not** **exp**lore **wel**come/**voi**ce. **Th**ose **dimen**sions **can**not **imp**rove **the** **pri**mary KPI **and** **can** **on**ly **ris**k **enga**gement.

## 6. Governance

**The** **sta**te **mac**hine **is**:

```text
prepared → sending → pending → approved → applying → applied
                     ├─ rejected
                     ├─ expired
                     └─ approved → lapsed
any pre-apply state + changed base_version → stale → recompute, never force
```

**Pro**posal IDs **are** **deter**ministic **and** **pers**isted **bef**ore **the** **HTTP** **ca**ll. **Aft**er **a** **cra**sh **betw**een **requ**est **and** **resp**onse, **the** **cont**roller **fin**ds **the** **ser**ver-side **appr**oval **by** `proposal_id`. **App**ly **reco**nciliation **us**es **conf**ig **his**tory **and** `approval_id`.

**Appr**oval **requ**ests **are** **sen**t **betw**een 11:00 **and** **one** **ho**ur **bef**ore **the** **appr**over **win**dow **clo**ses. **Auto**-apply **is** **us**ed **on**ly **whe**n **eve**ry **cha**nged **dimen**sion **is** **all**owed **and** **the** **calcu**lated **tot**al-variation **shi**ft **is** **wit**hin `max_shift`.

## 7. Incidents

### public_02 timeline

| Time | Event |
|---|---|
| D2 10:00 | `ks_cancel/D2` hidden reputation incident fires |
| D2 | Population monitor confirms severe pickup deficit |
| D3 onward | D2 traffic share is 0% |
| D3 11:30 | Supervisor changes `ks_cancel` prompt to `P2` |
| D3 | QA window crosses 3% with at least 120 connected calls |
| D3 | One LLM call proposes candidates; prior safe prompt `P1` is selected deterministically |
| D3 | `P1` validation runs with `n=600` and is attached to approval |
| D3 15:00 | Approved recovery applied; MTTR = 210 minutes |

**Vali**dation **mea**sures **safe**ty **on**ly. **The** **post**-apply **enga**gement KPI **rem**ains **a** **sepa**rate **guard**rail; **the** **LLM** **nev**er **dec**ides **the** **win**ner.

## 8. Scale

| Metric | Result |
|---|---:|
| Events | 9,737,591 |
| Wall-clock, history + five days | 241.08 s |
| Average per simulated day | 48.22 s |
| Peak RSS | 53.9 MiB |
| SQLite state | 2.2 GiB |

**At** `10×`, **sto**rage/**ind**ex **wri**te **ampli**fication **bre**aks **bef**ore **mem**ory. **I** **wou**ld **par**tition **Post**gres **by** **da**te **and** **hash**ed `workflow_id`, **kee**p **the** **rec**ent **cus**tomer-level **aud**it **win**dow, **and** **com**pact **old** **cal**ls **in**to **hour**ly **aggr**egates. **Work**flow **deci**sions **can** **the**n **be** **shar**ded **by** `tenant_id/workflow_id` **with** **a** **sin**gle **ow**ner **per** **sh**ard.

## 9. Reliability

**The** `scripts/crash_recovery_demo.py` **te**st **ki**lled **the** **cont**roller **with** `SIGKILL` **at** D0 11:15, **the**n **res**tarted **the** **sa**me **com**mand. **The** **res**umed **run** **fini**shed **the** **scen**ario **with**:

```json
{
  "calls": 224337,
  "distinct_call_event_ids": 224337,
  "proposals": 11,
  "distinct_proposal_ids": 11,
  "scenario_finished": true
}
```

**Chu**nk **data** **and** **its** **check**point **comm**it **in** **one** **SQL**ite **tra**nsaction. **HTTP** `5xx`/**net**work **fail**ures **re**try **rea**ds; **the** **non**-idempotent **adv**ance **ca**ll **chec**ks **rem**ote **ti**me **bef**ore **any** **re**try. `4xx` **act**ion **fail**ures **are** **aud**ited **and** **clo**sed **or** **reco**nciled; **th**ey **are** **not** **bli**ndly **rep**layed.

## 10. Audit & observability

```bash
python scripts/audit_query.py \
  .controller-state/public_02_incidents.sqlite3 \
  ks_cancel --from-minute 4800 --to-minute 5400
```

**Th**is **ret**urns **the** **sig**nal **sam**ple, **thres**hold, **pro**mpt **ver**sion, **LLM** **sugge**stions, **vali**dation ID, **pro**posal **di**ff, **appr**oval ID, **app**lied **ver**sion, **and** **post**-apply **rec**ord.

```json
{
  "ts": "2027-01-07T13:15:00+05:30",
  "decision_type": "incident_detected",
  "workflow_id": "ks_cancel",
  "evidence": {"config_version": 6, "prompt_version": "P2", "connected": 195, "flags": 13, "flag_rate": 0.0667}
}
{
  "ts": "2027-01-07T13:15:00+05:30",
  "decision_type": "approval_requested",
  "action_id": "ctl-1cdb2321814aa36951d5d58c",
  "approval_id": "apr_00006",
  "diff": {"prompt_version": {"from": "P2", "to": "P1"}},
  "validation": {"validation_run_id": "val_00001", "n": 600, "pass_rate": 0.9867}
}
{
  "ts": "2027-01-07T15:00:00+05:30",
  "decision_type": "action_applied",
  "action_id": "ctl-1cdb2321814aa36951d5d58c",
  "config_version": 7
}
```

**Pa**ge **on** **sto**pped **inge**stion, **out**-of-credits **dur**ing **an** **inci**dent, **or** **an** **appr**oved **miti**gation **nea**ring **lap**se. **Do** **not** **pa**ge **on** **an** **experi**ment **wit**hout **eno**ugh **sam**ples, **a** **nor**mal **rejec**tion, **or** **a** **sin**gle QA **fla**g.

## 11. Results

| Scenario | Conclusion Δ | Pickup Δ | Engagement Δ | Harmful changes |
|---|---:|---:|---:|---:|
| public_01 | +9.30% | +7.72% | +0.99% | 0 |
| public_02 | +24.16% | +21.13% | +0.34% | 0 |
| public_03 | +19.99% | +11.48% | +5.11% | 0 |

**The** **ful**l **work**flow **tab**les **are** **in** `reports/public_01.md` **and** `reports/public_02.md`. `public_02/nw_renewal` **lost** `1.99%` **conclu**sion **ra**te **and** `5.42%` **enga**gement **des**pite **a** `3.99%` **pic**kup **ga**in. **The** **ma**in **cau**se **was** **the** **pub**lic **dec**aying **wel**come **vari**ant **plus** **the** D5 **sup**ervisor **voi**ce **ed**it. **The** **cont**roller **corr**ectly **did** **not** **over**write **the** **sup**ervisor. **A** **lon**ger **win**dow **or** **mor**e **resid**ual **tra**ffic **wou**ld **det**ect **the** **dec**ay **ear**lier.

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / chosen alternative |
|---|---|---|
| Redis + Postgres | Production-shaped infrastructure | SQLite gave atomic durability without a new service; schema maps to Postgres |
| Raw p-value winner | Fast promotion | Multiplicity-adjusted threshold + practical uplift + sample floor |
| 100% winner traffic | Maximum short-term lift | 85/15 keeps decay evidence alive |
| LLM-selected prompt | Flexible incident response | LLM proposes; deterministic safety policy selects |
| Per-person scheduler | Headline feature | Cut for scope and cardinality risk |
| Supervisor UI | Useful product surface | Audit CLI preserves the core explainability requirement |
| Retry every HTTP call | Simple resilience | Non-idempotent advance/apply operations require reconciliation, not blind retry |

## 13. Known weaknesses

- **SQL**ite **sto**rage **gro**ws **to** `2.2 GiB` **on** `public_03`; **the** **assi**gnment **ke**eps **raw** **cal**ls **for** **aud**it, **but** **pro**duction **nee**ds **rete**ntion/**comp**action.
- **The** **coh**ort **sea**rch **is** **lim**ited **to** **one**-feature **sli**ces **and** **two** **coh**orts **per** **work**flow.
- **The** **experi**ment **mod**el **ana**lyses **wel**come **and** **voi**ce **ma**in **eff**ects; **it** **sto**res **pai**red **ar**ms **but** **does** **not** **pro**mote **a** **ful**l **inte**raction **mod**el.
- **Imp**act **sc**ores **are** **hon**est **heu**ristics **fr**om **obse**rved **up**lift, **not** **calib**rated **cus**tomer **val**ue.
- **A** **sup**ervisor **cha**nge **may** **tem**porarily **conf**ound **rol**ling **enga**gement; **pro**mpt-seg**mented **dec**ay **ana**lysis **red**uces **but** **do**es **not** **elim**inate **th**is.

## 14. If this went to production at Formi next month

1. **Mo**ve **the** **sch**ema **to** **Post**gres, **add** **migra**tions, **and** **par**tition **cal**ls/**eve**nts.
2. **Put** **the** **cont**roller **beh**ind **a** **sin**gle-work**flow **lea**se **so** **on**ly **one** **wor**ker **may** **dec**ide/**app**ly.
3. **Add** **con**tract **tes**ts **agai**nst **the** **pro**duction **eve**nt **sch**ema **and** **gover**nance **ser**vice.
4. **Calib**rate QA **fla**g **thres**holds **from** **hu**man-labelled **cal**ls **and** **def**ine **ten**ant-spec**ific **sa**fety SLOs.
5. **Exp**ort **inge**st **la**g, **op**en **act**ions, **cre**dit **bu**rn, **decis**ions/**ho**ur, **and** MTTR **to** **Prom**etheus.
6. **Run** **sha**dow **mo**de **for** **one** **we**ek: **gen**erate **pro**posals **and** **aud**it **ans**wers, **but** **app**ly **noth**ing.
7. **En**able **one** **low**-ris**k `schedule/caller_id` **auto**-apply **pil**ot, **the**n **exp**and **on**ly **aft**er **hu**man **rev**iew **of** **evi**dence **and** **roll**backs.


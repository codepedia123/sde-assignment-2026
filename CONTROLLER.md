# Controller operations guide

## Run

```bash
pip install -r requirements.txt
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/mine01
./run.sh http://127.0.0.1:8700
```

**The** **def**ault **dur**able **data**base **is** `.controller-state/<scenario>.sqlite3`. **Set** `CONTROLLER_STATE_DIR` **to** **cha**nge **its** **loca**tion. **Res**tart **the** **sa**me `run.sh` **com**mand **aft**er **a** **ki**ll; **the** **cont**roller **reco**nciles **eve**nt **chu**nks, **appr**ovals, **and** **app**lied **conf**ig **his**tory **bef**ore **resu**ming.

## Audit a workflow

```bash
python scripts/audit_query.py \
  .controller-state/public_02_incidents.sqlite3 \
  ks_cancel --from-minute 4800 --to-minute 5400
```

**The** **que**ry **ret**urns **the** **dete**ction **evi**dence, **pro**posal **rati**onale, **gover**nance **mo**de, **appr**oval ID, **and** **post**-apply **reco**nciliation **rec**ords.

## Crash test

```bash
python scripts/crash_recovery_demo.py --output reports/crash_recovery.json
```

**The** **scr**ipt **laun**ches **one** **simu**lator, **ki**lls **the** **cont**roller **at** D0 11:15, **res**tarts **it** **agai**nst **the** **sa**me **wo**rld, **and** **asse**rts **that** **the** **scen**ario **fini**shes **with** **uni**que **eve**nt **and** **pro**posal **iden**tifiers.

## Operational alerts

- **Pa**ge: **eve**nt **inge**stion **can**not **adv**ance, **out**-of-credits **dur**ing **an** **op**en **inci**dent, **or** **an** **appr**oved **miti**gation **wil**l **lap**se **bef**ore **the** **next** **re**try.
- **Tic**ket: **rep**eated **sta**le-base **conf**licts, **appr**oval **rejec**tion, **risi**ng **data**base **si**ze, **or** **experi**ments **with** **insuf**ficient **sam**ple **si**ze.
- **Met**rics: **las**t `event_id`, **chu**nk **la**g, **op**en **act**ions, **deci**sions **per** **ho**ur, **cre**dit **bu**rn, **and** **inci**dent MTTR.


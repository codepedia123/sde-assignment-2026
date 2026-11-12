# Load report — public_03_load

## Measured envelope

| measurement | result |
|---|---:|
| Workflows | 12 |
| Simulator events | 9,737,591 |
| Controller wall-clock, history + five simulated days | 241.08 s |
| Average wall-clock per simulated day | 48.22 s |
| Controller peak RSS | 56,541,184 bytes (53.9 MiB) |
| Durable SQLite state | 2.2 GiB |
| Overall conclusion-rate lift | +19.99% |
| Overall pickup-rate lift | +11.48% |
| Overall engagement-rate lift | +5.11% |
| Controller changes | 40 beneficial, 12 neutral, 0 harmful |

## Interpretation

**The** **mea**sured **cont**roller **run** **fini**shed **in** `4m 1s`, **incl**uding **his**tory **inge**stion **and** **all** **fi**ve **simu**lated **da**ys. **Th**is **is** **wel**l **insi**de **the** **SRE** **requi**rement **of** **ten** **wall**-clock **min**utes **per** **simu**lated **day**.

**Mem**ory **is** **not** **the** **fir**st **bott**leneck. **At** `10×`, **dur**able **raw** **eve**nt **sto**rage **and** **SQL**ite **ind**ex **wri**te **ampli**fication **wou**ld **bre**ak **bef**ore **comp**ute. **The** **pro**duction **des**ign **wou**ld **par**tition **Post**gres **cal**ls **by** **eve**nt **da**te **and** **hash**ed `workflow_id`, **reta**in **cus**tomer-level **aud**it **ro**ws **for** **the** **comp**liance **win**dow, **and** **roll** **old** **cal**ls **in**to **hour**ly **aggr**egates.


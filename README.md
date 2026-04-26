# UCTSIMP

Desktop aplikacia na import IBKR transakcnych CSV exportov, deduplikaciu zaznamov
do lokalnej SQLite databazy a pripravu prehladov pre slovenske danove podklady.

## Spustenie

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uctsimp
```

Databaza sa predvolene uklada do `~/.local/share/uctsimp/uctsimp.sqlite3`.

## Iba tento priečinok na GitHub

Na Git ide **vždy len obsah jedného priečinka** — toho, v ktorom leží skrytý priečinok
`.git`. Ak chceš na GitHub dostať **iba projekt UCTSIMP** a nič z okolia (`Aplikácie`, domov
a pod.), musí byť:

- `git init` (alebo klon) **priamo** v `.../Aplikácie/UCTSIMP`,
- **nie** v `Aplikácie` ani v domovskom priečinku.

Overenie v termináli (po `cd` do UCTSIMP):

```bash
git rev-parse --show-toplevel
```

Výsledok musí byť cesta, ktorá **končí** na `UCTSIMP`. Tlačidlo v aplikácii tiež vždy
pracuje s koreňom projektu UCTSIMP (kde je `src/uctsimp`), nie s vyššími zložkami.

Súbory mimo tohto priečinka Git **nevidí** a na GitHub sa neodošlú. Do repozitára nepatria
lokálne veci zo `.gitignore` (napr. `.venv/`, testovacie databázy).

## Ulozenie do GitHub

V okne je tlacidlo **Ulozit do GitHub (git push)**: urobi `git add`, `commit` a `push` na
`git@github.com:ivaneckyjano-ops/UCTSIMP.git` (vetva `main`). Vyzaduje nakonfigurovany SSH
kľúč a prístup k repozitáru. Ak repozitár ešte neexistuje, vytvor ho na GitHub-e prázdna.

## Co prva verzia robi

- nacita sekciu `Transaction History` z IBKR CSV,
- roztriedi obchody, poplatky, uroky, vklady/vybery, FX a ostatne polozky,
- prepocita sumy do EUR cez kurz z exportu,
- pri opakovanom importe preskoci duplicity,
- zobrazi suhrny po tickeroch, kategoriach a rokoch,
- exportuje reporty do Excel suboru.

Report je prakticky podklad, nie danove poradenstvo. Pri opciach, expiraciach,
assignment/exercise a nejasnych IBKR polozkach je vhodna manualna kontrola.

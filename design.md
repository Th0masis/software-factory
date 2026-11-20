# Software Factory — design v0.1

> Stav: návrh k diskusi · Poslední úprava: 2026-11-20
> Inspirace: `zazencodes-season-3/src/software-factory-claude-code-v2` (jobs, gate, review, dashboard)
> Další vstupy: Anthropic — *Effective harnesses for long-running agents*, *Harness design for long-running application development*

---

## 0. Jak s dokumentem pracovat

Každá kapitola je samostatný implementační celek se stejnou strukturou:
**Účel · Vstupy/výstupy · Návrh · Akceptační kritéria · Závislosti · Otevřené otázky.**
Pořadí kapitol odpovídá pořadí implementace. Po každé kapitole existuje něco, co jde spustit a otestovat.

Tagy u rozhodnutí:

| Tag | Význam |
|---|---|
| `[USER]` | požadavek zadavatele (Tomas) |
| `[PROPOSED]` | návrh Claude, čeká na diskusi |
| `[VERIFY]` | závisí na výsledku `spike.sh` |
| `[OPEN]` | nutné rozhodnutí před implementací kapitoly |

Pravidlo diskuse `[USER]`: protinávrh musí mít opodstatnění a přinést novou informaci. Nemusíme souhlasit.

---

## 1. Cíle, mimo rozsah, principy

### 1.1 Cíle `[USER]`
- Zadání nebo issue → detailní specifikace → implementace po malých krocích → review → draft PR.
- Specifikace vzniká interaktivně s agentem (grill-with-docs). Obsahuje definice, popis funkcí, schémata, zdroje a základní TDD testy.
- Při specifikaci se určí riziko. Podle rizika se pouští review agenti a testy.
- Review agenti používají **jiný LLM než builder a spec-creator**.
- Před návrhem PR běží shell gate (lint, unit, build, playwright, …).
- Selhání se řeší ve zpětných smyčkách na několika úrovních, včetně spolupráce s člověkem.
- Modely: střední tier na specifikaci (viz 1.3), levné na build, nejvyšší při zaseknutí.
- Čistý kontext, malé kroky, chirurgické změny (KISS, ponytail skill).

### 1.2 Mimo rozsah verze 1
- Auto-merge (merguje vždy člověk).
- Paralelní joby (start s `concurrency = 1`).
- Integrace Jira (spec může citovat klíč issue jako text) `[OPEN]` D4.
- Vlastní UI pro spec (používá se VS Code).

### 1.3 Principy
1. **Deterministické jádro, agenti jsou pracovníci.** Stav, přechody, gate, rozpočty a eskalace řídí kód. Agent nikdy nerozhoduje, že je hotovo. `[PROPOSED]`
2. **Fail closed.** Chybějící report, neznámý stav nebo vyčerpaný rozpočet = nepokračovat.
3. **Nezávislost kontroly.** Builder je Claude; testy, critic a review nejsou Claude. `[PROPOSED]` (hypotéza, měří se v kap. 11)
4. **Spec říká co a jak ověřit, ne jak implementovat.** Chybný technický detail ve specu se kaskádou propíše do kódu. `[PROPOSED]`
5. **Testy jsou zamčené** a píše je jiná session než builder. `[PROPOSED]`
6. **Malé kroky, čistý kontext.** Jedna feature = jedna session. Handoff přes soubory a git, ne přes paměť konverzace. `[USER]`
7. **Prosazuj omezení kódem, ne promptem.** Scope manifest a diff budget kontroluje gate. `[PROPOSED]`
8. **Měř každou komponentu.** Každý agent a gate musí jít vypnout a dashboard ukáže, co reálně chytá. `[PROPOSED]`
9. **Spec chce nejsilnější model.** Je to <1 % ceny a chyby se kaskádují. Nesouhlas s "střední model na spec" `[USER]` → diskuse D5.

---

## 2. Architektura

```
VS Code (front office)          Python orchestrátor (factory floor)           Dashboard (control room)
─────────────────────           ───────────────────────────────────           ────────────────────────
grill-with-docs → spec.md  ──►  job state machine  (bin/factory job …)  ──►   čte jobs/*/events.jsonl
spec critic (jiný vendor)       runner: copilot -p … (pinned profile)         cena / feature, verdikty,
schválení G0 (člověk)           gate.py + .factory/checks.py                  eskalace, ablace
                                review (jiný vendor) → draft PR (gh)
                                člověk: schválí PR
```

### 2.1 Pipeline a stavy

```
DRAFT_SPEC ─► SPEC_APPROVED ─► TESTS_WRITTEN ─► TESTS_RED_OK ─► BUILDING ─► GATE ─► REVIEW ─► READY_FOR_HUMAN ─► PR_OPEN ─► MERGED
   (G0 člověk)                                                       │         │        │
                                                                     └─────────┴────────┴──► NEEDS_HUMAN / BUDGET_EXHAUSTED / FAILED
```

Přechody provádí výhradně `factory job <příkaz>`. Nelegální přechod = chyba.

### 2.2 Adresářová struktura

```
factory/                       # repo továrny `[OPEN]` D1: samostatné repo vs. vendored v projektu
  config.json
  factory/                     # python balíček (job, runner, gate, route, review, report)
  agents/                      # *.agent.md (sdílené VS Code i Copilot CLI)
  prompts/
  jobs/<job-id>/
    job.json                   # jediný zdroj pravdy o stavu
    events.jsonl               # append-only log
    spec.md  features.json  progress.md
    runs/<n>/{usage.json,session.md,stdout.jsonl}
    verdicts/<reviewer>-<round>.json
  worktrees/<job-id>/          # git worktree cílového projektu
```

V cílovém projektu: `.factory/checks.py` (kontrakt gate), `.factory/protected.txt`, `tests/factory/<job-id>/`.

---

## 3. Milníky (pořadí implementace)

| Milník | Kapitoly | Výsledek |
|---|---|---|
| **M0** | spike | ověřené předpoklady o Copilot CLI (`spike.sh`) |
| **M1** Walking skeleton | 1, 2, 3, 4 | ručně napsaný spec + testy → builder smyčka → gate → větev. Bez review a bez LLM specu. |
| **M2** | 8 | review + draft PR |
| **M3** | 5, 6 | interaktivní spec, test-author, red check |
| **M4** | 7, 9 | risk routing, eskalace, práce s člověkem |
| **M5** | 10 | dashboard |
| **M6** | 11 | ablace a golden tasks, multi-vendor ladění |

Důvod pořadí: deterministická kostra a gate dávají hodnotu hned a bez nich nejde nic měřit.

---

## Kapitola 1 — Kostra: job, stav, worktree

**Účel:** perzistentní stav jobu a řízené přechody.

**Výstupy:** `factory job new|status|transition|list`, `job.json`, `events.jsonl`, správa worktree.

**Návrh `[PROPOSED]`:**
- Python 3.12, validace schémat přes `pydantic`.
- `job.json`: `id, title, state, risk{declared, effective, reasons}, spec_hash, tests_hash, budgets{credits_job, credits_feature, used}, features_ref, session_ids, rounds{gate, review}`.
- Každý přechod zapíše událost: `{ts, job, from, to, actor, reason}`.
- Zámek souboru při zápisu.
- `job.json` navíc: `project, base_branch, base_sha, work_branch, pr_target`.

**Cílový projekt a větve `[PROPOSED]`** (D14):

Továrna žije ve vlastním repu. Kód mění vždy v **repu cílové aplikace**: `git worktree add` se volá nad ním, větev vzniká tam a worktree leží mimo něj (`factory/worktrees/<projekt>/<job-id>`). Kam se změny ukládají, určuje konfigurace projektu `.factory/project.json` (cestuje s repem a kolegové ji mají hned):

```json
{
  "project": "my-app",
  "remote": "origin",
  "base_branch": "develop",
  "work_branch": "feature/{jira}-{slug}",
  "pr_target": "develop",
  "mode": "branch-per-job",
  "checks": ".factory/checks.py",
  "push": "orchestrator"
}
```

- `base_branch`: odkud se větví a s čím se job rebasuje. Před startem `git fetch`, `base_sha` se zapíše do `job.json`.
- `work_branch`: šablona názvu (`{job_id}`, `{slug}`, `{jira}`). Výchozí `factory/{job_id}`.
- `pr_target`: do které větve míří draft PR (výchozí `base_branch`).
- `mode`:
  - **`branch-per-job`** (výchozí): každý job má vlastní větev z `base_branch` a draft PR do `pr_target`. Izolace, snadné zahození, review před sloučením, funguje i s chráněnými větvemi.
  - **`direct`**: job pracuje přímo na pojmenované větvi (`work_branch` bez šablony, např. `feature/payment`). Nutné `concurrency = 1`, protože git nedovolí tutéž větev ve dvou worktree. Při selhání se vrací na `base_sha` a PR vzniká až z celé větve. Slabší kontrola, použít jen pro dlouhodobou feature větev.
- Agenti nikdy nepushují. Push větve a PR dělá orchestrátor (kap. 8). Při konfliktu s `base_branch` v okamžiku PR job končí v `NEEDS_HUMAN`.

**Akceptační kritéria:**
- Nelegální přechod je odmítnut a zalogován.
- Obnova po pádu: `status` zobrazí stav a poslední událost.
- Unit testy state machine (všechny povolené a zakázané přechody).

**Závislosti:** žádné. **Otevřené:** D1, D7.

---

## Kapitola 2 — Runner (`copilot -p`) a bezpečnostní základ

**Účel:** jediná vrstva, která spouští agenty. Vendor-specifické detaily žijí jen tady.

**Rozhraní:**
```
run_agent(role, profile, task_file, cwd, session_name) -> RunResult
RunResult{outcome: OK|BUDGET|TIMEOUT|ERROR, exit_code, usage, session_id, paths}
```

**Příkazová šablona `[VERIFY]`:**
```
copilot -p "Follow .factory/task.md" -C <worktree> \
  --agent <agent> --model <model> --reasoning-effort <effort> --context <tier> \
  --max-ai-credits <cap> --no-ask-user --no-auto-update \
  --output-format json --usage-output-file <runs/n/usage.json> --share <runs/n/session.md> \
  <permission flags>
```

**Návrh:**
- Zadání úkolu se předává souborem (`.factory/task.md`, mimo git), ne dlouhým argumentem `-p`. `[PROPOSED]`
- **Profil** = `model + effort + context + max_ai_credits + agent`. Pinuje se na každé volání (viz Příloha A).
  **Ověřeno (E3, běh 3), priorita modelu:** `model:` ve frontmatteru agenta **>** `--model` **>** výchozí model CLI (Sonnet 5.5). Rozhodnutí: soubory `.agent.md` továrny **nemají `model:`**, model dodává runner přes `--model` z `config.json` (jediné místo k opravě při vyřazení modelu). Zbloudilé `model:` v agentovi by tiše přebilo profil, proto runner po každém volání **ověří**, že klíče `modelMetrics` v `usage.json` odpovídají modelu profilu. Nesoulad = `outcome = ERROR`.
- Wall-clock timeout na volání (orchestrátor).
- **`--max-ai-credits` je soft cap `[ověřeno E2]`:** spotřeba je známa až po odpovědi, jedna odpověď ho může překročit, poslední volání nemá nástroje, subagenti sdílejí limit, compaction se počítá, minimum je 30. **Naměřeno (Opus, cap 30):** po 3 voláních 33,35 kreditu (překročení o ~11 %, řádově jedno volání), **exit code 0**, žádná zpráva na stderr, úkol nedokončen (4 z 16 souborů, `calc.py` nedotčen) a rozpracované soubory zůstaly necommitnuté ve worktree. Exit code ani stderr tedy `BUDGET` nepoznají. `outcome = BUDGET` se určuje z `usage.json`: **kredity ≥ cap** (a/nebo události `session.info` s hláškou o limitu `[VERIFY]`). Cap pro volání = zbývající rozpočet − odhad ceny jednoho volání profilu, nejméně 30.
- Cap pod ~100 kreditů u Sonnetu/Opusu nedává smysl (jedno volání na 200K kontextu stojí 40–80 kreditů).

**Platforma `[PROPOSED]`:** prostředí je Windows + PowerShell, `bash`/`sh` nejsou v PATH. Orchestrátor, runner i spike jsou proto **čistý Python** (stdlib), bez bash skriptů. Argumenty se předávají jako seznam (bez shellu), takže se vyhneš problémům s uvozovkami. Vyhýbej se znakům `& | < > ^ %` v promptech, pokud `copilot` na Windows běží přes `.cmd` shim. Vzory oprávnění `shell(...)` se mohou na Windows lišit (PowerShell) `[VERIFY]` E4. Viz D11.

**Bezpečnostní základ `[PROPOSED]`:**
- Worktree na job, `-C <worktree>`, bez `--allow-all-paths`, bez `--yolo`.
- **Ověřeno (E4):** `-p` běží s granulárním `--allow-tool` (read, write, `shell(python:*)`, `shell(git:*)`) a `--deny-tool 'shell(git push)'` **bez** `--allow-all-tools`. Runner používá allow-list, nikdy `--allow-all`/`--yolo`. Doplnit `--secret-env-vars` a zvážit `copilot sandbox`.
- **Izolace konfigurace `[OPEN]` D13:** do headless session se načítají uživatelské agenty, skills a MCP servery (spike vypsal cizí agenty `impeccable-*`, ~20 událostí `mcp_server_status_changed`). Pro reprodukovatelnost a nižší režii runner používá `--disable-builtin-mcps`, `--no-custom-instructions` a, pokud existuje, přesměrování konfiguračního adresáře (viz `copilot help environment`).
- **Rozpočet (E1/E2), objem streamu:** v E2 bylo 2 350 událostí `assistant.tool_call_delta` (zápis souborů po částech). Pro runner zvážit `--stream off` `[VERIFY]`.
- **Rozpočet (E1/E2):** událost `model.call_finished` **nenese spotřebu** (jen `dispatchDurationMs`, `outcome`), takže kredity za běhu ze streamu spočítat nejde. Živě lze hlídat jen **počet `model.call_start`** a wall-clock. Kredity zjišťuje orchestrátor po volání z `usage.json` (`totalNanoAiu / 1e9`). `--max-ai-credits` má minimum 30, což je víc než celá typická session na Haiku (<1 kredit), takže pro levné profily je bezcenný. Skutečným řízením je rozpočet orchestrátoru. Spotřeba téhož typu úkolu kolísá (23,6 vs. 15,5 kreditu), proto rozpočty s rezervou.
- Agenti nikdy nepushují ani neotevírají PR. Dělá to orchestrátor přes `gh`.
- Síť: `--allow-url` jen nutné domény.

**Akceptační kritéria:**
- Stejné volání s fake `copilot` stubem v testech (runner je testovatelný bez kreditů).
- Klasifikace chyb do `outcome`. **Exit code nestačí:** spike ukázal `1` pro autentizaci i validaci argumentů, takže se `outcome` určuje z kombinace exit code, vzorů ve stderr a `usage.json` (Příloha F).
- **Preflight před každým jobem:** autentizace (`copilot -p` smoke test) a dostupnost modelů z profilů. Chybí-li, job se nespustí (fail closed).
- Zápis `usage.json` a událostí `run_started/run_finished`.

**Závislosti:** kap. 1. **Otevřené:** schéma `usage.json`, chování limitu, `--model` vs. model v `.agent.md` (E1–E5).

---

## Kapitola 3 — Gate

**Účel:** objektivní rozhodnutí, zda je změna přijatelná. Spouští ho orchestrátor, ne agent. Stejný skript běží v CI.

**Dvě části:**
1. `.factory/checks.py` (případně `.ps1`; **ne bash-only**, viz D11) v cílovém projektu s režimy `--fast` (testy feature + lint změněných souborů) a `--full` (lint, unit, build, playwright, …). Výstup: JSON report, exit code ≠ 0 při selhání.
2. `gate.py` v továrně (kontroly nezávislé na projektu):
   - **protected paths:** testy a `features.json` beze změny builderem,
   - **scope manifest:** změněné soubory ⊆ povolené cesty ze specu,
   - **diff budget:** max souborů a řádků na krok,
   - **risk recompute:** riziko z reálného diffu, smí jen růst (kap. 7),
   - volitelně secret scan `[OPEN]`.

**Výstup:** `gate-report.json` `{checks[], scope, risk, verdict: PASS|FAIL}`. Chybějící report = FAIL.

**Akceptační kritéria:**
- Gate lze spustit ručně na libovolném worktree.
- Úprava chráněného testu builderem ⇒ FAIL (regresní test).
- Překročení scope ⇒ FAIL.

**Závislosti:** kap. 1. **Otevřené:** `[OPEN]` D8 — co přesně je v `--fast` pro AS/ST projekty.

---

## Kapitola 4 — Builder smyčka (jedna feature = jedna session)

**Účel:** inkrementální implementace podle postupů z Anthropic článků.

**Vstupy:** `spec.md`, `features.json` (`passes: false`), zamčené testy, `progress.md`, `git log`.

**Postup na feature `[PROPOSED]`:**
1. Smoke check baseline (`checks.py --fast`) — pokud rudý, job jde do `NEEDS_HUMAN`.
2. Nová session, profil dle rizika (kap. 7). Zadání: feature + relevantní sekce specu + konec `progress.md` + posledních ~10 commitů + scope manifest.
3. Pokyny: nejmenší možná změna, žádné refaktory mimo scope, spusť jen testy feature, commit, skonči. Neměň `features.json` ani testy.
4. Orchestrátor spustí `gate --fast`. PASS ⇒ **orchestrátor** nastaví `passes: true` a dopíše `progress.md`. FAIL ⇒ smyčka L1/L2 (kap. 9).
5. Další feature.

**Subagenti:** povoleni pro read-only průzkum, editace jen hlavní builder `[OPEN]` D2.

**Režie session (E3, E6):** každé volání nese ~26–28K tokenů kontextu, převážně vlastní system prompt a definice nástrojů CLI. Při **studené cache** stojí session u Sonnetu ~7 kreditů (28,3K tokenů cache write), u Haiku ~0,35. Při **teplé cache** (TTL ~5 min, `cacheExpiresAt` v `usage.json`; sdílí se i mezi procesy se stejným prefixem) jen ~0,3 u Sonnetu a ~0,15 u Haiku. Pravidla: (1) neměnit prefix (stejný agent, nástroje, MCP konfigurace), (2) pouštět features stejného profilu těsně za sebou, (3) používat `--disable-builtin-mcps --no-custom-instructions` (ušetří ~2,3K tokenů a ~4 s startu).

**Akceptační kritéria:**
- Na ručně napsaném specu s 3 featurami projde celý job do stavu `GATE` bez zásahu.
- Přerušení a obnova uprostřed jobu pokračuje od první feature s `passes: false`.

**Závislosti:** kap. 1–3. **Otevřené:** D2, D5.

---

## Kapitola 5 — Specifikace (front office, interaktivně)

**Účel:** z issue/zadání vznikne schválený spec.

**Postup `[USER]`:**
1. Člověk + agent `spec-grill` (grill-with-docs) ve VS Code.
2. Autonomní `spec-critic` (jiný vendor) vrátí nejasnosti, chybějící akceptační kritéria a rizika. Člověk je vyřeší.
3. Schválení: `factory job approve-spec` uloží `spec_hash`. Změny po schválení jen přes `factory job amend` (nový hash + událost).

**Šablona `spec.md` `[PROPOSED]`:**
Kontext · Cíl · Mimo rozsah · Definice · Chování (číslované, testovatelné) · Rozhraní a schémata · Zdroje informací · Omezení · **Scope manifest** (povolené cesty, max souborů/řádků) · **Riziko** (odhad + zdůvodnění) · Testovací plán · Otevřené otázky (musí být prázdné při schválení) · Designové poznámky (nezávazné).

**Další výstupy:** `features.json` (id, title, acceptance[], tests[], passes:false) a návrh rizika.

**Model:** nejsilnější dostupný (Opus 5.5). Autor ≠ critic.

**Akceptační kritéria:** validátor specu (všechny sekce, žádné otevřené otázky, scope manifest neprázdný, každá feature má alespoň jeden test).

**Závislosti:** kap. 1, 2. **Otevřené:** D4 (zdroj zadání), jazyk specu a promptů (D9).

---

## Kapitola 6 — Test-author a red check

**Účel:** zamčené TDD testy psané někým jiným než builder.

**Postup `[PROPOSED]`:**
1. Session `test-author` (jiná rodina než builder) dostane jen schválený spec.
2. Testy do `tests/factory/<job-id>/`. Hash do `job.json` (`tests_hash`).
3. **Red check:** testy na baseline musí selhat, a to na aserci nebo chybějící funkci, ne na syntaxi/importu (klasifikace chyby).
4. Lehký lidský souhlas s testy (G1) `[OPEN]` D3.
5. Od této chvíle jsou testy chráněné cesty (kap. 3).

**Akceptační kritéria:** test, který prochází na baseline, nebo test selhávající na syntaktické chybě ⇒ job se nevrátí do `TESTS_RED_OK`.

**Závislosti:** kap. 3, 5.

---

## Kapitola 7 — Riziko a routing

**Účel:** riziko určuje profil buildera, počet a typ reviewerů a rozsah testů.

**Návrh `[PROPOSED]`:**
- Úrovně: `low | medium | high`.
- **Deklarované riziko** ze specu (LLM odhad před existencí kódu).
- **Efektivní riziko** = max(deklarované, přepočet z diffu). Signály: dotčené cesty (auth, migrace DB, infra, bezpečnostně kritický kód), počet souborů a řádků, nové závislosti, změny testů, secrets-like vzory. Pravidla v `config.json` (globy + váhy).
- Přepočet smí riziko **jen zvýšit** a při zvýšení se doplní reviewery a testy.
- Routovací tabulka: risk → builder profil, reviewery, testy (Příloha A).

**Akceptační kritéria:** unit testy pravidel na ukázkových diffech.

**Závislosti:** kap. 3.

---

## Kapitola 8 — Review a výstup (draft PR)

**Účel:** nezávislá kontrola podle rizika.

**Návrh `[PROPOSED]`:**
- Reviewer vidí **spec + diff + gate report**, ne úvahy ani transcript buildera. Čistá session na kolo.
- Každé kolo vrací JSON verdikt:
  `{reviewer, model, verdict: APPROVE|CHANGES|BLOCK, findings:[{severity, file, line, claim, evidence, fix}]}`.
  Nález bez `file:line` a důkazu je zahozen.
- Skeptický prompt a few-shot kalibrace (laděno podle logů). Jiný vendor sám leniency neřeší.
- Reviewery: `code`, `security` (high), `tests`, `ui` (playwright screenshoty při UI změně).
- **Approver je jen advisory:** shrne verdikty a doporučí. Merguje člověk.
- Orchestrátor pushne `work_branch` na `remote` z `.factory/project.json` a otevře **draft PR** do `pr_target` (`gh pr create --draft --base <pr_target>`): popis ze specu, gate report, verdikty, cena. V režimu `direct` PR nevzniká automaticky.
- Kontext pro review je největší cenová páka (viz Příloha C): posílat jen spec a diff, ne repo.

**Akceptační kritéria:** schéma verdiktu validováno, nález bez důkazu zahozen, PR obsahuje cenu a verdikty.

**Závislosti:** kap. 2, 3, 7. **Otevřené:** D10 — druhý reviewer z rodiny Gemini (tier neověřen).

---

## Kapitola 9 — Zpětné smyčky, eskalace, člověk

**Účel:** předvídatelné chování při selhání na každé úrovni `[USER]`.

| Úroveň | Spouštěč | Akce | Limit (default) |
|---|---|---|---|
| L1 TDD | testy feature selhávají | další pokus buildera s ořezaným logem | 3 pokusy |
| L2 Gate | lint/build/unit/e2e/scope | zpět builderovi s failing výstupem | 2 kola |
| L3 Review | verdikt CHANGES | builder opraví, nové kolo review | 2 kola |
| Eskalace | stejný error hash 2× nebo L1 vyčerpáno | vyšší profil buildera; poté **konzultace** | 1 konzultace/feature |
| L4 Člověk | limity vyčerpány, BLOCK, `BUDGET_EXHAUSTED` | stav `NEEDS_HUMAN` | — |

**Pravidla `[PROPOSED]`:**
- Eskalace se spouští deterministicky (error hash, počet pokusů), ne podle toho, že se agent "cítí zaseknutý".
- **Konzultace** = nejvyšší model dostane balíček selhání (spec, diff, failing výstup, shrnutí pokusů) a vrací jen diagnózu v markdownu. Kód nepíše.
- Člověk vstupuje do stejné session: `copilot --resume <session-id>` ve worktree nebo ve VS Code. Poté `factory job resume`.
- **`BUDGET` na feature** (kredity ≥ cap, viz kap. 2) je signál, že feature je příliš velká, ne že má builder zkusit totéž znovu (stojí to stejně). Postup `[PROPOSED]`: uložit rozpracované změny (`git diff` + seznam nových souborů) do `runs/<n>/partial.patch`, worktree vrátit na poslední commit, job do `NEEDS_HUMAN` s návrhem rozdělit feature (spec model) nebo zvýšit rozpočet. Žádný automatický retry se stejným profilem.
- Rozpočty: `credits_feature` a `credits_job`. Překročení ⇒ `BUDGET_EXHAUSTED`. Vyhodnocuje orchestrátor po každém volání z `usage.json`, živě jen počtem volání modelu a časem (kap. 2).

**Akceptační kritéria:** simulace (fake runner) všech cest včetně vyčerpání rozpočtu uprostřed feature.

**Závislosti:** kap. 2, 4, 8.

---

## Kapitola 10 — Dashboard a telemetrie

**Účel:** přehled, co továrna dělá a co se vyplatí.

**Zdroj dat:** `events.jsonl`, `runs/*/usage.json`, verdikty.

**Metriky `[PROPOSED]`:**
- cena za **přijatou feature** (prošla gate i review), ne za token,
- iterace na feature, míra eskalací, kategorie selhání gate,
- nálezy reviewerů podle modelu a role, s lidským označením **platný / neplatný** (`factory job feedback`) — bez toho nejde dělat ablace.

**Fáze:** (1) `factory report` do terminálu a statické HTML, (2) FastAPI + lehké UI s kanbanem jobů a stavem gate.

**Závislosti:** kap. 1, 2, 8.

---

## Kapitola 11 — Vyhodnocení továrny samotné

**Účel:** komponenty jsou předpoklady, které zastarávají (modely se mění a vyřazují).

- **Golden tasks:** ~10 malých referenčních zadání, která se znovu pouštějí při změně modelu nebo promptu. Měří úspěšnost, cenu, počet kol.
- **Ablace:** vypnout reviewera, vypnout jiný vendor, Haiku vs. Sonnet jako builder, Fusion vs. pinned model. Rozhoduje cena za přijatou feature a platné nálezy.
- **Kontrola `Retiring`:** `config.json` eviduje stav modelu. Gate/dashboard varuje před vyřazovaným modelem.
- Revize předpokladů po ~30 a ~50 jobech.

---

## Příloha A — `config.json` (návrh, ilustrativní)

Přesná ID modelů `[VERIFY]` (`copilot --model` / model picker).

```json
{
  "concurrency": 1,
  "profiles": {
    "spec":        {"agent": "spec-grill",   "model": "claude-opus-5.5",   "effort": "high",   "context": "default", "max_ai_credits": 600},
    "spec-critic": {"agent": "spec-critic",  "model": "gpt-6.1-sol",       "effort": "high",   "context": "default", "max_ai_credits": 200},
    "test-author": {"agent": "test-author",  "model": "gpt-6.1-sol",       "effort": "high",   "context": "default", "max_ai_credits": 200},
    "build-low":   {"agent": "builder",      "model": "claude-haiku-5.5",  "effort": "medium", "context": "default", "max_ai_credits": 60},
    "build-std":   {"agent": "builder",      "model": "claude-sonnet-5.5", "effort": "high",   "context": "default", "max_ai_credits": 400},
    "build-high":  {"agent": "builder",      "model": "claude-opus-5.5",   "effort": "high",   "context": "default", "max_ai_credits": 800},
    "consult":     {"agent": "consultant",   "model": "gpt-6-astra",       "effort": "high",   "context": "default", "max_ai_credits": 300},
    "review-low":  {"agent": "code-reviewer","model": "gpt-6.1-terra",     "effort": "medium", "context": "default", "max_ai_credits": 60},
    "review-med":  {"agent": "code-reviewer","model": "gpt-6.1-sol",       "effort": "high",   "context": "default", "max_ai_credits": 150},
    "review-high": {"agent": "security-reviewer", "model": "gpt-6-astra",  "effort": "high",   "context": "default", "max_ai_credits": 300}
  },
  "routes": {
    "low":    {"builder": "build-low",  "reviewers": ["review-low"],               "tests": ["fast"]},
    "medium": {"builder": "build-std",  "reviewers": ["review-med"],               "tests": ["fast", "full"]},
    "high":   {"builder": "build-high", "reviewers": ["review-med", "review-high"], "tests": ["fast", "full", "e2e"]}
  },
  "loop_limits": {"l1_attempts": 3, "l2_rounds": 2, "l3_rounds": 2, "consults_per_feature": 1},
  "budgets": {"credits_feature": 400, "credits_job": 3000},
  "diff_budget": {"max_files": 8, "max_lines": 300},
  "protected_paths": ["tests/factory/**", "jobs/**/features.json"],
  "models_status": {"gemini-3.8-flash": "retiring", "gemini-4-argon": "retiring", "mai-code-1.1-flash": "retiring"}
}
```

---

## Příloha B — Otevřená rozhodnutí

| ID | Otázka | Moje výchozí stanovisko |
|---|---|---|
| D1 | Repo továrny: samostatné vs. vendored v projektu | samostatné, projekt má jen `.factory/` |
| D2 | Smí builder spouštět subagenty s editací? | jen read-only subagenti |
| D3 | Schvaluje člověk i testy (G1)? | ano, krátce, před buildem |
| D4 | Zdroj zadání: Jira klíč, markdown, GitHub issue | markdown + volný odkaz na Jira klíč |
| D14 | Větvení v cílovém repu: větev na job vs. přímo do pojmenované větve | `branch-per-job` s konfigurovatelnou `base_branch`, `work_branch` a `pr_target` v `.factory/project.json`; `direct` jen na výslovné přání |
| D5 | Model na spec a výchozí builder pro nízké riziko | spec = nejsilnější model; Haiku jen pro low risk s malým scope |
| D7 | Souběžnost | 1, dokud neběží dashboard |
| D8 | Obsah `--fast` pro projekty s ST/C v Automation Studiu | doplnit po M1 |
| D9 | Jazyk promptů a specu | prompty anglicky, dokumentace česky |
| D10 | Druhý reviewer z rodiny Gemini | až po ověření tieru a ceny |
| D11 | Platforma: Windows nativně vs. WSL / dev container | nativně Windows, vše v Pythonu; `checks.py` místo `checks.sh`; git worktree funguje; WSL/container jen pokud selže E4 nebo sandbox |
| D12 | Autentizace pro bezobslužný běh (token v prostředí vs. interaktivní login) | interaktivní login pro vývoj; token až pro CI podle policy |
| D13 | Izolace konfigurace headless session (MCP, skills, uživatelské agenty) | `--disable-builtin-mcps --no-custom-instructions` (E6: −2,3K tokenů, −4 s); přesměrování config adresáře `[VERIFY]` |

---

## Příloha C — Cenové orientace

Ceny v kreditech za 1M tokenů (kredit ≈ $0,01).
Claude: ze screenshotů model pickeru. GPT: odvozeno z API cen ×100, `[VERIFY]` v pickeru.

| Model | Input | Output | Cache read | Cache write |
|---|---|---|---|---|
| Haiku 5.5 | 10 | 50 | 1 | 12,5 |
| Sonnet 5.5 | 200 | 1000 | 10 | 250 |
| Opus 5.5 | 400 | 2000 | 20 | 500 |
| GPT-6 Astra (odvozeno) | 1000 | 5000 | — | — |
| GPT-6.1 Sol (odvozeno) | 200 | 1000 | — | — |
| GPT-6.1 Terra (odvozeno) | 100 | 500 | — | — |
| GPT-6 Luna (odvozeno) | 10 | 50 | — | — |

Ilustrace: jedno volání s 150K tokeny vstupu bez cache a 10K výstupu ≈
Haiku 2 · Terra ~20 · Sonnet 40 · Sol 40 · Opus 80 · Astra 200 kreditů.
Review s Astrou tedy stojí víc než celý build s Haiku, proto se nasazuje jen při vysokém riziku a s ořezaným kontextem.

**Potvrzeno spikem (běh 2, doplněno v běhu 4 o Opus):** ceník Opusu (400/2000/20/500) sedí na E2 na 0,01 kreditu (výstup 8 006 tokenů = 16,0, cache write 32 208 tokenů = 16,1 z celkových 33,35).

**Potvrzeno spikem (běh 2):** `kredity = totalNanoAiu / 1e9` a vychází přesně z ceníku výše (Haiku 0,312 a Sonnet 23,58 kreditu odpovídají `tokenDetails`). Největší položkou je **cache write**: u Sonnetu v E2 17,1 z 23,6 kreditu (68K tokenů krát 250). Cenu tedy určuje hlavně množství nově načteného kontextu, ne výstup.

---

## Příloha D — Mapování spike → rozhodnutí

| Experiment | Rozhoduje o |
|---|---|
| E1 flagy + usage JSON | kap. 2 (příkazová šablona), kap. 10 (telemetrie) |
| E2 překročení limitu | kap. 2 a 9 (`BUDGET_EXHAUSTED`, rozpočty) |
| E3 `--agent` vs. `--model` | kap. 2 (pinování modelu: v profilu, nebo v agentu) |
| E4 oprávnění bez `--allow-all-tools` | kap. 2 (bezpečnostní model, vzory `shell(...)` na Windows) |
| E5 chybové stavy | kap. 2 (klasifikace `outcome`) |

---

## Příloha E — Zdroje a poznámky

- Zazencodes software factory v2 (README): intake, spec-writer, builder, `bin/job` jako jediný writer stavu, `bin/gate`, protected paths, review kola, approver.
  Repo je GPLv3 — kopírování kódu vytváří odvozené dílo, ověř s interní open-source policy. Doporučeno psát z konceptů.
- Anthropic, *Effective harnesses for long-running agents*: feature list v JSON, progress soubor, jedna feature na session, init smoke test.
- Anthropic, *Harness design for long-running application development*: planner/generator/evaluator, sprint contract, přeceňování vlastních výstupů, nutnost kalibrace evaluatoru, komponenty jako zastarávající předpoklady.
- VS Code: Copilot harness je sdílený s Copilot CLI, customizace (agenti, skills) se sdílejí. Dynamic workflows v CLI jsou experimentální — nepoužity v v1.

---

## Příloha F — Výsledky spiku

### Běh 1 (2026-11-20, Windows 11, Copilot CLI 1.0.104, Python 3.14, git 2.56)

**Ověřeno:**
- Všechny flagy z návrhu existují v `copilot --help` (včetně `--max-autopilot-continues` a `--no-custom-instructions`).
- `copilot` se na Windows spouští přímo jako `copilot.EXE` z Pythonu bez shellu, argumenty se předávají jako seznam. Runner v čistém Pythonu je tedy proveditelný.
- `usage.json` se zapíše **i při selhání** (exit 1). Klíče: `totalPremiumRequestCost, totalUserRequests, totalNanoAiu, totalApiDurationMs, sessionStartTime, codeChanges{linesAdded, linesRemoved, filesModifiedCount, filesModified}, modelMetrics, agentMetrics, providerModelMetrics, lastCallInputTokens, lastCallOutputTokens`.
  Telemetrie (kap. 10) může stavět na `modelMetrics` (po modelu) a `totalNanoAiu` (převod na kredity `[VERIFY]` E1).
- Validace argumentů proběhne na straně klienta ještě před autentizací:
  `--reasoning-effort ultra` → `invalid value 'ultra'`; `--max-ai-credits 5` → `Use at least 30 AI credits`.
- Exit code je `1` u všech sledovaných chyb (autentizace, neplatný effort, cap pod minimem).

**Neověřeno (běh selhal na `No authentication information found`):** E1 (flagy v praxi, schéma `modelMetrics`), E2 (překročení limitu), E3 (`--agent` vs. `--model`), E4 (oprávnění), E5a/E5d (neexistující model a agent).

**Rozhodnutí z běhu 1:**
- `outcome` se neurčuje z exit code (viz kap. 2).
- Do runneru patří preflight autentizace.
- Pro bezobslužný provoz je potřeba token v prostředí (`COPILOT_GITHUB_TOKEN`, `GH_TOKEN` nebo `GITHUB_TOKEN`) `[OPEN]` D12: jaký typ tokenu povolí ABB policy.

### Běh 2 (2026-11-20, po `/login`)

| Exp. | Výsledek |
|---|---|
| E1 | Haiku 5.5 medium splnil triviální krok za 23 s a **0,312 kreditu**; testy PASS, změněn jen `calc.py`. Flagy `--reasoning-effort`, `--context`, `--max-ai-credits` přijaty (vliv effortu neměřen). |
| E2 | **Nerozhodnuto:** spotřeba 23,58 kreditu byla pod limitem 30, takže se cap nespustil. Zopakovat s 45 soubory (úprava v `spike.py`). |
| E3 | Výchozí model CLI je Sonnet 5.5. Agent (fm = Sonnet) + `--model haiku` skončil na Sonnetu, tedy `--model` agenta nepřebil. Fm = výchozí model, takže nepoznáme, zda frontmatter funguje. Zopakovat s fm = Haiku. |
| E4 | **Allow-list funguje** bez `--allow-all-tools` (0,307 kreditu, PASS, změněn jen `calc.py`). Bezpečnostní model viz kap. 2. |
| E5 | Exit code je vždy `1`. Rozlišení podle stderr (tabulka níže). |

**Vzory chyb pro klasifikaci `outcome`:**

| Případ | Vzor ve stderr |
|---|---|
| bez přihlášení | `No authentication information found` |
| neexistující model | `Model "<x>" from --model flag is not available` |
| neplatný effort | `invalid value '<x>' for '--reasoning-effort'` |
| cap pod minimem | `Invalid value for --max-ai-credits` |
| neexistující agent | `No such agent: <x>, available: …` |

**Další zjištění:**
- `usage.json` obsahuje `modelMetrics` podle modelu, `tokenDetails` (input, cache_read, cache_write, output), `reasoningTokens`, `currentModel` a `codeChanges`. Telemetrie z kap. 10 je tím pokryta bez parsování transcriptů.
- `totalPremiumRequestCost` je zbytek legacy účtování, ignorovat.
- Režie kontextu ~29K tokenů na volání a start 14–28 s u triviálního dotazu. Zkoumá E6.
- Stream `--output-format json` má události `model.call_start/finished/final_result`, `tool.execution_start/complete`, `assistant.message`, `session.*`.
- Do session se načítají uživatelské agenty a MCP servery (D13).

### Běh 3 (2026-11-20)

| Exp. | Výsledek |
|---|---|
| E2 | **Opět nerozhodnuto.** 45 souborů stálo jen 15,5 kreditu (dřív 20 souborů 23,6), model čte chytře a levně. Cap 30 se nespustil. Úkol přepsán na výstupně těžký (16 modulů na Opusu). |
| E3 | **Vyřešeno.** Agent s `model: claude-haiku-5.5` použil Haiku (frontmatter funguje, formát ID stejný jako u `--model`). Agent s fm Haiku + `--model sonnet` použil **Haiku** (agent vyhrává). Agent bez fm + `--model haiku` použil Haiku (`--model` platí, když agent model nemá). |
| E6 | Lean flagy: `lastCallInputTokens` 28 258 → 25 909 (−8 %), start 14 s → 10 s. Cenový rozdíl (0,353 vs. 0,129) je zkreslený cache hitem druhého běhu a nepřičítá se flagům. Zbývající ~26K je režie CLI. |

**Další zjištění:**
- Studená vs. teplá cache: Haiku 0,35 vs. 0,13–0,16, Sonnet 7,07 kreditu (28,3K tokenů cache write) vs. řádově 0,3.
- Události streamu: `model.call_finished` obsahuje `outcome` a `dispatchDurationMs`, `tool.execution_complete` obsahuje `shellExecution.exitCode`. Spotřeba v nich není.
- Nezkoumáno: zmenšení tool schémat přes `--available-tools` (potřeba názvy nástrojů, viz `copilot help permissions`).

### Běh 4 (2026-11-20) — E2 vyřešeno

Opus 5.5, `--max-ai-credits 30`, úkol „16 modulů ručně“:

| Veličina | Naměřeno |
|---|---|
| Spotřeba | **33,35 kreditu** (cap 30 překročen o 3,35 ≈ 11 %), 3 volání modelu, 78 s |
| Exit code | **0** |
| stderr | jen „Session exported to …“, žádná zpráva o limitu |
| Stav worktree | 4 z 16 modulů (345 řádků), `calc.py` nedotčen, vše **necommitnuté** |
| `usage.json` | úplný (`aiCreditsStatus: complete`), součet odpovídá ceníku |
| Stream | `session.info` ×6 (pravděpodobně časová osa limitu 50/75/90 %, obsah neviděn) |

**Rozhodnutí:** `BUDGET` se určuje z `usage.json` (kredity ≥ cap), ne z exit code (kap. 2). Rozpracovaná práce se ukládá jako patch a worktree se resetuje (kap. 9). Cap se nastavuje s rezervou na jedno volání.

**Zbývá ověřit:** obsah událostí `session.info` při limitu (přesný text pro detekci), `--stream off`, vliv `--reasoning-effort` na cenu.

### Další krok
Spike je z hlediska návrhu uzavřen. Další práce je implementace M1 (kap. 1–4). Zbývající dotazy se ověří přímo v runneru.

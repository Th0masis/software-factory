# Software Factory — design v0.1

> Stav: kanonický návrh; schválená rozhodnutí jsou evidována v `docs/implementation/design-deviations.md` · Poslední úprava: 2026-11-24
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
- Zadání nebo issue → detailní specifikace → implementace po malých krocích → Gate → review → člověk publikuje PR.
- Specifikace vzniká interaktivně s agentem (grill-with-docs). Obsahuje definice, popis funkcí, schémata, zdroje a stabilně identifikovaná Acceptance Criteria (AC).
- Test Author připraví nezávislé automatizované acceptance testy před Builderem; Builder implementuje feature po TDD slices a může doplnit další trvalé testy.
- Při specifikaci se určí riziko. Podle rizika se pouští review agenti a testy.
- Review agenti používají **jiný LLM než builder a spec-creator**.
- Před návrhem PR běží shell gate (lint, unit, build, playwright, …).
- Selhání se řeší ve zpětných smyčkách na několika úrovních, včetně spolupráce s člověkem.
- Modely: nejsilnější dostupný model na specifikaci, role-specific profily na build a review.
- Čistý kontext, malé kroky, chirurgické změny (KISS, ponytail skill).

### 1.2 Mimo rozsah verze 1
- Auto-merge (merguje vždy člověk).
- Paralelní joby (start s `concurrency = 1`).
- Integrace Jira (spec může citovat klíč issue jako text) `[OPEN]` D4.
- Vlastní UI pro spec (používá se VS Code).

### 1.3 Principy
1. **Deterministické jádro, agenti jsou pracovníci.** Stav, přechody, gate, rozpočty a eskalace řídí kód. Agent nikdy nerozhoduje, že je hotovo. `[PROPOSED]`
2. **Fail closed.** Chybějící report, neznámý stav nebo vyčerpaný rozpočet = nepokračovat.
3. **Nezávislost kontroly.** Test Author běží v samostatné session a profilu; odlišná modelová rodina je konfigurovatelná, ne povinná. Kvalitu nezávislosti vyhodnocují golden tasks v kap. 11.
4. **Spec říká co a jak ověřit, ne jak implementovat.** Chybný technický detail ve specu se kaskádou propíše do kódu. `[PROPOSED]`
5. **Nezávislé testy jsou zamčené.** Test Authorovy testy schvaluje člověk před Builderem a po checkpointu je Builder nesmí měnit ani odstranit bez nového schválení, hashe a baseline-red ověření. Builder může přidávat vlastní trvalé testy.
6. **Malé kroky, čistý kontext.** Jeden slice = jedna Builder session. Handoff přes soubory a Git, ne přes paměť konverzace.
7. **Prosazuj omezení kódem, ne promptem.** Scope manifest a protected-path pravidla kontroluje Gate.
8. **Měř každou komponentu.** Každý agent a gate musí jít vypnout a dashboard ukáže, co reálně chytá. `[PROPOSED]`
9. **Spec chce nejsilnější dostupný model.** Chyby ve specifikaci se kaskádují do testů a implementace.
10. **Trvalé testy patří do projektu.** Testy chránící dlouhodobé chování jsou v existující testovací struktuře cílového projektu a jeho běžném test/CI procesu. Job-local zůstávají jen výslovně dočasné diagnostiky.
11. **Gate neuhaduje.** Každé gating AC musí mít automatizované ověření. Chybějící, přeskočené nebo nespouštěné povinné ověření znamená FAIL; manuální kontrola není PASS Gate.

---

## 2. Architektura

```
VS Code (front office)          Python orchestrátor (factory floor)           Dashboard (control room)
─────────────────────           ───────────────────────────────────           ────────────────────────
grill-with-docs → spec.md  ──►  job state machine  (bin/factory job …)  ──►   čte jobs/*/events.jsonl
spec critic (jiný vendor)       Test Author (samostatná session/profile)      cena / feature, verdikty,
schválení G0/G1 (člověk)        runner: copilot -p … (pinned profile)         eskalace, ablace
                                gate.py + .factory/checks.py
                                review (jiný vendor)
                                člověk: publikuje po review
```

### 2.1 Pipeline a stavy

```
DRAFT_SPEC ─► SPEC_APPROVED ─► TESTS_WRITTEN ─► TESTS_RED_OK ─► BUILDING ─► GATE ─► REVIEW ─► READY_FOR_HUMAN ─► PR_OPEN ─► MERGED
   (G0 člověk)                                                       │         │        │
                                                                     └─────────┴────────┴──► NEEDS_HUMAN / FAILED
```

`BUDGET_EXHAUSTED` je důvod/událost; job přechází do `NEEDS_HUMAN`. Před `TESTS_RED_OK` u Test Author workflow proběhne Test Author, G1, baseline-red ověření a test-only checkpoint. Stavy zůstávají řízené orchestrátorem; přesné události a recovery definuje implementační kontrakt.
Přechody provádí výhradně `factory job <příkaz>`. Nelegální přechod = chyba.

### 2.2 Adresářová struktura

```
factory/                       # samostatné repo továrny (DD-01)
  config.json
  factory/                     # python balíček (job, runner, gate, route, review, report)
  agents/                      # *.agent.md (sdílené VS Code i Copilot CLI)
  prompts/
  jobs/<job-id>/
    job.json                   # atomický rebuildable snapshot
    events.jsonl               # autoritativní append-only log
    spec.md  features.json  progress.md
    tests.json                 # schválené Test Author testy, ID a hash
    gate/<attempt-id>/         # neměnné reporty a logy
    runs/<n>/{usage.json,session.md,stdout.jsonl}
    verdicts/<reviewer>-<round>.json
  worktrees/<job-id>/          # git worktree cílového projektu
```

V M1 je checks kontrakt `.factory/checks.py` v cílovém projektu. Nové v2 joby používají člověkem
schválený Factory-owned adaptér mimo cílový worktree. V cílovém projektu zůstává
`.factory/protected.txt` a testy v existující projektové struktuře. Factory nepředepisuje
univerzální test adresář; trvalé testy nejsou pouze v `jobs/<job-id>/`.

---

## 3. Milníky (pořadí implementace)

| Milník | Kapitoly | Výsledek |
|---|---|---|
| **M0** | spike | ověřené předpoklady o Copilot CLI (`spike.sh`) |
| **M1** Walking skeleton | 1, 2, 3, 4 | ručně napsaný spec + testy → builder smyčka → gate → větev. Bez review a bez LLM specu. |
| **M2** | 8 | review; člověk publikuje po review |
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
- `events.jsonl` je autoritativní append-only journal; `job.json` je atomicky aktualizovaný
  snapshot, který lze z journalu obnovit.
- Každý přechod zapíše událost: `{ts, job, from, to, actor, reason}`. Pozdější kapitoly přidají
  pouze svá schémata a pole.
- Zámek souboru při zápisu.
- Job metadata zahrnují projekt, původní branch/base SHA a připojený worktree. Vypočítané údaje
  a chapter-specific pole se ukládají až s příslušným kontraktem.

**Cílový projekt a větve** (DD-03, DD-08, DD-14):

Továrna žije ve vlastním repu. Kód mění vždy v **repu cílové aplikace**: worktree se připojí
k existující připravené větvi a leží mimo cílový repozitář. Job nejprve vzniká jako metadata.
V M1 se worktree připojí po schválení specifikace; u nových v2 jobů se připojí před G0 a Factory
ověří výchozí baseline. Factory větev sama nevytváří, nemaže, neresetuje ani nepřepisuje.

Konfigurace projektu `.factory/project.json` určuje cílový repozitář; M1 používá i target checks:

```json
{
  "project": "my-app",
  "remote": "origin",
  "base_branch": "develop",
  "checks": ".factory/checks.py"
}
```

- `base_branch`: cílová větev, kterou člověk použije při přípravě work branch. Factory zaznamená
  použitý `base_sha`; synchronizace a řešení konfliktů jsou samostatné řízené operace.
- Agenti ani orchestrátor nepushují a nevytvářejí PR. Po review publikuje člověk (DD-20).
- `branch-per-job`, automatické vytváření větví, paralelní joby a automatické PR jsou odloženy.

**Akceptační kritéria:**
- Nelegální přechod je odmítnut a zalogován.
- Obnova po pádu: `status` zobrazí stav a poslední událost.
- Unit testy state machine (všechny povolené a zakázané přechody).

**Závislosti:** žádné.

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
- Agenti ani orchestrátor nepushují ani neotevírají PR. Publikuje člověk po review.
- Síť: `--allow-url` jen nutné domény.

**Akceptační kritéria:**
- Stejné volání s fake `copilot` stubem v testech (runner je testovatelný bez kreditů).
- Klasifikace chyb do `outcome`. **Exit code nestačí:** spike ukázal `1` pro autentizaci i validaci argumentů, takže se `outcome` určuje z kombinace exit code, vzorů ve stderr a `usage.json` (Příloha F).
- **Preflight před každým jobem:** autentizace (`copilot -p` smoke test) a dostupnost modelů z profilů. Chybí-li, job se nespustí (fail closed).
- Zápis `usage.json` a událostí `run_started/run_finished`.

**Závislosti:** kap. 1. **Otevřené:** schéma `usage.json`, chování limitu, `--model` vs. model v `.agent.md` (E1–E5).

---

## Kapitola 3 — Gate

**Účel:** deterministicky ověřit změnu projektu. Gate spouští orchestrátor, ne agent; Gate sama
nemění stav jobu ani nespouští review.

**Dvě části:**
1. M1 používá `.factory/checks.py` v cílovém projektu. Nový v2 workflow používá schválený
   Factory-owned adaptér. Slice fast spouští vybrané testy a relevantní rychlé regrese bez
   čekajících testů; full spouští úplný build a projektovou testovou sadu.
2. Factory `gate.py` ověřuje scope, chráněné cesty, integritu schválených Test Author testů,
   kontrolní report a výsledek projektu.

Baseline-red je kontrola připravenosti vstupu Test Authora, ne Gate verdict a ne další Gate režim.
Gate CLI zachovává `factory job gate JOB_ID --base SHA --mode fast|full [--slice SLICE_ID]`.

**Feature Validation:** existující finální full Gate po všech slices slouží jako Feature/Job
Validation. Nevzniká druhý Gate engine ani duplicitní běh stejné sady.

**Report v2:** strojově čitelný report obsahuje `gate_type`, `job_id`, `feature_ids`, volitelné
`slice_id`, původní `job_base_sha`, schválený `gate_base_sha`, přesný `comparison_base_sha`,
testovaný `head_sha`, čas a trvání, required checks, AC↔test výsledky, hash vstupů a odkazy na
logy. `risk` zůstává `DEFERRED` do kapitoly 7.

**Verdict:** povinné kontroly rozhodují o PASS/FAIL; v2 může při schváleném opakovaném selhání
existujícího testu vrátit `PASS_WITH_EXCEPTION` s pravdivým FAIL výsledkem a důkazy. Shoda důkazů
neprokazuje stejnou příčinu. Chybějící, neplatné, neprovedené nebo přeskočené povinné ověření
znamená FAIL. WARNING je možné jen u výslovně nepovinných kontrol.

**Scope a ochrana:**
- změny mimo schválený scope nebo chráněné cesty znamenají FAIL;
- Test Authorův výsledek se ověřuje proti původnímu `job_base_sha`; po G1 je jeho test-only commit
  `gate_base_sha` a integrita testů se dále ověřuje hashem;
- `comparison_base_sha` určuje přesný diff kontrolovaný daným Gate pokusem (pro slice typicky
  commit před tímto slice, pro finální Gate `gate_base_sha`);
- Gate neprovádí risk recompute, automatické CI úpravy ani skryté rozšiřování scope.

**Akceptační kritéria:** chybějící/neplatný report, změna chráněného Test Author testu, scope
porušení nebo nesplněná required kontrola vždy blokuje pokračování. Gate zůstává state-neutral.

**Závislosti:** kap. 1–2, 4–6. Risk routing vlastní kap. 7.

---

## Kapitola 4 — Builder smyčka (jeden slice = jedna session)

**Účel:** implementovat schválenou feature v malých vertikálních slices.

**Vstupy:** schválený `spec.md` se stabilními AC ID, M1 ručně registrovaný test manifest nebo
Test Authorův schválený manifest (M3), příslušný Gate base, schválený slice plan, `progress.md`
a Git history.

**Postup:**
1. M1 zachovává ručně připravené a registrované vstupy. V Test Author workflow orchestrátor před
   Builderem vyžaduje G1, úspěšný baseline-red a test-only checkpoint; poté uloží `gate_base_sha`.
2. Builder dostane novou session pro jeden slice, jeho AC/test mapování, scope, ověřený handoff
   a relevantní historii.
3. Builder používá TDD: připraví/doplní vlastní testy podle potřeby, implementuje nejmenší změnu
   a spustí relevantní testy. Všechny registrované testy zůstávají beze změny; v Test Author
   workflow jsou navíc chráněny hashem. Nové trvalé testy ukládá do projektové konvence; dočasné
   diagnostiky se neprezentují jako regresní ochrana.
4. Builder vytvoří commit před Slice Gate. Gate ověří tento přesný commit; PASS nebo ve v2
   `PASS_WITH_EXCEPTION` umožní orchestrátoru zaznamenat slice jako ověřený.
5. Po všech slices běží jeden finální full Gate. PASS nebo ve v2 `PASS_WITH_EXCEPTION` otevře
   `REVIEW`; FAIL zachová práci a vyžaduje lidský zásah.

Chapter 4/M1 nemá automatické retry. Builder nikdy nepushuje ani nevytváří PR. Factory nevynucuje
univerzální test adresář a nemění CI bez lidského schválení.

**Subagenti:** Test Author je samostatná session/profile před Builderem. Builderovy další role nebo
editující subagenti se tímto rozhodnutím nepovolují; zůstává platný schválený read-only režim.

**Režie session (E3, E6):** každé volání nese ~26–28K tokenů kontextu, převážně vlastní system prompt a definice nástrojů CLI. Při **studené cache** stojí session u Sonnetu ~7 kreditů (28,3K tokenů cache write), u Haiku ~0,35. Při **teplé cache** (TTL ~5 min, `cacheExpiresAt` v `usage.json`; sdílí se i mezi procesy se stejným prefixem) jen ~0,3 u Sonnetu a ~0,15 u Haiku. Pravidla: (1) neměnit prefix (stejný agent, nástroje, MCP konfigurace), (2) pouštět features stejného profilu těsně za sebou, (3) používat `--disable-builtin-mcps --no-custom-instructions` (ušetří ~2,3K tokenů a ~4 s startu).

**Závislosti:** kap. 1–3, 5–6. Retry policy patří do kap. 9.

---

## Kapitola 5 — Specifikace (front office, interaktivně)

**Účel:** z issue/zadání vznikne schválená specifikace s testovatelnými požadavky.

**Postup `[USER]`:**
1. Člověk + agent `spec-grill` (grill-with-docs) ve VS Code.
2. `spec-critic` (jiný vendor) navrhne nejasnosti, chybějící AC a rizika; člověk je vyřeší.
3. Schválení G0 uloží hash specifikace. Změny po schválení probíhají jen řízeným amendem
   s novým hashem a událostí.

**Šablona `spec.md`:** kontext · cíl · mimo rozsah · definice · rozhraní/schémata · zdroje ·
omezení · scope manifest · riziko a odůvodnění · testovací plán · otevřené otázky · designové
poznámky. Acceptance Criteria jsou stabilně označené `AC-001`, `AC-002`, … a každé popisuje
pozorovatelné chování. Každé gating AC musí být automatizovatelně ověřitelné.

Nepožadujeme, aby Builderovy testy duplikovaly všechna AC. Test Authorův manifest mapuje
nezávislé acceptance testy na AC; Builderovy testy doplňují technické a regresní případy.

**Model:** nejsilnější dostupný. Autor ≠ critic.

**Akceptační kritéria:** schválená specifikace nemá otevřené otázky, má netriviální scope a každé
gating AC má stabilní ID a ověřitelný popis.

**Závislosti:** kap. 1–2. Zdroj zadání a jazyk promptů/specifikace zůstávají dle D4/D9.

---

## Kapitola 6 — Test Author a baseline-red

**Účel:** připravit nezávislé trvalé acceptance testy před Builderem.

**Postup:**
1. Samostatná Test Author session/profile načte schválenou specifikaci a zjistí konvence cílového
   projektu. Jiná modelová rodina je volitelná konfigurace, nikoli pevná podmínka.
2. Test Author vytvoří testy přímo v existující projektové testovací struktuře. Job artefakty
   obsahují manifest, hashe, reporty a logy, ne jedinou kopii trvalých testů.
3. Orchestrátor ověří úplný diff Test Authora: povolené jsou jen schválené testové cesty; jiná
   změna znamená FAIL a eskalaci. Jde o post-run kontrolu, ne OS sandbox. G1 může schválit jen
   konkrétní změny testových cest, nikoli obecné obejití protected pravidel.
4. Orchestrátor spočítá candidate manifest a hashe. Člověk provede G1 revizi přesného diffu testů,
   jejich cest a AC mapování. Člověk nemusí automatizované testy ručně spouštět.
5. Baseline-red spustí testy proti původnímu `job_base_sha`. PASS-red znamená očekávanou aserční
   nebo chybějící-funkčnost chybu; chyba syntaxe, importu, setupu či neplatný report není RED.
6. Orchestrátor commitne pouze přesně schválené testové cesty, ověří obsah a commit SHA, zaznamená
   test-only checkpoint jako `gate_base_sha` a teprve potom uvolní Buildera.

Test Author testy jsou po checkpointu chráněny hashem. Jejich změna/odstranění vyžaduje nové G1,
nový hash a opakovaný baseline-red. Builder smí přidávat vlastní trvalé testy do schváleného scope,
ale nesmí měnit Test Authorovu sadu. Nepovinné manuální kontroly jsou mimo automatický Gate; nelze
je vykázat jako PASS.

**Akceptační kritéria:** chybějící G1, neúplné AC mapování, test procházející na baseline,
syntax/setup failure, změna mimo test scope nebo neshoda hashů zastaví workflow před Builderem.

**Závislosti:** kap. 3 a 5. Workflow respektuje ruční M1 registraci dle DD-23; nenahrazuje ji
zpětně.

---

## Kapitola 7 — Riziko a routing

**Účel:** riziko určuje profil buildera, počet a typ reviewerů a rozsah testů.

**Návrh `[PROPOSED]`:**
- Úrovně: `low | medium | high`.
- Deklarované riziko ze schválené specifikace; efektivní riziko je maximum deklarovaného a
  deterministického přepočtu z diffu.
- Signály: citlivé cesty, rozsah změny, nové závislosti a změny testů. Přepočet smí riziko jen
  zvýšit.
- Routovací tabulka určuje profily, reviewery a požadované testy včetně podmínek pro E2E.
- Dokud Chapter 7 není implementována, Gate report označí risk jako `DEFERRED`; Factory nesmí
  tvrdit, že E2E nebo jiné risk-required testy byly vybrány či spuštěny routingem. Pokud je nutný
  test relevantní a výběr není spolehlivý, použije se širší dostupná sada.

**Závislosti:** kap. 3–6. Risk recompute zůstává mimo odpovědnost základního Gate (DD-19).

---

## Kapitola 8 — Review a výstup (publikuje člověk)

**Účel:** nezávislá kontrola podle rizika.

**Návrh `[PROPOSED]`:**
- Reviewer vidí schválenou specifikaci, celý diff od `job_base_sha`, Gate report v2 a potřebné
  artefakty; ne úvahy ani transcript Buildera. Každé kolo používá čistou session.
- Reviewer vrací JSON verdict `{reviewer, model, verdict: APPROVE|CHANGES|BLOCK,
  findings:[{severity,file,line,claim,evidence,fix}]}`. Nález bez konkrétního umístění a důkazu se
  nepoužije jako blokující finding.
- Reviewery: `code`, `security` dle rizika, `tests`, `ui` pro relevantní UI změny.
- Approver je advisory; merge rozhoduje člověk.
- Builder ani orchestrátor nepushují a nevytvářejí PR. Po review člověk publikuje změnu a vytvoří
  PR. Factory může následně zaznamenat člověkem dodaný PR/issue odkaz a přejít do `PR_OPEN`.
- CI workflow a remote publikaci Factory nemění bez samostatného lidského pokynu.

**Závislosti:** kap. 2–7. Stav a evidence PR jsou součástí tohoto Chapter 8 workflow; automatické
pushnutí a draft PR orchestrátorem je nahrazeno DD-20.

---

## Kapitola 9 — Zpětné smyčky, eskalace, člověk

**Účel:** předvídatelné zacházení se selháním a lidskou eskalací.

Chapter 4/M1 v současnosti automatické retry nemá: Gate FAIL, chyba Runneru, neplatný report,
neúspěšný baseline-red nebo nejasná recovery zastaví postup, zachovají práci a vyžádají člověka.
Následující návrhy retry zůstávají návrhem pro samostatný Chapter 9 kontrakt; nesmějí být
implementovány jako skrytá smyčka v Chapter 4.

| Úroveň | Spouštěč | Navržená akce | Stav |
|---|---|---|---|
| L1 TDD | automatizované testy feature selhávají | omezený další pokus Buildera | návrh, odložen |
| L2 Gate | lint/build/unit/e2e/scope | oprava podle failing reportu | návrh, odložen |
| L3 Review | verdict CHANGES | oprava a nové review kolo | návrh, odložen |
| Eskalace | opakovaný error nebo limit pokusů | vyšší profil a případná konzultace | návrh, odložen |
| L4 Člověk | BLOCK, nejednoznačnost nebo vyčerpaný limit/rozpočet | `NEEDS_HUMAN` | fail-closed pravidlo |

Budoucí retry kontrakt musí odlišit chybu slice Gate od finální Feature/Job Validation, mít
deterministické limity a nesmí automaticky resetovat ani zahodit worktree. `WARNING` nikdy
nepřekryje required kontrolu. Konzultace může vracet diagnózu, nikoli kód.

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

## Příloha A — `config.json` (ilustrativní)

Přesná ID modelů `[VERIFY]` (`copilot --model` / model picker). Role routing, E2E výběr a retry
limity jsou pouze návrhy pro Chapter 7/9 a nejsou součástí aktuálního M1 workflow.

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
  "budgets": {"credits_feature": 400, "credits_job": 3000},
  "models_status": {"gemini-3.8-flash": "retiring", "gemini-4-argon": "retiring", "mai-code-1.1-flash": "retiring"}
}
```

Numeric diff budgets are not enforced by the Gate. Test Author integrity is tracked by the approved
manifest hashes; any additional protected paths are project-specific policy in
`.factory/protected.txt`.

---

## Příloha B — Otevřená rozhodnutí

| ID | Otázka | Moje výchozí stanovisko |
|---|---|---|
| D2 | Smí builder spouštět subagenty s editací? | jen read-only subagenti |
| D4 | Zdroj zadání: Jira klíč, markdown, GitHub issue | markdown + volný odkaz na Jira klíč |
| D8 | Obsah `--fast` pro projekty s ST/C v Automation Studiu | doplnit po M1 |
| D9 | Jazyk promptů a specu | prompty anglicky, dokumentace česky |
| D10 | Druhý reviewer z rodiny Gemini | až po ověření tieru a ceny |
| D11 | Platforma: Windows nativně vs. WSL / dev container | nativně Windows, vše v Pythonu; `checks.py` místo `checks.sh`; git worktree funguje; WSL/container jen pokud selže E4 nebo sandbox |
| D12 | Autentizace pro bezobslužný běh (token v prostředí vs. interaktivní login) | interaktivní login pro vývoj; token až pro CI podle policy |

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

# Plano — redesenho do wrapper `jxl_photo.py` (2026-11-03, JST)

- **Base:** `b364497` (v2.19.2). Companheiro do audit
  `AI_tools/261103_Claude_audit_full-repo.md`.
- **Pedido do dono:** um wrapper mais fácil de usar, que permita usar *todas*
  as flags sem expert flags e sem editar cada script, e que seja fácil de
  estender quando surgir uma opção nova.
- **Como usar este documento:** são 4 fases independentes, cada uma vira um
  release. As Fases 1 e 2 estão detalhadas o bastante para codar seguindo o
  texto. As Fases 3 e 4 trazem as decisões de design já tomadas, mas o planner
  local deve expandi-las num plano literal (`AI_tools/YYMMDD_Claude_plan_…`)
  antes de entregar ao coder, como manda o AGENTS.md.

---

## 1. Diagnóstico (o que o código mostra hoje)

| Medida | Valor | Consequência |
|---|---|---|
| Bifurcações `if RICH_AVAILABLE and console:` | **120** | Cada pergunta e cada texto existem duas vezes (Rich e `input()`), e as cópias divergem: em `explorer_thumbnail_flow` o texto puro já é mais curto que o Rich. Os testes cobrem quase só o caminho `input()`. |
| Montadores de comando | **2** quase idênticos | `execute_workflow` (`jxl_photo.py:8720-9107`) e `_build_manifest_entry_cmd` (`:8023-8345`). Toda flag nova entra nos dois, em ordens diferentes. |
| Implementações dos gates | **2** | As mesmas regras (derivativo × delete, in-place, lossy + delete-skipped, HHMM) estão em `execute_workflow` (`:8546-8710`) e `_execute_manifest_workflow` (`:6369-6760`). |
| Estado de uma execução | `workflow` (~37 chaves) + `advanced_options` (~43) + `mode_config` + ~45 campos `last_*` em `ToolConfig` + presets (snapshot dos `last_*`) | O dicionário avançado é reconstruído do zero e precisa de listas de "carregar adiante" (`_carry_recompress_policies`, `:5445`). A perda silenciosa de opções já foi bug. |
| Flags dos scripts que o wrapper nunca passa | **27** (lista na §4) | Inclui operações que as próprias mensagens dos scripts recomendam: `--restamp-moved` (dica da recusa da guarda) e `--buffering 1` (dica do teto de memória). |
| Settings só editáveis no topo do `.py` | ~50, listados em `docs/README_jxl_tools.md:809-921` | O usuário do jxl-photo-exe dificilmente edita o script empacotado. |
| `InteractiveMenu` | **7 233 linhas**, ~100 métodos | Wizard, gates, execução, manifest, presets e manutenção na mesma classe. |
| Testes ligados ao wrapper | 55 arquivos importam `jxl_photo`; 27 roteirizam prompts **por ordem** (`answers = ["", "8", "", …]`) | Reordenar uma pergunta quebra testes que validam regras de segurança, não a ordem. |

**Pontos para tocar adicionar uma flag hoje:** pergunta Rich + pergunta texto
puro, chave em `workflow`/`advanced_options`, listas de carry-over, os 2
montadores, campo `last_*` + `_SESSION_CHOICES`, replay de preset
(`_run_saved_session`), resumo (`_wizard_confirm`, `_describe_session`),
tabela da doc. São mais ou menos 9 lugares.

**O que já existe e serve de semente:** `_CHILD_OPTION_DEFAULTS`
(`jxl_photo.py:2365`) já é um registro embrionário (opção → setting do filho
+ fallback). `tests/test_docs_cover_the_flags.py` já extrai as flags dos
filhos por AST, e a mesma técnica serve para o teste de cobertura.

---

## 2. Arquitetura recomendada

```
                    ┌─────────────────────────────┐
                    │  OPTION REGISTRY (Opt[...])  │  ← única fonte de verdade
                    └──────┬───────┬───────┬───────┘
          tela de revisão  │       │       │  presets / último job / manifest
          busca, defaults  │       │       │  doc gerada, teste de cobertura
                           ▼       ▼       ▼
   UI (RichUI | PlainUI | ScriptedUI) ── Job ── gates únicos ── build_child_cmd ── _stream_child
```

1. **Registro de opções** (`Opt`): um item por flag. Diz em que direções ela
   vale, o tipo, o grupo e o nível na UI, o texto de ajuda, de onde vem o
   default (setting do filho), quando aparece, conflitos e requisitos, se é
   perigosa (gate HHMM), como vira argv e qual coluna de manifest usa, se
   houver.
2. **Montador único** `build_child_cmd(...)`, usado pela execução direta,
   pelo manifest e pelo replay de preset.
3. **Camada de UI única** com renderizadores Rich e texto puro e um
   `ScriptedUI` para testes que responde **por id de pergunta**, não pela
   ordem.
4. **`Job`**: um objeto só com o que a execução precisa. Presets v2 e "último
   job" são `Job` serializados, com migração automática do formato atual.
5. **Gates únicos** (`_job_gates`): uma lista de regras aplicada aos três
   caminhos (direto, manifest e preset).
6. **Overlay de settings dos scripts** (opcional, Fase 4): um JSON por usuário
   que sobrepõe os settings do topo dos 4 scripts, editado no menu de
   Configurações do wrapper, sem tocar nos `.py`.

Decisões gerais, sem alternativa em aberto:

- O registro fica **dentro de `jxl_photo.py`**, numa seção
  `# === OPTION REGISTRY ===` perto do topo. Nada de arquivo novo nestas fases:
  o jxl-photo-exe e o `PIP/` empacotam arquivos por nome.
- Os filhos **não mudam de CLI** nas Fases 1 a 3. Na Fase 2 só ganham
  `build_parser()` (mover o bloco de `add_argument` sem mudar comportamento).
  Na Fase 4 ganham o helper do overlay.
- Defaults **sempre lidos do filho** (`_child_setting` e afins). Nenhum literal
  de default no registro, só o fallback de quando o import falha, como hoje.
- Presets e `--run-preset` do dono **não podem quebrar**. O formato antigo é
  lido para sempre (migração na leitura) e o novo é escrito só ao salvar.

---

## 3. A nova experiência (UX)

### 3.1 Menu principal (Fase 3)

```
JXL Photo 2.2x.0   cjxl ✓  djxl ✓  exiftool ✓  magick ✓  jxl-thumb ✓

  CONVERT
   1  New job…                    archive TIFFs, export JPEGs, smaller copies…
   2  Repeat last job             TIFF → JXL · mode 7 · d=0.1 · G:\2025
   3  Presets (5)                 run · save · edit · show the schedule command
   4  Folder list (manifest)      generate · open · run

  MAINTAIN
   5  Renamed or moved folders    fix the provenance records (no re-encode)
   6  Explorer thumbnails         add · renew · remove
   7  JPEG archives (jbrd)        audit · repair · EXIF for IrfanView
   8  Reset the ICC test cache

  SETTINGS
   9  Settings                    wrapper defaults · script settings
   D  Dependencies
   0  Exit
```

O conteúdo de hoje continua todo lá: "Reset/Move settings" passa para
dentro de 9. A Fase 2 só *acrescenta* entradas ao menu atual (letras `R` e
`C`) para não mexer na numeração antes da Fase 3.

### 3.2 "New job": 3 perguntas e uma tela de revisão (Fase 3)

1. **O que fazer?** Lista de tarefas em vez de origem e destino:

   ```
   1  Archive TIFFs as JXL masters              TIFF → JXL
   2  Restore TIFFs from JXL masters            JXL → TIFF
   3  Make smaller or resized JXL copies        JXL → JXL
   4  Export JPEG/PNG for sharing               JXL → JPEG/PNG
   5  Archive JPEGs losslessly (bit-exact)      JPEG → JXL
   6  Get the original JPEGs back               JXL → JPEG (bit-exact)
   7  Re-encode JPEGs lossy                     JPEG → JXL
   ```

   Cada tarefa vira (origin, dest, conversion_type), os mesmos valores de hoje.
2. **Qual pasta?** O `FolderAnalyzer` atual roda e mostra o que achou ("412
   TIFF em `_EXPORT\TIFF16` → modo 7 sugerido"). O usuário aceita com Enter ou
   escolhe outro modo.
3. **Qualidade**, só quando faz sentido. Por exemplo, para arquivar: "Lossless
   / Near-lossless d=0.1 [padrão] / Custom".
4. **Tela de revisão**, onde *todas* as opções ficam acessíveis:

```
Review — Archive TIFFs as JXL masters
Folder  G:\2025\250118_吾妻山公園 [FINAL]\01_Z7   412 TIFF in _EXPORT\TIFF16 → mode 7

 ESSENTIALS                                                   from
  1  Distance              0.1  near-lossless                 settings
  2  Effort                7                                  script
  3  Workers               16                                 last job
  4  Existing outputs      sync (only newer TIFFs)            script
  5  Delete the TIFFs      no
 MULTI-PAGE & SCANS
  6  Multi-page TIFFs      split                              script
  7  Thumbnail pages       auto                               script
  8  Film scans            lossless (scan distance 0)         script
 PERFORMANCE
  9  Staging folder        E:\staging_jxl                     settings
     (+3 advanced: buffering, preflight, clean staging)

 [1-9] change   [a] all options (24)   [/] search   [c] command line
 [d] dry run    [r] RUN               [s] save as preset   [b] back
```

Regras da tela:

- A coluna **from** mostra de onde veio cada valor: `script` (topo do script
  ou overlay), `settings` (Configurações do wrapper), `preset`, `last job` ou
  `you`.
- **`[a]`** mostra todas as opções da direção; **`[/]`** busca por texto nas
  labels, flags e ajuda ("buf" acha `--buffering`). Assim nenhuma flag fica
  presa nos expert flags.
- **Editar** usa o tipo do registro: lista de escolhas, número com faixa,
  liga/desliga, caminho.
- **Conflitos aparecem na hora.** Ligar "Delete" esconde e explica as opções
  de derivativo; um modo in-place esconde o rename; quem tem `requires` (por
  exemplo verify-roundtrip) só aparece quando o requisito está ligado.
- **`[d]` roda em dry-run e volta para a revisão.** O usuário ajusta e então
  roda de verdade. Hoje o dry-run é uma pergunta sim/não sem volta.
- **`[c]`** mostra o argv exato, sem `--delete-confirm-off`, via `_display_cmd`.
- **Opções perigosas** (delete, guarda off, in-place, allow-incomplete-groups)
  ficam num grupo vermelho "Deletion & in-place". O gate HHMM continua sendo
  cobrado na hora de rodar, como hoje.
- **Expert flags** continuam como último recurso, mas validadas contra o
  parser do filho (Fase 2): uma flag desconhecida é recusada antes de rodar, e
  uma flag que já existe no registro gera a dica "use a opção N na revisão".

---

## 4. Flags que faltam: onde cada uma entra

Todas viram entradas do registro (Fase 2) e aparecem na revisão no grupo
indicado. As de manutenção viram entradas de menu.

| Flag | Script(s) | Onde | Nível | Observação |
|---|---|---|---|---|
| `--restamp-moved` | encoder | Menu de manutenção: "Renamed or moved folders" | — | Dry-run por padrão. Pede a pasta e o modo; oferece reaproveitar modo, marker, subfolder e jxl-folder de um preset ou do último job. O encoder recusa a combinação com delete/overwrite. |
| `--restamp-then-sync` | encoder | Revisão, "Existing outputs" (só TIFF→JXL com sync e sem delete) | advanced | Conflita com delete e overwrite, igual ao filho. |
| `--buffering` | encoder, recomp | Revisão, "Performance" | advanced | Escolhas: default / 0 / 1 / 2 / 3. Ajuda: "1 = streaming, ~3× menos memória". O teto de memória já recomenda isso no log. |
| `--no-preflight` | encoder, recomp | "Performance" | advanced | |
| `--clean-staging` | os 4 | "Performance", só quando há staging | advanced | Ver o achado E-F do audit sobre execuções concorrentes. |
| `--icc-png-strategy` | encoder | "Colour & scans", só lossy | advanced | |
| `--scan-scope` | encoder, recomp | "Colour & scans" | advanced | |
| `--clear-icc-cache` | encoder | Menu de manutenção: "Reset the ICC test cache" | — | Roda sem pasta de entrada (o filho trata antes de exigir o input). |
| `--icc-cache-dir` | encoder | Overlay (Fase 4) | — | É configuração da máquina. |
| `--warn-thumbnail-discard` | encoder | "Multi-page" | expert | |
| `--allow-incomplete-groups` | decoder | "Deletion & in-place", só com delete | advanced, perigoso | |
| `--compress-metadata` | transcoder | "Metadata", só JPEG→JXL | advanced | Ajuda: "smaller files, IrfanView shows no EXIF". |
| `--output-name` | transcoder | "Files & folders", convert modos 1/3 | advanced | |
| `--sharpen-sigma/-gain/-threshold` | transcoder, recomp | "Derivative", só com sharpen ≠ none | expert | |
| `--to-srgb` | transcoder | **não oferecer** | — | Alias; o wrapper usa `--icc-profile sRGB`. |
| `--embed-thumbnail` | encoder | **não oferecer** | — | Deprecated. |

As internas (`--summary-json`, `--version`, `--delete-confirm-off`,
`--dry-run`) ficam em `NOT_OFFERED` com o motivo. O wrapper as emite por
regra, não como opção do usuário.

---

## 5. Fase 1 — Registro e montador único (sem mudança visível)

**Objetivo:** com o mesmo comportamento de hoje, toda flag passa a sair do
registro, e o teste de cobertura falha quando um filho ganha uma flag que o
wrapper não declarou.

### 5.1 O registro (código a criar em `jxl_photo.py`, logo após `_CHILD_OPTION_DEFAULTS`)

```python
DIRECTIONS = ("tiff2jxl", "jxl2tiff", "jxl2jxl", "jpeg2jxl", "jxl2jpeg", "jxl2png")

@dataclass(frozen=True)
class Opt:
    id: str                    # chave estável (legacy_key abaixo mapeia o formato atual)
    flag: str                  # "--buffering"
    directions: frozenset      # subset de DIRECTIONS
    kind: str                  # "bool" | "tri" | "choice" | "int" | "float" | "text" | "path"
    group: str                 # id de GROUPS
    level: str                 # "essential" | "advanced" | "expert"
    label: str
    help: str
    neg_flag: str = ""         # kind "tri": "--no-rederive-on-encode-change"
    choices: tuple = ()
    lo: Optional[float] = None
    hi: Optional[float] = None
    default_from: Optional[tuple] = None  # (module, option) -> _child_default(...)
    legacy_key: str = ""       # "advanced.on_downgrade" | "workflow.staging" | ...
    emit_when: str = "set"     # "set" (só se tem valor) | "always" (hoje sempre passa)
    when: Optional[Callable] = None       # (ctx) -> bool : aplicável/visível
    requires: tuple = ()       # ids que precisam estar ligados
    conflicts: tuple = ()      # ids que não podem estar ligados
    danger: bool = False       # cobra o gate HHMM / relaxa uma proteção
    persist: bool = True
    doc: str = ""              # "README_jxl_tiff_encoder.md#…"

GROUPS = (("essentials", "ESSENTIALS"), ("existing", "EXISTING OUTPUTS"),
          ("deletion", "DELETION & IN-PLACE"), ("multipage", "MULTI-PAGE & SCANS"),
          ("colour", "COLOUR & SCANS"), ("metadata", "METADATA"),
          ("derivative", "DERIVATIVE (resize/colour/sharpen)"),
          ("files", "FILES & FOLDERS"), ("performance", "PERFORMANCE"))

REGISTRY: Tuple[Opt, ...] = (...)        # Fase 1: só o que o wrapper JÁ emite
NOT_OFFERED: Dict[str, str] = {...}      # flag -> motivo (as 27 da §4 + internas)
```

- **Fase 1 = só o que o wrapper já emite hoje** (~45 ids). As 27 faltantes
  entram em `NOT_OFFERED` com o motivo "Phase 2" e saem de lá na Fase 2.
- `legacy_key` liga cada id ao lugar onde o valor mora hoje
  (`advanced_options[...]`, `workflow[...]`, `mode_config[...]`). Assim a
  Fase 1 não muda nenhum dicionário.
- `_CHILD_OPTION_DEFAULTS` fica como está (`tests/test_wrapper_child_defaults.py`
  o fixa). `default_from` só aponta para ele.

### 5.2 O montador único

```python
def build_child_cmd(script: str, origin: str, dest: str, source: str,
                    dest_path: Optional[str], mode: int, workers: int,
                    workflow: Dict, advanced: Dict, *, manifest: bool,
                    row_options: Optional[Dict] = None) -> List[str]:
```

1. Positionais, `--mode`, `--workers`, e `--summary-json` **só** quando
   `manifest=True` (hoje a execução direta não passa).
2. Para cada `Opt` com a direção em `directions` e `when(ctx)` verdadeiro, o
   valor vem de `legacy_key`; a emissão segue o `kind` e o `emit_when`.
3. **Regras de política** em funções nomeadas, chamadas em ordem fixa
   (copiar a semântica exata de hoje):
   - `--delete-confirm-off` quando houver delete (o wrapper já cobrou o HHMM)
     ou recompressor in-place (`_recompress_entry_in_place`);
   - supressão de `--delete-source` em linha de derivativo ou com
     `--icc-profile` no transcoder (`:8299-8324`);
   - `_append_provenance_flags` e `_append_overwrite_guard_flag` sem mudança;
   - `--export-jxl-folder` só para tiff2jxl/jxl2jxl; `--exclude-folders` só
     para tiff2jxl/jxl2tiff (com o aviso de uma vez por execução que já existe);
   - em modo 2, o diretório de saída é inserido logo após o input (gh-59317)
     e o `mkdir` continua em `execute_workflow`, antes de montar.
4. `_append_expert_flags` **por último**, como hoje.

`execute_workflow` e `_build_manifest_entry_cmd` mantêm assinatura e efeitos
colaterais e passam a chamar `build_child_cmd`.

### 5.3 Checklist de assinaturas (AGENTS.md, "Changing a function's signature")

Nenhuma assinatura muda na Fase 1. Estes pontos de teste precisam continuar
existindo com o mesmo nome e aridade em **todas** as fases (os números são
quantos testes fazem monkeypatch neles): `InteractiveMenu._stream_child`
(28), `._confirm_archive_mode` (14), `._confirm_lossy_delete_skipped` (8),
`.execute_workflow` (7), `._run_subprocess` (7),
`._manifest_needs_collision_scan` (3), `.show_main_menu` (2),
`._wizard_select_mode` (2), `._wizard_delete_gateway` (2), `._pick_manifest`
(2), `._manifest_output_collisions` (2), `._confirm_manifest_entries` (2),
`._render_manifest_summary`, `._manifest_source_overlaps`, `._confirm_hhmm`,
e `ConfigManager._get_config_path` (42). Antes de remover ou renomear
qualquer um deles numa fase posterior, rodar
`grep -rn "<nome>" --include=*.py .` (ignorando `claude/`, `PIP/` e
`deprecated/`) e migrar os testes primeiro.

### 5.4 Testes

1. **Golden de argv** (rede de segurança do refactor, criada ANTES de mexer no
   código):
   - `tools/gen_wrapper_argv_golden.py` monta uma matriz de ~40 workflows (6
     direções × modos 0/2/6/7/8 × delete on/off, derivativo, staging, exclude,
     rename, expert flags, dry-run, linha de manifest com colunas) e grava o
     argv que o código **atual** produz em
     `tests/fixtures/wrapper_argv_golden.json`. Rodar contra o HEAD extraído
     (`git show HEAD:jxl_photo.py > <tmp>/jxl_photo.py`), nunca com
     `git stash`.
   - `tests/test_wrapper_argv_golden.py` compara cada caso de forma canônica:
     positionais iguais e na mesma posição, multiconjunto de pares
     (flag, valor) igual, e o bloco dos expert flags igual e no fim. A ordem
     entre flags diferentes não importa para o argparse, e os dois montadores
     atuais já usam ordens diferentes.
2. **Cobertura**, `tests/test_wrapper_option_registry.py`:
   - toda flag de `add_argument` dos 4 filhos (AST, a mesma função do
     `test_docs_cover_the_flags.py`) está no `REGISTRY` ou no `NOT_OFFERED`;
   - nenhuma flag do registro deixa de existir nos filhos das suas direções;
   - `choices` do registro iguais aos do argparse (via `ast.literal_eval`);
   - toda entrada tem `label`, `help`, `group` válido e `level` válido.
   - Prova pré-fix: falha no HEAD (não existe `REGISTRY`).
3. A suíte inteira com o mesmo resultado da baseline do PC.

**Critérios de aceite:** `python -m py_compile jxl_photo.py`; golden 100%;
cobertura verde; `python -m pytest tests/ -n 8 -q` igual à baseline. A bateria
não é necessária (nenhum filho muda), mas rodar uma vez os presets do dono via
`--run-preset` com `--dry-run` e comparar o argv logado com o de antes.

---

## 6. Fase 2 — UI única, tela "todas as opções" e as flags que faltam

### 6.1 Camada de UI (código a criar em `jxl_photo.py`)

```python
class UI:                                  # interface
    def section(self, title: str) -> None: ...
    def note(self, text: str) -> None: ...     # também warn() e error()
    def table(self, columns, rows) -> None: ...
    def choose(self, qid, prompt, options: List[Tuple[str, str]], default=None) -> str: ...
    def confirm(self, qid, prompt, default: bool) -> bool: ...
    def ask_int(self, qid, prompt, default, lo, hi) -> int: ...
    def ask_float(self, qid, prompt, default, lo, hi) -> float: ...
    def ask_text(self, qid, prompt, default="") -> str: ...
    def ask_path(self, qid, prompt, default="") -> str: ...   # _strip_surrounding_quotes incluso

class RichUI(UI): ...      # Prompt/Confirm/IntPrompt do rich
class PlainUI(UI): ...     # input(); MESMA ordem e mesmos textos de pergunta
class ScriptedUI(UI):      # testes: answers={qid: valor}; pergunta desconhecida = erro
```

- `qid` é estável e hierárquico (`"menu.main"`, `"repair.folder"`,
  `"opt.buffering"`), e é por ele que os testes respondem.
- O `ui` é escolhido uma vez em `_main()` (`RichUI` se `RICH_AVAILABLE`, senão
  `PlainUI`) e guardado em `InteractiveMenu.ui`.
- **Migrar nesta fase só:** `repair_jbrd_flow`, `explorer_thumbnail_flow`, os
  dois flows novos (§6.3) e a tela "todas as opções" (§6.2). O resto do wizard
  continua como está; a migração completa vem na Fase 3.

### 6.2 Tela "todas as opções" no resumo atual (Step 7, `_wizard_confirm`, `:5978`)

- Nova tecla **`[o] all options for this job`**: lista as entradas do
  `REGISTRY` aplicáveis (direção + `when`), agrupadas por `GROUPS`, com valor
  efetivo e origem. Permite editar pelo número, buscar com `/`, e volta ao
  resumo.
- A edição grava no `legacy_key` da entrada, então os montadores e o
  salvamento de sessão continuam iguais.
- Conflitos e requisitos são checados ao editar; a mensagem vem do registro.

### 6.3 Flags faltantes, `build_parser()` e validação dos expert flags

- Mover as 27 flags da §4 de `NOT_OFFERED` para o `REGISTRY` (as duas "não
  oferecer" e as internas ficam). Persistência: um campo novo
  `last_extra_options: Dict[str, Any]` em `ToolConfig`, para não criar 20
  campos `last_*`, lido e gravado por `save_last_session` e
  `_run_saved_session`. Presets antigos sem o campo continuam valendo.
- Menu atual: entradas novas **`R` Renamed or moved folders** (restamp) e
  **`C` Reset the ICC test cache**. Os números 1-9 e 0 não mudam.
- `build_parser()` no encoder, no decoder e no recompressor: mover o bloco de
  `add_argument` para fora de `main()` sem mudar uma flag; o transcoder já tem.
  `test_docs_cover_the_flags.py` continua verde, e um teste novo checa que as
  option strings de `build_parser()` são iguais às do AST.
- Validação dos expert flags: o wrapper importa o filho e chama
  `build_parser().parse_known_args(tokens)`; o que sobrar desconhecido é
  recusado antes de rodar e antes de cobrar o HHMM. Uma flag que já está no
  registro gera a dica.

### 6.4 Testes da Fase 2

- `ScriptedUI` nos flows novos e migrados: restamp em dry-run monta
  `jxl_tiff_encoder.py <pasta> --mode N … --restamp-moved --dry-run`; o cache
  ICC monta `jxl_tiff_encoder.py --clear-icc-cache`.
- Cada uma das 27 flags: tem entrada no registro, aparece na tela "todas as
  opções" da direção certa e sai no argv quando ligada (um teste
  parametrizado, sem roteiro de prompts).
- Os testes atuais baseados em `input()` continuam passando sem mudança,
  porque os flows não migrados mantêm a ordem.
- `tests/test_wrapper_argv_golden.py` continua verde, já que nenhuma opção
  nova é ligada por padrão.

**Critérios de aceite:** suíte igual à baseline mais os testes novos;
cobertura verde com `NOT_OFFERED` reduzido; o flow de restamp testado à mão
numa pasta renomeada com caminho hostil (japonês + `[ ]` + espaço). Como
`build_parser()` mexe nos filhos, rodar a bateria completa
(`python tools/real_photo_battery.py`).

---

## 7. Fase 3 — Menu por tarefas, tela de revisão, `Job` e presets v2

Decisões tomadas (o planner local detalha arquivo por função):

- **Menu e fluxo:** exatamente o da §3. O wizard antigo (Steps 1-7) sai; as
  funções de passo que testes usam como ponto de monkeypatch (§5.3) viram
  adaptadores finos até os testes migrarem, e então são removidas. Não manter
  dois wizards.
- **`Job`:**

  ```python
  @dataclass
  class Job:
      task: str                  # "archive_tiff", "restore_tiff", "derive_jxl", ...
      source: str
      dest: Optional[str]
      mode: int                  # 0-8, 99 = manifest
      options: Dict[str, Any]    # SÓ o que foi escolhido (você/preset); defaults ficam no filho
      expert_flags: str = ""
      manifest_path: Optional[str] = None
  ```

  Guardar só o que foi escolhido é deliberado: se depois o usuário mudar um
  setting do script (ou o overlay da Fase 4), o preset passa a refletir a
  mudança, seguindo a regra "settings never hardcoded".
- **Presets v2:** `{"schema": 2, ...Job}`. Ler v1 (snapshot `last_*`) com
  `job_from_session_v1()`, mapeando via `legacy_key`; gravar v2 só ao salvar.
  `--run-preset` aceita os dois. Teste: para cada preset em fixtures e para
  uma **cópia** da config real do dono (USERPROFILE temporário, como a
  bateria faz), o argv de v1 → Job → `build_child_cmd` é igual ao argv atual.
- **Gates únicos:** `_job_gates(job, resolved_entries) -> (refusals,
  confirmations)` com as regras de hoje (derivativo × delete e in-place, rename
  in-place, jbrd sem rename, decode degradado × delete, `--icc-profile` ×
  delete, lossy + delete-skipped, recusas sem assistência, HHMM e in-place).
  Usado pelos três caminhos. Os testes de gate atuais precisam passar **sem
  edição**, porque testam o resultado via `execute_workflow` e
  `_execute_manifest_workflow`.
- **Colunas do manifest** geradas pelo registro (`manifest_column` mais um
  `parse` próprio quando a sintaxe é especial, como `long:N` no Resize) para
  as **9 colunas existentes**, com a mesma semântica de célula
  vazia/ausente/`-`. `tests/test_manifest_row_options.py` precisa passar sem
  edição. Colunas novas só quando o dono pedir.
- **CLI do wrapper:** `--job FILE.json` roda uma descrição de Job (com os
  mesmos gates do preset sem assistência) e `--print-job` imprime o Job revisado
  em JSON. Útil para automação e para testes.
- **Divisão da classe:** `InteractiveMenu` vira `MenuApp` (loop e menus),
  `JobWizard` (tarefa, pasta, qualidade e revisão), `JobRunner` (gates,
  montagem, streaming, resumo), `ManifestRunner` (carga, guardas, colisões),
  `Maintenance` (restamp, miniaturas, jbrd, cache ICC) e `PresetStore`. Tudo
  no mesmo arquivo. `InteractiveMenu` fica como fachada com os métodos da
  §5.3 até os testes migrarem.
- **Doc gerada:** a tabela "What can be configured…" de
  `docs/README_jxl_tools.md` passa a sair do registro entre os marcadores
  `<!-- options:start -->` e `<!-- options:end -->`, via
  `tools/gen_options_doc.py`; um teste falha se regenerar der diff. As seções
  "Main menu options" e "Workflow wizard steps" são reescritas.
- **Release:** `docs/upgrading.md` ganha "menu reorganizado; presets migram
  sozinhos; atalhos numéricos mudaram". O resto segue o padrão (TOOLKIT_VERSION
  nos 5, version_history, jxl-photo-exe).

**Critérios de aceite:** suíte verde; bateria verde (ela roda os 4 presets do
dono duas vezes via `--run-preset`); os testes que roteirizavam prompts por
ordem foram reescritos com `ScriptedUI` sem perder nenhuma asserção de regra.

---

## 8. Fase 4 (opcional) — Overlay de settings dos scripts

Resolve o "sem configurar scripts individuais" para os ~50 settings sem flag
(pastas por modo, TEMP_DIR, timeouts, teto de memória, listas de software,
tamanhos de miniatura e preview, políticas do recompressor, defaults do
transcoder).

- **Arquivo:** JSON `{"jxl_tiff_encoder": {"EXPLORER_THUMBNAIL_SIZE": 512}, ...}`.
- **Caminho:** env `JXLPHOTO_SETTINGS` (string vazia = desligado); senão
  `SCRIPT_DIR/.jxl_script_settings.json` se existir (modo portátil, espelho de
  `ConfigManager._get_config_path`); senão
  `%USERPROFILE%\.jxl_script_settings.json` (`~` fora do Windows).
- **Nos 4 filhos:** `_apply_settings_overlay(globals(), "<módulo>")`, com
  paridade fixada em `tests/test_helper_parity.py`, chamado logo depois do
  bloco de settings e **antes** da captura de `_RUN_DEFAULTS` (ou de
  `_prev_run_globals` no decoder). Assim o overlay vira "o setting do script"
  e as regras de reset por execução continuam valendo.
- **Validação:** tipo igual ao do valor atual (tupla para listas de software);
  nome desconhecido, tipo errado ou nome em `SAFETY_SETTINGS` gera warning e é
  ignorado. `SAFETY_SETTINGS = {DELETE_SOURCE, DELETE_CONFIRM,
  DELETE_SKIPPED, OVERWRITE_GUARD, VERIFY_ROUNDTRIP, DELETE_SOURCE_REQUIRE_MD5,
  STORE_MD5, ALLOW_INCOMPLETE_GROUPS, PROVENANCE_CHECK, ADOPT_SCAN}`: essas
  continuam escolhas por execução (com os gates) ou edição do arquivo.
- **Auditoria:** depois do `setup_logger`, cada filho loga uma linha
  `Script settings overlay: <caminho>: N value(s): A, B`. O `--version` não
  muda.
- **Wrapper:** Settings → "Script settings", editor gerado de
  `SETTINGS_REGISTRY` (módulo, nome, tipo, faixa ou escolhas, label, ajuda,
  grupo), mostrando o valor efetivo (overlay ou script) e "voltar ao valor do
  script".
- **Hermeticidade:** `tests/conftest.py` e `tools/real_photo_battery.py`
  exportam `JXLPHOTO_SETTINGS=""`. Um overlay de verdade no PC nunca pode
  vazar para os testes, e o modo portátil coloca o arquivo dentro do repo.
- **Testes:** overlay aplicado antes de `_RUN_DEFAULTS` (um segundo `main()`
  no mesmo processo mantém o valor do overlay); safety recusado; tipo errado
  recusado; desligado com env vazio; caminhos com japonês, `[ ]` e espaço;
  paridade do helper.
- **Aceite:** suíte verde; bateria verde com overlay vazio **e** com um overlay
  que mude, por exemplo, `EXPLORER_THUMBNAIL_SIZE=512`, conferindo o efeito no
  arquivo.

---

## 9. Como adicionar uma opção depois disso (proposta para o AGENTS.md)

1. Adicionar a flag no filho e no README dele (regra de hoje, testada).
2. Adicionar **uma** entrada `Opt(...)` no `REGISTRY`: id, flag, direções,
   tipo, grupo, nível, label, ajuda, `default_from` e, se precisar,
   `when`/`requires`/`conflicts`/`danger`.
3. Se for setting sem flag: uma entrada em `SETTINGS_REGISTRY` (Fase 4).
4. Rodar os testes. A cobertura falha se faltou o passo 2, e a doc gerada
   falha até rodar `tools/gen_options_doc.py`.

A opção aparece sozinha na revisão, na busca, nos presets, no argv e na doc.
Escrever uma pergunta nova no wizard só é necessário se ela for
**essencial** e merecer ser perguntada antes da revisão.

---

## 10. Fora do escopo e riscos

- **Fora:** dividir o wrapper em vários arquivos ou pacote (mexe no
  empacotamento do exe e do PIP); mudar a CLI dos filhos além do
  `build_parser()` e do overlay; traduzir a UI (fica em inglês); mudar
  qualquer gate de segurança.
- **Riscos:**
  - Churn de testes nas Fases 2 e 3. Mitigação: `ScriptedUI` por qid e a
    regra de não editar asserções de regra.
  - Presets agendados do dono. Mitigação: golden de argv por preset e leitura
    de v1 para sempre.
  - Regressão de gate ao unificar. Mitigação: os testes de gate atuais
    precisam passar sem edição.
  - Overlay escondido mudando o comportamento. Mitigação: linha de log
    obrigatória e `SAFETY_SETTINGS` fora do alcance.
- **Ordem sugerida e tamanho relativo:** Fase 1 (média, maior retorno por
  risco) → Fase 2 (média; já entrega "todas as flags no wrapper") → Fase 3
  (grande; a UX nova) → Fase 4 (média; opcional, mas resolve os settings).
  Os itens do audit (X-A, X-B, W-A…) são independentes e podem vir antes ou
  intercalados.

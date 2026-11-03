# Audit completo do jxl-photo — 2026-11-03 (JST)

- **Base:** `b364497` (v2.19.2, `main`), branch de trabalho `claude/magical-hypatia-6m0yth`
- **Escopo:** os 4 backends (`jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`,
  `jxl_jpeg_transcoder.py`, `jxl_recompressor.py`), o wrapper `jxl_photo.py` e a
  interação entre eles. Fora do escopo, como manda o AGENTS.md: `deprecated/`,
  `claude/`, `PIP/`, `hdr/`.
- **Feito por:** Claude (sessão na nuvem), só com leitura estática e provas
  sintéticas. **Nenhum codec real rodou aqui** (sem cjxl/djxl/exiftool/magick/
  imagecodecs) e não houve bateria com fotos reais.
- **Para quem:** a sessão local do Claude, para verificar cada item com as
  fixtures e a suíte no PC do dono antes de qualquer correção (fluxo
  planner → coder → reviewer do AGENTS.md).

## Como foi feito e o que isso significa

1. Mapa AST das ~39 mil linhas e leitura completa dos caminhos de maior risco:
   `main()` dos 5 scripts, todos os portões de deleção, staging/promoção, trocas
   in-place, sync, leitura de marcadores, multipágina, checksums/jbrd, montagem
   dos comandos do wrapper, execução do manifest, presets não assistidos e o
   streaming dos filhos. Partes de menos risco (textos do wizard, preflight
   consultivo, relatórios) foram só passadas por cima.
2. Quatro **provas sintéticas** em Python puro, todas com caminho hostil
   (japonês, `[ ]`, espaço, parênteses), em `AI_tools/261103_audit_probes/`.
   Elas confirmam X-A, E-G e X-B. Rodar com `python <probe>.py .` na raiz do repo.
3. **Baseline da suíte no Linux** (Python 3.13.18, `-n 4`): `112 failed, 2431
   passed, 329 skipped, 14 errors in 67.66s`. A grande maioria das falhas vem da
   falta de cjxl/djxl/exiftool/imagecodecs e de caminhos `C:\` do Windows, então
   **não serve de baseline**. A baseline de verdade é a do PC do dono (Python
   3.12, `python -m pytest tests/ -n 8 -q`). Duas falhas foram investigadas e
   são de ambiente (ver "Pontos de atenção").

**Legenda.** "Confirmado" = reproduzido por prova sintética aqui. "Por leitura"
= dedução do código, precisa de prova local. Severidade: **Crítica** = perda
de dados provável; **Alta** = perda de dados ou corrupção silenciosa possível;
**Média** = resultado errado, execução que falha ou degradação visível, sem
perda; **Baixa** = robustez, desempenho ou cosmético. Probabilidade = chance de
acontecer no uso real do dono (biblioteca grande, Windows, presets agendados,
exports do Capture One).

## Resumo

Não achei nada **crítico**. Os portões de deleção dos quatro backends são
fail-closed em todos os caminhos que li, e as trocas in-place usam temp +
`os.replace` + `_changed_since`. Os pontos que pedem atenção primeiro:

1. **X-A (Alta / Baixa, confirmado):** quando a leitura em lote dos marcadores
   falha, o resultado é igual a "arquivo sem marcador", e a guarda de
   sobrescrita deixa passar. É o único caminho fail-open que encontrei numa
   proteção contra perda de dados.
2. **X-B (Média / Média, confirmado):** decoder e recompressor nos modos 6/7
   pegam as saídas dos *outros* scripts dentro de `_EXPORT` (`16B_JXL_small`,
   `JXL_jpeg`). Com exports TIFF16 + JPEG da mesma foto (o normal no Capture
   One), a execução inteira aborta por destino duplicado.
3. **E-G (Média / Média, confirmado):** `reorder_jxl_boxes` (nos três
   backends) move a box `uuid` da miniatura do Explorer para depois do
   codestream. `--restamp-moved`, `--restamp-then-sync` e o carimbo do
   `--provenance adopt` passam por ela, e o `--refresh-explorer-thumbnails` não
   detecta nem corrige isso.
4. **W-A (Média / Média-baixa):** o wrapper mata o filho após 60 min sem saída,
   e as leituras de marcadores em lote do planejamento (decoder, encoder) não
   imprimem nada entre lotes. Em biblioteca grande com disco ocupado (#537), o
   filho pode morrer ainda planejando.
5. **T-A (Média / Baixa):** JXL→JPEG/PNG no transcoder sem `--to-srgb` (o padrão
   do wizard) ignora o perfil ICC guardado no XMP. O resultado sai com cores
   erradas e ainda perde o token do perfil.
6. **E-B / D-B (Média):** memória por worker não contabilizada. No encoder, os
   buffers Python do PNG ficam fora do teto, e a fração passou de 0.8 para 1.0
   na v2.19.1. O decoder não tem teto, e o preview faz um float64 da imagem
   inteira para gerar 256 px.

## Tabela de achados

| ID | Script(s) | Achado | Sev. | Prob. | Estado |
|---|---|---|---|---|---|
| X-A | enc, dec, trans, recomp | Lote de marcadores ilegível = "sem marcador" → guarda de sobrescrita fail-open | Alta | Baixa | Confirmado (enc, trans) |
| X-B | dec, recomp (+ wrapper) | Modos 6/7 pegam saídas de outros scripts sob `_EXPORT` → aborta por destino duplicado | Média | Média | Confirmado |
| E-G | enc, recomp, trans | `reorder_jxl_boxes` joga a box `uuid` (miniatura) para depois do codestream; restamp/adopt atingidos; refresh não cura | Média | Média | Confirmado |
| W-A | wrapper + dec, enc | Timeout ocioso fixo de 60 min × leituras em lote silenciosas no planejamento | Média | Média-baixa | Por leitura |
| T-A | trans | JXL→JPEG/PNG direto ignora o ICC do XMP (cor errada) e remove o token | Média | Baixa | Por leitura |
| E-B | enc | Teto de memória ignora buffers Python (≈1–1,4 GB transitório por worker em 45 MP); fração 1.0 | Média | Média | Por leitura |
| D-B | dec | `add_jpeg_preview` converte a imagem inteira em float64 duas vezes (~2,2 GB em 45 MP); decoder sem teto de memória | Média | Média-baixa | Por leitura |
| T-F | trans | Convert lossy: falha da cópia de metadados é engolida, mas o portão apaga a fonte | Média-baixa | Baixa | Por leitura |
| T-B | trans | JXL 16 bits → JPEG 8 bits + `--delete-source` apaga o master; decoder (D2) e recompressor nunca fazem isso | Média (design) | Baixa | Por leitura |
| R-A | recomp | In-place com staging: integridade checada DEPOIS do `os.replace` sobre a única cópia | Alta se ocorrer | Muito baixa | Por leitura |
| E-J | enc | Teste ICC "cautious" usa `CJXL_DISTANCE` global, não a distância do arquivo (`SCAN_DISTANCE`) | Baixa-média | Baixa | Por leitura |
| R-B | recomp | Veto de grupo só vê páginas desta execução → página "solitária" apagada, documento dividido entre pastas | Baixa | Baixa | Por leitura |
| E-C | enc (+ cópias) | `_long_codec_paths` ignora caminhos relativos → caminho absoluto >260 sem `\\?\` | Baixa | Baixa | Por leitura |
| E-D | enc | ID de grupo com `resolve()`: troca de letra de drive + recodificação parcial → dois grupos incompletos | Baixa | Baixa | Por leitura |
| R-C | recomp | `_same_dir` compara strings: saída = entrada via junction/UNC/subst não é vista como in-place | Baixa | Muito baixa | Por leitura |
| E-F | 4 backends | `--clean-staging` de uma execução concorrente apaga saídas que esperam a pasta fechar | Baixa | Baixa | Por leitura |
| T-C | trans | Timeout da trava do `checksums.md5` não lança exceção → "filed" falso, a nova tentativa no fim não acontece | Baixa | Baixa | Por leitura |
| T-G | trans | `--repair-jbrd` troca o arquivo sem restaurar o mtime (o relayout restaura) | Baixa | Baixa | Por leitura |
| T-E | trans | `DELETE_SOURCE_REQUIRE_MD5=False`: skip JXL→JPEG apaga o JXL só com checagem estrutural | Baixa | Muito baixa | Por leitura |
| D-D | dec | `copy_metadata`: rc do "clear" de `dc:Relation` não é checado → tokens internos vazam para o TIFF | Baixa | Muito baixa | Por leitura |
| E-H | enc, dec, recomp | Âncora do marcador procurada no caminho ABSOLUTO (pastas acima da entrada contam) | Baixa | Baixa | Por leitura |
| E-I | 3 backends | `reorder_jxl_boxes`: arquivo inteiro na RAM ×2 e concatenação quadrática | Baixa | — | Por leitura |
| E-E | enc | Verify lossy de master com ICC blob (estratégias always/heuristic) sempre reprova → nunca apaga | Info | Muito baixa | Por leitura |
| D-E | dec | `_verify_tiff_integrity` só aceita a assinatura TIFF clássica (42), não BigTIFF (43) | Info | Muito baixa | Por leitura |
| W-B | wrapper | `--mode 8` nos expert flags passa pelo gate in-place do wrapper (o filho pede a confirmação) | Info | Baixa | Por leitura |

## Detalhes

### X-A — Lote de marcadores ilegível vira "sem marcador" e a sobrescrita passa

- **Onde:** os leitores em lote pré-preenchem `{"src": None, "srcsum": None}` e
  deixam assim quando o lote falha (`continue` com só um warning):
  - `jxl_tiff_encoder.py:4683` (`_read_source_markers_batch`), consumido em
    `jxl_tiff_encoder.py:5021` (`_foreign_overwrite_reason`) e na guarda não
    estrita de `main()`, `jxl_tiff_encoder.py:7704-7708`;
  - `jxl_tiff_decoder.py:978`, consumido em `jxl_tiff_decoder.py:5623-5629`
    (`if _unmarked and not _strict_guard: continue`);
  - `jxl_jpeg_transcoder.py:4127-4134` (`_foreign_overwrite_filter`, saídas lossy);
  - `jxl_recompressor.py:5560-5566` (`_foreign_overwrite_refusals`, cujo
    comentário diz que "markerless OU unreadable" mantém o comportamento antigo
    de propósito).
- **O que acontece:** "não consegui ler" e "não tem marcador" viram o mesmo
  dicionário. A guarda não estrita, que existe para não sobrescrever o arquivo
  de *outra* foto (#268, #496–#499), trata o arquivo como legado sem marcador e
  libera a sobrescrita.
- **Cenário de perda:** modo que colapsa pastas (2/4/5/6/7, ou modo 0 com pasta
  de saída). Uma execução anterior apagou a fonte da foto A depois de arquivar
  `A.jxl`. Agora chega outra foto B com o mesmo nome, mais nova (sync) ou com
  `--overwrite`. Se o lote do exiftool desse arquivo der timeout, stall, JSON
  inválido ou rc≠0 sem stdout, `A.jxl`, a única cópia de A, é sobrescrito. O
  #537 mostra que lotes falham de verdade em disco ocupado.
- **Mitigações que existem:** no decoder em modo sync o arquivo é relido
  individualmente por `_decode_output_is_ours`, que é fail-closed, então lá só
  `--overwrite` explícito expõe o problema. A guarda estrita (delete em modo
  que colapsa) e o `--delete-skipped` (`_skipped_archive_proof`,
  `_provenance_proves_archive`, `_markers_prove_archive`) são fail-closed e
  estão corretos.
- **Prova:** `python AI_tools/261103_audit_probes/probe_unread_markers.py .`.
  Ela simula `rc=1` e stdout vazio no `_run_captured` e mostra
  `{'src': None, 'srcsum': None}`, `_foreign_overwrite_reason -> None` e
  `_foreign_overwrite_filter refused -> []`.
- **Verificar localmente:** teste com exiftool real. Fazer um master com
  marcador de outra fonte num modo que colapsa e uma fonte mais nova. Forçar a
  falha só da leitura em lote (monkeypatch de `_run_captured` quando o comando
  traz `-j` e `stall=`). Esperado hoje: OVERWRITE. Esperado depois da correção:
  REFUSED. Fazer o mesmo no decoder (com `--overwrite`), no transcoder (lossy) e
  no recompressor.
- **Correção sugerida:** o leitor devolve um terceiro estado (por exemplo
  `info["unread"] = True`), e toda guarda não estrita recusa (ou relê
  individualmente) em vez de tratar como sem marcador. Manter o helper com
  paridade nos 4 backends.

### X-B — Modos 6/7 do decoder e do recompressor pegam as saídas dos outros scripts

- **Onde:** `jxl_tiff_decoder.py:4320` (`find_jxls_mode6`) e `:4335`
  (`find_jxls_mode7`, com `EXPORT_JXL_SUBFOLDER = ""` em `:1470`), e
  `jxl_recompressor.py:3324`, que só pula as próprias pastas
  (`_is_own_output_path`, `:3257`). As pastas dos outros scripts são
  `jxl_recompressor.py:991` (`EXPORT_JXL_FOLDER = "16B_JXL_small"`) e
  `jxl_jpeg_transcoder.py:1860` (`EXPORT_JXL_FOLDER = "JXL_jpeg"`).
- **O que acontece:** o encoder tem a proteção espelho (lê as pastas de saída
  do decoder via `ast`, `jxl_tiff_encoder.py:6769-6796`). Decoder e
  recompressor não têm proteção equivalente para as saídas do recompressor e do
  transcoder. Os dois descem um nível abaixo do marcador, então:
  - `_EXPORT/16B_JXL/x.jxl` (encoder) e `_EXPORT/16B_JXL_small/x.jxl`
    (derivativo do recompressor) → decoder modo 6/7 → os dois viram
    `_EXPORT/16B_TIFF/x.tif`;
  - `_EXPORT/TIFF16/x.tif` → encoder → `_EXPORT/16B_JXL/x.jxl`, e
    `_EXPORT/JPEG/x.jpg` → transcoder → `_EXPORT/JXL_jpeg/x.jxl`. Depois disso,
    decoder modo 6 → os dois viram `16B_TIFF/x.tif`, e recompressor modo 6 →
    os dois viram `16B_JXL_small/x.jxl`.
- **Efeito:** `_abort_on_duplicate_outputs` encerra a execução **inteira** com
  exit 2. É fail-closed, sem perda, mas o preset ou a linha do manifest passa a
  falhar sempre. Se os nomes forem diferentes (por exemplo com `--rename-to`),
  o derivativo é decodificado como se fosse master, e com `--delete-source` o
  derivativo é apagado.
- **Prova:** `probe_decoder_mode6.py` e `probe_cross_mode6.py` (caminhos com
  `吾妻山公園 [FINAL]` e `DSC_0001 (2)`).
- **Verificar localmente:** montar a árvore do evento com `TIFF16/` e `JPEG/` da
  mesma foto. Rodar encoder modo 6, transcoder JPEG→JXL modo 6 e recompressor
  modo 6 com `--resize-long 2048`. Em seguida rodar decoder modo 6 e
  recompressor modo 6 de novo. Esperado hoje: "Duplicate output destination"
  com exit 2. Rodar também pelo wrapper (manifest), cujo `_skip_check` em
  `jxl_photo.py:7584-7629` precisa acompanhar.
- **Correção sugerida:** abaixo do marcador, decoder e recompressor pulam as
  pastas de saída conhecidas dos outros scripts, lidas das configurações deles
  via `ast`, como o encoder já faz. Espelhar no `_skip_check` do wrapper.
  Documentar no README dos modos 6/7.

### E-G — `reorder_jxl_boxes` move a miniatura do Explorer para depois do codestream

- **Onde:** `jxl_tiff_encoder.py:3848-3879`, `jxl_recompressor.py:3371` e
  `jxl_jpeg_transcoder.py:2269`. Qualquer box fora de
  `META_ORDER`/`META_EXTRA`/codestream cai em `other_boxes`, que vai para o
  fim. O comentário em `jxl_tiff_encoder.py:2589` afirma o contrário ("this
  toolkit's box reorder keep it in place").
- **Quem é atingido:** `_rewrite_master_relation` (`jxl_tiff_encoder.py:4829`),
  usado por `--restamp-moved`, `--restamp-then-sync` e o carimbo do
  `--provenance adopt`, todos em masters que já têm miniatura (lossless por
  padrão, ou todos com jxl-thumb). O encode normal não é atingido porque a
  miniatura é adicionada depois do reorder.
- **Efeito:** depende do jxl-thumb. O docstring de `_write_thumbnail_box` diz
  "right before the codestream, where a reader finds it without crossing the
  image data". Se o `ReadThumbnailBox` do jxl-thumb parar no primeiro
  `jxlc`/`jxlp`, a miniatura deixa de ser achada e o Explorer volta a
  decodificar o arquivo inteiro (~1,8 s por 45 MP em HDD). Pixels e metadados
  não mudam.
- **Agravante (R-D):** `_refresh_one_thumbnail` (`jxl_recompressor.py:5993`)
  considera "current" quando a impressão digital bate, esteja a box onde
  estiver. A box fora de lugar nunca volta.
- **Prova:** `probe_reorder_uuid.py`: `['ftyp','uuid','jxlc','Exif']` →
  `['ftyp','Exif','jxlc','uuid']` nos três scripts.
- **Verificar localmente:** (1) no repo do jxl-thumb, ver se `ReadThumbnailBox`
  varre todas as boxes ou para no codestream; (2) master lossless real com
  miniatura → renomear a pasta → `--restamp-moved` → listar a ordem das boxes e
  conferir a miniatura no Explorer.
- **Correção sugerida:** no reorder, manter a `uuid` (ou qualquer box
  desconhecida que estava antes do primeiro codestream) antes do codestream, na
  ordem original. O refresh também deveria tratar "box depois do codestream"
  como trabalho a fazer. Manter a paridade nos três scripts.

### W-A — Wrapper mata o filho durante o planejamento silencioso

- **Onde:** `jxl_photo.py:8419` (`_stream_child(..., idle_timeout=3600)`, sem
  configuração). Leitores silenciosos: `jxl_tiff_decoder.py:4424`
  (`_read_multipage_markers_batch`, que lê **todo** JXL de entrada, 400 por
  lote, STALL de 180 s e TIMEOUT de 1800 s por lote) e
  `jxl_tiff_encoder.py:4683` (guarda estrita e restamp leem todas as saídas
  existentes). O recompressor já imprime progresso
  (`jxl_recompressor.py:2682`, "Encode records: N/M read"), mas os outros não.
- **Cenário:** o #537 mediu mais de 180 s por lote no disco ocupado do dono.
  Uns 20 lotes (≈8 mil JXLs) dão mais de 60 min sem nenhuma linha, e o wrapper
  mata o filho com "No output for 60 min". O preset agendado falha em toda
  execução, sem perda de dados.
- **Verificar localmente:** medir quanto dura o planejamento do decoder no
  modo 6 sobre a maior biblioteca, no disco lento (entre "Files found" e "TIFF
  outputs planned"). Ou um teste que roda `_stream_child` com um filho que
  dorme mais que um `idle_timeout` reduzido.
- **Correção sugerida:** uma linha de progresso por lote em todos os leitores
  em lote dos 4 backends (o mesmo padrão do recompressor). Opcionalmente,
  idle_timeout configurável no wrapper.

### T-A — JXL→JPEG/PNG direto ignora o perfil guardado no XMP

- **Onde:** `jxl_jpeg_transcoder.py:5800-5811` (ramo direto do djxl quando não
  há `--icc-profile` nem derivativo) e `_copy_metadata`
  (`jxl_jpeg_transcoder.py:1804-1805`), que remove os tokens
  `jxlphoto-iccprofile`. No wizard, a pergunta "Convert to sRGB?" tem padrão
  "não" (`jxl_photo.py:5147`).
- **O que acontece:** em um master lossy cujo perfil real só existe no XMP, os
  pixels estão marcados como sRGB. É o caso de quando o encoder pulou o iCCP:
  ICC_PNG_STRATEGY cautious recusou, heuristic ou skip (eciRGB v2, ROMM com
  toe, LUT de scanner, página cinza com perfil RGB). O djxl escreve o JPEG/PNG
  como sRGB com números de outro espaço, ou seja, cor errada sem aviso. Em
  seguida o token com o perfil original é removido. O caminho via magick
  (`_source_profile_args`, `jxl_jpeg_transcoder.py:5161`) trata isso direito
  (precedência XMP ICC > iCCP > chunk sRGB).
- **Verificar localmente:** TIFF em eciRGB v2 com `--distance 1` (o log deve
  mostrar "Skipping ICC in PNG iCCP"). Depois transcoder JXL→JPEG sem
  `--to-srgb` e comparar com decoder→TIFF→conversão para sRGB. Conferir
  `exiftool -ICC_Profile:ProfileDescription` no JPEG e a ausência do token.
- **Correção sugerida:** se `_carried_icc(...)` existe, ou se
  `_decoded_in_original_space` é False, passar pelo caminho magick atribuindo o
  perfil carregado, sem converter.

### E-B — Teto de workers do encoder não conta os buffers do próprio Python

- **Onde:** `jxl_tiff_encoder.py:783` (`_memory_capped_workers`, que estima só
  o pico do cjxl), `:862` (`WORKER_MEMORY_FRACTION = 1.0` desde a v2.19.1),
  `convert_one` em `:5391` (`img.astype` copia mesmo com o mesmo dtype) e
  `make_png_bytes` em `:3725`.
- **O que acontece:** em 45 MP 16 bits RGB, cada worker tem um pico
  transitório no processo Python de ≈1–1,4 GB: `asarray` (270 MB), cópia
  `astype`, cópia `>u2`, PNG cru, saída do zlib e mais duas concatenações de
  bytes de ~200 MB. Depois ficam ~200 MB presos enquanto o cjxl roda. O
  `reorder_jxl_boxes` (E-I) também lê o arquivo inteiro e escreve outro na
  memória. A nota da v2.19.1 diz que "a estimativa já fica acima do pico
  medido", mas o que foi medido é o pico do cjxl. Em 2026-10-04 já houve uma
  execução perdida por falta de memória.
- **Verificar localmente:** `--workers 16` em TIFFs lossless de 45 MP,
  observando o commit charge (Performance Monitor, "Committed Bytes", ou
  Process Explorer em python.exe + cjxl.exe) contra a linha "Memory: ~X GB per
  worker".
- **Correção sugerida:** somar ~4× o tamanho do array de 16 bits ao custo por
  worker, ou voltar a fração para 0.8. Reduzir as cópias: `astype(...,
  copy=False)`, uma única cópia big-endian, `b"".join` no PNG.

### D-B — Preview do decoder converte a imagem inteira em float64; decoder sem teto

- **Onde:** `jxl_tiff_decoder.py:2845-2846`
  (`np.rint(img_data / 257).astype(np.uint8)` sobre a imagem **inteira** para
  um preview de 256 px). `ADD_JPEG_PREVIEW = True` é o padrão (`:1176`). O
  decoder não tem `_memory_capped_*`, os workers padrão são `min(cpu, 16)` e o
  README cita testes com 32.
- **Efeito:** ≈2,2 GB de float64 por worker em 45 MP (4 GB em 84 MP), além do
  djxl e dos arrays de página. Um `MemoryError` dentro do preview é engolido
  ("Preview generation failed") e o TIFF sai sem preview; em outros pontos vira
  erro por arquivo. Também deixa a máquina paginando.
- **Verificar localmente:** decoder com `--workers 16` e 32 sobre masters de
  45 a 84 MP, medindo os private bytes de python.exe durante "Adding JPEG
  preview".
- **Correção sugerida:** subamostrar antes de converter
  (`img[::k, ::k]` para ~4× o tamanho alvo) ou usar matemática inteira
  (`(a.astype(np.uint32) * 255 + 32767) // 65535`). Avaliar um teto como o do
  encoder.

### T-F — Convert lossy: metadado perdido não impede a deleção

- **Onde:** `jxl_jpeg_transcoder.py:4862-4872` (`encode_to_jxl`) e
  `:5817`, `:5835-5842` (`decode_to_image`). `_copy_metadata` engole qualquer
  erro, o rc do exiftool dos marcadores/History não é checado e as exceções vão
  só para DEBUG. Os portões em `:6318-6392` (`cmd_convert`) e `:6941-7011`
  (`_process_file_group`) só checam integridade e se a fonte mudou.
- **Contraste:** encoder e decoder tratam uma cópia de metadados que falhou
  como ERRO, e o portão de deleção mantém a fonte.
- **Efeito:** cjxl e djxl já carregam Exif/XMP sozinhos, então o que se perde
  é o que só o `_copy_metadata` copia (IPTC-IIM e afins) e o marcador de
  proveniência. Sem o marcador, um `--delete-skipped` posterior falha fechado.
- **Verificar localmente:** monkeypatch de `_run_exiftool_argfile` para falhar
  dentro de `encode_to_jxl`, depois `--force-convert --delete-source` num JPEG
  com IPTC. Hoje a fonte é apagada.
- **Correção sugerida:** checar o rc, marcar o status como "error" (ou pelo
  menos bloquear o delete) quando a cópia ou os marcadores falharem.

### T-B — JXL 16 bits → JPEG 8 bits com `--delete-source` apaga o master (decisão de design)

- **Onde:** portão em `jxl_jpeg_transcoder.py:6318`. As recusas de derivativo
  em `:7532-7545` cobrem só resize, sharpen e `--icc-profile`.
- **Inconsistência:** o decoder considera `--depth 8` de uma fonte mais
  profunda uma "cópia degradada" que nunca apaga (D2,
  `jxl_tiff_decoder.py:3627-3633`). O recompressor nunca apaga derivativos. Já
  o transcoder apaga um master lossless de 16 bits depois de escrever um JPEG
  q90 de 8 bits, protegido só pelo token HHMM. O wrapper passa
  `--delete-confirm-off` e faz a própria confirmação.
- **Para decidir:** se a regra "um derivativo nunca apaga o master" deve
  incluir a redução de profundidade/qualidade. Hoje o `#542` só impede que o
  modo auto faça isso sem `--from-jxl`.

### R-A — In-place com staging: integridade conferida depois da troca

- **Onde:** `jxl_recompressor.py:5200-5250`. O código faz
  `shutil.move(staging → dest_tmp)`, depois `os.replace(dest_tmp, final)`, e só
  então `_verify_jxl_integrity(final)`.
- **Risco:** em modo in-place o `final` é a única cópia. Uma cópia
  cross-volume danificada (antivírus, ponte USB instável) que não lança erro
  substitui o master antes de qualquer checagem. É raro, mas irreversível.
- **Correção sugerida:** verificar `dest_tmp` (integridade, e tamanho ou hash
  iguais ao arquivo do staging) **antes** do `os.replace`.

### E-J — Teste ICC "cautious" com a distância errada para scans de filme

- **Onde:** `should_embed_icc_in_png` (`jxl_tiff_encoder.py:2980`), chamado
  em `:5465` sem a distância do arquivo. `_cautious_should_embed_icc` (`:2910`)
  usa `CJXL_DISTANCE` na chave do cache, e `_cautious_test_icc_depth` (`:2842`)
  usa `-d CJXL_DISTANCE` e `if CJXL_DISTANCE > 0 and ...`.
- **Cenário:** `CJXL_DISTANCE = 0` com `--scan-distance 1` (ou o inverso). O
  teste roda lossless, a recusa de "lossy ICC blob" nunca dispara, o veredito
  "embed" vai para o cache com `d=0`, e o scan lossy sai com o perfil de
  scanner no iCCP, exatamente o caso que o cautious existe para evitar. O
  verify do portão de deleção ainda segura o TIFF.
- **Correção sugerida:** passar `_dist` adiante e usar `d=<distância do
  arquivo>` na chave do cache.

### Itens menores

- **R-B** (`jxl_recompressor.py:5761-5780`): o veto de grupo só vê páginas
  presentes **nesta** execução. Com entrada de arquivo único,
  `--exclude-folders` ou um irmão filtrado no modo 6/7, a página é tratada como
  solitária e apagada depois que a cópia chega a outra pasta. O documento fica
  dividido. Usar a contagem `jxlphoto-pages` como o decoder faz.
- **E-C** (`jxl_tiff_encoder.py:2154-2155`, e as cópias com paridade):
  `_long_codec_paths` ignora argumentos relativos, e o `main()` não resolve
  `args.input`. Entrada relativa num cwd profundo deixa o cjxl/djxl com
  caminho absoluto acima de 260 sem `\\?\`. Verificar se o wrapper sempre passa
  caminho absoluto; se sim, só a CLI direta é afetada. Correção: `abspath`
  antes do teste.
- **E-D** (`jxl_tiff_encoder.py:6353`): o ID de grupo usa
  `tiff.resolve()`. Se a letra do drive externo mudar (G: → F:) e só parte de
  um split for recodificada (página que falhou antes), saem dois grupos
  incompletos. O decoder recusa (fail-closed) e é preciso `--overwrite` para
  curar.
- **R-C** (`jxl_recompressor.py:6688`, `:6748`): `_same_dir` compara strings.
  Saída = entrada por junction/UNC/subst/8.3 não é vista como in-place, e o
  temp é trocado sobre a fonte pelo caminho comum (sem segurar o grupo, sem
  checar a fonte antes da troca). O `--delete-source` acaba bloqueado pelo X2.
  Só acontece com `--overwrite`, já que no sync o mtime é igual. Usar
  `os.path.samefile`.
- **E-F** (`_clean_staging` nos 4 backends): só o mtime acima de 1 h protege
  uma execução concorrente. Mas saídas prontas **esperam** no staging até a
  pasta delas fechar (numa execução plana, o lote inteiro, por horas). Um
  `--clean-staging` concorrente apaga essas saídas → MOVE FAILED, nenhuma fonte
  apagada, trabalho perdido. Não há trava no diretório de staging.
- **T-C** (`jxl_jpeg_transcoder.py:2060`, `:2168`): no timeout da trava,
  `_append_checksum_line` só loga e retorna. `_file_checksum_lines` devolve
  True, o arquivo entra em `filed` e a nova tentativa do fim do run é pulada.
  É fail-closed (o portão faz KEEP e a adoção #556 acontece na próxima
  execução). A correção é fazer a função lançar exceção ou devolver False.
- **T-G** (`jxl_jpeg_transcoder.py:3378`): `_repair_one_jbrd` faz o
  `os.replace` sem `os.utime` (o exiftool atualizou o mtime da cópia), enquanto
  `_relayout_one_jbrd` restaura a data. Depois de um repair as decisões do sync
  invertem.
- **T-E** (`jxl_jpeg_transcoder.py:3924-3936`): só com
  `DELETE_SOURCE_REQUIRE_MD5 = False` (fora do padrão). Um skip JXL→JPEG apaga
  o JXL só com a checagem estrutural de qualquer JPEG com aquele nome. Poderia
  exigir a comparação por reconstrução, como o encode faz.
- **D-D** (`jxl_tiff_decoder.py:2678-2681`): o rc do
  `-XMP-dc:Relation=` não é checado (nem a leitura `rr`). Se falhar, os tokens
  internos do JXL (src/srcsum do TIFF de origem, mpg/page/pages, derived,
  iccprofile) ficam no TIFF ao lado dos novos. Os leitores pegam o ÚLTIMO
  token, então a proveniência ainda funciona; é só poluição de metadados.
- **E-H** (`jxl_tiff_encoder.py:2000-2047` e os finders `:6815`/`:6841`, com
  cópias no decoder e no recompressor): a âncora é a primeira pasta do caminho
  ABSOLUTO que bate com o marcador, inclusive acima da entrada. Uma raiz como
  `C1_Export` ou `Lightroom_Export` (que batem com `_EXPORT` pelas regras
  documentadas) vira âncora.
- **E-I** (`jxl_tiff_encoder.py:3885-3902` e cópias): `out += h + p` é
  quadrático, e o arquivo fica inteiro na RAM duas vezes. Usar `b"".join` ou
  escrita em stream.
- **E-E** (`jxl_tiff_encoder.py:5098-5165`): o verify lossy decodifica sem
  `--icc_out`. Num master lossy com ICC blob (estratégias always ou heuristic;
  o cautious já recusa), o djxl devolve sRGB linear, a razão de médias fica
  abaixo de 0.7 e o resultado é sempre KEEP. Fail-closed, só funcional.
- **D-E** (`jxl_tiff_decoder.py:1131`): só a assinatura 42 passa. BigTIFF
  (43) falharia como "integrity", mas o `TiffWriter` com `bigtiff=False`
  recusa mais de 4 GB antes disso. Só informativo.
- **W-B** (`jxl_photo.py:8696-8708`): o gate in-place do wrapper olha
  `workflow['mode']`, e um `--mode 8` nos expert flags vence pelo argparse. Aí
  quem confirma é o recompressor, interativo, ou exit 3 sem assistência.
  Fail-closed, só informativo.

## Pontos de atenção (não são bugs)

- **Teste de paridade × versão do Python.** Com Python 3.13,
  `tests/test_helper_parity.py::test_known_equivalent_variants_are_unchanged[_abort_on_duplicate_outputs]`
  falha: os **quatro** hashes mudam juntos (`a80bdc…` → `327989…`, transcoder
  `2125e8…` → `d22cb3…`). A causa é o `ast.dump` do 3.13, que omite campos
  vazios, então não há drift no código. Como `py` no PC do dono é 3.13, rodar a
  suíte com `py` quebra esse teste. Vale normalizar com `show_empty` ou fixar a
  versão.
- `tests/test_output_icc.py::test_cli_output_icc_in_place_single_file_is_refused`
  falhou aqui só porque a checagem de ferramentas (exit 1) vem antes da recusa
  in-place (exit 2). No PC, com as ferramentas, deve passar.
- **Literal igual ao default** (`jxl_recompressor.py:6600`): o código faz
  `EXPORT_JXL_FOLDER == "16B_JXL_small"`. É só uma dica no log e é inofensivo,
  mas contraria a regra do AGENTS.md "never repeat the default as a literal".
  Comparar com o valor de importação.
- **Proveniência por caminho:** trocar a letra do drive externo transforma todo
  registro em "outra fonte" (mesma classe de "pasta renomeada"; o
  `--restamp-moved` resolve). Se ainda não estiver, vale uma linha em
  `docs/behavior_and_limitations.md`.

## O que foi revisado e parece sólido

Para a sessão local não refazer:

- Portões de deleção dos 4 backends: fail-closed em todas as ramificações
  lidas, incluindo staging não promovido, skip sem prova, fonte alterada desde
  a leitura (X2), páginas descartadas, verify de pixels no encoder, grupos
  incompletos ou marcadores não lidos no decoder, reconstrução real do jbrd no
  transcoder e veto de grupo e MD5 de cópia no recompressor.
- Escrita via temp `.tmp` + `os.replace` em todos os caminhos de saída;
  `_promote_from_staging`; `_changed_since` antes de cada troca in-place;
  `_output_changed_since_judged` (#560) no decoder.
- Checksums do transcoder: trava entre processos, gravação só depois da troca
  (#556), adoção por reconstrução com hash antes e depois, `_jxl_binds_to_archived_jpeg`,
  e recusa de arquivo sem linha (#557, #558).
- Agrupamento multipágina do decoder (srcsum, contagem, conflitos,
  `_mpg_marker_failures`) e o tudo-ou-nada in-place do recompressor.
- Wrapper: detecção de `--delete-source` nos expert flags (abreviações e
  ambiguidades), gates de presets não assistidos (delete, in-place, manifest),
  leitura de CSV com BOM e recusa de ANSI, scan de colisões entre entradas
  usando os resolvers dos próprios filhos, e `_kill_process_tree`.

## O que NÃO foi verificado

- Nada com codecs, exiftool ou ImageMagick reais. A bateria e os testes de
  codec real precisam rodar no PC.
- Os números de memória de E-B e D-B são estimativas por leitura; precisam ser
  medidos.
- O comportamento do jxl-thumb (decide a severidade de E-G).
- Leitura linha a linha das partes de UI do wizard, do preflight consultivo e
  dos textos de resumo.

## Sugestão de ordem para a sessão local

1. Baseline da suíte no PC (`python -m pytest tests/ -n 8 -q`) e das 4 provas.
2. Provar X-A com um teste real-exiftool (falha forçada só no lote), nos 4
   backends.
3. Provar X-B com a árvore TIFF16 + JPEG em caminho hostil, rodando os scripts
   reais.
4. Conferir o jxl-thumb e provar E-G com master real + `--restamp-moved`.
5. Medir W-A, E-B e D-B na maior biblioteca e no disco lento.
6. T-A com um TIFF em eciRGB v2 lossy.
7. Para cada item confirmado: plano (Claude) → coder → review, com o teste de
   regressão falhando contra o código atual (`git show HEAD:<arquivo>`), como
   pede o AGENTS.md.

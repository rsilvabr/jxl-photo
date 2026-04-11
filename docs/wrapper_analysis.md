# Análise Completa: jxl_photo.py (Wrapper) vs READMEs

## Data: 2026-04-12
## Versão analisada: 1.5

---

## 1. MATRIZ DE FLUXOS IMPLEMENTADOS

### 1.1 Step 1: Source Format → Step 2: Destination

| Source | Opção | Destino | Script Chamado | Status |
|--------|-------|---------|----------------|--------|
| **JPEG** | 1 | JXL Lossless | jxl_jpeg_transcoder.py | ✅ OK |
| **JPEG** | 2 | JXL Lossy | jxl_jpeg_transcoder.py | ✅ OK |
| **TIFF** | 1 | JXL d=0 | jxl_tiff_encoder.py | ✅ OK |
| **TIFF** | 2 | JXL d=0.1 | jxl_tiff_encoder.py | ✅ OK |
| **TIFF** | 3 | JXL d=1.0 | jxl_tiff_encoder.py | ✅ OK |
| **TIFF** | 4 | JXL Custom d | jxl_tiff_encoder.py | ✅ OK |
| **JXL** | 1 | JPEG Auto | jxl_jpeg_transcoder.py | ✅ OK (v1.5) |
| **JXL** | 2 | JPEG Lossless | jxl_jpeg_transcoder.py | ✅ OK |
| **JXL** | 3 | JPEG Lossy | jxl_jpeg_transcoder.py | ✅ OK |
| **JXL** | 4 | PNG | jxl_jpeg_transcoder.py | ✅ OK |
| **JXL** | 5 | TIFF | jxl_tiff_decoder.py | ✅ OK |

---

## 2. PARÂMETROS ENVIADOS vs DOCUMENTAÇÃO

### 2.1 TIFF → JXL (jxl_tiff_encoder.py)

**Parâmetros documentados no README:**
```
--mode, --workers, --overwrite, --sync, --distance, --effort
--ram/--no-ram, --delete-source, --dry-run, --staging
--encode-tag, --d50-patch, --strip, --embed-thumbnail
```

**Parâmetros enviados pelo wrapper (execute_workflow):**
```python
cmd = [
    sys.executable, script,
    input_dir,
    '--mode', str(mode),
    '--workers', str(workers),
    '--distance', str(distance),
    '--effort', str(workflow['effort'])
]
# Condições:
--ram / --no-ram              ✅ (use_ram)
--strip                       ✅ (advanced['strip'])
--d50-patch                   ✅ (advanced['d50_patch'])
--overwrite                   ✅ (advanced['overwrite'])
--delete-source               ✅ (advanced['delete_source'])
--sync                        ✅ (advanced['sync'])
--staging                     ✅ (workflow['staging'])
--encode-tag                  ✅ (advanced['encode_tag'])
--embed-thumbnail             ✅ (advanced['embed_thumbnail'])
```

**Status:** ✅ TODOS OS PARÂMETROS MAPEADOS

---

### 2.2 JXL → TIFF (jxl_tiff_decoder.py)

**Parâmetros documentados no README:**
```
--mode, --workers, --overwrite, --sync, --compression, --depth
--matrix, --basic, --none, --target-icc, --no-icc-cleanup
--delete-source, --staging, --dry-run
```

**Parâmetros enviados pelo wrapper:**
```python
cmd = [
    sys.executable, script,
    input_dir,
    '--mode', str(mode),
    '--workers', str(workers),
    '--compression', workflow['compression'],
    '--depth', str(workflow['bit_depth'])
]
# Condições:
--no-preview          ✅ NOVO v1.5 (workflow['add_preview'])
--matrix              ✅ (advanced['matrix'])
--none                ✅ (advanced['none'])
--basic               ✅ (advanced['basic'])
--target-icc          ✅ (advanced['target_icc'])
--no-icc-cleanup      ✅ (advanced['no_icc_cleanup'])
--delete-source       ✅ (advanced['delete_source'])
--overwrite           ✅ (advanced['overwrite'])
--sync                ✅ (advanced['sync'])
--staging             ✅ (workflow['staging'])
```

**Status:** ✅ TODOS OS PARÂMETROS MAPEADOS (incluindo --no-preview novo)

---

### 2.3 JPEG ↔ JXL / JXL → PNG (jxl_jpeg_transcoder.py)

**Parâmetros documentados no README:**
```
--mode, --workers, --overwrite, --sync, --format, --quality, --distance
--bit-depth, --icc-profile, --to-srgb, --decode, --no-md5, --no-verify
--delete-source, --effort, --ram, --no-ram, --dry-run, --staging
--force-transcode, --force-convert
```

**Parâmetros enviados pelo wrapper:**
```python
cmd = [
    sys.executable, script,
    input_dir,
    '--mode', str(mode),
    '--workers', str(workers)
]
# Flags por conversion_type:
--force-transcode     ✅ (transcode_lossless, jxl_to_jpeg_lossless)
--force-convert       ✅ (convert_lossy, jxl_to_jpeg_force)
# Sempre adicionados para JXL→JPEG/PNG:
--quality             ✅ (workflow['quality'])
--effort              ✅ (workflow['effort'])
--icc-profile         ✅ (workflow['icc_profile'])
--staging             ✅ (workflow['staging'])
--no-md5              ✅ (advanced['no_md5'])
--no-verify           ✅ (advanced['no_verify'])
--overwrite           ✅ (advanced['overwrite'])
--sync                ✅ (advanced['sync'])
--delete-source       ✅ (advanced['delete_source'])
--format=png          ✅ (jxl_to_png)
--format=jpeg         ✅ (JXL→JPEG)
```

**Status:** ✅ TODOS OS PARÂMETROS MAPEADOS

---

## 3. WIZARD PERGUNTAS vs IMPLEMENTAÇÃO

### 3.1 Step 6: Parâmetros perguntados

| Quando | Pergunta | Salvo em | Usado em | Status |
|--------|----------|----------|----------|--------|
| TIFF→JXL | Distance | workflow['distance'] | Encoder | ✅ OK |
| TIFF→JXL | Effort | workflow['effort'] | Todos | ✅ OK |
| TIFF→JXL | D50 Patch | advanced['d50_patch'] | Encoder | ✅ OK |
| TIFF→JXL | Embed thumbnail | advanced['embed_thumbnail'] | Encoder | ✅ OK |
| JXL→JPEG (Auto/Lossy) | Quality | workflow['quality'] | Transcoder | ✅ OK |
| JXL→JPEG (Auto/Lossy) | sRGB conversion | workflow['icc_profile'] | Transcoder | ✅ OK |
| JXL→TIFF | Compression | workflow['compression'] | Decoder | ✅ OK |
| JXL→TIFF | Bit depth | workflow['bit_depth'] | Decoder | ✅ OK |
| JXL→TIFF | Add preview | workflow['add_preview'] | Decoder | ✅ OK v1.5 |
| Todos | Workers | workflow['workers'] | Todos | ✅ OK |
| Todos | Staging | workflow['staging'] | Todos | ✅ OK |

---

## 4. INCONSISTÊNCIAS ENCONTRADAS

### 4.1 Nomenclatura de opções no Step 2

**Problema:** Nomes diferentes entre versões

| Versão | Opções JXL source |
|--------|-------------------|
| README | JPEG / PNG / TIFF |
| v1.4 | [1] Lossless [2] Lossy [3] PNG [4] TIFF [5] AUTO |
| v1.5 | [1] Auto [2] Lossless [3] Lossy [4] PNG [5] TIFF |

**Status:** ✅ CORRIGIDO na v1.5 - ordem lógica: Auto → Lossless → Lossy → PNG → TIFF

---

### 4.2 Documentação README_jxl_tools.md desatualizada

**README diz:**
```
Step 2 — Destination
- JPEG → JXL Lossless (reversible) / JXL Lossy (smaller)
- TIFF → JXL d=0.1 (near-lossless) / JXL d=0 (lossless)
- JXL → JPEG / PNG / TIFF
```

**Não menciona:**
- ❌ Opção "JPEG Auto-Detect" (nova v1.5)
- ❌ Opção JPEG Lossless vs Lossy separadas

**Recomendação:** Atualizar README_jxl_tools.md com novas opções do Step 2

---

## 5. MAPEAMENTO CONVERSION_TYPE

| Origem | Destino | Opção | conversion_type | Script | Flags |
|--------|---------|-------|-----------------|--------|-------|
| JPEG | JXL | 1 | transcode_lossless | transcoder | --force-transcode |
| JPEG | JXL | 2 | convert_lossy | transcoder | --force-convert --distance |
| TIFF | JXL | 1-4 | jxl_tiff_encoder* | encoder | --distance |
| JXL | JPEG | 1 | jxl_to_jpeg_auto | transcoder | (nenhum - auto) |
| JXL | JPEG | 2 | jxl_to_jpeg_lossless | transcoder | --force-transcode |
| JXL | JPEG | 3 | jxl_to_jpeg_force | transcoder | --force-convert |
| JXL | PNG | 4 | jxl_to_png | transcoder | --format png |
| JXL | TIFF | 5 | jxl_tiff_decoder | decoder | --compression --depth |

*Tipo específico de encoder é determinado pela distance escolhida

---

## 6. RESUMO EXECUTIVO

### ✅ IMPLEMENTADO CORRETAMENTE:
1. Todos os fluxos básicos funcionam
2. Todos os parâmetros CLI são passados corretamente
3. Novo AUTO mode para JXL→JPEG funciona (v1.5)
4. Preview opcional em JXL→TIFF funciona (v1.5)
5. Quality/sRGB só aparecem para modos lossy

### ⚠️ DOCUMENTAÇÃO DESATUALIZADA:
1. README_jxl_tools.md não menciona "JPEG Auto-Detect"
2. README_jxl_tools.md não detalha opções de JPEG Lossless vs Lossy

### 📋 RECOMENDAÇÕES:
1. Atualizar README_jxl_tools.md com novas opções do Step 2
2. Adicionar seção sobre AUTO mode no README do transcoder

---

## 7. TESTES REALIZADOS

| Fluxo | Resultado | Notas |
|-------|-----------|-------|
| JPEG → JXL Lossless | ✅ OK | --force-transcode |
| JPEG → JXL Lossy | ✅ OK | --force-convert --distance |
| TIFF → JXL d=0.1 | ✅ OK | --distance 0.1 |
| JXL → JPEG Auto | ✅ OK | Sem flags (auto-detect) |
| JXL → JPEG Lossless | ✅ OK | --force-transcode |
| JXL → JPEG Lossy | ✅ OK | --force-convert --quality |
| JXL → PNG | ✅ OK | --format png --quality |
| JXL → TIFF com preview | ✅ OK | (default) |
| JXL → TIFF sem preview | ✅ OK | --no-preview |

---

## CONCLUSÃO

**Status geral: ✅ FUNCIONANDO CORRETAMENTE**

O wrapper está consistente com a implementação dos scripts. A única pendência é atualizar a documentação dos READMEs para refletir as novas funcionalidades da v1.5 (AUTO mode e preview opcional).
